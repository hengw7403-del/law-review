import json
import unittest
from app.prompts.risk_judge import build_messages, PROMPT_VERSION


class ConcreteIssueBoundaryTests(unittest.TestCase):
    def test_normal_arrangements_are_not_required_to_repeat_every_rule(self):
        messages = build_messages('按已确认需求清单及测试用例验收，十日内反馈。', 'party_b', 'acceptance', [])
        system = messages[0]['content']
        self.assertIn('不能看到其他条款或附件', system)
        self.assertIn('不是每条都必须逐项重复写全', system)
        self.assertIn('不能因本条未写标准期限而报缺失', system)
        payload = json.loads(messages[1]['content'])
        self.assertEqual(payload['rule_definition']['rule_id'], 'acceptance')

    def test_confidentiality_requires_specific_unfavorable_arrangement(self):
        system = build_messages('资料仅用于本项目，终止后返还。', 'party_b', 'confidentiality_data', [])[0]['content']
        self.assertIn('未单设违约金', system)
        self.assertIn('不能单独证明必须约定保密违约金', system)
        self.assertIn('concrete-issue', PROMPT_VERSION)
