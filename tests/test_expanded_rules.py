import unittest
from app.services.review_rules import CLAUSE_RULES, MISSING_RULES
from app.services.review_planner import build_check_plan
from scripts.preview_rule_plan import preview


class ExpandedRulesTests(unittest.TestCase):
    def test_definitions_and_applicability_examples(self):
        self.assertEqual(len(CLAUSE_RULES), 10)
        self.assertEqual(len(MISSING_RULES), 5)
        for rule_id in list(CLAUSE_RULES)[3:]:
            rule = CLAUSE_RULES[rule_id]
            self.assertIn(len(rule['check_points']), (3, 4))
            self.assertEqual(len(rule['positive_examples']), 2)
            self.assertEqual(len(rule['negative_examples']), 2)
            for text in rule['positive_examples']:
                self.assertEqual(len(build_check_plan([{'id':'1','seq':1,'content':text}], [rule_id])['units']), 1)
        for rule in MISSING_RULES.values():
            self.assertGreater(len(rule['impact']), 20)

    def test_plan_is_offline_complete_and_not_overbroad(self):
        result = preview()
        self.assertEqual(result['raw_units'], 120)
        self.assertEqual(result['clause_units'], 45)
        self.assertEqual(result['not_applicable'], 75)
        self.assertEqual(result['contract_units'], 5)
        self.assertTrue(all(row['count'] <= 5 for row in result['rows']))
        self.assertEqual(result['model_calls'], 0)

    def test_context_exclusions_preserve_real_mixed_arrangements(self):
        text = '催告后十个工作日仍未改正可解除。知识产权及争议约定继续有效。'
        self.assertEqual(build_check_plan([{'id':'1','seq':1,'content':text}], ['milestones','intellectual_property'])['units'], [])
        text += '项目工期六十日，成果知识产权归甲方。'
        self.assertEqual(len(build_check_plan([{'id':'1','seq':1,'content':text}], ['milestones','intellectual_property'])['units']), 2)
