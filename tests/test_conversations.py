import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from openai import OpenAI
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from app import db
from app.models import Base,Contract,Ticket,Conversation
from app.api.conversations import router
from app.services.auth import current_user,Principal
from app.services.llm import LLMClient
from tests.review_helpers import seed_contract_a

class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.engine=db.create_db_engine('sqlite:///'+(Path(self.tmp.name)/'test.db').as_posix());Base.metadata.create_all(self.engine)
        self.sf=sessionmaker(bind=self.engine,expire_on_commit=False);self.cid=seed_contract_a(self.sf)
        with self.sf() as s:self.uid=s.get(Contract,self.cid).owner_id
        self.app=FastAPI();self.app.include_router(router);self.app.dependency_overrides[current_user]=lambda:Principal(self.uid,'u','reviewer',1)
        self.patch=patch.object(db,'SessionLocal',self.sf);self.patch.start();self.client=TestClient(self.app)
    def tearDown(self):self.client.close();self.patch.stop();self.engine.dispose();self.tmp.cleanup()
    def model(self,slots,ready=False):
        def handle(request):return httpx.Response(200,json={'id':'mock','model':'mock','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps({'reply':'请确认选择','slots':slots,'ready':ready})}}],'usage':{'prompt_tokens':100,'completion_tokens':40,'total_tokens':140}})
        sdk=OpenAI(api_key='test',base_url='https://test.invalid',http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        return patch('app.services.conversations.LLMClient',side_effect=lambda sf:LLMClient(sf,sdk_client=sdk,emit_metrics=lambda r:None))
    def test_three_turn_idempotent_completion_and_ownership(self):
        key=self.client.post('/conversations').json()['conversation_id']
        self.assertEqual(self.client.post('/conversations').json()['conversation_id'],key)
        with self.model({'contract_id':None,'focus_points':[],'priority':'normal'}):
            response=self.client.post('/conversations/'+key+'/messages',json={'content':'你是谁'})
            self.assertFalse(response.json()['ready']);self.assertIn('工单助手',response.json()['reply'])
        with self.model({'contract_id':self.cid,'focus_points':['付款'],'priority':'normal'}):
            self.assertFalse(self.client.post('/conversations/'+key+'/messages',json={'content':self.cid+'，关注付款'}).json()['ready'])
        with self.model({'contract_id':self.cid,'focus_points':['付款'],'priority':'normal'},True):
            result=self.client.post('/conversations/'+key+'/messages',json={'content':'确认创建'}).json()
            self.assertTrue(result['ready']);self.assertEqual(result['cost']['model_calls'],3)
            self.assertEqual(result['ticket']['status'],'reviewing');self.assertTrue(result['ticket']['review_task_id'])
        self.assertEqual(self.client.get('/conversations/'+key).json()['status'],'completed')
        from app.services.tickets import detail
        self.assertEqual(detail(result['ticket']['id'],Principal(self.uid,'u','reviewer',1))['cost']['total_tokens'],420)
        self.assertEqual(self.client.post('/conversations/'+key+'/messages',json={'content':'再建'}).status_code,409)
        self.app.dependency_overrides[current_user]=lambda:Principal('other','other','reviewer',1)
        self.assertEqual(self.client.get('/conversations/'+key).status_code,404)
    def test_forged_contract_cannot_create_ticket(self):
        key=self.client.post('/conversations').json()['conversation_id']
        with self.model({'contract_id':'forged','focus_points':[],'priority':'normal'},True):
            r=self.client.post('/conversations/'+key+'/messages',json={'content':'确认创建'}).json()
        self.assertFalse(r['ready']);self.assertIsNone(r['ticket']);self.assertTrue(r['candidates'])

    def test_explicit_abandon_releases_active_slot(self):
        key=self.client.post('/conversations').json()['conversation_id']
        self.assertEqual(self.client.post('/conversations/'+key+'/abandon').json()['status'],'abandoned')
        self.assertNotEqual(self.client.post('/conversations').json()['conversation_id'],key)
