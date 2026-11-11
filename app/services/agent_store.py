"""SQL Agent persistence. External calls never occur inside these transactions."""
from collections import OrderedDict
import copy
from datetime import datetime, timezone
import json
import re
import threading
import uuid
from fastapi import HTTPException
from sqlalchemy import select, text, func, or_, and_
from app import db
from app.models import AgentRun, AgentStep, ReviewTask, KnowledgeChunk, LLMLog, Ticket


def dt(value=None):
    if isinstance(value,datetime):return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
    if not value:return datetime.now(timezone.utc)
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def iso(value):
    return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat() if value else None


def compact(run):
    state=run.get('state',{})
    reviews={cid:{k:copy.deepcopy(v[k]) for k in ('task_id','contract_id','party_stance','reused','ticket_id','status','source_agent_run_id') if k in v}
             for cid,v in state.get('reviews',{}).items()}
    for cid,entry in reviews.items():
        if state['reviews'][cid].get('result'):entry['status']='completed'
    data={k:run[k] for k in ('summary','round_policy','contracts_pending_stance','tools','progress','cost','error_code','partial') if k in run}
    listed=state.get('listed')
    data['state']={'listed':None if listed is None else [{k:c[k] for k in ('id','name','title','size') if k in c} for c in listed],'contract_targets':state.get('contract_targets'),
        'fetched':{cid:{'id':cid} for cid in state.get('fetched',{})},'reviews':reviews,'written':state.get('written',{})}
    # Rehydrate law references from knowledge_chunks rather than copy law bodies.
    data['search_ids']=[r['chunk_id'] for r in run.get('search_results',state.get('search_results',[])) if r.get('chunk_id')]
    return data


def args_json(step):
    args=copy.deepcopy(step.get('args') or {})
    if step.get('tool')=='write_result' and 'result' in args:
        value=args['result'];args['result']={'task_id':value.get('task_id')} if isinstance(value,dict) else {}
    return json.dumps(args,ensure_ascii=False)


def assign(row,values):
    for key,value in values.items():
        old=getattr(row,key)
        same=(old is not None and dt(old)==dt(value)) if isinstance(value,datetime) else old==value
        if not same:setattr(row,key,value)


class AgentStore:
    def __init__(self,root=None,session_factory=None):
        # root remains accepted for caller compatibility; it is never written.
        self.sf=session_factory or db.SessionLocal
        self.lock=threading.RLock();self.cache=OrderedDict()

    def path(self,key):
        """Legacy validation hook. Persistence has no filesystem path."""
        if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{32}',key):raise HTTPException(404,'执行记录不存在')
        return key

    def save(self,run,insert_only=False):
        from app.services.agent import round_stats
        self.path(run['run_id'])
        if run.get('round_policy')=='dynamic-v1':run.update(round_stats(run))
        run['updated_at']=datetime.now(timezone.utc).isoformat()
        run.setdefault('created_at',run['updated_at'])
        with self.lock,self.sf.begin() as s:
            s.execute(text('BEGIN IMMEDIATE'))
            row=s.get(AgentRun,run['run_id'])
            if row is not None and insert_only:return False
            if row is None:
                row=AgentRun(id=run['run_id']);s.add(row)
            cost=run.get('cost',{})
            stopped=run.get('stopped_reason')
            values={'request':run.get('request',''),'status':run['status'],'created_by':run['user_id'],
                'contracts_total':run.get('contracts_total'),'contracts_processed':run.get('contracts_processed',len(run.get('state',{}).get('written',{}))),
                'rounds_used':run.get('rounds_used',len(run.get('steps',[]))),'rounds_limit':run.get('rounds_limit'),
                'stopped_reason':None if not stopped else ('MAX_ROUNDS_REACHED' if stopped=='MAX_ROUNDS_REACHED' else 'ERROR'),
                'planning_tokens':cost.get('planning',{}).get('total_tokens'),'review_tokens':cost.get('reviews',{}).get('total_tokens'),
                'total_calls':cost.get('total_model_calls',0),'error_message':run.get('summary') if run['status'] in {'failed','partial'} else None,
                'created_at':dt(run.get('created_at')),'started_at':dt(run.get('started_at') or run.get('created_at')),
                'finished_at':dt(run['finished_at']) if run.get('finished_at') else None,
                'context_json':json.dumps(compact(run),ensure_ascii=False)}
            assign(row,values)
            s.flush()
            existing={r.round:r for r in s.scalars(select(AgentStep).where(AgentStep.run_id==row.id))}
            candidates={v.get('task_id') for v in run.get('state',{}).get('reviews',{}).values()}
            ticket_ids={v.get('ticket_id') for v in run.get('state',{}).get('reviews',{}).values()};ticket_ids.discard(None)
            linked={r.id:r.review_task_id for r in s.scalars(select(Ticket).where(Ticket.id.in_(ticket_ids)))} if ticket_ids else {}
            candidates.update(linked.values())
            candidates.update(v.get('task_id') for v in run.get('steps',[]));candidates.discard(None)
            task_ids=set(s.scalars(select(ReviewTask.id).where(ReviewTask.id.in_(candidates)))) if candidates else set()
            for step in run.get('steps',[]):
                n=step['round'];entry=run.get('state',{}).get('reviews',{}).get(step.get('args',{}).get('contract_id'),{})
                task_id=(step.get('task_id') or entry.get('task_id') or linked.get(entry.get('ticket_id'))) if step.get('tool')=='review_contract' else None
                record=existing.get(n)
                if record is None:
                    record=AgentStep(id=uuid.uuid5(uuid.NAMESPACE_URL,row.id+':'+str(n)).hex,run_id=row.id,round=n);s.add(record)
                values={'max_rounds':step.get('max_rounds'),'decision':step.get('decision'), 'tool':step.get('tool'),
                    'args':args_json(step),'result_ok':int(step['result_ok']) if step.get('result_ok') is not None else None,
                    'result_summary':str(step.get('result_summary') or '')[:200], 'tokens':step.get('tokens'),
                    'created_at':dt(step.get('created_at') or step.get('started_at') or run.get('created_at')),
                    'review_task_id':task_id if task_id in task_ids else None}
                assign(record,values)
        with self.lock:
            self.cache[run['run_id']]=copy.deepcopy(run);self.cache.move_to_end(run['run_id'])
            while len(self.cache)>32:self.cache.popitem(last=False)
        return True

    @staticmethod
    def fields(row):
        names=('id','request','status','contracts_total','contracts_processed','rounds_used','rounds_limit','stopped_reason',
               'planning_tokens','review_tokens','total_calls','error_message','created_by')
        return {k:getattr(row,k) for k in names} | {k:iso(getattr(row,k)) for k in ('created_at','started_at','finished_at')}

    def read(self,key,user=None):
        self.path(key)
        if user is not None and user.role not in {'admin','reviewer'}:raise HTTPException(403,'角色不足')
        with self.lock,self.sf() as s:
            row=s.get(AgentRun,key)
            if row is None:raise HTTPException(404,'执行记录不存在')
            run=json.loads(row.context_json)
            run.update(self.fields(row));run.update(run_id=row.id,user_id=row.created_by)
            run.setdefault('state',{'listed':None,'fetched':{},'reviews':{},'written':{}})
            steps=list(s.scalars(select(AgentStep).where(AgentStep.run_id==key).order_by(AgentStep.round)))
            run['steps']=[{'id':r.id,'round':r.round,'max_rounds':r.max_rounds,'decision':r.decision,'tool':r.tool,
                'args':json.loads(r.args),'result_ok':None if r.result_ok is None else bool(r.result_ok),
                'result_summary':r.result_summary,'tokens':r.tokens,'created_at':iso(r.created_at),
                'task_id':r.review_task_id,'review_task_id':r.review_task_id,
                'status':('running' if row.status=='running' else 'failed') if r.result_ok is None else 'completed' if r.result_ok else 'failed'} for r in steps]
            if run.get('search_ids'):
                laws={r.id:r for r in s.scalars(select(KnowledgeChunk).where(KnowledgeChunk.id.in_(run['search_ids'])))}
                run['search_results']=[{'chunk_id':key,'article_no':laws[key].article_no,'content':laws[key].content,
                    'source_file':laws[key].source_file} for key in run['search_ids'] if key in laws]
            # Only the active executor needs full transient text. DB is always
            # authoritative for lookup/status and the timeline after restart.
            cached=self.cache.get(key)
            if cached:
                run.update({k:copy.deepcopy(v) for k,v in cached.items() if k not in self.fields(row)})
                run.update(self.fields(row))
            for step in run['steps']:
                step['result_summary']=str(step.get('result_summary') or '')[:200]
                if step.get('result_ok') is None and row.status!='running':
                    step['status']='failed';step['result_summary']='运行已结束，该步骤执行结果未确认'
            return run

    def listing(self,limit=20,status=None,created_by=None):
        query=select(AgentRun)
        if status:query=query.where(AgentRun.status==status)
        if created_by:query=query.where(AgentRun.created_by==created_by)
        with self.sf() as s:rows=[self.fields(r) for r in s.scalars(query.order_by(AgentRun.created_at.desc(),AgentRun.id.desc()).limit(limit))]
        # A legal handoff may start a review after the Agent completed. Refresh
        # the displayed counters in ONE log query, without scanning JSON/files.
        keys={r['id'] for r in rows}
        if not keys:return rows
        groups={key:{'planning':[],'review':[]} for key in keys}
        origin=func.json_extract(ReviewTask.input_snapshot,'$.agent_run_id')
        planning_id=func.substr(LLMLog.request_id,7,32)
        with self.sf() as s:
            logs=s.execute(select(LLMLog,origin).outerjoin(ReviewTask,ReviewTask.id==LLMLog.review_id).where(or_(
                and_(LLMLog.request_id.like('agent:%'),planning_id.in_(keys)),origin.in_(keys)))).all()
            for log,owner in logs:
                key=log.request_id[6:38] if log.purpose=='agent' else owner
                if key in groups:groups[key]['planning' if log.purpose=='agent' else 'review'].append(log)
            for row in rows:
                group=groups[row['id']]
                for kind,field in [('planning','planning_tokens'),('review','review_tokens')]:
                    values=group[kind]
                    row[field]=sum(v.prompt_tokens+v.completion_tokens for v in values) if all(v.prompt_tokens is not None and v.completion_tokens is not None for v in values) else None
                row['total_calls']=len(group['planning'])+len(group['review'])
        with self.sf.begin() as s:
            for row in rows:
                record=s.get(AgentRun,row['id'])
                if record:assign(record,{k:row[k] for k in ('planning_tokens','review_tokens','total_calls')})
        return rows

    def record_cost(self,key,cost):
        values={'planning_tokens':cost['planning']['total_tokens'],'review_tokens':cost['reviews']['total_tokens'],
                'total_calls':cost['total_model_calls']}
        with self.sf.begin() as s:
            row=s.get(AgentRun,key)
            if row:assign(row,values)
        return values

    def recover(self):
        with self.sf() as s:keys=list(s.scalars(select(AgentRun.id).where(AgentRun.status=='running')))
        for key in keys:
            run=self.read(key);run.update(status='failed',partial=True,error_code='AGENT_INTERRUPTED',stopped_reason='ERROR',
                finished_at=datetime.now(timezone.utc).isoformat(),summary='服务重启，Agent已中断；已有审查任务及结果保留，未自动重试或写回。')
            for step in run['steps']:
                if step['result_ok'] is None:step.update(result_ok=False,status='failed',result_summary='服务重启时此步骤未结束，执行结果需核对')
            self.save(run)
