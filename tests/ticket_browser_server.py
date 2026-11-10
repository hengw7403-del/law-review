"""Test-only SDK transport; never imported by production app/worker."""
import json,os,httpx
from openai import OpenAI
from app.main import app
from app.services import conversations
from app.services.llm import LLMClient
import uvicorn

def factory(sf):
    def handle(request):
        payload=json.loads(request.content);messages=payload['messages'];data=json.loads(messages[0]['content'].rsplit('\n',1)[-1]);slots=data['slots'];content=messages[-1]['content'];ready=False
        for c in data['candidates']:
            if c['id'] in content:slots['contract_id']=c['id']
        if '付款' in content:slots['focus_points']=['付款账期']
        if '不存在' in content:slots['contract_id']=None;reply='没有找到这份合同，请从候选中选择。'
        elif '你是谁' in content:reply='我是法务工单助手，请选择合同。'
        elif '确认' in content:ready=bool(slots['contract_id']);reply='已确认。'
        else:reply='请选择合同，确认关注点后回复确认。'
        result={'reply':reply,'slots':slots,'ready':ready}
        return httpx.Response(200,json={'id':'test','model':'mock-ticket-browser','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result,ensure_ascii=False)}}],'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150}})
    sdk=OpenAI(api_key='test-not-real',base_url='https://test.invalid',http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    return LLMClient(sf,sdk_client=sdk)
conversations.LLMClient=factory
uvicorn.run(app,host='127.0.0.1',port=8004)
