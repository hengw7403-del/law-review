"""Paid first-boot Docker acceptance, one intent + at most 49 worker attempts."""
import json,sys,httpx,time
from contextlib import contextmanager
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];FILE=ROOT/'work/docker-firstboot-20261110/acceptance.json';BASE='http://127.0.0.1:18080'

@contextmanager
def client():
    private=json.loads((ROOT/'work/initial-user-credentials.json').read_text(encoding='utf-8'))
    c=httpx.Client(base_url=BASE,trust_env=False,timeout=90)
    try:
        r=c.post('/auth/login',json={'username':'reviewer01','password':private['reviewer01']['password']});r.raise_for_status();c.headers['Authorization']='Bearer '+r.json()['access_token'];yield c
    finally:c.close()

def prepare():
    assert not FILE.exists(),'Keep existing paid acceptance; do not repeat'
    with client() as c:
        r=c.post('/search',json={'query':'违约金过高怎么办'});r.raise_for_status();search=r.json();assert search['results'],'Bootstrap knowledge is not searchable'
        r=c.post('/contracts',data={'title':'技术服务合同','stance':'party_b'},files={'file':('contract_c_bad.txt',(ROOT/'samples/contract_c_bad.txt').read_bytes())});r.raise_for_status();contract=r.json()['contract_id']
        r=c.post('/conversations',json={});r.raise_for_status();conversation=r.json()['conversation_id']
        FILE.write_text(json.dumps({'base':BASE,'contract_id':contract,'conversation_id':conversation,'search':search},ensure_ascii=False,indent=2),encoding='utf-8')
        r=c.post('/conversations/'+conversation+'/messages',json={'content':'确认创建工单，合同ID '+contract+'。请审查全部条款，优先级普通；合同ID和建单已明确确认。'});r.raise_for_status();reply=r.json()
        data=json.loads(FILE.read_text(encoding='utf-8'));data['conversation_reply']=reply;FILE.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        assert reply.get('ticket') and reply['ticket']['status']=='reviewing',reply
        ticket=reply['ticket'];data.update(ticket_id=ticket['id'],task_id=ticket['review_task_id'])
        r=c.get('/tasks/'+data['task_id']);r.raise_for_status();state=r.json();assert (state['total_items'],state['clause_items'],state['contract_items'])==(125,120,5)
        data['queued_task']=state;FILE.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ticket_id':data['ticket_id'],'task_id':data['task_id'],'total_items':125,'clause_items':120,'contract_items':5,'planned_model_items':state['planned_model_items'],'conversation_cost':reply['cost']},ensure_ascii=False))

def wait():
    data=json.loads(FILE.read_text(encoding='utf-8'))
    with client() as c:
        previous=None
        for _ in range(300):
            r=c.get('/tasks/'+data['task_id']);r.raise_for_status();state=r.json()
            if state['processed_items']!=previous:print(json.dumps({'status':state['status'],'processed':state['processed_items'],'total':state['total_items'],'calls':state['metrics']['model_calls']}),flush=True);previous=state['processed_items']
            if state['status'] in {'completed','partial_failed','failed'}:break
            time.sleep(2)
        else:raise AssertionError('Worker did not complete')
        results=c.get('/reviews/'+data['task_id']+'/items');results.raise_for_status();ticket=c.get('/tickets/'+data['ticket_id']);ticket.raise_for_status()
        data.update(task=state,results=results.json(),ticket=ticket.json());FILE.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        assert state['status']=='completed',state
        assert state['done_items']==125 and state['processed_items']==125 and state['progress']==1
        assert data['ticket']['status']=='needs_human'
        missing=[item for item in data['results']['items'] if item['scope']=='contract'];assert len(missing)==5
        assert any(item['check_status']!='insufficient' for item in missing)
        print(json.dumps({'task':state,'ticket_status':data['ticket']['status'],'stats':data['results']['stats'],'missing_statuses':[(i['rule_id'],i['check_status']) for i in missing]},ensure_ascii=False),flush=True)

if __name__=='__main__':
    if sys.argv[1]=='prepare':prepare()
    else:wait()
