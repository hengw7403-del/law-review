import unittest
from app.prompts.risk_judge import SYSTEM_PROMPT,RULE_GUIDANCE
from app.services.review_planner import build_check_plan
from app.services.chunker import chunk_law,SourceInfo
from tests.parse_helpers import ROOT

class PaymentBoundaryTests(unittest.TestCase):
    def test_payment_boundary_without_keyword_narrowing(self):
        self.assertIn('违约金',RULE_GUIDANCE['payment'])
        self.assertIn('180日',SYSTEM_PROMPT)
        self.assertIn('payment必须applicable=false',SYSTEM_PROMPT)
        for text in ['验收通过后180日内支付全部款项','甲方逾期付款按价款千分之一承担违约责任']:
            self.assertEqual(len(build_check_plan([{'id':'c','seq':1,'content':text}],['payment'])['units']),1)
    def test_jurisdiction_sources_keep_annotations_out_of_quotes(self):
        total=0
        for filename in ['civil_procedure_agreed_jurisdiction.txt','civil_procedure_jurisdiction_interpretation.txt']:
            text=(ROOT/'samples/laws'/filename).read_text(encoding='utf-8')
            self.assertIn('需人工核对',text)
            chunks=chunk_law(text,filename,SourceInfo(filename,filename));total+=len(chunks)
            self.assertTrue(all('需人工核对' not in c.content for c in chunks))
        self.assertEqual(total,3)
