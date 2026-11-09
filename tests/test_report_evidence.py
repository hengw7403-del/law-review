import unittest
from app.services.report_evidence import compact_evidence
from tests.test_applicability import sample_clauses


class CompactEvidenceTests(unittest.TestCase):
    def test_delivery_only_references_acceptance_warranty_liability(self):
        _, _, clauses = sample_clauses()
        result = {"evidence": [{"clause_id": c["id"]} for c in clauses]}
        selected = compact_evidence(result, clauses, "delivery_default", True)
        self.assertEqual([s["clause_id"] for s in selected], ["5", "9", "10"])
        self.assertTrue(all(s["explanation"] for s in selected))
        self.assertTrue(all("content" not in s for s in selected))

    def test_statutes_use_only_actual_citations_deduplicate_and_cap_five(self):
        result = {"evidence": [{"chunk_id": str(i), "article_no": "第{}条".format(i)} for i in range(8)],
                  "candidates": [{"chunk_id": "unused"}], "support_note": "本项引用法条核对违约责任范围。"}
        result["evidence"].append(result["evidence"][0])
        selected = compact_evidence(result, [], "liability")
        self.assertEqual(len(selected), 5)
        self.assertNotIn("unused", [s["chunk_id"] for s in selected])
        self.assertTrue(all(s["explanation"] for s in selected))
