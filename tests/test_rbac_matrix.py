"""Real JWT/API role matrix on a disposable DB; no paid model requests."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import CheckConstraint, event, MetaData, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import db
from app.main import app
from app.migrations import expand_user_roles
from app.models import Clause, Contract, Conversation, ReviewItem, ReviewTask, Ticket, User
from app.services.auth import hash_password
from app.services.review_tasks import create_review, utc_now
from tests.review_helpers import seed_contract_a


class RBACMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hashed = hash_password('private-rbac-test-password')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = db.create_db_engine('sqlite:///' + (Path(self.tmp.name) / 'rbac.db').as_posix())
        db.Base.metadata.create_all(self.engine)
        self.sf = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.secret = 'rbac-tests-only-signing-key-32bytes'
        self.patches = [patch.object(db, 'engine', self.engine), patch.object(db, 'SessionLocal', self.sf),
                        patch.dict('os.environ', {'JWT_SECRET': self.secret, 'DEEPSEEK_API_KEY': '',
                                                  'CONTRACT_UPLOAD_DIR': str(Path(self.tmp.name) / 'uploads')})]
        for p in self.patches: p.start()
        with self.sf.begin() as s:
            for uid, role in [('admin','admin'), ('reviewer01','reviewer'), ('reviewer02','reviewer'), ('requester01','requester'), ('requester02','requester')]:
                s.add(User(id=uid, username=uid, password_hash=self.hashed, role=role))
        self.contracts, self.tasks = {}, {}
        for number in [1, 2]:
            owner = 'requester0' + str(number)
            cid = seed_contract_a(self.sf)
            with self.sf.begin() as s:
                s.get(Contract, cid).owner_id = owner
            tid = create_review(self.sf, cid, rules=['liability'], created_by=owner)['task_id']
            with self.sf.begin() as s:
                task = s.get(ReviewTask, tid)
                task.status, task.done_items, task.total_items, task.finished_at = 'completed', number, number, utc_now()
                clauses = list(s.scalars(select(Clause).where(Clause.contract_id == cid).order_by(Clause.seq)))
                for n in range(number):
                    s.add(ReviewItem(id='risk{}-{}'.format(number,n), review_id=tid, item_key='risk{}'.format(n),
                                     clause_id=clauses[n].id, rule_id='liability', execution_status='done',
                                     check_status='supported', risk_level='high', machine_result=json.dumps(
                                         {'check_status':'supported','description':'测试风险','evidence':[]})))
                s.add(Conversation(id='conv'+str(number), user_id=owner, collected='{}'))
                s.add(Ticket(id='ticket'+str(number), title='测试工单', contract_id=cid, requester_id=owner,
                             status='needs_human', high_risk_count=number, review_task_id=tid, raw_request='test',
                             updated_at=utc_now()))
            self.contracts[number], self.tasks[number] = cid, tid
        self.client = TestClient(app)
        self.client.__enter__()
        self.tokens = {uid:jwt.encode({'user_id':uid,'auth_version':1,'exp':datetime.now(timezone.utc)+timedelta(hours=1)},
                                     self.secret,algorithm='HS256')
                       for uid in ['admin','reviewer01','reviewer02','requester01','requester02']}

    def tearDown(self):
        self.client.__exit__(None,None,None)
        for p in reversed(self.patches):p.stop()
        self.engine.dispose()
        self.tmp.cleanup()

    def headers(self, uid):
        return {'Authorization':'Bearer ' + self.tokens[uid]}

    def test_each_role_each_endpoint_matrix(self):
        cid, tid = self.contracts[1], self.tasks[1]
        sample = (Path(__file__).resolve().parents[1] / 'samples/contract_c_bad.txt').read_bytes()
        mock = Mock()
        mock.complete.return_value = (json.dumps({'reply':'请继续选择','slots':{'contract_id':None,'focus_points':[],'priority':'normal'},'ready':False}),{},1)
        with patch('app.services.conversations.LLMClient', return_value=mock):
            for uid in ['admin','reviewer01','reviewer02','requester01']:
                h = self.headers(uid)
                with self.sf.begin() as s:
                    ticket=s.get(Ticket,'ticket1');ticket.status='needs_human'
                with self.subTest(user=uid,endpoint='POST /contracts'):
                    response=self.client.post('/contracts',headers=h,data={'party_stance':'party_b'},files={'file':('c.txt',sample,'text/plain')})
                    self.assertEqual(response.status_code,201,response.text)
                    self.assertEqual(response.json()['owner_id'],uid)
                with self.subTest(user=uid,endpoint='POST /conversations'):
                    self.assertEqual(self.client.post('/conversations',headers=h,json={}).status_code,200)
                calls=[('GET','/contracts',200,None),('GET','/contracts/'+cid,200,None),
                       ('POST','/contracts/'+cid+'/review',202,{}),('GET','/tasks/'+tid,200,None),
                       ('GET','/reviews/'+tid+'/items',200,None),('GET','/reviews/'+tid+'/report',200,None),
                       ('GET','/tickets',200,None),('GET','/tickets/ticket1',200,None),
                       ('POST','/tickets/ticket1/confirm',403 if uid.startswith('requester') else 200,{}),
                       ('GET','/conversations/conv1',200 if uid in {'admin','requester01'} else 404,None),
                       ('POST','/conversations/conv1/messages',200 if uid in {'admin','requester01'} else 404,{'content':'继续'}),
                       ('GET','/dashboard/summary',200,None),('GET','/admin/users',200 if uid=='admin' else 403,None),
                       ('GET','/admin/knowledge',200 if uid=='admin' else 403,None),
                       ('PATCH','/admin/contracts/'+cid+'/reviewer',200 if uid=='admin' else 403,{'reviewer_id':'reviewer01'})]
                for method,path,status,body in calls:
                    with self.subTest(user=uid,endpoint=method+' '+path):
                        response=self.client.request(method,path,headers=h,**({'json':body} if body is not None else {}))
                        self.assertEqual(response.status_code,status,response.text)
                with self.sf.begin() as s:s.get(Ticket,'ticket1').status='done'
                with self.subTest(user=uid,endpoint='POST /tickets/id/review'):
                    self.assertEqual(self.client.post('/tickets/ticket1/review',headers=h,json={}).status_code,202)
                with self.subTest(user=uid,endpoint='POST /tickets/id/close'):
                    self.assertEqual(self.client.post('/tickets/ticket1/close',headers=h).status_code,200)

    def test_requester_isolation_includes_pending_and_every_resource_path(self):
        h=self.headers('requester02');cid=self.contracts[1];tid=self.tasks[1]
        with self.sf.begin() as s:s.get(Ticket,'ticket1').status='pending'
        calls=[('GET','/contracts/'+cid),('GET','/contracts/'+cid+'/clauses'),('POST','/contracts/'+cid+'/review'),
               ('GET','/tasks/'+tid),('GET','/reviews/'+tid+'/items'),('GET','/reviews/'+tid+'/report'),
               ('GET','/tickets/ticket1'),('POST','/tickets/ticket1/close'),('POST','/tickets/ticket1/review'),
               ('GET','/conversations/conv1'),('POST','/conversations/conv1/abandon')]
        for method,path in calls:
            with self.subTest(path=path):self.assertEqual(self.client.request(method,path,headers=h).status_code,404)
        self.assertEqual(self.client.post('/conversations/conv1/messages',headers=h,json={'content':'继续'}).status_code,404)
        self.assertEqual(self.client.post('/tickets/ticket1/confirm',headers=h,json={}).status_code,403)
        rows=self.client.get('/contracts',headers=h).json()['contracts']
        self.assertEqual([r['contract_id'] for r in rows],[self.contracts[2]])
        self.assertEqual([r['id'] for r in self.client.get('/tickets',headers=h).json()['tickets']],['ticket2'])
        for uid in ['admin','reviewer01','reviewer02']:
            self.assertEqual(len(self.client.get('/tickets',headers=self.headers(uid)).json()['tickets']),2)

    def test_dashboard_counts_and_payload_are_role_scoped(self):
        for uid in ['admin','reviewer01','reviewer02','requester01','requester02']:
            with self.subTest(user=uid):
                response=self.client.get('/dashboard/summary',headers=self.headers(uid));self.assertEqual(response.status_code,200,response.text)
                data=response.json();own=uid.startswith('requester')
                self.assertEqual((data['contracts'],data['tickets'],data['tickets_needs_human'],data['high_risk_total']),
                                 (1,1,1,1 if uid=='requester01' else 2) if own else (2,2,2,3))
                if own:
                    self.assertEqual([t['id'] for t in data['submissions']],['ticket'+uid[-1]])
                    self.assertEqual(data['needs_human'],[]);self.assertEqual(data['recent_activity'],[])
                elif uid.startswith('reviewer'):
                    self.assertEqual(len(data['needs_human']),2)
        with self.sf.begin() as s:
            row=s.get(Ticket,'ticket1');row.status='done';row.updated_at=utc_now()
        self.assertEqual(self.client.get('/dashboard/summary',headers=self.headers('reviewer01')).json()['today_done'],1)

    def test_requester_login_and_confirmation_do_not_mutate(self):
        response=self.client.post('/auth/login',json={'username':'requester01','password':'private-rbac-test-password'})
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['role'],'requester')
        for uid in ['requester01','requester02']:
            self.assertEqual(self.client.post('/tickets/ticket1/confirm',headers=self.headers(uid),json={}).status_code,403)
        with self.sf() as s:self.assertEqual(s.get(Ticket,'ticket1').status,'needs_human')
        self.assertEqual(self.client.post('/tickets/ticket1/confirm',headers=self.headers('reviewer02'),json={'note':'法务确认'}).status_code,200)
        self.assertEqual(self.client.post('/tickets/ticket1/confirm',headers=self.headers('reviewer02'),json={}).status_code,409)

    def test_anonymous_cannot_bypass_ui(self):
        for method,path,body in [('GET','/dashboard/summary',None),('GET','/tickets',None),('GET','/contracts',None),
                                 ('GET','/admin/users',None),('GET','/admin/knowledge',None),
                                 ('POST','/tickets/ticket1/confirm',{}),('POST','/tickets/ticket1/close',None),
                                 ('POST','/conversations/conv1/messages',{'content':'继续'})]:
            with self.subTest(path=path):
                self.assertEqual(self.client.request(method,path,**({'json':body} if body is not None else {})).status_code,401)

    def test_admin_conversation_access_preserves_owner_and_candidate_isolation(self):
        mock=Mock()
        mock.complete.return_value=(json.dumps({'reply':'已确认','slots':{'contract_id':self.contracts[1],'focus_points':[],'priority':'normal'},'ready':True}),{},1)
        with patch('app.services.conversations.LLMClient',return_value=mock):
            response=self.client.post('/conversations/conv1/messages',headers=self.headers('admin'),json={'content':'确认创建 '+self.contracts[1]})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual([c['id'] for c in response.json()['candidates']],[self.contracts[1]])
        with self.sf() as s:
            ticket=s.get(Ticket,response.json()['ticket']['id'])
            self.assertEqual(ticket.requester_id,'requester01')
        self.assertEqual(self.client.get('/conversations/conv1',headers=self.headers('reviewer01')).status_code,404)
        self.assertEqual(self.client.get('/tickets/'+ticket.id,headers=self.headers('reviewer01')).json()['messages'],[])


class RoleMigrationTests(unittest.TestCase):
    def test_parent_rebuild_is_idempotent_and_preserves_children_and_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=db.create_db_engine('sqlite:///'+(Path(directory)/'old.db').as_posix())
            try:
                old=MetaData()
                for table in db.Base.metadata.sorted_tables:table.to_metadata(old)
                table=old.tables['users']
                for constraint in list(table.constraints):
                    if isinstance(constraint,CheckConstraint) and 'role IN' in str(constraint.sqltext):table.constraints.remove(constraint)
                table.append_constraint(CheckConstraint("role IN ('admin','reviewer')"))
                old.create_all(engine);sf=sessionmaker(bind=engine)
                cid=seed_contract_a(sf)
                with sf() as s:
                    uid=s.get(Contract,cid).owner_id
                    before=tuple(s.execute(select(User.id,User.password_hash,User.auth_version,User.role).where(User.id==uid)).one())
                expand_user_roles(engine);expand_user_roles(engine)
                with sf.begin() as s:
                    self.assertEqual(tuple(s.execute(select(User.id,User.password_hash,User.auth_version,User.role).where(User.id==uid)).one()),before)
                    self.assertEqual(s.get(Contract,cid).owner_id,uid)
                    s.add(User(id='requester',username='requester',role='requester',password_hash='!'))
                with engine.connect() as c:
                    self.assertEqual(c.exec_driver_sql('PRAGMA foreign_keys').scalar(),1)
                    self.assertEqual(c.exec_driver_sql('PRAGMA foreign_key_check').all(),[])
                    self.assertNotIn('_alembic',c.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='contracts'").scalar())
                with self.assertRaises(IntegrityError):
                    with sf.begin() as s:s.add(User(id='bad',username='bad',role='invalid',password_hash='!'))
            finally:engine.dispose()

    def test_failed_rebuild_rolls_back_and_restores_foreign_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=db.create_db_engine('sqlite:///'+(Path(directory)/'rollback.db').as_posix())
            try:
                with engine.begin() as c:
                    c.exec_driver_sql("CREATE TABLE users (id TEXT NOT NULL PRIMARY KEY, username TEXT NOT NULL UNIQUE, org_name TEXT, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','reviewer')), active INTEGER NOT NULL DEFAULT 1, auth_version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
                    c.exec_driver_sql("INSERT INTO users(id,username,password_hash,role) VALUES ('u','kept','hash','admin')")
                def fail_copy(conn,cursor,statement,parameters,context,executemany):
                    if statement.lstrip().startswith('INSERT INTO _alembic_tmp_users'):
                        raise RuntimeError('injected copy failure')
                event.listen(engine,'before_cursor_execute',fail_copy)
                with self.assertRaises(RuntimeError):expand_user_roles(engine)
                event.remove(engine,'before_cursor_execute',fail_copy)
                with engine.connect() as c:
                    self.assertEqual(c.exec_driver_sql("SELECT username FROM users WHERE id='u'").scalar(),'kept')
                    self.assertEqual(c.exec_driver_sql('PRAGMA foreign_keys').scalar(),1)
                    self.assertEqual(c.exec_driver_sql("SELECT count(*) FROM sqlite_master WHERE name='_alembic_tmp_users'").scalar(),0)
                expand_user_roles(engine)
            finally:engine.dispose()
