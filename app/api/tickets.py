from typing import Optional,Literal
from fastapi import APIRouter,Depends
from pydantic import BaseModel,Field
from app.services.auth import current_user
from app.services import tickets as service
router=APIRouter(prefix='/tickets',tags=['tickets'])
class StanceRequest(BaseModel):
    stance:Literal['party_a','party_b']
@router.post('/{key}/stance',status_code=202)
def stance(key:str,body:StanceRequest,user=Depends(current_user)):return service.set_stance(key,user,body.stance)
@router.get('')
def listing(status:Optional[str]=None,user=Depends(current_user)):return service.listing(user,status)
@router.get('/{key}')
def detail(key:str,user=Depends(current_user)):return service.detail(key,user)
class Confirmation(BaseModel):
    note:str=Field(default='',max_length=2000)
@router.post('/{key}/confirm')
def confirm(key:str,body:Optional[Confirmation]=None,user=Depends(current_user)):return service.confirm(key,user,body.note if body else '')
@router.post('/{key}/review',status_code=202)
def review(key:str,user=Depends(current_user)):return service.review(key,user)
@router.post('/{key}/close')
def close(key:str,user=Depends(current_user)):return service.close(key,user)
