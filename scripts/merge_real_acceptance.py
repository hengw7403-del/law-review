"""Real formal-DB merge acceptance; prepare with worker stopped, then finish.

At most two conversation attempts. Set worker budget from the DB baseline
before starting it; combined conversation+worker limit is 55 attempts.
"""
import json
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'work/merge-real-acceptance.json'


def main():
    action = sys.argv[1]
    private = json.loads((ROOT / 'work/initial-user-credentials.json').read_text(encoding='utf-8'))
    with httpx.Client(base_url='http://localhost',trust_env=False,timeout=90) as c:
        response=c.post('/auth/login',json={'username':'reviewer01','password':private['reviewer01']['password']})
        response.raise_for_status()
        token=response.json()['access_token'];c.headers['Authorization']='Bearer '+token
        if action=='prepare':
            assert not STATE.exists(),'Do not duplicate an existing paid acceptance'
            response=c.post('/contracts',data={'party_stance':'party_b','title':'坑合同 · merge性能验收'},
                            files={'file':('contract_c_bad.txt',(ROOT/'samples/contract_c_bad.txt').read_bytes(),'text/plain')})
            response.raise_for_status();cid=response.json()['contract_id']
            response=c.post('/conversations',json={});response.raise_for_status()
            conversation=response.json()['conversation_id']
            state={'base':'http://localhost','contract_id':cid,'conversation_id':conversation,'dialogue':[]}
            STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
            for _ in range(2):
                response=c.post('/conversations/'+conversation+'/messages',json={'content':'确认创建工单，合同ID '+cid+'。请检查全部条款，关注违约金、付款条件和单方解除，优先级普通。合同ID明确，确认建单。'})
                response.raise_for_status();reply=response.json();state['dialogue'].append(reply)
                STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
                if reply.get('ticket'):break
            assert reply.get('ticket') and reply['ticket']['review_task_id'],reply
            state.update(ticket_id=reply['ticket']['id'],task_id=reply['ticket']['review_task_id'])
            response=c.get('/tasks/'+state['task_id']);response.raise_for_status();state['queued_task']=response.json()
            STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
            (ROOT/'work/merge-monitor-token').write_text(token,encoding='utf-8')
            assert (state['queued_task']['total_items'],state['queued_task']['clause_items'],state['queued_task']['contract_items'])==(125,120,5)
            print(json.dumps({'task_id':state['task_id'],'ticket_id':state['ticket_id'],'planned_model_items':state['queued_task']['planned_model_items'],'conversation_calls':reply['cost']['model_calls']},ensure_ascii=False))
        elif action=='finish':
            state=json.loads(STATE.read_text(encoding='utf-8'))
            for key,url in [('task','/tasks/'+state['task_id']),('ticket','/tickets/'+state['ticket_id']),('results','/reviews/'+state['task_id']+'/items')]:
                response=c.get(url);response.raise_for_status();state[key]=response.json()
            state['functional_passed']=(state['task']['status']=='completed' and state['task']['done_items']==125
                                        and state['ticket']['status']=='needs_human' and state['ticket']['high_risk_count']==1)
            STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'task_id':state['task_id'],'status':state['task']['status'],'done':state['task']['done_items'],
                              'ticket_status':state['ticket']['status'],'high':state['ticket']['high_risk_count'],
                              'review_calls':state['task']['metrics']['model_calls'],'stats':state['results']['stats'],
                              'functional_passed':state['functional_passed']},ensure_ascii=False))
        else:raise ValueError('Unknown action')


if __name__=='__main__':main()
