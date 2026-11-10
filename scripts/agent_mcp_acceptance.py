"""Paid MCP Agent acceptance against formal Compose API and local OA.

Run list then review. It refuses to repeat an already-started paid run.
All results remain in law.db and /data/agent-runs. OA is in-memory.
"""
import json
from pathlib import Path
import sys
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'work/agent-mcp-acceptance.json'


def main():
    kind=sys.argv[1]
    assert kind in {'list','review'}
    state=json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    assert kind not in state,'Keep existing paid run; poll it rather than duplicating'
    private=json.loads((ROOT/'work/initial-user-credentials.json').read_text(encoding='utf-8'))
    with httpx.Client(base_url='http://localhost',trust_env=False,timeout=30) as c:
        response=c.post('/auth/login',json={'username':'reviewer01','password':private['reviewer01']['password']});response.raise_for_status()
        c.headers['Authorization']='Bearer '+response.json()['access_token']
        prompt='列出 OA 的合同' if kind=='list' else '审一下 C001'
        if kind=='review':
            me=c.get('/auth/me');me.raise_for_status()
            assert me.json()['org_name']=='示例澄川信息技术有限公司','Confirm stance via existing user organization before paid review'
        response=c.post('/agent/run',json={'request':prompt});response.raise_for_status();rid=response.json()['run_id']
        state[kind]={'run_id':rid,'request':prompt}
        STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        previous=None
        for _ in range(600):
            response=c.get('/agent/runs/'+rid);response.raise_for_status();run=response.json()
            progress=(len(run['steps']),run.get('progress',{}).get('processed_items'),run['status'])
            if progress!=previous:
                print(json.dumps({'kind':kind,'run_id':rid,'status':run['status'],'steps':len(run['steps']),
                                  'progress':run.get('progress'),'cost':run.get('cost')},ensure_ascii=False),flush=True)
                previous=progress
            if run['status'] in {'completed','failed'}:break
            time.sleep(2)
        else:raise TimeoutError('Agent still running; inspect existing run, do not repeat')
        state[kind]=run
        if kind=='review':
            response=c.get('http://localhost:9000/api/contracts/C001');response.raise_for_status();state[kind]['oa_after']=response.json()
        STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'kind':kind,'run_id':rid,'status':run['status'],'summary':run['summary'],'error_code':run.get('error_code'),'cost':run.get('cost'),
                          'steps':[{k:s.get(k) for k in ['round','tool','status','result_summary']} for s in run['steps']]},ensure_ascii=False),flush=True)
        assert run['status']=='completed',run.get('summary')
        if kind=='list':assert '3' in run['summary']
        else:
            assert state[kind]['oa_after']['result']
            assert run['details'][0]['task_id']==state[kind]['oa_after']['result']['task_id']


if __name__=='__main__':main()
