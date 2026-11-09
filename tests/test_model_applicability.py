import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from openai import OpenAI
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.api.judge import router
from app.db import Base, create_db_engine
from app.models import Clause, KnowledgeChunk, LLMLog, ReviewItem, ReviewTask
from app.prompts.risk_judge import SYSTEM_PROMPT, PROMPT_VERSION
from app.services.chunker import SourceInfo
from app.services.knowledge import enable_source, import_law
from app.services.llm import LLMClient
from app.services.report import generate_report
from app.services.review_tasks import create_review, get_task
from app.services.review_worker import ReviewWorker
from app.services.risk_judge import RiskJudge
from app.services.search import build_search_index
from app.services.validator import RiskOutput, validate_evidence
from tests.parse_helpers import ROOT
from tests.review_helpers import seed_contract_a


def model_output(**updates):
    return {"applicable": True, "has_issue": True, "risk_level": "medium", "description": "待核查的责任边界。",
            "reason": "涉及迟延付款责任的范围。", "suggestion": "明确责任计算口径。", "evidence_ids": [], "support_note": "", **updates}


class ModelApplicabilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "tri-state.db"
        self.engine = create_db_engine("sqlite:///" + self.path.as_posix())
        Base.metadata.create_all(self.engine)
        self.sf = sessionmaker(bind=self.engine)
        self.cid = seed_contract_a(self.sf)
        for filename, sid in (("civil_code_liability.txt", "law1"), ("civil_code_termination.txt", "law2")):
            with self.sf() as session:
                import_law(session, (ROOT / "samples/laws" / filename).read_text(encoding="utf-8"), filename, SourceInfo(sid, sid))
            with self.sf() as session:
                enable_source(session, sid)
        with self.sf() as session:
            self.index = build_search_index(session)
            self.kid = session.scalar(select(KnowledgeChunk.id).where(KnowledgeChunk.article_no == "第五百八十五条"))

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def test_strict_applicable_is_required_and_prompt_covers_positive_negative_and_termination(self):
        data = model_output()
        self.assertEqual(len(data), 8)
        for change in ({"applicable": "false"}, {"applicable": None}, {"applicable": False}):
            with self.assertRaises(ValidationError):
                RiskOutput.model_validate({**data, **change})
        with self.assertRaises(ValidationError):
            RiskOutput.model_validate({k: v for k, v in data.items() if k != "applicable"})
        self.assertIn("去哪打官司", SYSTEM_PROMPT)
        self.assertIn("赔偿责任", SYSTEM_PROMPT)
        self.assertIn("严重违反", SYSTEM_PROMPT)
        self.assertIn("liability必须applicable=true", SYSTEM_PROMPT)
        self.assertIn("applicable", PROMPT_VERSION)

    def test_not_applicable_needs_no_database_evidence_and_ignores_stray_citation(self):
        session = Mock()
        result = validate_evidence(RiskOutput.model_validate(model_output(applicable=False, has_issue=False, risk_level="none", evidence_ids=["k_fake"])), [], session)
        session.execute.assert_not_called()
        self.assertEqual((result["applicable"], result["has_issue"], result["check_status"]), (False, False, "not_applicable"))
        self.assertEqual(result["evidence"], [])

    def test_three_state_matrix_preserves_valid_citations_and_insufficient(self):
        text = "甲方逾期付款，每日按合同价款的千分之一支付违约金，未约定累计上限。"
        candidates = self.index.search(text + " 违约金 违约责任", 5)
        for updates, status in ((dict(has_issue=False, risk_level="none"), "no_issue_detected"),
                                (dict(evidence_ids=[self.kid]), "supported"),
                                (dict(evidence_ids=[]), "insufficient"),
                                (dict(evidence_ids=["k_fake"]), "insufficient")):
            with self.subTest(status=status), self.sf() as session:
                result = validate_evidence(RiskOutput.model_validate(model_output(**updates)), candidates, session)
                self.assertTrue(result["applicable"])
                self.assertEqual(result["check_status"], status)

    def test_worker_model_not_applicable_persists_resumes_and_progress_is_complete(self):
        with self.sf.begin() as session:
            for clause in session.execute(select(Clause).where(Clause.contract_id == self.cid)).scalars():
                clause.content = "违约责任背景下只讲协商争议。" if clause.seq == 1 else "甲方逾期付款违约金未设上限。" if clause.seq == 2 else "设备名称和工作信息。"
        def factory(index, task_id, snapshot):
            def handler(request):
                clause = json.loads(json.loads(request.content)["messages"][1]["content"])["clause_text"]
                data = model_output(applicable=False, has_issue=False, risk_level="none", reason="只有争议处理背景，未分配违约责任。") if "协商" in clause else model_output(evidence_ids=[self.kid])
                return httpx.Response(200, json={"id": "mock", "object": "chat.completion", "created": 0, "model": "deepseek-chat",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})
            sdk = OpenAI(api_key="mock-not-real", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
            return RiskJudge(index, self.sf, LLMClient(self.sf, sdk_client=sdk, emit_metrics=lambda _: None))
        task = create_review(self.sf, self.cid, rules=["liability"])["task_id"]
        worker = ReviewWorker(self.sf, Path(self.directory.name) / "worker.lock", judge_factory=factory)
        self.assertEqual(worker.claim(), task)
        worker.execute(task)
        state = get_task(self.sf, task)
        self.assertEqual(state["status"], "completed")
        self.assertEqual((state["planned_model_items"], state["executed_items"], state["model_not_applicable_items"], state["keyword_not_applicable_items"]), (2, 2, 1, 10))
        self.assertEqual(state["progress"], 1)
        with self.sf() as session:
            snapshot = json.loads(session.get(ReviewTask, task).input_snapshot)
            first_id = next(c["id"] for c in snapshot["clauses"] if c["seq"] == 1)
            item = session.scalar(select(ReviewItem).where(ReviewItem.review_id == task, ReviewItem.clause_id == first_id))
            self.assertIsNone(item.risk_level)
            self.assertEqual(item.execution_status, "done")
        report = generate_report(self.sf, task)
        self.assertIn("模型判断本条不涉及本规则", report)
        self.assertIn("未达到适用关键词条件", report)
        self.assertIn("依据不足", report)
        worker.execute(task)
        with self.sf() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(LLMLog)), 2)

    def test_preview_dispute_clause_liability_returns_not_applicable(self):
        def handler(request):
            data = model_output(applicable=False, has_issue=False, risk_level="none", reason="仅约定法院和协商争议处理，不涉及违约责任。")
            return httpx.Response(200, json={"id": "mock", "object": "chat.completion", "created": 0, "model": "deepseek-chat",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(data)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
        sdk = OpenAI(api_key="mock-not-real", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        client = LLMClient(self.sf, sdk_client=sdk, emit_metrics=lambda _: None)
        app = FastAPI()
        app.include_router(router)
        app.state.risk_judge = RiskJudge(self.index, self.sf, client)
        try:
            with TestClient(app) as http:
                response = http.post("/judge/preview", json={"clause_text": "第十二条 争议由双方协商，协商不成提交法院。", "stance": "party_a", "rule_id": "liability"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["check_status"], "not_applicable")
            self.assertFalse(response.json()["applicable"])
        finally:
            client.close()
