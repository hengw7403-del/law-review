import copy
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import db
from app.api.agent import router
from app.models import Contract,LLMLog,ReviewTask
from app.services.agent import AgentStore,AgentRunner,AgentManager,costs,max_rounds
from app.services.agent_llm import AgentLLMClient
from app.services.auth import Principal,current_user
from app.services.llm import CallContext
from app.services.mcp_client import MCPClient,MCPError
from tests.review_helpers import seed_contract_a
from tests.test_mcp_client import TOOLS


def call(name,args=None):
    return {'role':'assistant','tool_calls':[{'id':uuid.uuid4().hex,'type':'function','function':{'name':name,'arguments':json.dumps(args or {})}}]}


class FakeMCP:
    function_definitions=staticmethod(MCPClient.function_definitions)
    def __init__(self,contract_count=3):self.writes=[];self.contract_count=contract_count
    def list_tools(self):return TOOLS
    def close(self):pass
    def call_tool(self,name,args):
        if name=='list_contracts':return [{'id':'C{:03d}'.format(n+1)} for n in range(self.contract_count)]
        if name=='get_contract':return {'id':args['contract_id'],'name':'contract.txt','content':'甲方：星桥科技\n乙方：澄川技术\n第一条 服务范围\n'+'提供技术服务。'*20}
        if name=='write_result':self.writes.append(copy.deepcopy(args));return {'ok':True}
        raise MCPError('UNKNOWN_TOOL','Unknown tool')


class FakeLLM:
    def __init__(self,messages):self.responses=iter(messages);self.requests=[]
    def decide(self,messages,tools,context):self.requests.append(copy.deepcopy(messages));return next(self.responses)
    def close(self):pass


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.engine=db.create_db_engine('sqlite:///'+(Path(self.tmp.name)/'agent.db').as_posix())
        db.Base.metadata.create_all(self.engine);self.sf=sessionmaker(bind=self.engine,autoflush=False)
        cid=seed_contract_a(self.sf);self.contract_id=cid
        with self.sf() as s:self.uid=s.get(Contract,cid).owner_id
        self.user=Principal(self.uid,'test','reviewer',1,'澄川技术')
        self.store=AgentStore(Path(self.tmp.name)/'runs')

    def tearDown(self):self.engine.dispose();self.tmp.cleanup()

    def run_agent(self,responses,request='列出OA合同',runner_type=AgentRunner,client=None):
        key=uuid.uuid4().hex;client=client or FakeMCP();llm=FakeLLM(responses)
        self.store.save({'run_id':key,'user_id':self.uid,'request':request,'status':'running','steps':[],
                         'state':{'listed':None,'fetched':{},'reviews':{},'written':{}}})
        runner=runner_type(self.store,self.sf,client_factory=lambda:client,llm_factory=lambda sf:llm)
        runner.execute(key,self.user)
        return self.store.read(key),client,llm

    def test_list_flow_and_truthful_summary(self):
        run,_,_=self.run_agent([call('list_contracts'),{'role':'assistant','content':'审查了99份'}])
        self.assertEqual(run['status'],'completed');self.assertEqual(len(run['steps']),1)
        self.assertIn('找到3份',run['summary']);self.assertNotIn('99',run['summary'])

    def test_unknown_tool_and_tool_failure_are_returned_to_model(self):
        run,_,llm=self.run_agent([call('does_not_exist'),call('list_contracts'),{'role':'assistant','content':'完成'}])
        self.assertEqual(run['status'],'completed');self.assertEqual(run['steps'][0]['error_code'],'UNKNOWN_TOOL')
        self.assertIn('UNKNOWN_TOOL',llm.requests[1][-1]['content'])

    def test_dynamic_tool_calls_and_budget_stop_preserve_partial(self):
        run,_,_=self.run_agent([call('list_contracts') for _ in range(11)])
        self.assertEqual((run['status'],run['error_code'],len(run['steps'])),('failed','MAX_ROUNDS_REACHED',11))
        self.assertEqual(run['contracts_total'],3);self.assertIn('0/3',run['summary'])
        with patch('app.services.agent.costs',return_value={'planning':{'usage_complete':True,'known_tokens':50000},'reviews':{},'total_model_calls':0}):
            run,_,llm=self.run_agent([])
        self.assertEqual(run['error_code'],'LLMBudgetExceeded');self.assertEqual(llm.requests,[])
        with patch('app.services.agent.costs',return_value={'planning':{'usage_complete':True,'known_tokens':0},'reviews':{},'total_model_calls':200}):
            run,_,llm=self.run_agent([])
        self.assertEqual(run['error_code'],'LLMBudgetExceeded');self.assertEqual(llm.requests,[])

    def test_cannot_write_fabricated_result(self):
        run,client,llm=self.run_agent([call('write_result',{'contract_id':'C001','result':{'high':99}}),{'role':'assistant','content':'没做'}])
        self.assertEqual(client.writes,[]);self.assertEqual(run['steps'][0]['error_code'],'REVIEW_REQUIRED')

    def test_canonical_writeback_after_local_review_and_result_link(self):
        class StubReview(AgentRunner):
            def review(self,run,user,args):
                result={'task_id':'real-test-task','stats':{'high':1}}
                run['state']['reviews'][args['contract_id']]={'task_id':'real-test-task','contract_id':'test-contract','result':result}
                return {'result':result}
        run,client,_=self.run_agent([call('get_contract',{'contract_id':'C001'}),call('review_contract',{'contract_id':'C001'}),
            call('write_result',{'contract_id':'C001','result':{'task_id':'forged','stats':{'high':99}}}),{'role':'assistant','content':'完成'}],
            request='审一下C001',runner_type=StubReview)
        self.assertEqual(run['status'],'completed');self.assertEqual(client.writes[0]['result']['stats']['high'],1)
        self.assertIn('已写回OA 1份',run['summary'])

    def test_incomplete_review_and_stance_are_not_faked(self):
        run,_,_=self.run_agent([call('get_contract',{'contract_id':'C001'}),{'role':'assistant','content':'已经审完'}],request='审一下C001')
        self.assertEqual(run['error_code'],'REVIEW_NOT_EXECUTED')
        run,_,_=self.run_agent([call('get_contract',{'contract_id':'C001'}),call('review_contract',{'contract_id':'C001','party_stance':'party_a'}),
                              {'role':'assistant','content':'无法选择'}],request='审一下C001')
        self.assertEqual(run['steps'][1]['error_code'],'STANCE_REQUIRED')

    def test_recovery_and_run_ownership(self):
        key=uuid.uuid4().hex;self.store.save({'run_id':key,'user_id':self.uid,'status':'running','steps':[]})
        self.store.recover();self.assertEqual(self.store.read(key)['error_code'],'AGENT_INTERRUPTED')
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):self.store.read(key,Principal('other','other','reviewer',1))
        self.assertEqual(self.store.read(key,Principal('admin','admin','admin',1))['status'],'failed')
        with self.assertRaises(HTTPException):self.store.read('../../secret')

    def test_function_call_logging_uses_real_sdk_mock_transport_and_no_transaction(self):
        def handler(request):
            with self.engine.begin() as c:c.exec_driver_sql('UPDATE contracts SET revision=revision')
            body=json.loads(request.content);self.assertIn('tools',body);self.assertNotIn('response_format',body)
            with self.sf() as s:self.assertEqual(s.scalar(select(LLMLog.error_code)),'CALL_IN_PROGRESS')
            return httpx.Response(200,json={'id':'mock','model':'test','choices':[{'index':0,'message':{'role':'assistant','content':None,
                'tool_calls':[{'id':'tool1','type':'function','function':{'name':'list_contracts','arguments':'{}'}}]},'finish_reason':'tool_calls'}],
                'usage':{'prompt_tokens':30,'completion_tokens':10,'total_tokens':40}})
        sdk=OpenAI(api_key='test-only',base_url='https://test.invalid',http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        client=AgentLLMClient(self.sf,sdk_client=sdk,emit_metrics=lambda record:None)
        key=uuid.uuid4().hex
        try:
            response=client.decide([{'role':'user','content':'list'}],MCPClient.function_definitions(TOOLS),CallContext('agent:'+key+':one',purpose='agent'))
            self.assertEqual(response['tool_calls'][0]['function']['name'],'list_contracts')
            cost=costs(self.sf,key);self.assertEqual((cost['planning']['model_calls'],cost['planning']['total_tokens'],cost['reviews']['model_calls']),(1,40,0))
        finally:client.close()

    def test_review_tokens_are_separate_but_model_calls_are_combined(self):
        key=uuid.uuid4().hex
        with self.sf.begin() as s:
            s.add(ReviewTask(id='child',contract_id=self.contract_id,review_version=1,created_by=self.uid,
                             input_snapshot=json.dumps({'agent_run_id':key}),status='completed'))
            s.flush()
            for log_id,purpose,tokens,review_id,request_id in [('a','agent',100,None,'agent:'+key+':a'),('b','risk_judge',120000,'child','review:b')]:
                s.add(LLMLog(id=log_id,request_id=request_id,review_id=review_id,purpose=purpose,provider='test',model='test',
                             attempt_no=1,prompt_tokens=tokens,completion_tokens=0))
        result=costs(self.sf,key)
        self.assertEqual(result['planning']['total_tokens'],100)
        self.assertEqual(result['reviews']['total_tokens'],120000)
        self.assertEqual(result['total_model_calls'],2)

    def test_api_roles_async_and_persisted_status(self):
        gate=threading.Event()
        class WaitRunner:
            def __init__(self,*args,**kwargs):pass
            def execute(self,key,user):gate.wait(3)
        manager=AgentManager(self.store,self.sf,runner_factory=WaitRunner)
        application=FastAPI();application.include_router(router);application.state.agent_manager=manager
        application.dependency_overrides[current_user]=lambda:self.user
        with TestClient(application) as c:
            response=c.post('/agent/run',json={'request':'列出OA合同'});self.assertEqual(response.status_code,202)
            key=response.json()['run_id'];self.assertEqual(c.get('/agent/runs/'+key).json()['status'],'running')
            application.dependency_overrides[current_user]=lambda:Principal('other','x','reviewer',1)
            self.assertEqual(c.get('/agent/runs/'+key).status_code,404)
            application.dependency_overrides[current_user]=lambda:Principal(self.uid,'x','requester',1)
            self.assertEqual(c.post('/agent/run',json={'request':'list'}).status_code,403)
            self.assertEqual(c.get('/agent/runs/'+key).status_code,403)
            application.dependency_overrides[current_user]=lambda:Principal(self.uid,'x','admin',1)
            self.assertEqual(c.get('/agent/runs/'+key).status_code,200)
        gate.set()
