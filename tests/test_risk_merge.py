import json
from pathlib import Path
import tempfile
import unittest
from sqlalchemy import select, MetaData, CheckConstraint
from sqlalchemy.orm import sessionmaker
from app.db import Base, create_db_engine
from app.models import ReviewItem, ReviewTask
from app.migrations import add_applicability_and_preamble
from app.services.review_tasks import create_review, refresh_progress, get_task
from app.services.risk_merge import merge_task_items
from app.services.results import review_items
from app.services.report import generate_report
from tests.review_helpers import seed_contract_a


class RiskMergeTests(unittest.TestCase):
    def test_migration_highest_risk_distinct_points_and_idempotent_report(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_db_engine('sqlite:///'+(Path(directory)/'test.db').as_posix())
            old = MetaData()
            for table in Base.metadata.sorted_tables:
                table.to_metadata(old)
            table = old.tables['review_items']
            for constraint in list(table.constraints):
                if isinstance(constraint, CheckConstraint) and 'check_status IN' in str(constraint.sqltext):
                    table.constraints.remove(constraint)
                    table.append_constraint(CheckConstraint("check_status IN ('supported','insufficient','failed','no_issue_detected','not_applicable')"))
            old.create_all(engine)
            sf = sessionmaker(bind=engine, autoflush=False)
            cid = seed_contract_a(sf)
            tid = create_review(sf,cid)['task_id']
            add_applicability_and_preamble(engine)
            add_applicability_and_preamble(engine)
            with sf.begin() as s:
                snap = json.loads(s.get(ReviewTask,tid).input_snapshot)
                c1,c2 = snap['clauses'][0]['id'],snap['clauses'][1]['id']
                entries = [('a',c1,'liability','medium','甲方可随时单方解除，解除权不对等。'),
                           ('b',c1,'termination','high','甲方任意解除且乙方无权解除，解除权不对等。'),
                           ('c',c1,'payment','medium','付款账期180日过长。'),
                           ('d',c2,'termination','high','甲方任意解除且乙方无权解除，解除权不对等。')]
                for key,clause,rule,risk,description in entries:
                    result = dict(check_status='supported',description=description,reason=description,
                                  model_output={'description':description},evidence=[])
                    s.add(ReviewItem(id=key,review_id=tid,item_key=key,clause_id=clause,rule_id=rule,
                                    execution_status='done',check_status='supported',risk_level=risk,
                                    machine_result=json.dumps(result,ensure_ascii=False)))
                s.flush();merge_task_items(s,tid);s.flush();refresh_progress(s,s.get(ReviewTask,tid))
            with sf.begin() as s:
                merge_task_items(s,tid)
                self.assertEqual(s.get(ReviewItem,'a').check_status,'merged')
                self.assertEqual(json.loads(s.get(ReviewItem,'a').machine_result)['merged_into'],'b')
                self.assertIn('model_output', json.loads(s.get(ReviewItem,'a').machine_result))
                self.assertEqual(s.get(ReviewItem,'b').check_status,'supported')
                self.assertEqual(s.get(ReviewItem,'c').check_status,'supported')
                self.assertEqual(s.get(ReviewItem,'d').check_status,'supported')
            data = review_items(sf,tid)
            self.assertEqual(data['stats']['merged'],1)
            self.assertEqual(data['stats']['high'],2)
            self.assertEqual(get_task(sf,tid)['done_items'],4)
            report = generate_report(sf,tid)
            self.assertNotIn('检查单元：a\n',report)
            self.assertIn('a → b',report)
            with engine.connect() as c:
                self.assertEqual(c.exec_driver_sql('PRAGMA foreign_key_check').all(),[])
            engine.dispose()

    def test_prompt_forbids_absence_but_preserves_written_disadvantages(self):
        from app.prompts.risk_judge import SYSTEM_PROMPT
        for text in ['条款级禁止评价缺少约定','has_issue=false','付款180日','不设上限','仅甲方可任意解除']:
            self.assertIn(text,SYSTEM_PROMPT)
