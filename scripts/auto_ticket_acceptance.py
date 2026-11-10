"""Real formal DB acceptance; global persisted 25-attempt budget, no SDK mocks."""
import json,sys
from pathlib import Path
import httpx
from sqlalchemy import select,func
from app import db
from app.models import LLMLog,ReviewTask,Ticket
from app.services.auth import Principal
from app.services.llm import LLMClient,LLMBudgetExceeded
from app.services.risk_judge import RiskJudge
from app.services.review_worker import ReviewWorker,WorkerLock
from app.services.review_tasks import get_task
from app.services.tickets import detail
ROOT=Path(__file__).resolve().parents[1];FILE=ROOT/'work/auto-ticket-acceptance.json'

def log_count():
    with db.SessionLocal() as s:return s.scalar(select(func.count()).select_from(LLMLog))
def save(state):FILE.write_text(json.dumps(state,ensure_ascii=False,indent=2,default=str),encoding='utf-8')

def prepare():
    if FILE.exists():print('Existing run retained; not issuing new calls.');return
    credentials=json.loads((ROOT/'work/initial-user-credentials.json').read_text(encoding='utf-8'))
    state={'baseline_logs':log_count(),'runs':{},'events':[]};save(state)
    with httpx.Client(base_url='http://127.0.0.1:8005',trust_env=False,timeout=90) as c:
        r=c.post('/auth/login',json={'username':'reviewer01','password':credentials['reviewer01']['password']});r.raise_for_status();c.headers['Authorization']='Bearer '+r.json()['access_token']
        for label,cid in [('bad','c0220fbfcb4449f1ae7ffc802de04a75'),('clean','be8fae3c414e41d78fab0a50be2f5e38')]:
            r=c.post('/conversations',json={});r.raise_for_status();conversation=r.json()['conversation_id']
            content='确认创建工单，合同ID '+cid+'，关注付款和解除，普通优先级。我已明确确认，请按本轮确认信息建单。'
            for attempt in range(2):
                assert log_count()-state['baseline_logs']<3,'Reserve at least 22 attempts for reviews'
                r=c.post('/conversations/'+conversation+'/messages',json={'content':content});r.raise_for_status();data=r.json();state['events'].append({'label':label,'response':data});save(state)
                if data.get('ticket'):break
                content='确认创建工单，合同ID '+cid
            assert data.get('ticket') and data['ticket']['status']=='reviewing',data
            state['runs'][label]={'conversation_id':conversation,'ticket_id':data['ticket']['id'],'task_id':data['ticket']['review_task_id']};save(state)
            print(json.dumps({'label':label,**state['runs'][label],'status':'reviewing'},ensure_ascii=False),flush=True)
        with db.SessionLocal.begin() as s:
            for run in state['runs'].values():
                task=s.get(ReviewTask,run['task_id']);snapshot=json.loads(task.input_snapshot);snapshot['call_limit']=25;task.input_snapshot=json.dumps(snapshot,ensure_ascii=False)

def worker():
    state=json.loads(FILE.read_text(encoding='utf-8'))
    class GlobalBudget:
        def reserve(self):
            if log_count()-state['baseline_logs']>=25:raise LLMBudgetExceeded('Acceptance total limit 25')
    def factory(index,task_id,snapshot):return RiskJudge(index,db.SessionLocal,LLMClient(db.SessionLocal,budget=GlobalBudget()))
    worker=ReviewWorker(db.SessionLocal,ROOT/'law.worker.lock',judge_factory=factory)
    ids={r['task_id'] for r in state['runs'].values()}
    with WorkerLock(worker.lock_path):
        worker.recover()
        with db.SessionLocal() as s:
            queued=set(s.scalars(select(ReviewTask.id).where(ReviewTask.status.in_(['queued','running']))));assert queued<=ids,'Unexpected queued tasks; do not bill unrelated jobs'
        while True:
            task_id=worker.claim()
            if task_id is None:break
            assert task_id in ids;worker.execute(task_id)
    user=Principal('5eceabfc593d4eae95f8f07bb544529b','reviewer01','reviewer',1)
    for label,run in state['runs'].items():
        # Capture persisted ticket state before any detail GET fallback runs.
        with db.SessionLocal() as s:
            row=s.get(Ticket,run['ticket_id']);run['worker_persisted_status']=row.status;run['worker_high_risk_count']=row.high_risk_count
        run['task']=get_task(db.SessionLocal,run['task_id']);run['ticket']=detail(run['ticket_id'],user)
        print(json.dumps({'label':label,'status':run['worker_persisted_status'],'high':run['worker_high_risk_count'],'task':run['task']['status'],'metrics':run['task']['metrics']},ensure_ascii=False),flush=True)
    state['new_calls']=log_count()-state['baseline_logs'];save(state)
    assert state['new_calls']<=25
    assert state['runs']['bad']['worker_persisted_status']=='needs_human'
    assert state['runs']['clean']['worker_persisted_status']=='done'

if __name__=='__main__':
    if sys.argv[1]=='prepare':prepare()
    else:worker()
