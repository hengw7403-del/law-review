"""Free integration checks: genuine services/SQLite, model responses stubbed."""
import copy
import json
import uuid
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, func, inspect
from sqlalchemy.exc import IntegrityError
from app import db
from app.models import Contract, Ticket, ReviewTask, LLMLog
from app.migrations import agent_stance_handoff
from app.services.agent import AgentRunner, AgentManager
from app.services.agent_tools import reusable_handoff, content_hash, search_law
from app.services.auth import Principal, current_user
from app.services import tickets
from app.api.tickets import router
from app.services.review_tasks import create_review, ReviewError
from tests import test_agent as fixtures
from tests.test_agent import FakeMCP, call
import unittest


class HandoffTests(unittest.TestCase):
    setUp=fixtures.AgentTests.setUp
    tearDown=fixtures.AgentTests.tearDown
    run_agent=fixtures.AgentTests.run_agent
    # Reuse isolated fixtures and existing regression cases, never paid calls.
    def pending(self):
        with self.sf.begin() as s:s.get(Contract,self.contract_id).party_stance=None
        return tickets.stance_pending(self.sf,self.contract_id,self.user,'审合同',source_system='test',
            source_contract_id='C002',source_content_hash='x',agent_run_id=uuid.uuid4().hex)

    def test_unknown_stance_creates_pending_not_failed(self):
        self.user=Principal(self.uid,'test','admin',1,None)
        run,client,_=self.run_agent([call('get_contract',{'contract_id':'C001'}),
            call('review_contract',{'contract_id':'C001'}),{'role':'assistant','content':'已交法务'}],request='审一下C001')
        self.assertEqual(run['status'],'completed');self.assertEqual(run['contracts_pending_stance'],1)
        entry=run['state']['reviews']['C001']
        with self.sf() as s:
            self.assertEqual(s.get(Ticket,entry['ticket_id']).status,'needs_stance')
            self.assertIsNone(s.get(Contract,entry['contract_id']).party_stance)
            self.assertEqual(s.scalar(select(func.count()).select_from(ReviewTask)),0)
            self.assertEqual(s.scalar(select(func.count()).select_from(LLMLog)),0)
        self.assertFalse(client.writes)
        public=AgentManager(self.store,self.sf).public(run['run_id'],self.user)
        self.assertTrue(public['details'][0]['ticket_path']);self.assertIsNone(public['details'][0]['result_path'])

    def test_pending_idempotency_and_no_guessed_task(self):
        key=self.pending()
        self.assertEqual(key,tickets.stance_pending(self.sf,self.contract_id,self.user,'再次审'))
        with self.assertRaises(ReviewError) as exc:create_review(self.sf,self.contract_id)
        self.assertEqual(exc.exception.code,'STANCE_REQUIRED')

    def test_legal_stance_atomic_bind_all_rules_and_permission(self):
        key=self.pending()
        application=FastAPI();application.include_router(router)
        application.dependency_overrides[current_user]=lambda:self.user
        with patch.object(db,'SessionLocal',self.sf),TestClient(application) as c:
            self.user=Principal(self.uid,'test','requester',1)
            self.assertEqual(c.post('/tickets/'+key+'/stance',json={'stance':'party_b'}).status_code,403)
            self.user=Principal('someone','test','requester',1)
            self.assertEqual(c.get('/tickets/'+key).status_code,404)
            self.user=Principal(self.uid,'test','reviewer',1)
            self.assertEqual(c.post('/tickets/'+key+'/stance',json={'stance':'invented'}).status_code,422)
            response=c.post('/tickets/'+key+'/stance',json={'stance':'party_b'})
            self.assertEqual(response.status_code,202,response.text)
            self.assertEqual(c.post('/tickets/'+key+'/stance',json={'stance':'party_a'}).status_code,409)
            data=c.get('/tickets/'+key).json();self.assertEqual(data['status'],'reviewing')
        with self.sf() as s:
            ticket=s.get(Ticket,key);task=s.get(ReviewTask,ticket.review_task_id)
            snap=json.loads(task.input_snapshot)
            self.assertEqual((task.total_items,len(snap['rules']),len(snap['missing_rules'])),(125,10,5))
            self.assertEqual(snap['contract']['stance'],'party_b')
            self.assertEqual(snap['agent_run_id'],ticket.agent_run_id)
            self.assertEqual(s.get(Contract,self.contract_id).party_stance,'party_b')

    def test_explicit_stance_and_active_conflict(self):
        task=create_review(self.sf,self.contract_id,party_stance='party_b')
        with self.sf() as s:self.assertEqual(json.loads(s.get(ReviewTask,task['task_id']).input_snapshot)['contract']['stance'],'party_b')
        with self.assertRaises(ReviewError):create_review(self.sf,self.contract_id,party_stance='party_a')

    def test_failure_rolls_back_stance_and_ticket(self):
        key=self.pending()
        with self.sf.begin() as s:s.get(Contract,self.contract_id).parse_status='parse_failed'
        with patch.object(db,'SessionLocal',self.sf),self.assertRaises(HTTPException):tickets.set_stance(key,self.user,'party_b')
        with self.sf() as s:
            self.assertEqual(s.get(Ticket,key).status,'needs_stance');self.assertIsNone(s.get(Contract,self.contract_id).party_stance)
            self.assertEqual(s.scalar(select(func.count()).select_from(ReviewTask)),0)

    def test_source_stance_beats_org_and_fabrication_is_rejected(self):
        run={'run_id':uuid.uuid4().hex,'request':'把OA里的合同都审一遍；C002请显式复用本轮法务已明确乙方立场并完成的真实审查','steps':[],
            'state':{'fetched':{'C001':{'id':'C001','name':'contract.txt','content':Path('samples/contract_a.txt').read_text(encoding='utf-8'),'stance':'party_a'}},'reviews':{}}}
        runner=AgentRunner(self.store,self.sf,review_timeout=0)
        # A mismatching model-selected B cannot override trusted OA A.
        with self.assertRaises(Exception) as exc:runner.review(run,self.user,{'contract_id':'C001','stance':'party_b'})
        self.assertEqual(exc.exception.code,'STANCE_REQUIRED')
        # Stop at task polling; use real snapshot creation, no model call.
        with self.assertRaises(Exception):runner.review(run,self.user,{'contract_id':'C001'})
        with self.sf() as s:
            task=s.scalar(select(ReviewTask));self.assertEqual(json.loads(task.input_snapshot)['contract']['stance'],'party_a')

    def test_search_routes_and_argument_guards(self):
        run,_,llm=self.run_agent([call('search_law',{'query':'违约金上限是多少'}),{'role':'assistant','content':'未找到依据'}],request='违约金上限是多少')
        self.assertEqual(run['status'],'completed');self.assertEqual([s['tool'] for s in run['steps']],['search_law'])
        self.assertEqual(run['answer'],'未找到依据')
        with self.sf() as s:self.assertEqual(s.scalar(select(func.count()).select_from(ReviewTask)),0)
        for args in [{'query':''},{'query':'x','top_k':True},{'query':'x','top_k':99},{'query':'x','invented':1}]:
            with self.subTest(args=args),self.assertRaises(Exception):search_law(self.sf,args)

    def test_handoff_reuse_requires_real_completed_and_same_context(self):
        key=self.pending();source={'content':'body'}
        with patch.object(db,'SessionLocal',self.sf):result=tickets.set_stance(key,self.user,'party_b')
        with self.sf.begin() as s:
            row=s.get(Ticket,key);row.source_content_hash=content_hash(source)
            task=s.get(ReviewTask,result['task_id']);task.status='completed';row.status='needs_human'
        entry=reusable_handoff(self.sf,'test','C002',source,'party_b',self.user)
        self.assertTrue(entry['reused']);self.assertEqual(entry['task_id'],result['task_id'])
        for endpoint,cid,body,stance in [('other','C002',source,'party_b'),('test','C003',source,'party_b'),
            ('test','C002',{'content':'changed'},'party_b'),('test','C002',source,'party_a')]:
            self.assertIsNone(reusable_handoff(self.sf,endpoint,cid,body,stance,self.user))
        with self.sf.begin() as s:s.get(ReviewTask,result['task_id']).status='running'
        self.assertIsNone(reusable_handoff(self.sf,'test','C002',source,'party_b',self.user))

    def test_needs_stance_filter_and_cannot_close(self):
        key=self.pending()
        with patch.object(db,'SessionLocal',self.sf):
            data=tickets.listing(self.user,'needs_stance')
            self.assertEqual(data['counts']['needs_stance'],1);self.assertEqual(data['tickets'][0]['id'],key)
            with self.assertRaises(HTTPException):tickets.close(key,self.user)

    def test_migration_repeated_preserves_children(self):
        agent_stance_handoff(self.engine);agent_stance_handoff(self.engine)
        with self.engine.connect() as c:
            self.assertEqual(c.exec_driver_sql('PRAGMA foreign_key_check').all(),[])
            self.assertEqual(c.exec_driver_sql('SELECT count(*) FROM clauses').scalar(),12)
            self.assertTrue(next(v for v in inspect(c).get_columns('contracts') if v['name']=='party_stance')['nullable'])

    def test_migration_from_real_previous_schema_preserves_rows(self):
        from sqlalchemy import MetaData,CheckConstraint
        from sqlalchemy.orm import sessionmaker
        from tests.review_helpers import seed_contract_a
        old=MetaData()
        for table in db.Base.metadata.sorted_tables:table.to_metadata(old)
        old.tables['contracts'].c.party_stance.nullable=False
        table=old.tables['tickets']
        for constraint in list(table.constraints):
            if isinstance(constraint,CheckConstraint) and 'needs_stance' in str(constraint.sqltext):
                table.constraints.remove(constraint)
        table.append_constraint(CheckConstraint("status IN ('pending','reviewing','needs_human','done','closed')"))
        for name in ('source_system','source_contract_id','source_content_hash','agent_run_id'):
            table._columns.remove(table.c[name])
        engine=db.create_db_engine('sqlite:///'+(Path(self.tmp.name)/'old.db').as_posix())
        try:
            old.create_all(engine);sf=sessionmaker(bind=engine);cid=seed_contract_a(sf)
            with engine.begin() as c:
                uid=c.exec_driver_sql('SELECT owner_id FROM contracts').scalar()
                c.exec_driver_sql("INSERT INTO tickets(id,title,contract_id,requester_id,raw_request) VALUES ('t','t',?,?, 'old')",(cid,uid))
            agent_stance_handoff(engine);agent_stance_handoff(engine)
            with engine.begin() as c:
                self.assertEqual(c.exec_driver_sql('PRAGMA foreign_key_check').all(),[])
                self.assertEqual(c.exec_driver_sql('SELECT count(*) FROM clauses').scalar(),12)
                c.exec_driver_sql("UPDATE tickets SET status='needs_stance'")
                c.exec_driver_sql('UPDATE contracts SET party_stance=NULL')
                with self.assertRaises(IntegrityError):c.exec_driver_sql("UPDATE tickets SET status='invalid'")
        finally:engine.dispose()
