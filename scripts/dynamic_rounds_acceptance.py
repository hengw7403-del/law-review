"""Paid formal-DB acceptance: ONE one-contract run and ONE three-contract run.

Refuses duplicate starts. Free 15-contract limit testing lives in unit tests.
Do not reset OA until the previous run's write-backs have been archived here.
"""
import json
from pathlib import Path
import sys
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'work/dynamic-rounds-acceptance.json'


def main():
    count=int(sys.argv[1]);assert count in {1,3}
    state=json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    key=str(count);assert key not in state,'Existing paid run must be inspected, not repeated'
    if count==3:
        assert state.get('1',{}).get('status')=='completed','Archive the first phase before resetting OA'
        assert state['1']['cost']['total_model_calls']<=60,'A full 200-call second run would exceed the 260-call acceptance budget'
    private=json.loads((ROOT/'work/initial-user-credentials.json').read_text(encoding='utf-8'))
    with httpx.Client(base_url='http://localhost',trust_env=False,timeout=35) as c:
        response=c.post('/auth/login',json={'username':'reviewer01','password':private['reviewer01']['password']});response.raise_for_status()
        c.headers['Authorization']='Bearer '+response.json()['access_token']
        assert c.get('/auth/me').json()['org_name']=='示例澄川信息技术有限公司'
        response=c.post('http://localhost:9000/api/reset',params={'count':count});response.raise_for_status()
        assert response.json()['count']==count
        prompt='审一遍' if count==1 else '把 OA 里的合同都审一遍'
        response=c.post('/agent/run',json={'request':prompt});response.raise_for_status();rid=response.json()['run_id']
        state[key]={'run_id':rid,'request':prompt,'contracts_total':count}
        STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        previous=None
        for _ in range(900):
            response=c.get('/agent/runs/'+rid);response.raise_for_status();run=response.json()
            signature=(run.get('rounds_used'),run.get('rounds_limit'),run.get('contracts_processed'),
                       run.get('progress',{}).get('task_id'),run.get('progress',{}).get('processed_items'),run['status'])
            if signature!=previous:
                previous=signature
                print(json.dumps({'count':count,'run_id':rid,**{k:run.get(k) for k in ['status','rounds_used','rounds_limit','contracts_processed','contracts_total','stopped_reason','progress','cost']}},ensure_ascii=False),flush=True)
            if run['status'] in {'completed','failed'}:break
            time.sleep(2)
        else:raise TimeoutError('Poll existing run; never duplicate a paid acceptance')
        run['oa_results']={}
        for n in range(count):
            cid='C{:03d}'.format(n+1)
            response=c.get('http://localhost:9000/api/contracts/'+cid);response.raise_for_status()
            run['oa_results'][cid]=response.json()['result']
        state[key]=run
        STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'run_id':rid,**{k:run.get(k) for k in ['status','rounds_used','rounds_limit','contracts_processed','contracts_total','stopped_reason','summary','cost']},'tasks':run['details']},ensure_ascii=False))
        assert run['status']=='completed',run.get('summary')
        assert (run['rounds_used'],run['rounds_limit'],run['contracts_processed'],run['contracts_total'])==((4,5,1,1) if count==1 else (10,11,3,3))
        assert run['stopped_reason'] is None
        assert all(r and r['status']=='completed' and r['processed_items']==r['total_items'] for r in run['oa_results'].values())
        assert all('max_rounds' in s and s.get('result_ok') is True for s in run['steps'])


if __name__=='__main__':main()
