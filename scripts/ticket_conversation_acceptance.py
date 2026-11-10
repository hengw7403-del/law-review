"""Real curl conversations; credentials never appear in command arguments/output."""
import json,subprocess,httpx
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
base='http://127.0.0.1:8002'
private=json.loads((ROOT/'work/initial-user-credentials.json').read_text(encoding='utf-8'))
with httpx.Client(base_url=base,trust_env=False) as client:
    response=client.post('/auth/login',json={'username':'reviewer01','password':private['reviewer01']['password']});response.raise_for_status();token=response.json()['access_token']
def curl(path,payload=None):
    config=['url = '+json.dumps(base+path),'header = '+json.dumps('Authorization: Bearer '+token)]
    if payload is not None:
        body=ROOT/'work/ticket-curl-body.json';body.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
        config+=['header = "Content-Type: application/json"','request = "POST"','data-binary = '+json.dumps('@'+body.as_posix())]
    r=subprocess.run(['curl.exe','--silent','--show-error','--fail-with-body','--config','-'],input='\n'.join(config),text=True,capture_output=True,encoding='utf-8')
    if r.returncode:raise RuntimeError(r.stdout+r.stderr)
    return json.loads(r.stdout)
start=curl('/conversations',{})
key=start['conversation_id'];transcript=[{'request':'POST /conversations','response':start}]
for content in ['帮我审一下不存在的蓝鲸采购合同','选择 contract_c_bad.txt，合同ID c0220fbfcb4449f1ae7ffc802de04a75，关注付款账期和单方解除，普通优先级','确认创建工单']:
    result=curl('/conversations/'+key+'/messages',{'content':content});transcript.append({'content':content,'response':result})
    print(json.dumps(transcript[-1],ensure_ascii=False))
    if result['ready']:break
(ROOT/'work/ticket-step2-curl.json').write_text(json.dumps(transcript,ensure_ascii=False,indent=2),encoding='utf-8')
assert result['ready'], 'Three-turn real dialogue did not complete; inspect transcript before any additional calls'
assert result['cost']['total_tokens']<=8000
