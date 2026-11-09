import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.api.reviews import ReviewRequest
from app.db import Base, create_db_engine
from app.models import Clause, LLMLog, ReviewItem
from app.prompts.missing_judge import build_messages
from app.services.missing_judge import GapOutput, validate_gap
from app.services.report import generate_report
from app.services.review_rules import MISSING_RULES
from app.services.review_tasks import create_review, get_task
from tests.review_helpers import seed_contract_a, start_worker, stop_process_tree


class DefaultGapTests(unittest.TestCase):
    def test_schema_requires_concrete_impact_and_strict_boolean(self):
        data = dict(has_gap=True, gap_description="缺少逾期付款的责任计算方式。",
                    impact="损失金额及因果关系举证可能延长追偿时间。", suggestion="约定计算基数、日费率和累计上限。")
        GapOutput.model_validate(data)
        for change in ({"impact": "存在风险"}, {"impact": "  "}, {"has_gap": "true"}):
            with self.assertRaises(ValidationError):
                GapOutput.model_validate({**data, **change})
        with self.assertRaises(ValidationError):
            GapOutput.model_validate({k: v for k, v in data.items() if k != "impact"})

    def test_outline_and_all_five_rules_supported(self):
        ReviewRequest(rules=[], missing_rules=list(MISSING_RULES))
        clauses = [{"id": "c", "original_no": "第一条", "content": "字" * 80 + "HIDDEN"}]
        for rid in ("payment_default", "delivery_default"):
            messages = build_messages(clauses, "party_a", MISSING_RULES[rid])
            self.assertNotIn("HIDDEN", messages[1]["content"])
            self.assertIn('"has_gap"', messages[0]["content"])
            self.assertIn('"impact"', messages[0]["content"])

    def test_hidden_payment_responsibility_prevents_false_gap(self):
        clauses = [{"id": "10", "original_no": "第十条", "content": "字" * 80 + "甲方逾期付款应承担相同计算口径的责任。"}]
        output = GapOutput(has_gap=True, gap_description="未检出甲方逾期付款的责任。",
                           impact="会增加损失金额及因果关系的举证负担。", suggestion="明确逾期付款的违约金计算方式。")
        result = validate_gap(output, clauses, "payment_default")
        self.assertEqual(result["check_status"], "insufficient")
        self.assertIsNone(result["has_gap"])
        self.assertIn("第十条", result["gap_description"])

    def test_worker_gap_and_no_gap_are_single_calls_and_reported_without_fake_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "law.db"
            engine = create_db_engine("sqlite:///" + path.as_posix())
            Base.metadata.create_all(engine)
            sf = sessionmaker(bind=engine, autoflush=False)
            process = None
            try:
                cid = seed_contract_a(sf)
                # Controlled mock fixture: remove the payment cross-reference.
                with sf.begin() as session:
                    clause = session.execute(select(Clause).where(Clause.contract_id == cid, Clause.seq == 10)).scalar_one()
                    clause.content = "第十条 乙方逾期交付应按日承担违约责任，累计不超过合同价款的百分之十。"
                task = create_review(sf, cid, 1, [], ["payment_default", "delivery_default"])["task_id"]
                process = start_worker(path, Path(directory) / "worker.log")
                self.assertEqual(process.wait(timeout=30), 0)
                self.assertEqual(get_task(sf, task)["status"], "completed")
                with sf() as session:
                    items = session.execute(select(ReviewItem).where(ReviewItem.review_id == task)).scalars().all()
                    self.assertEqual(len(items), 2)
                    results = {}
                    for item in items:
                        self.assertIsNone(item.clause_id)
                        self.assertEqual(item.item_key, "contract:" + item.rule_id)
                        logs = session.execute(select(LLMLog).where(LLMLog.item_id == item.id)).scalars().all()
                        self.assertEqual(len(logs), 1)
                        results[item.rule_id] = json.loads(item.machine_result)
                    self.assertTrue(results["payment_default"]["has_gap"])
                    self.assertFalse(results["delivery_default"]["has_gap"])
                    self.assertEqual(results["delivery_default"]["check_status"], "no_issue_detected")
                    self.assertIn("第十条", results["delivery_default"]["gap_description"])
                report = generate_report(sf, task)
                self.assertIn("## 合同级缺失", report)
                self.assertIn("缺了会怎样（影响）", report)
                self.assertIn("### 合同级检查", report)
            finally:
                if process:
                    stop_process_tree(process)
                engine.dispose()
