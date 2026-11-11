"""No paid calls. Database persistence, API history and legacy import."""
import copy
import json
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch
from sqlalchemy import select,event
from sqlalchemy.exc import IntegrityError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.models import AgentRun,AgentStep,ReviewTask
from app.services.agent_store import AgentStore
from app.services.agent import AgentManager
from app.services.auth import current_user,Principal
from app.api.agent import router
from app.migrations import add_agent_tables
from scripts.migrate_agent_runs import migrate
from tests import test_agent as fixtures
from tests.test_agent import call


class AgentDatabaseTests(unittest.TestCase):
    setUp=fixtures.AgentTests.setUp
    tearDown=fixtures.AgentTests.tearDown
    run_agent=fixtures.AgentTests.run_agent

    def sample(self):
        return {'run_id':uuid.uuid4().hex,'user_id':self.uid,'request':'列合同','status':'running',
            'created_at':'2026-11-10T19:59:00+08:00','steps':[],
            'state':{'listed':None,'fetched':{},'reviews':{},'written':{}}}

    def test_each_step_commits_and_fresh_store_reads_without_files(self):
        run=self.sample();self.store.save(run)
        run['steps'].append({'round':1,'max_rounds':10,'tool':'list_contracts','decision':'call list_contracts',
            'args':{},'result_ok':True,'result_summary':'x'*250,'tokens':0})
        self.store.save(run)
        fresh=AgentStore(session_factory=self.sf);value=fresh.read(run['run_id'],self.user)
        self.assertEqual(len(value['steps']),1);self.assertEqual(len(value['steps'][0]['result_summary']),200)
        self.assertEqual(value['created_at'],'2026-11-10T11:59:00+00:00')
        self.assertFalse((Path(self.tmp.name)/'runs').exists())

    def test_unchanged_steps_do_not_update_and_no_full_results(self):
        run=self.sample();run['steps']=[{'round':1,'tool':'write_result','args':{'contract_id':'C001','result':{'task_id':'t','body':'PRIVATE_RESULT'}},'result_ok':True,'result_summary':'已写回'}]
        run['state']['fetched']={'C001':{'content':'PRIVATE_CONTRACT'}}
        run['state']['reviews']={'C001':{'contract_id':self.contract_id,'task_id':'t','result':{'findings':['PRIVATE_RESULT']}}}
        self.store.save(run);updates=[]
        def observe(conn,cursor,statement,parameters,context,executemany):
            if statement.startswith('UPDATE agent_steps'):updates.append(statement)
        event.listen(self.engine,'before_cursor_execute',observe)
        try:self.store.save(run)
        finally:event.remove(self.engine,'before_cursor_execute',observe)
        self.assertEqual(updates,[])
        with self.sf() as s:
            row=s.get(AgentRun,run['run_id']);step=s.scalar(select(AgentStep))
            self.assertNotIn('PRIVATE_CONTRACT',row.context_json);self.assertNotIn('PRIVATE_RESULT',row.context_json+step.args)

    def test_relational_task_link_and_unique_round(self):
        with self.sf.begin() as s:s.add(ReviewTask(id='real-task',contract_id=self.contract_id,review_version=1,status='completed',input_snapshot='{}',created_by=self.uid))
        run=self.sample();run['steps']=[{'round':1,'tool':'review_contract','args':{'contract_id':'C001'},'task_id':'real-task','result_ok':True}]
        self.store.save(run);self.store.save(run)
        with self.sf() as s:
            self.assertEqual(len(list(s.scalars(select(AgentStep)))),1)
            self.assertEqual(s.scalar(select(AgentStep.review_task_id)),'real-task')
        with self.assertRaises(IntegrityError),self.sf.begin() as s:
            s.add(AgentStep(id='duplicate',run_id=run['run_id'],round=1,created_at=__import__('datetime').datetime.now()))

    def test_database_checks_and_migration_twice(self):
        add_agent_tables(self.engine);add_agent_tables(self.engine)
        run=self.sample();self.store.save(run)
        for sql in ["UPDATE agent_runs SET status='bad'","UPDATE agent_runs SET created_by='missing'",
            "INSERT INTO agent_steps(id,run_id,round,args,created_at) VALUES ('x','missing',1,'{}',CURRENT_TIMESTAMP)"]:
            with self.subTest(sql=sql),self.assertRaises(IntegrityError),self.engine.begin() as c:c.exec_driver_sql(sql)

    def test_import_idempotent_preserves_files_and_reports_bad_records(self):
        directory=Path(self.tmp.name)/'legacy';directory.mkdir();run=self.sample();run['status']='completed'
        path=directory/(run['run_id']+'.json');body=json.dumps(run);path.write_text(body,encoding='utf-8')
        report=migrate(directory,self.sf);self.assertEqual((report['imported'],report['failed']),(1,0))
        run['request']='should not overwrite';path.write_text(json.dumps(run),encoding='utf-8')
        report=migrate(directory,self.sf);self.assertEqual(report['skipped'],1)
        self.assertEqual(AgentStore(session_factory=self.sf).read(run['run_id'])['request'],'列合同')
        self.assertTrue(path.exists());(directory/'broken.json').write_text('{',encoding='utf-8')
        report=migrate(directory,self.sf);self.assertEqual(report['failed'],1)
        self.assertEqual(report['imported'],0);self.assertTrue(path.exists())

    def test_restart_marks_only_running_failed_and_keeps_steps(self):
        run=self.sample();run['steps']=[{'round':1,'tool':'list_contracts','args':{},'result_ok':True},
            {'round':2,'tool':'get_contract','args':{},'result_ok':None}]
        self.store.save(run);fresh=AgentStore(session_factory=self.sf);fresh.recover()
        restored=fresh.read(run['run_id'])
        self.assertEqual(restored['status'],'failed');self.assertEqual(len(restored['steps']),2)
        self.assertTrue(restored['steps'][0]['result_ok']);self.assertFalse(restored['steps'][1]['result_ok'])
        self.assertEqual(restored['stopped_reason'],'ERROR');self.assertIsNotNone(restored['finished_at'])

    def test_list_filters_roles_and_cross_user_detail(self):
        run=self.sample();run.update(status='completed');self.store.save(run)
        other=self.sample();other.update(status='failed',created_at='2026-11-10T20:00:00+08:00');self.store.save(other)
        manager=AgentManager(self.store,self.sf)
        app=FastAPI();app.include_router(router);app.state.agent_manager=manager
        user=[Principal('other-legal','other','reviewer',1)]
        app.dependency_overrides[current_user]=lambda:user[0]
        with TestClient(app) as c:
            data=c.get('/agent/runs?limit=1').json()['runs'];self.assertEqual(data[0]['id'],other['run_id'])
            self.assertEqual(len(c.get('/agent/runs?status=completed&created_by='+self.uid).json()['runs']),1)
            self.assertEqual(c.get('/agent/runs?status=&created_by=').status_code,200)
            self.assertEqual(c.get('/agent/runs?limit=101').status_code,422)
            self.assertEqual(c.get('/agent/runs?status=bad').status_code,422)
            self.assertEqual(c.get('/agent/runs/'+run['run_id']).status_code,200)
            user[0]=Principal('other','requester','requester',1)
            self.assertEqual(c.get('/agent/runs').status_code,403)
            self.assertEqual(c.get('/agent/runs/'+run['run_id']).status_code,403)

    def test_deterministic_runner_persists_without_model(self):
        run,_,_=self.run_agent([call('list_contracts'),{'role':'assistant','content':'完成'}])
        fresh=AgentStore(session_factory=self.sf).read(run['run_id'])
        self.assertEqual(fresh['status'],'completed');self.assertEqual(fresh['steps'][0]['tool'],'list_contracts')
        self.assertEqual(fresh['steps'][0]['tokens'],0)

    def test_future_stance_confirmation_links_task_to_step(self):
        from app.models import Contract
        from app import db
        from app.services import tickets
        run=self.sample();self.store.save(run)
        with self.sf.begin() as s:s.get(Contract,self.contract_id).party_stance=None
        key=tickets.stance_pending(self.sf,self.contract_id,self.user,'审一下',agent_run_id=run['run_id'],source_contract_id='C002')
        run['steps']=[{'round':1,'tool':'review_contract','args':{'contract_id':'C002'},'result_ok':True}]
        run['state']['reviews']={'C002':{'ticket_id':key,'contract_id':self.contract_id,'task_id':None,'status':'needs_stance'}}
        self.store.save(run)
        with patch.object(db,'SessionLocal',self.sf):result=tickets.set_stance(key,self.user,'party_b')
        self.assertEqual(AgentStore(session_factory=self.sf).read(run['run_id'])['steps'][0]['review_task_id'],result['task_id'])
