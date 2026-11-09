import unittest
from app.prompts.risk_judge import RULE_GUIDANCE, SYSTEM_PROMPT
from app.services.clause_splitter import split_document
from tests.parse_helpers import ROOT


class ContrastSamplesTests(unittest.TestCase):
    def test_rule_scope_and_examples_agree(self):
        self.assertNotIn('相关效力问题',RULE_GUIDANCE['dispute_resolution'])
        self.assertIn('适用法律',RULE_GUIDANCE['dispute_resolution'])
        self.assertIn('合同签订地人民法院管辖',SYSTEM_PROMPT)
        self.assertIn('本合同终止后争议解决条款仍然有效',SYSTEM_PROMPT)
        self.assertIn('不检查合同终止后的条款存续效力',SYSTEM_PROMPT)

    def test_exact_three_bad_edits_and_clean_identical_body(self):
        a=(ROOT/'samples/contract_a.txt').read_text(encoding='utf-8')
        clean=(ROOT/'samples/contract_c_clean.txt').read_text(encoding='utf-8')
        bad=(ROOT/'samples/contract_c_bad.txt').read_text(encoding='utf-8')
        self.assertEqual(a.split('\n',1)[1],clean.split('\n',1)[1])
        ac=split_document(a)['clauses']; bc=split_document(bad)['clauses']
        self.assertEqual(len(ac),len(bc)); self.assertEqual(len(bc),12)
        self.assertEqual([i+1 for i,(x,y) in enumerate(zip(ac,bc)) if x['content']!=y['content']],[2,10,11])
        self.assertNotIn('百分之十',bc[9]['content']); self.assertIn('不设上限',bc[9]['content'])
        self.assertIn('180日内支付全部款项',bc[1]['content'])
        self.assertIn('乙方无权单方解除',bc[10]['content'])
