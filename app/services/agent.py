"""Bounded Agent coordinator; independent threads and database checkpoints.

Existing contract/review services and the normal worker perform the review.
No model or MCP HTTP call holds a database session/transaction open.
"""
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from app import db
from app.models import LLMLog, ReviewTask
from app.prompts.agent import SYSTEM, REVIEW_TOOL, SEARCH_TOOL
from app.services.agent_tools import search_law, reusable_handoff, content_hash
from app.services.agent_llm import AgentLLMClient
from app.services.contracts import create_contract
from app.services.llm import CallContext, LLMBudgetExceeded
from app.services.mcp_client import MCPClient, MCPError
from app.services.party_parser import party_detection
from app.services.results import review_items
from app.services.review_rules import CLAUSE_RULES, MISSING_RULES
from app.services.review_tasks import create_review, get_task, utc_now


BASE_MAX_ROUNDS=10
MAX_ROUNDS_CEILING=40
PER_CONTRACT_ROUNDS=3
MAX_PLANNING_TOKENS=50000
MAX_MODEL_CALLS=200


def max_rounds(contract_count=None):
    """Unknown scope gets a base budget; known scope gets three steps per contract."""
    if contract_count is None:return min(BASE_MAX_ROUNDS,MAX_ROUNDS_CEILING)
    if isinstance(contract_count,bool) or not isinstance(contract_count,int) or contract_count<0:
        raise ValueError('合同数量必须是非负整数或None')
    return min(2+contract_count*PER_CONTRACT_ROUNDS,MAX_ROUNDS_CEILING)


def round_stats(run):
    state=run.get('state',{})
    targets=state.get('contract_targets')
    # Freeze the FIRST successful list: pending lists shrink after write-back
    # and retries must not change the original scope or inflate the limit.
    if targets is None and state.get('listed') is not None:
        targets=[c['id'] for c in state['listed']]
        state['contract_targets']=targets
    total=len(targets) if targets is not None else None
    written=set(state.get('written',{}))
    processed=len(written.intersection(targets)) if targets is not None else len(written)
    if total is None and run.get('status')!='running':
        requested=set(x.upper() for x in re.findall(r'\bC\d{3}\b',run.get('request',''),re.I))
        if requested:total=len(requested)
        elif state.get('reviews'):total=len(state['reviews'])
    return {'rounds_used':len(run.get('steps',[])),
            'rounds_limit':max_rounds(len(targets) if targets is not None else None),
            'contracts_processed':processed,'contracts_total':total,
            'contracts_pending_stance':sum(v.get('status')=='needs_stance' for v in state.get('reviews',{}).values()),
            'stopped_reason':run.get('stopped_reason')}


def log_round(run,step):
    print(json.dumps({'event':'agent_round','run_id':run['run_id'],
        **{k:step.get(k) for k in ['round','max_rounds','decision','tool','args','result_ok']},
        'max_rounds_after':round_stats(run)['rounds_limit']},ensure_ascii=False),flush=True)


def require_agent_role(user):
    if user.role not in {'admin','reviewer'}:raise HTTPException(403,'仅法务和管理员可使用AI助手')


from app.services.agent_store import AgentStore


def costs(session_factory,run_id):
    prefix='agent:'+run_id+':%'
    with session_factory() as s:
        task_ids=select(ReviewTask.id).where(func.json_extract(ReviewTask.input_snapshot,'$.agent_run_id')==run_id)
        logs=list(s.scalars(select(LLMLog).where(or_(LLMLog.request_id.like(prefix),LLMLog.review_id.in_(task_ids)))))
    def group(rows):
        known=all(r.prompt_tokens is not None and r.completion_tokens is not None for r in rows)
        return {'model_calls':len(rows),'total_tokens':sum((r.prompt_tokens or 0)+(r.completion_tokens or 0) for r in rows) if known else None,
                'known_tokens':sum((r.prompt_tokens or 0)+(r.completion_tokens or 0) for r in rows),
                'duration_ms':sum(r.duration_ms or 0 for r in rows),'usage_complete':known}
    return {'planning':group([r for r in logs if r.purpose=='agent']),
            'reviews':group([r for r in logs if r.purpose!='agent']),'total_model_calls':len(logs)}


class AgentRunner:
    def __init__(self,store,session_factory,client_factory=MCPClient,llm_factory=AgentLLMClient,
                 poll_interval=1,review_timeout=600,stop=None):
        self.store=store;self.sf=session_factory;self.client_factory=client_factory;self.llm_factory=llm_factory
        self.poll_interval=poll_interval;self.review_timeout=review_timeout;self.stop=stop or threading.Event()

    def check_budget(self,run,messages=None,tools=None):
        cost=costs(self.sf,run['run_id']);run['cost']=cost
        if cost['total_model_calls']>=MAX_MODEL_CALLS:raise LLMBudgetExceeded('规划与审查累计调用已达200次')
        if not cost['planning']['usage_complete']:raise LLMBudgetExceeded('规划调用用量未知，停止继续计费')
        if messages is not None:
            # Conservative UTF-8 bound plus framing/output allowance. This is
            # not a claim that bytes equal DeepSeek tokens.
            upper=len(json.dumps([messages,tools],ensure_ascii=False).encode('utf-8'))+3072
            if cost['planning']['known_tokens']+upper>MAX_PLANNING_TOKENS:
                raise LLMBudgetExceeded('继续规划可能超过50000 tokens上限')
        return cost

    def review(self,run,user,args):
        cid=args.get('contract_id');state=run['state']
        if set(args)-{'contract_id','stance','party_stance'} or not isinstance(cid,str):
            raise MCPError('INVALID_TOOL_ARGUMENTS','review_contract需要contract_id与可选stance')
        requested=args.get('stance',args.get('party_stance'))
        if 'stance' in args and 'party_stance' in args and args['stance']!=args['party_stance']:
            raise MCPError('INVALID_TOOL_ARGUMENTS','stance与party_stance不能冲突')
        if requested is not None and requested not in {'party_a','party_b'}:
            raise MCPError('INVALID_TOOL_ARGUMENTS','stance只能为party_a或party_b')
        if cid not in state['fetched']:raise MCPError('GET_CONTRACT_FIRST','请先get_contract获取该合同')
        source=state['fetched'][cid]
        if cid not in state['reviews']:
            if state.get('contract_targets') is not None and cid not in state['contract_targets']:
                raise MCPError('TARGET_MISMATCH','该合同不在首次列表确定的任务范围中')
            suggested=party_detection(source['content'],user.org_name)['suggested_stance']
            # The model may pass a stance only if the user explicitly supplied it.
            stated=set(re.findall(r'(?:我方(?:立场)?(?:是|为)?\s*[:：]?\s*|站在)(甲方|乙方)',run['request']))
            # A reference to another task's historic stance is not an instruction
            # to apply that stance to every contract in the batch.
            stated.update(re.findall(r'(?:^|[，,；;\s])(?:按|用|采用|使用|站在)?(甲方|乙方)立场(?:审查|审核|审阅)?(?=[，,。；;\s]|$)',run['request']))
            stated.update({'甲方' if v=='party_a' else '乙方' for v in re.findall(r'\bparty_[ab]\b',run['request'])})
            if len(stated)>1:raise MCPError('STANCE_REQUIRED','请求中包含多个我方立场，请明确后重新发起')
            explicit=('party_a' if '甲方' in stated else 'party_b') if stated else None
            requested_ids=set(x.upper() for x in re.findall(r'\bC\d{3}\b',run['request'],re.I))
            if requested_ids and not re.search('都|全部|所有',run['request']) and cid not in requested_ids:
                raise MCPError('TARGET_MISMATCH','该合同不在用户指定的审查目标中')
            source_stance=source.get('stance')
            if source_stance is not None and source_stance not in {'party_a','party_b'}:
                raise MCPError('INVALID_OA_DATA','OA立场字段无效')
            stance=explicit or source_stance or suggested
            if requested and requested!=stance:
                raise MCPError('STANCE_REQUIRED','不能由模型自行选择立场；请按组织匹配或让用户明确甲乙方')
            endpoint=state.get('source_system','unknown')
            reusable=reusable_handoff(self.sf,endpoint,cid,source,stance,user) if stance else None
            if reusable:
                state['reviews'][cid]=reusable
            else:
                uploaded=create_contract(self.sf,user,source.get('name','oa-contract.txt'),source['content'].encode('utf-8'),stance,
                                         'OA '+cid+' · '+source.get('name',cid))
            if not stance:
                from app.services.tickets import stance_pending
                ticket_id=stance_pending(self.sf,uploaded['contract_id'],user,run['request'],source_system=endpoint,
                    source_contract_id=cid,source_content_hash=content_hash(source),agent_run_id=run['run_id'])
                state['reviews'][cid]={'contract_id':uploaded['contract_id'],'status':'needs_stance',
                    'ticket_id':ticket_id,'task_id':None,'party_stance':None}
                self.store.save(run)
                return {'contract_id':cid,'status':'needs_stance','ticket_id':ticket_id,
                        'summary':'已交法务明确我方立场，尚未审查'}
        if state['reviews'].get(cid,{}).get('status')=='needs_stance':
            return {'contract_id':cid,**state['reviews'][cid]}
        if cid not in state['reviews']:
            cost=self.check_budget(run)
            reserve=max(1,round_stats(run)['rounds_limit']-len(run['steps']))
            remaining=MAX_MODEL_CALLS-cost['total_model_calls']-reserve
            def cap(s,task_id):
                task=s.get(ReviewTask,task_id);snapshot=json.loads(task.input_snapshot)
                if len(snapshot['plan'])>remaining:raise LLMBudgetExceeded('剩余调用预算不足以完整审查此合同')
                snapshot['call_limit']=min(snapshot['call_limit'],remaining)
                snapshot['agent_run_id']=run['run_id'];snapshot['oa_contract_id']=cid
                task.input_snapshot=json.dumps(snapshot,ensure_ascii=False)
            task=create_review(self.sf,uploaded['contract_id'],rules=list(CLAUSE_RULES),missing_rules=list(MISSING_RULES),
                               created_by=user.id,_on_created=cap)
            state['reviews'][cid]={'task_id':task['task_id'],'contract_id':uploaded['contract_id'],'party_stance':stance}
            self.store.save(run)
        entry=state['reviews'][cid];deadline=time.monotonic()+self.review_timeout
        while True:
            if self.stop.is_set():raise MCPError('AGENT_INTERRUPTED','Agent服务正在停止；审查任务保留')
            task=get_task(self.sf,entry['task_id'])
            run['progress']={'tool':'review_contract','contract_id':cid,'processed_items':task['processed_items'],'total_items':task['total_items'],'task_id':task['task_id']}
            run['cost']=costs(self.sf,run['run_id']);self.store.save(run)
            if task['status'] in {'completed','partial_failed','failed'}:break
            if time.monotonic()>deadline:raise MCPError('REVIEW_TIMEOUT','等待worker超时，任务保留，未写回OA')
            self.stop.wait(self.poll_interval)
        if task['status']!='completed':raise MCPError('REVIEW_INCOMPLETE','审查未全部完成，不能作为完整结果写回OA')
        result=review_items(self.sf,entry['task_id'])
        canonical={'task_id':entry['task_id'],'contract_id':entry['contract_id'],'party_stance':entry['party_stance'],
                   'reused':entry.get('reused',False),'ticket_id':entry.get('ticket_id'),
                   'status':task['status'],'stats':result['stats'],'processed_items':task['processed_items'],
                   'total_items':task['total_items'],'result_path':'/workspace/results/'+entry['task_id'],
                   'all_clauses_insufficient':result.get('all_clauses_insufficient',False),
                   'findings':[{'clause_no':i['clause_no'],'rule_id':i['rule_id'],'risk_level':i['risk_level'],
                                'description':i['description'],'suggestion':i['suggestion'],'evidence':i['evidence']}
                               for i in result['items'] if i['check_status']=='supported']}
        entry['result']=canonical;self.store.save(run)
        return {'contract_id':cid,'status':task['status'],
                'high':result['stats']['high'],'medium':result['stats']['medium'],'low':result['stats']['low'],
                'summary':'审查完成，高风险{}、中风险{}、低风险{}'.format(result['stats']['high'],result['stats']['medium'],result['stats']['low']),
                'result':canonical}

    def dispatch(self,run,user,client,name,args,external_names):
        # Local capabilities share the tool catalog with MCP; no second server.
        if name=='review_contract':return self.review(run,user,args)
        if name=='search_law':
            result=search_law(self.sf,args)
            run['state']['search_results']=result
            return result
        if name not in external_names:raise MCPError('UNKNOWN_TOOL','工具不存在，请从可用工具中选择')
        if name=='write_result':
            cid=args.get('contract_id');entry=run['state']['reviews'].get(cid,{})
            if not entry.get('result'):raise MCPError('REVIEW_REQUIRED','必须先完成真实review_contract，禁止写入模型编造的结果')
            # Canonical server result wins over every model-supplied field.
            output=client.call_tool(name,{'contract_id':cid,'result':entry['result']})
            if not isinstance(output,dict) or output.get('ok') is not True:raise MCPError('WRITE_NOT_ACKNOWLEDGED','OA没有确认写回成功')
            run['state']['written'][cid]=True
            return output
        output=client.call_tool(name,args)
        if name=='list_contracts':
            if not isinstance(output,list):raise MCPError('INVALID_OA_DATA','合同列表格式错误')
            ids=[c.get('id') if isinstance(c,dict) else None for c in output]
            if any(not isinstance(cid,str) or not cid for cid in ids) or len(set(ids))!=len(ids):
                raise MCPError('INVALID_OA_DATA','合同列表必须包含唯一且非空的ID')
            if run['state']['listed'] is None:
                run['state']['listed']=output
                run['state']['contract_targets']=ids
        elif name=='get_contract':
            if not isinstance(output,dict) or not isinstance(output.get('content'),str) or output.get('id')!=args.get('contract_id'):
                raise MCPError('INVALID_OA_DATA','合同正文或ID格式错误')
            run['state']['fetched'][output['id']]=output
        return output

    @staticmethod
    def factual_summary(run):
        state=run['state'];reviews=[v for v in state['reviews'].values() if v.get('result')]
        waiting=sum(v.get('status')=='needs_stance' for v in state['reviews'].values())
        if waiting:return '{}份完成，{}份待明确立场；已写回OA {}份。'.format(len(state['written']),waiting,len(state['written']))
        if 'search_results' in state:return '检索到{}条法规依据。'.format(len(state['search_results']))
        if reviews:
            high=sum(v['result']['stats'].get('high',0)>0 for v in reviews)
            return '已完成{}份合同审查，其中{}份有高风险；已写回OA {}份。'.format(len(reviews),high,len(state['written']))
        if state['listed'] is not None:
            return '找到{}份待审合同：{}。'.format(len(state['listed']),'、'.join(x.get('id','') for x in state['listed']))
        if state['fetched']:return '已获取{}份合同正文，尚未完成审查或写回。'.format(len(state['fetched']))
        return '尚未完成外部操作。'

    @staticmethod
    def planning_result(name,result):
        """Planner needs dispatch facts, not a second copy of contract/evidence.

        Full text/results remain in run state, the DB, and canonical OA output.
        Keeping only routing facts also keeps multi-contract planning in its
        existing 50k-token budget without changing review prompts or rules.
        """
        if name=='get_contract' and isinstance(result,dict) and 'content' in result:
            return {k:result[k] for k in ['id','name','size','stance'] if k in result} | {'content_received':True,'content_length':len(result['content'])}
        if name=='review_contract' and isinstance(result,dict) and 'result' in result:
            value=result['result']
            return {'contract_id':result.get('contract_id'),
                    'result':{k:value[k] for k in ['task_id','contract_id','status','stats','result_path','reused','ticket_id'] if k in value}}
        return result

    @staticmethod
    def limit_summary(run):
        stats=round_stats(run);state=run['state'];targets=state.get('contract_targets')
        if targets is None:
            return '合同总数尚未确定，已完成{}份；达到{}步上限，返回已完成部分。'.format(stats['contracts_processed'],stats['rounds_limit'])
        pending=[(n+1,cid) for n,cid in enumerate(targets) if cid not in state['written']]
        detail='、'.join('第{}份 {} 未处理完'.format(n,cid) for n,cid in pending)
        return '完成了{}/{}份；{}。达到{}步上限，返回已完成部分。'.format(
            stats['contracts_processed'],stats['contracts_total'],detail or '已写回全部目标，未完成最终收尾',stats['rounds_limit'])

    def execute(self,key,user):
        run=self.store.read(key);client=None;llm=None
        run.update(round_policy='dynamic-v1',stopped_reason=None)
        try:
            client=self.client_factory();external=client.list_tools()
            if any(t.get('name') in {'review_contract','search_law'} for t in external):raise MCPError('TOOL_NAME_COLLISION','外部工具与本地工具重名')
            run['state']['source_system']=getattr(client,'endpoint','unknown')
            tools=client.function_definitions(external)+[REVIEW_TOOL,SEARCH_TOOL];names={t['name'] for t in external}
            run['tools']=[t['function']['name'] for t in tools];self.store.save(run)
            messages=[{'role':'system','content':SYSTEM},{'role':'user','content':run['request']}]
            llm=self.llm_factory(self.sf)
            while True:
                if len(run['steps'])>=round_stats(run)['rounds_limit']:
                    raise MCPError('MAX_ROUNDS_REACHED',self.limit_summary(run))
                if self.stop.is_set():raise MCPError('AGENT_INTERRUPTED','服务停止，已有结果保留')
                messages[0]['content']=SYSTEM+'\n当前执行预算：'+json.dumps(round_stats(run),ensure_ascii=False)
                before=self.check_budget(run,messages,tools)['planning']['known_tokens'];self.store.save(run)
                context=CallContext('agent:'+key+':'+uuid.uuid4().hex,purpose='agent')
                message=llm.decide(messages,tools,context)
                planning_delta=costs(self.sf,key)['planning']['known_tokens']-before
                messages.append(message);calls=message.get('tool_calls') or []
                if not calls:
                    run['model_summary']=message.get('content','')
                    if re.search('审一下|审查|审一遍|审阅|审核',run['request']) and not run['state']['reviews'] and run['state']['listed']!=[]:
                        raise MCPError('REVIEW_NOT_EXECUTED','尚未完成真实审查')
                    deferred={cid for cid,v in run['state']['reviews'].items() if v.get('status')=='needs_stance'}
                    handled=set(run['state']['written'])|deferred
                    if run['state']['reviews'] and not set(run['state']['reviews']).issubset(handled):
                        raise MCPError('WRITEBACK_INCOMPLETE','审查或写回尚未完成')
                    if run['state']['reviews'] and re.search('都|全部|所有',run['request']):
                        targets=run['state']['listed']
                        if targets is None or not {c['id'] for c in targets}.issubset(handled):
                            raise MCPError('PARTIAL_REVIEW','尚未完成全部OA合同的审查和写回')
                    run.update(status='completed',summary=self.factual_summary(run))
                    if 'search_results' in run['state']:
                        run['answer']=message.get('content','');run['search_results']=run['state']['search_results']
                    break
                # Recheck after EVERY tool: the first list can change the limit
                # even inside a model response containing multiple tool calls.
                for call in calls:
                    if len(run['steps'])>=round_stats(run)['rounds_limit']:
                        raise MCPError('MAX_ROUNDS_REACHED',self.limit_summary(run))
                    function=call.get('function',{});name=function.get('name','')
                    step={'round':len(run['steps'])+1,'max_rounds':round_stats(run)['rounds_limit'],
                          'tokens':planning_delta,
                          'decision':'call '+name,'tool':name,'args':{},'result_ok':None,
                          'status':'running','result_summary':'正在执行','started_at':utc_now()}
                    run['steps'].append(step);self.store.save(run)
                    planning_delta=0
                    try:
                        args=json.loads(function.get('arguments','{}'))
                        if not isinstance(args,dict):raise ValueError('工具参数必须是JSON对象')
                        if len(json.dumps(args).encode())>65536:raise ValueError('工具参数超限')
                        step['args']=args
                        result=self.dispatch(run,user,client,name,args,names)
                        if name=='list_contracts':summary='找到{}份合同'.format(len(result))
                        elif name=='get_contract':summary='拿到{}正文，{}字符'.format(args.get('contract_id'),len(result['content']))
                        elif name=='review_contract':summary=('待法务明确立场' if result.get('status')=='needs_stance' else
                            ('复用真实审查，' if result['result'].get('reused') else '审查完成，')+'发现{}处高风险'.format(result['result']['stats']['high']))
                        elif name=='search_law':summary='检索到{}条法规'.format(len(result))
                        elif name=='write_result':summary='已写回OA'
                        else:summary='工具执行成功'
                        step.update(status='completed',result_summary=summary,result_ok=True)
                        if name=='review_contract':
                            value=result.get('result',result)
                            step.update(reused=value.get('reused',False),task_id=value.get('task_id'),ticket_id=value.get('ticket_id'))
                    except LLMBudgetExceeded:
                        raise
                    except Exception as exc:
                        if not isinstance(exc,(MCPError,ValueError)):
                            logging.getLogger(__name__).exception('Agent tool failed: %s',name)
                        result={'error':{'code':getattr(exc,'code',type(exc).__name__),'message':str(exc)}}
                        step.update(status='failed',error_code=result['error']['code'],result_summary=str(exc),result_ok=False)
                    step['finished_at']=utc_now();self.store.save(run);log_round(run,step)
                    messages.append({'role':'tool','tool_call_id':call['id'],
                                     'content':json.dumps(self.planning_result(name,result),ensure_ascii=False)})
        except Exception as exc:
            run.update(status='partial' if run['state'].get('written') else 'failed',partial=True,error_code=getattr(exc,'code',type(exc).__name__),
                       stopped_reason=getattr(exc,'code',type(exc).__name__),
                       summary=self.factual_summary(run)+' 未完成：'+str(exc))
            if run['error_code']=='MAX_ROUNDS_REACHED':run['summary']=self.limit_summary(run)
            for step in run['steps']:
                if step['status']=='running':
                    step.update(status='failed',result_ok=False,result_summary=str(exc),finished_at=utc_now());log_round(run,step)
        finally:
            for resource in [client,llm]:
                if resource:
                    try:resource.close()
                    except Exception:pass
            run['cost']=costs(self.sf,key);run['finished_at']=utc_now();self.store.save(run)


class AgentManager:
    def __init__(self,store=None,session_factory=None,runner_factory=AgentRunner):
        self.sf=session_factory or db.SessionLocal;self.store=store or AgentStore(session_factory=self.sf)
        self.runner_factory=runner_factory;self.lock=threading.Lock();self.active=set();self.stop=threading.Event()
        self.store.recover()

    def start(self,user,request):
        require_agent_role(user)
        with self.lock:
            if len(self.active)>=2:raise HTTPException(409,'AI助手执行中，请稍后再试')
            key=uuid.uuid4().hex
            run={'run_id':key,'user_id':user.id,'request':request,'status':'running','steps':[],
                 'round_policy':'dynamic-v1','stopped_reason':None,
                 'summary':'正在连接OA','state':{'listed':None,'fetched':{},'reviews':{},'written':{}},'created_at':utc_now()}
            self.store.save(run);self.active.add(key)
        def work():
            try:self.runner_factory(self.store,self.sf,stop=self.stop).execute(key,user)
            except Exception:
                logging.getLogger(__name__).exception('Agent failed outside runner: %s',key)
                try:
                    saved=self.store.read(key)
                    saved.update(status='failed',error_code='AGENT_INTERNAL_ERROR',stopped_reason='AGENT_INTERNAL_ERROR',partial=True,summary='Agent执行异常，已有任务与结果保留。')
                    self.store.save(saved)
                except Exception:logging.getLogger(__name__).exception('Unable to persist Agent failure')
            finally:
                with self.lock:self.active.discard(key)
        threading.Thread(target=work,name='agent-'+key[:8],daemon=True).start()
        return key

    def public(self,key,user):
        require_agent_role(user);run=self.store.read(key,user)
        fields={k:v for k,v in run.items() if k!='state'}
        # Historic fixed-limit runs are not retroactively relabeled dynamic.
        for k,v in {'rounds_used':len(run['steps']),'rounds_limit':None,
                    'contracts_processed':len(run['state']['written']),'contracts_total':None,
                    'stopped_reason':run.get('error_code') if run['status']=='failed' else None}.items():fields.setdefault(k,v)
        fields['cost']=costs(self.sf,key)
        fields.update(self.store.record_cost(key,fields['cost']))
        fields['details']=[{'oa_contract_id':cid,'task_id':entry.get('task_id'),'contract_id':entry['contract_id'],
                           'reused':entry.get('reused',False),'status':entry.get('status','completed' if entry.get('result') else 'reviewing'),
                           'ticket_id':entry.get('ticket_id'),
                           'ticket_path':'/workspace/tickets/'+entry['ticket_id'] if entry.get('ticket_id') else None,
                           'result_path':'/workspace/results/'+entry['task_id'] if entry.get('task_id') else None}
                          for cid,entry in run['state']['reviews'].items()]
        return fields
