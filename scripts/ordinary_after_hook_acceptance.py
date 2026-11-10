import json,httpx
from pathlib import Path
from sqlalchemy import select,func
from app import db
from app.models import ReviewTask,Ticket,LLMLog
from app.services.review_worker import ReviewWorker,WorkerLock
from app.services.review_tasks import get_task
from app.services.llm import LLMClient,LLMBudgetExceeded
from app.services.risk_judge import RiskJudge

ROOT=Path(__file__).resolve().parents[1];output=ROOT/'work/ordinary-after-hook.json'
state=json.loads((ROOT/'work/auto-ticket-acceptance.json').read_text(encoding='utf-8'))
def count():
    with db.SessionLocal() as s:return s.scalar(select(func.count()).select_from(LLMLog))
assert count()-state['baseline_logs']<25,'No remaining paid call budget'
assert not output.exists(),'Keep prior result; do not repeat paid smoke'
credentials=json.loads((ROOT/'work/initial-user-credentials.json').read_text(encoding='utf-8'))
with httpx.Client(base_url='http://127.0.0.1:8005',trust_env=False) as c:
    r=c.post('/auth/login',json={'username':'reviewer01','password':credentials['reviewer01']['password']});r.raise_for_status();c.headers['Authorization']='Bearer '+r.json()['access_token']
    r=c.post('/contracts',data={'title':'[验收]普通上传审查（无工单）','stance':'party_b'},files={'file':('contract_a.txt',(ROOT/'samples/contract_a.txt').read_bytes())});r.raise_for_status();contract=r.json()['contract_id']
    r=c.post('/contracts/'+contract+'/review',json={'max_clauses':4,'rules':['intellectual_property']});r.raise_for_status();task_id=r.json()['task_id']
record={'contract_id':contract,'task_id':task_id};output.write_text(json.dumps(record),encoding='utf-8')
with db.SessionLocal.begin() as s:
    task=s.get(ReviewTask,task_id);snapshot=json.loads(task.input_snapshot);assert len(snapshot['plan'])==1;snapshot['call_limit']=1;task.input_snapshot=json.dumps(snapshot,ensure_ascii=False)
class LastCall:
    def reserve(self):
        if count()-state['baseline_logs']>=25:raise LLMBudgetExceeded('Total acceptance limit 25')
def factory(index,task_id,snapshot):return RiskJudge(index,db.SessionLocal,LLMClient(db.SessionLocal,budget=LastCall()))
worker=ReviewWorker(db.SessionLocal,ROOT/'law.worker.lock',judge_factory=factory)
with WorkerLock(worker.lock_path):
    worker.recover();assert worker.claim()==task_id;worker.execute(task_id)
record['task']=get_task(db.SessionLocal,task_id)
with db.SessionLocal() as s:assert not s.scalar(select(Ticket.id).where(Ticket.review_task_id==task_id))
record['total_new_calls']=count()-state['baseline_logs'];output.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
assert record['task']['status']=='completed';assert record['total_new_calls']<=25
print(json.dumps(record,ensure_ascii=False))
