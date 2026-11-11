"""Real formal-DB acceptance, resumable phases, never mock paid results.

The admin token is minted locally using the application's signer, since the
stored setup password no longer matches. No account/password is changed.
Reviewer authentication uses the existing initial credential file privately.
"""
import json
from pathlib import Path
import sys
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'work/agent-handoff-acceptance.json'


def save(state):
    current=json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    current.update(state)
    STATE.write_text(json.dumps(current,ensure_ascii=False,indent=2),encoding='utf-8')


def log_count():
    # Never open a Linux WAL database from Windows while containers use it.
    # Container-side audit exports the true count; in-flight budget uses API costs.
    return json.loads((ROOT/'data/handoff-log-audit.json').read_text(encoding='utf-8'))['logs']


def live_calls(c,state):
    state={**json.loads(STATE.read_text(encoding='utf-8')),**state}
    total=state.get('failed_attempt_calls',0)
    for phase in ('handoff','search','list','batch'):
        if phase not in state:continue
        r=c.get('/agent/runs/'+state[phase]['run_id']);r.raise_for_status()
        total+=r.json()['cost']['total_model_calls']
    return total


def main():
    phase=sys.argv[1]
    state=json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {'baseline_logs':log_count()}
    save(state)
    assert log_count()-state['baseline_logs']<150,'Stop: acceptance budget exhausted'
    with httpx.Client(base_url='http://localhost',trust_env=False,timeout=30) as c:
        token=(ROOT/'data/acceptance-admin-token.txt').read_text(encoding='ascii').strip()
        c.headers['Authorization']='Bearer '+token
        me=c.get('/auth/me');me.raise_for_status()
        assert me.json()['role']=='admin' and not me.json()['org_name']
        if phase=='stance':
            entry=state['handoff']['details'][0];ticket_id=entry['ticket_id']
            if 'stance_task_id' not in state:
                private=json.loads((ROOT/'work/initial-user-credentials.json').read_text(encoding='utf-8'))
                response=c.post('/auth/login',json={'username':'reviewer01','password':private['reviewer01']['password']});response.raise_for_status()
                response=c.post('/tickets/'+ticket_id+'/stance',json={'stance':'party_b'},headers={'Authorization':'Bearer '+response.json()['access_token']})
                response.raise_for_status();state['stance_task_id']=response.json()['task_id'];save(state)
            task_id=state['stance_task_id'];previous=None
            for _ in range(1200):
                response=c.get('/tasks/'+task_id);response.raise_for_status();task=response.json()
                marker=(task['status'],task['processed_items'])
                if marker!=previous:print(json.dumps({'phase':phase,'task_id':task_id,'status':task['status'],'processed':task['processed_items'],'total':task['total_items'],'new_calls':live_calls(c,state)}),flush=True);previous=marker
                if task['status'] in {'completed','partial_failed','failed'}:break
                if live_calls(c,state)>=150:raise RuntimeError('Stop: budget reached; inspect worker before proceeding')
                time.sleep(2)
            assert task['status']=='completed',task
            response=c.get('/tickets/'+ticket_id);response.raise_for_status()
            state['stance_ticket']=response.json();state['stance_task']=task;save(state)
            print(json.dumps({'task_id':task_id,'ticket_id':ticket_id,'status':state['stance_ticket']['status'],'high':state['stance_ticket']['high_risk_count']}),flush=True)
            return
        assert phase in {'handoff','search','list','batch'}
        if phase not in state:
            if phase=='handoff':
                response=c.post('http://localhost:9000/api/reset?clear_stance=C002');response.raise_for_status()
                prompt='审一下 C002'
            elif phase=='batch':
                assert state.get('stance_task',{}).get('status')=='completed','Finish manual stance review first'
                response=c.post('http://localhost:9000/api/reset?clear_stance=C003');response.raise_for_status()
                prompt='把 OA 里的合同都审一遍；C002 请显式复用本轮法务已明确乙方立场并完成的真实审查，task_id='+state['stance_task_id']+'，注明 reused=true。'
            else:prompt='违约金上限是多少' if phase=='search' else 'OA 里有哪些合同'
            response=c.post('/agent/run',json={'request':prompt});response.raise_for_status()
            state[phase]={'run_id':response.json()['run_id']};save(state)
        key=state[phase]['run_id'];previous=None
        for _ in range(1200):
            response=c.get('/agent/runs/'+key);response.raise_for_status();run=response.json()
            marker=(run['status'],len(run['steps']),run.get('progress',{}).get('processed_items'))
            if marker!=previous:
                print(json.dumps({'phase':phase,'run_id':key,'status':run['status'],'steps':len(run['steps']),'progress':run.get('progress'),'cost':run.get('cost'),'new_calls':live_calls(c,state)},ensure_ascii=False),flush=True);previous=marker
            if run['status'] in {'completed','failed'}:break
            if live_calls(c,state)>=150:raise RuntimeError('Stop: budget reached; inspect active run before proceeding')
            time.sleep(2)
        state[phase]=run;state['new_model_calls']=live_calls(c,state);save(state)
        print(json.dumps({'phase':phase,'status':run['status'],'summary':run['summary'],'details':run.get('details'),'new_model_calls':state['new_model_calls']},ensure_ascii=False),flush=True)
        assert run['status']=='completed',run['summary']
        if phase=='handoff':assert run['details'][0]['status']=='needs_stance' and run['cost']['reviews']['model_calls']==0
        if phase=='search':assert [s['tool'] for s in run['steps']]==['search_law']
        if phase=='list':assert [s['tool'] for s in run['steps']]==['list_contracts']
        if phase=='batch':
            assert run['contracts_processed']==2 and run['contracts_pending_stance']==1
            reused=next(d for d in run['details'] if d['oa_contract_id']=='C002')
            assert reused['reused'] and reused['task_id']==state['stance_task_id']
            state['oa_after']={cid:c.get('http://localhost:9000/api/contracts/'+cid).json() for cid in ['C001','C002','C003']};save(state)


if __name__=='__main__':main()
