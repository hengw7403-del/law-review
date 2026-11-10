import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from app.db import Base,create_db_engine
from app.models import Contract,ReviewTask,ReviewItem
from app.api.reviews import router
from app.services.auth import Principal,current_user
from app.services.review_tasks import create_review
from tests.review_helpers import seed_contract_a

class ResultsTests(unittest.TestCase):
    def test_items_sort_snapshot_stats_and_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            engine=create_db_engine('sqlite:///'+(Path(d)/'law.db').as_posix());Base.metadata.create_all(engine)
            sf=sessionmaker(bind=engine);cid=seed_contract_a(sf);tid=create_review(sf,cid)['task_id']
            with sf.begin() as s:
                owner=s.get(Contract,cid).owner_id;snap=json.loads(s.get(ReviewTask,tid).input_snapshot)
                for n,(status,risk) in enumerate([('no_issue_detected',None),('insufficient','unknown'),('supported','medium'),('supported','high'),('not_applicable',None),('failed','unknown')]):
                    s.add(ReviewItem(id=str(n),review_id=tid,item_key='item:'+str(n),clause_id=snap['clauses'][0]['id'],rule_id='liability',execution_status='done',check_status=status,risk_level=risk,machine_result=json.dumps({'description':'<script>bad</script>','evidence':[]})))
            app=FastAPI();app.include_router(router)
            with patch('app.api.reviews.db.SessionLocal',sf),TestClient(app) as client:
                self.assertEqual(client.get('/reviews/'+tid+'/items').status_code,401)
                app.dependency_overrides[current_user]=lambda:Principal(owner,'owner','reviewer',1)
                data=client.get('/reviews/'+tid+'/items').json()
                self.assertEqual(data['stats']['high'],1);self.assertEqual(data['stats']['no_issue'],1)
                self.assertEqual([i['risk_level'] for i in data['items'][:2]],['high','medium'])
                self.assertEqual(data['items'][2]['check_status'],'insufficient')
                self.assertEqual(data['items'][-1]['check_status'],'no_issue_detected')
                self.assertTrue(data['items'][0]['clause_content'].startswith('第一条'))
                app.dependency_overrides[current_user]=lambda:Principal('other','other','requester',1)
                self.assertEqual(client.get('/reviews/'+tid+'/items').status_code,404)
                app.dependency_overrides[current_user]=lambda:Principal('admin','admin','admin',1)
                self.assertEqual(client.get('/reviews/'+tid+'/items').status_code,200)
            engine.dispose()
