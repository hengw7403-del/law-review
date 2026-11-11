from fastapi import APIRouter, Depends, HTTPException, Request, Query
from typing import Optional, Literal
from pydantic import BaseModel, Field
from app.services.auth import current_user
from app.services.agent import require_agent_role

router=APIRouter(prefix='/agent',tags=['agent'])


class AgentRequest(BaseModel):
    request: str=Field(min_length=1,max_length=2000,strict=True)


def manager(request):
    value=getattr(request.app.state,'agent_manager',None)
    if value is None:raise HTTPException(503,'AI助手暂不可用，其他审查功能不受影响')
    return value


@router.post('/run',status_code=202)
def start(body:AgentRequest,request:Request,user=Depends(current_user)):
    require_agent_role(user)
    if not body.request.strip():raise HTTPException(422,'请求不能为空')
    return {'run_id':manager(request).start(user,body.request.strip())}


@router.get('/runs')
def runs(request:Request,limit:int=Query(default=20,ge=1,le=100),
         status:Optional[str]=None,
         created_by:Optional[str]=None,user=Depends(current_user)):
    require_agent_role(user)
    if status and status not in {'running','completed','failed','partial'}:raise HTTPException(422,'不支持的执行状态')
    return {'runs':manager(request).store.listing(limit,status,created_by)}


@router.get('/runs/{key}')
def get_run(key:str,request:Request,user=Depends(current_user)):
    require_agent_role(user)
    return manager(request).public(key,user)
