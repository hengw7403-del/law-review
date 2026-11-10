"""Formal ticket API -> existing queued task; worker is run separately."""
import json,httpx
from pathlib import Path
from app import db
from app.models import ReviewTask
ROOT=Path(__file__).resolve().parents[1]
transcript=json.loads((ROOT/'work/ticket-step2-curl.json').read_text(encoding='utf-8'));key=transcript[-1]['response']['ticket']['id']
credentials=json.loads((ROOT/'work/initial-user-credentials.json').read_text(encoding='utf-8'))
with httpx.Client(base_url='http://127.0.0.1:8003',trust_env=False) as client:
    response=client.post('/auth/login',json={'username':'reviewer01','password':credentials['reviewer01']['password']});response.raise_for_status();client.headers['Authorization']='Bearer '+response.json()['access_token']
    states=[]
    for method,path in [('GET','/tickets/'+key),('POST','/tickets/'+key+'/assign'),('POST','/tickets/'+key+'/review')]:
        response=client.request(method,path);response.raise_for_status();states.append({'method':method,'path':path,'response':response.json()});print(json.dumps(states[-1],ensure_ascii=False))
    task=states[-1]['response']['task_id']
    with db.SessionLocal.begin() as s:
        row=s.get(ReviewTask,task);snapshot=json.loads(row.input_snapshot);snapshot['call_limit']=15;row.input_snapshot=json.dumps(snapshot,ensure_ascii=False)
    (ROOT/'work/ticket-step3-states.json').write_text(json.dumps({'ticket_id':key,'task_id':task,'states':states},ensure_ascii=False,indent=2),encoding='utf-8')
