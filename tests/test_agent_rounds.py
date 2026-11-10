"""Free deterministic limit tests; simulated review is never a paid acceptance."""
from contextlib import redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import patch

from app.services.agent import AgentRunner,max_rounds
from tests import test_agent as fixtures
from tests.test_agent import FakeMCP,call


class SimulatedReview(AgentRunner):
    def review(self,run,user,args):
        cid=args['contract_id']
        result={'task_id':'SIMULATED-'+cid,'stats':{'high':0},'findings':['test-only'*1000]}
        run['state']['reviews'][cid]={'task_id':result['task_id'],'contract_id':'TEST-'+cid,'result':result}
        return {'contract_id':cid,'result':result}


def workflow(count,finished=True):
    messages=[call('list_contracts')]
    for n in range(count):
        cid='C{:03d}'.format(n+1)
        messages += [call('get_contract',{'contract_id':cid}),call('review_contract',{'contract_id':cid}),
                     call('write_result',{'contract_id':cid,'result':{'task_id':'SIMULATED-'+cid}})]
    if finished:messages.append({'role':'assistant','content':'完成'})
    return messages


class DynamicRoundsTests(unittest.TestCase):
    # Reuse setup helpers without inheriting/rerunning the old test methods.
    setUp=fixtures.AgentTests.setUp
    tearDown=fixtures.AgentTests.tearDown
    run_agent=fixtures.AgentTests.run_agent

    def test_formula_and_bounds(self):
        for count,expected in [(None,10),(0,2),(1,5),(3,11),(15,40),(1000,40)]:
            self.assertEqual(max_rounds(count),expected)
        for count in [-1,True,1.5]:
            with self.assertRaises(ValueError):max_rounds(count)

    def test_one_and_three_finish_and_log_every_round(self):
        for count,used,limit in [(1,4,5),(3,10,11)]:
            stream=StringIO()
            with redirect_stdout(stream):
                run,client,llm=self.run_agent(workflow(count),request='把OA合同都审一遍',
                    runner_type=SimulatedReview,client=FakeMCP(count))
            self.assertEqual(run['status'],'completed')
            self.assertEqual((run['rounds_used'],run['rounds_limit'],run['contracts_processed'],run['contracts_total']),
                             (used,limit,count,count))
            self.assertIsNone(run['stopped_reason']);self.assertEqual(len(client.writes),count)
            logs=[json.loads(line) for line in stream.getvalue().splitlines()]
            self.assertEqual(len(logs),used);self.assertEqual(logs[0]['max_rounds'],10)
            self.assertEqual(logs[0]['max_rounds_after'],limit)
            for record in logs:
                self.assertTrue({'round','max_rounds','decision','tool','args','result_ok'}.issubset(record))
                self.assertTrue(record['result_ok'])
            self.assertTrue(all(step['max_rounds']==limit for step in run['steps'][1:]))

    def test_fifteen_ceiling_finishes_thirteen_not_fifteen(self):
        run,client,_=self.run_agent(workflow(15),request='把OA合同都审一遍',
            runner_type=SimulatedReview,client=FakeMCP(15))
        self.assertEqual((run['rounds_used'],run['rounds_limit']),(40,40))
        self.assertEqual((run['contracts_processed'],run['contracts_total']),(13,15))
        self.assertEqual(run['stopped_reason'],'MAX_ROUNDS_REACHED')
        self.assertIn('13/15',run['summary']);self.assertIn('C014',run['summary']);self.assertIn('C015',run['summary'])
        self.assertEqual(len(client.writes),13)

    def test_partial_two_of_three_says_which_contract(self):
        with patch('app.services.agent.MAX_ROUNDS_CEILING',8):
            run,_,_=self.run_agent(workflow(3),request='把OA合同都审一遍',runner_type=SimulatedReview)
        self.assertEqual((run['contracts_processed'],run['contracts_total']),(2,3))
        self.assertEqual(run['stopped_reason'],'MAX_ROUNDS_REACHED')
        self.assertIn('2/3',run['summary']);self.assertIn('第3份 C003 未处理完',run['summary'])

    def test_unknown_scope_uses_base_and_failed_calls_count(self):
        run,_,_=self.run_agent([call('unknown') for _ in range(10)])
        self.assertEqual((run['rounds_used'],run['rounds_limit']),(10,10))
        self.assertIsNone(run['contracts_total'])
        self.assertTrue(all(s['result_ok'] is False for s in run['steps']))

    def test_first_list_is_frozen_even_if_later_list_changes(self):
        class ChangingMCP(FakeMCP):
            def call_tool(self,name,args):
                value=super().call_tool(name,args)
                if name=='list_contracts':self.contract_count=15 if self.contract_count==3 else 1
                return value
        run,_,_=self.run_agent([call('list_contracts') for _ in range(3)]+[{'role':'assistant','content':'完成'}],client=ChangingMCP())
        self.assertEqual((run['contracts_total'],run['rounds_limit']),(3,11))
        self.assertEqual([s['max_rounds'] for s in run['steps']],[10,11,11])
        run,_,_=self.run_agent([call('list_contracts'),call('get_contract',{'contract_id':'C004'}),
            call('review_contract',{'contract_id':'C004'}),{'role':'assistant','content':'无法继续'}],request='把OA合同都审一遍')
        self.assertEqual(run['steps'][2]['error_code'],'TARGET_MISMATCH')
        self.assertEqual((run['contracts_processed'],run['contracts_total']),(0,3))

    def test_context_is_compact_but_full_result_reaches_oa(self):
        run,client,llm=self.run_agent(workflow(1),request='把OA合同都审一遍',
            runner_type=SimulatedReview,client=FakeMCP(1))
        after_fetch=next(m for m in llm.requests[2] if m['role']=='tool' and 'content_received' in m['content'])
        self.assertNotIn('提供技术服务',after_fetch['content'])
        self.assertIn('提供技术服务',run['state']['fetched']['C001']['content'])
        self.assertEqual(client.writes[0]['result']['findings'],['test-only'*1000])
        self.assertNotIn('test-only',json.dumps(llm.requests[-1],ensure_ascii=False))

    def test_empty_scope_completes_without_fabricated_review(self):
        run,client,_=self.run_agent(workflow(0),request='把OA合同都审一遍',runner_type=SimulatedReview,client=FakeMCP(0))
        self.assertEqual(run['status'],'completed')
        self.assertEqual((run['contracts_processed'],run['contracts_total'],run['rounds_limit']),(0,0,2))
        self.assertEqual(client.writes,[])
