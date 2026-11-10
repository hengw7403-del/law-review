from fastapi import APIRouter,Depends
from pydantic import BaseModel,Field
from app.services.auth import current_user
from app.services import conversations as service

router=APIRouter(prefix='/conversations',tags=['conversations'])
class Message(BaseModel):
    content: str=Field(min_length=1,max_length=500)

@router.post('')
def start(user=Depends(current_user)):return service.start(user)

@router.get('/{key}')
def read(key:str,user=Depends(current_user)):return service.read(key,user)

@router.post('/{key}/abandon')
def abandon(key:str,user=Depends(current_user)):return service.abandon(key,user)

@router.post('/{key}/messages')
def send(key:str,body:Message,user=Depends(current_user)):return service.send(key,user,body.content)
