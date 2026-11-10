import json,unittest
from fastapi import HTTPException
from sqlalchemy import select
from tests.test_conversations import ConversationTests
from app.models import Ticket,Contract,User,ReviewTask,ReviewItem
from app.services.ticket_completion import complete_tickets
from app.services.auth import Principal
from app.services import tickets
from app.services.review_tasks import utc_now

class TicketTests(ConversationTests):
    def test_assignment_review_dedupe_visibility_and_filter(self):
        with self.sf.begin() as s:
            s.get(Contract,self.cid).reviewer_id=None
            s.add(User(id='second',username='second',password_hash='x',role='reviewer'))
            s.add(Ticket(id='ticket',title='t',contract_id=self.cid,requester_id=self.uid,raw_request='test'))
        first=Principal(self.uid,'u','reviewer',1);second=Principal('second','second','reviewer',1);other=Principal('stranger','stranger','requester',1)
        self.assertEqual(len(tickets.listing(second,'pending')['tickets']),1)
        result=tickets.review('ticket',first)
        self.assertEqual(tickets.listing(other)['tickets'],[])
        with self.assertRaises(HTTPException) as error:tickets.detail('ticket',other)
        self.assertEqual(error.exception.status_code,404)
        with self.sf() as s:self.assertIsNone(s.get(Contract,self.cid).reviewer_id)
        self.assertEqual(tickets.review('ticket',first)['task_id'],result['task_id'])
        with self.assertRaises(HTTPException):tickets.close('ticket',first)
        with self.sf.begin() as s:
            task=s.get(ReviewTask,result['task_id']);task.status='completed';task.finished_at=utc_now()
            complete_tickets(s,task)
        self.assertEqual(tickets.detail('ticket',first)['status'],'done')
        self.assertEqual(len(tickets.listing(first,'done')['tickets']),1)
        self.assertEqual(tickets.close('ticket',first)['status'],'closed')

    def test_high_risk_requires_confirmation_and_excludes_merged(self):
        first=Principal(self.uid,'u','reviewer',1)
        with self.sf.begin() as s:s.add(Ticket(id='ticket',title='t',contract_id=self.cid,requester_id=self.uid,raw_request='test'))
        result=tickets.review('ticket',first)
        with self.sf.begin() as s:
            task=s.get(ReviewTask,result['task_id']);task.status='completed';snap=json.loads(task.input_snapshot)
            for key,status in [('high','supported'),('duplicate','merged')]:
                s.add(ReviewItem(id=key,review_id=task.id,item_key=key,clause_id=snap['clauses'][0]['id'],rule_id='liability',execution_status='done',check_status=status,risk_level='high',machine_result='{}'))
            s.flush();complete_tickets(s,task)
        detail=tickets.detail('ticket',first)
        self.assertEqual((detail['status'],detail['high_risk_count']),('needs_human',1))
        self.assertEqual(len(tickets.listing(first,'needs_human')['tickets']),1)
        with self.assertRaises(HTTPException):tickets.close('ticket',first)
        confirmed=tickets.confirm('ticket',first,'已复核')
        self.assertEqual(confirmed['status'],'done');self.assertEqual(confirmed['confirmed_by'],self.uid)
        with self.assertRaises(HTTPException) as error:tickets.confirm('ticket',first)
        self.assertEqual(error.exception.status_code,409)

    def test_active_review_keeps_pending_but_contract_api_dedupes(self):
        from app.services.review_tasks import create_review
        original=create_review(self.sf,self.cid)['task_id']
        with self.sf.begin() as s:s.add(Ticket(id='ticket',title='t',contract_id=self.cid,requester_id=self.uid,raw_request='test'))
        first=Principal(self.uid,'u','reviewer',1)
        with self.assertRaises(HTTPException):tickets.review('ticket',first)
        item=tickets.detail('ticket',first)
        self.assertEqual(item['status'],'pending');self.assertIsNone(item['review_task_id']);self.assertTrue(item['review_error'])
        self.assertEqual(create_review(self.sf,self.cid)['task_id'],original)
