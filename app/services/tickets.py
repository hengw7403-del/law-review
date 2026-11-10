import json,uuid
from datetime import datetime,timezone
from fastapi import HTTPException
from sqlalchemy import select,text
from app import db
from app.models import Ticket,Contract,User,ReviewTask,Conversation
from app.services.access import visible_contract,ticket_visibility
from app.services.conversations import history,costs
from app.services.review_tasks import create_review,utc_now,ReviewError,task_metrics
from app.services.review_rules import CLAUSE_RULES,MISSING_RULES

def permitted(user):
    return ticket_visibility(user)

def visible(s,key,user):
    ticket=s.scalar(select(Ticket).where(Ticket.id==key,permitted(user)))
    if ticket is None:raise HTTPException(404,'资源不存在')
    return ticket

def sync(s,row):
    if row.status=='reviewing' and row.review_task_id:
        task=s.get(ReviewTask,row.review_task_id)
        if task and task.status in {'completed','partial_failed','failed'}:
            from app.services.ticket_completion import complete_tickets
            complete_tickets(s,task)

def serialize(s,row):
    requester=s.get(User,row.requester_id);assignee=s.get(User,row.assignee_id) if row.assignee_id else None
    from app.services.ticket_title import ticket_title
    return {key:getattr(row,key) for key in ['id','title','conversation_id','contract_id','requester_id','assignee_id','status','priority','raw_request','review_task_id','created_at','updated_at','high_risk_count','needs_human_reason','confirmed_by','confirmed_at','confirmation_note','review_error']} | {'title':ticket_title(s.get(Contract,row.contract_id)),'focus_points':json.loads(row.focus_points),'requester_name':requester.username,'assignee_name':assignee.username if assignee else None}

def listing(user,status=None):
    with db.SessionLocal.begin() as s:
        # Synchronize completion before filtering, without adding worker coupling.
        rows=list(s.scalars(select(Ticket).where(permitted(user)).order_by(Ticket.created_at.desc(),Ticket.id.desc())))
        for row in rows:sync(s,row)
        valid={'pending','reviewing','needs_human','done','closed'}
        if status and status not in valid:raise HTTPException(422,'不支持的工单状态')
        return {'tickets':[serialize(s,row) for row in rows if not status or row.status==status]}

def detail(key,user):
    with db.SessionLocal.begin() as s:
        row=visible(s,key,user);sync(s,row);data=serialize(s,row)
        conversation=s.get(Conversation,row.conversation_id) if row.conversation_id else None
        data['messages']=history(s,row.conversation_id) if conversation and (user.role=='admin' or user.id==conversation.user_id) else []
        conversation_cost=costs(s,row.conversation_id) if row.conversation_id else {'model_calls':0,'total_tokens':0,'duration_ms':0}
        review_cost=task_metrics(s,row.review_task_id) if row.review_task_id else {'model_calls':0,'total_tokens':0,'model_duration_ms':0}
        data['cost']={'conversation':conversation_cost,'review':review_cost,'model_calls':conversation_cost['model_calls']+review_cost['model_calls'],
                      'total_tokens':None if conversation_cost['total_tokens'] is None or review_cost['total_tokens'] is None else conversation_cost['total_tokens']+review_cost['total_tokens']}
        return data

def review(key,user):
    with db.SessionLocal() as s:
        row=visible(s,key,user)
        if row.review_task_id and row.status in {'reviewing','needs_human','done'}:return {'task_id':row.review_task_id,'status':row.status}
        if row.status!='pending':raise HTTPException(409,'工单当前不可触发审查')
        visible_contract(s,row.contract_id,user);contract_id=row.contract_id
    def bind(s,task_id):
        row=s.get(Ticket,key)
        if row.status!='pending':raise ReviewError('TICKET_CHANGED','工单状态已变化')
        row.review_task_id=task_id;row.status='reviewing';row.review_error=None;row.updated_at=utc_now()
    try:
        result=create_review(db.SessionLocal,contract_id,rules=list(CLAUSE_RULES),missing_rules=list(MISSING_RULES),created_by=user.id,_on_created=bind)
        if result.get('deduplicated'):raise ReviewError('ACTIVE_REVIEW_EXISTS','合同已有活动审查任务，请稍后重试')
        return {'task_id':result['task_id'],'status':'reviewing'}
    except Exception as exc:
        with db.SessionLocal.begin() as s:
            row=s.get(Ticket,key)
            if row.status=='pending':row.review_error=exc.message if isinstance(exc,ReviewError) else '自动触发审查失败，请稍后重试';row.updated_at=utc_now()
        if isinstance(exc,ReviewError):raise HTTPException(exc.status_code,exc.message)
        raise

def confirm(key,user,note=''):
    if user.role not in {'reviewer','admin'}:raise HTTPException(403,'角色不足')
    with db.SessionLocal.begin() as s:
        s.execute(text('BEGIN IMMEDIATE'));row=visible(s,key,user);sync(s,row)
        if row.status!='needs_human':raise HTTPException(409,'只有待人工确认的工单可以确认')
        row.status='done';row.confirmed_by=user.id;row.confirmed_at=datetime.now(timezone.utc);row.confirmation_note=note;row.updated_at=utc_now()
        return serialize(s,row)

def close(key,user):
    with db.SessionLocal.begin() as s:
        s.execute(text('BEGIN IMMEDIATE'));row=visible(s,key,user);sync(s,row)
        if row.status in {'reviewing','needs_human'}:raise HTTPException(409,'审查执行中或待人工确认，暂不能关闭')
        row.status='closed';row.updated_at=utc_now();return serialize(s,row)
