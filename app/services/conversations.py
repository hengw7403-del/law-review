import json,re,time,uuid
from datetime import datetime,timezone,timedelta
from typing import List,Optional,Literal
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,StrictBool,Field
from sqlalchemy import select,text,func
from app import db
from app.models import Conversation,ConversationMessage,Contract,Ticket,LLMLog,User
from app.services.auth import Principal
from app.services.access import visibility,visible_contract
from app.services.llm import CallContext,LLMClient
from app.services.review_tasks import utc_now
from app.prompts.ticket_intent import SYSTEM

GREETING='你好，请告诉我想审查哪份合同；也可以说明重点关注的问题。'
DEFAULT={'contract_id':None,'focus_points':[],'priority':'normal'}

class Slots(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    contract_id: Optional[str]
    focus_points: List[str]=Field(max_length=6)
    priority: Literal['low','normal','urgent']

class Intent(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    reply: str=Field(min_length=1,max_length=600)
    slots: Slots
    ready: StrictBool

def owned(session,key,user):
    row=session.scalar(select(Conversation).where(Conversation.id==key,True if user.role=='admin' else Conversation.user_id==user.id))
    if row is None:raise HTTPException(404,'资源不存在')
    return row


def conversation_owner(session, row):
    owner=session.get(User,row.user_id)
    return Principal(owner.id,owner.username,owner.role,owner.auth_version,owner.org_name)

def history(session,key):
    return [{'role':m.role,'content':m.content,'created_at':m.created_at} for m in session.scalars(select(ConversationMessage).where(ConversationMessage.conversation_id==key).order_by(ConversationMessage.created_at,ConversationMessage.id))]

def costs(session,key):
    rows=list(session.scalars(select(LLMLog).where(LLMLog.request_id.like(key+':%'),LLMLog.purpose=='conversation')))
    complete=all(r.prompt_tokens is not None and r.completion_tokens is not None for r in rows)
    return {'model_calls':len(rows),'total_tokens':sum((r.prompt_tokens or 0)+(r.completion_tokens or 0) for r in rows) if complete else None,'duration_ms':sum(r.duration_ms or 0 for r in rows)}

def start(user):
    with db.SessionLocal.begin() as s:
        s.execute(text('BEGIN IMMEDIATE'))
        row=s.scalar(select(Conversation).where(Conversation.user_id==user.id,Conversation.status=='active'))
        if not row:
            row=Conversation(id=uuid.uuid4().hex,user_id=user.id,collected=json.dumps(DEFAULT),created_at=utc_now(),updated_at=utc_now());s.add(row);s.flush()
            s.add(ConversationMessage(id=uuid.uuid4().hex,conversation_id=row.id,role='assistant',content=GREETING,created_at=utc_now()))
        return {'conversation_id':row.id,'greeting':GREETING}

def read(key,user):
    with db.SessionLocal() as s:
        row=owned(s,key,user);collected=json.loads(row.collected)
        ticket=s.scalar(select(Ticket.id).where(Ticket.conversation_id==key))
        actor=conversation_owner(s,row)
        candidates=[{'id':c.id,'title':c.title} for c in s.scalars(select(Contract).where(visibility(actor),Contract.parse_status=='parsed').order_by(Contract.created_at.desc(),Contract.id.desc()).limit(10))]
        return {'conversation_id':key,'status':row.status,'slots':{k:collected.get(k,v) for k,v in DEFAULT.items()},'messages':history(s,key),'ticket_id':ticket,'cost':costs(s,key),'candidates':candidates}

def abandon(key,user):
    with db.SessionLocal.begin() as s:
        s.execute(text('BEGIN IMMEDIATE'));row=owned(s,key,user);lease=json.loads(row.collected).get('_inflight')
        if lease and time.time()-lease['time']<120:raise HTTPException(409,'消息仍在处理中，请稍后重新开始')
        if row.status=='active':row.status='abandoned';row.updated_at=utc_now()
        return {'status':row.status}

def send(key,user,content):
    if not content.strip():raise HTTPException(422,'消息不能为空')
    turn=uuid.uuid4().hex
    with db.SessionLocal.begin() as s:
        s.execute(text('BEGIN IMMEDIATE'));row=owned(s,key,user)
        if row.status!='active':raise HTTPException(409,'会话已完成，请查看工单')
        actor=conversation_owner(s,row)  # Admin acts for the owner, not as a new requester.
        collected=json.loads(row.collected);lease=collected.get('_inflight')
        if lease and time.time()-lease['time']<120:raise HTTPException(409,'上一条消息仍在处理中，请稍后重试')
        usage=costs(s,key)
        if usage['model_calls']>=5 or usage['total_tokens'] is None:raise HTTPException(409,'会话调用预算已用完或用量未知，请人工处理')
        candidates=[{'id':c.id,'title':c.title[:100]} for c in s.scalars(select(Contract).where(visibility(actor),Contract.parse_status=='parsed').order_by(Contract.created_at.desc(),Contract.id.desc()).limit(10))]
        messages=[{'role':'system','content':SYSTEM+'\n'+json.dumps({'slots':{k:collected.get(k,v) for k,v in DEFAULT.items()},'candidates':candidates},ensure_ascii=False)}]
        messages += [{'role':m['role'],'content':m['content']} for m in history(s,key)]
        messages.append({'role':'user','content':content})
        # Conservative UTF-8 upper bound plus framing/output allowance, not an assumed probability/tokenizer.
        if usage['total_tokens']+sum(len(m['content'].encode('utf-8')) for m in messages)+512+256>8000:raise HTTPException(409,'本轮可能超过8000 token预算，请缩短输入或人工处理')
        collected['_inflight']={'id':turn,'time':time.time()};row.collected=json.dumps(collected);row.updated_at=utc_now()
    client=LLMClient(db.SessionLocal)
    context=CallContext(key+':'+turn,purpose='conversation')
    try:
        raw,_,_=client.complete(messages,context,max_retries=0,max_output_tokens=512)
        intent=Intent.model_validate_json(raw)
        if any(not v.strip() or len(v)>80 for v in intent.slots.focus_points):raise ValueError('Invalid focus')
        slots=intent.slots.model_dump()
        if slots['contract_id'] is not None and slots['contract_id'] not in {c['id'] for c in candidates}:
            slots['contract_id']=None
            intent.reply='没有找到可确认的合同，请从最近上传的合同中选择。'
        chosen=next((c for c in candidates if c['id']==slots['contract_id']),None)
        if chosen and sum(c['title']==chosen['title'] for c in candidates)>1 and chosen['id'] not in '\n'.join(m['content'] for m in messages if m['role']=='user'):
            slots['contract_id']=None
            intent.reply='有多份同名合同，请点击候选合同，使用ID确认具体哪一份。'
        if re.search(r'你是谁|天气|讲个笑话',content):
            slots={k:collected.get(k,v) for k,v in DEFAULT.items()}
            intent.ready=False
            intent.reply='我是法务工单助手，可以帮你选择合同、记录关注点并创建初审工单。你想审查哪份合同？'
        ready=bool(intent.ready and slots['contract_id'] and re.search(r'确认|提交|创建|建单|是的|可以|就这个|没问题',content) and not re.search(r'不确认|别提交|不要|取消|你是谁',content))
        with db.SessionLocal.begin() as s:
            s.execute(text('BEGIN IMMEDIATE'));row=owned(s,key,user)
            if json.loads(row.collected).get('_inflight',{}).get('id')!=turn:raise HTTPException(409,'会话已变化，请刷新')
            existing=history(s,key);row.collected=json.dumps(slots,ensure_ascii=False);row.updated_at=utc_now()
            last=datetime.fromisoformat(existing[-1]['created_at']) if existing else datetime.now(timezone.utc)
            if last.tzinfo is None:last=last.replace(tzinfo=timezone.utc)
            user_time=max(datetime.now(timezone.utc),last+timedelta(microseconds=1))
            s.add(ConversationMessage(id=uuid.uuid4().hex,conversation_id=key,role='user',content=content,created_at=user_time.isoformat()))
            ticket=None
            if ready:
                contract=visible_contract(s,slots['contract_id'],actor)
                if contract.parse_status!='parsed':raise HTTPException(409,'合同当前不可审查')
                from app.services.ticket_title import ticket_title
                ticket=Ticket(id=uuid.uuid4().hex,title=ticket_title(contract),conversation_id=key,contract_id=contract.id,requester_id=actor.id,focus_points=json.dumps(slots['focus_points'],ensure_ascii=False),priority=slots['priority'],raw_request='\n'.join([m['content'] for m in existing if m['role']=='user']+[content]),created_at=utc_now(),updated_at=utc_now());s.add(ticket);row.status='completed';intent.reply='工单已创建，正在自动启动审查。'
            elif intent.ready:
                intent.reply='请确认以上合同、关注点和优先级，确认后再创建工单。'
            assistant_id=uuid.uuid4().hex
            s.add(ConversationMessage(id=assistant_id,conversation_id=key,role='assistant',content=intent.reply,created_at=max(datetime.now(timezone.utc),user_time+timedelta(microseconds=1)).isoformat()))
            response={'reply':intent.reply,'slots':slots,'ready':ready,'candidates':candidates,'ticket':None,'cost':costs(s,key)}
        if ticket is not None:
            from app.services.tickets import review
            try:review(ticket.id,actor)
            except Exception:pass  # Ticket remains committed; its persisted error is returned below.
            with db.SessionLocal.begin() as s:
                saved=s.get(Ticket,ticket.id)
                response['ticket']={k:getattr(saved,k) for k in ['id','title','status','review_task_id','review_error']}
                response['reply']='工单已创建，系统正在审查。' if saved.review_task_id else '工单已创建，但自动审查尚未启动：'+(saved.review_error or '请稍后重试')
                s.get(ConversationMessage,assistant_id).content=response['reply']
        return response
    except HTTPException:raise
    except Exception as exc:
        if context.attempts and not context.attempts[-1].get('error_code'):client.mark_invalid_output(context)
        raise HTTPException(502,'对话服务暂时不可用或输出格式错误，请重试') from exc
    finally:
        client.close()
        with db.SessionLocal.begin() as s:
            row=owned(s,key,user);value=json.loads(row.collected)
            if value.get('_inflight',{}).get('id')==turn:
                value.pop('_inflight',None);row.collected=json.dumps(value,ensure_ascii=False)
