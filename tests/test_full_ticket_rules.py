import tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app import db
from app.models import Base,Contract,ReviewTask,ReviewItem,Ticket
from app.services.auth import Principal
from app.services import tickets
from app.services.review_rules import CLAUSE_RULES,MISSING_RULES
from app.services.review_tasks import refresh_progress,get_task
from app.services.review_quality import all_clauses_insufficient
from tests.review_helpers import seed_contract_a

class FullTicketRulesTests(unittest.TestCase):
    def test_full_snapshot_progress_and_quality_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            engine=db.create_db_engine('sqlite:///'+(Path(d)/'test.db').as_posix());Base.metadata.create_all(engine);sf=sessionmaker(bind=engine,autoflush=False)
            cid=seed_contract_a(sf)
            with sf.begin() as s:
                uid=s.get(Contract,cid).owner_id;s.add(Ticket(id='t',title='t',contract_id=cid,requester_id=uid,raw_request='test'))
            with patch.object(db,'SessionLocal',sf):tid=tickets.review('t',Principal(uid,'u','reviewer',1))['task_id']
            with sf.begin() as s:
                task=s.get(ReviewTask,tid);snapshot=json.loads(task.input_snapshot)
                self.assertEqual(snapshot['rules'],list(CLAUSE_RULES));self.assertEqual(snapshot['missing_rules'],list(MISSING_RULES));self.assertEqual(task.total_items,125)
                for n,unit in enumerate(snapshot['plan']+snapshot['not_applicable']):
                    status='not_applicable' if unit in snapshot['not_applicable'] else 'no_issue_detected' if unit['scope']=='contract' else 'insufficient'
                    s.add(ReviewItem(id=str(n),review_id=tid,item_key=unit['item_key'],clause_id=unit['clause_id'],rule_id=unit['rule_id'],execution_status='done',check_status=status,risk_level='unknown' if status=='insufficient' else None,machine_result='{}'))
                s.flush();refresh_progress(s,task);task.status='completed';self.assertEqual(task.done_items,125)
                self.assertTrue(all_clauses_insufficient(s,task))
            data=get_task(sf,tid);self.assertEqual((data['clause_items'],data['contract_items'],data['done_items'],data['processed_items'],data['progress']),(120,5,125,125,1))
            with sf.begin() as s:
                row=s.scalar(select(ReviewItem).where(ReviewItem.review_id==tid,ReviewItem.check_status=='insufficient'));row.check_status='no_issue_detected';row.risk_level=None;s.flush()
                self.assertFalse(all_clauses_insufficient(s,s.get(ReviewTask,tid)))
            engine.dispose()
