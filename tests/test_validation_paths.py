from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.api.judge import router as judge_router
from app.api.reviews import router as review_router, ReviewRequest
from app.db import Base, create_db_engine
from app.models import Clause, Contract, KnowledgeChunk, ReviewItem, ReviewTask
from app.services.auth import Principal, current_user
from app.services.chunker import SourceInfo
from app.services.knowledge import enable_source, import_law
from app.services.llm import LLMClient
from app.services.review_planner import build_check_plan
from app.services.review_tasks import create_review
from app.services.review_worker import ReviewWorker
from app.services.risk_judge import RiskJudge
from app.services.search import build_search_index
from app.services.validator import RiskOutput, validate_evidence
from tests.parse_helpers import ROOT
from tests.review_helpers import seed_contract_a

CLAUSE = "甲方逾期付款，每日按合同价款的千分之一支付违约金，未约定累计上限。"


class ValidationPathTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "paths.db"
        self.engine = create_db_engine("sqlite:///" + self.path.as_posix())
        Base.metadata.create_all(self.engine)
        self.sf = sessionmaker(bind=self.engine, autoflush=False)
        self.cid = seed_contract_a(self.sf)
        self.clients = []
        for filename, sid in (("civil_code_liability.txt", "law1"), ("civil_code_termination.txt", "law2")):
            with self.sf() as session:
                import_law(session, (ROOT / "samples/laws" / filename).read_text(encoding="utf-8"), filename, SourceInfo(sid, sid))
            with self.sf() as session:
                enable_source(session, sid)
        with self.sf() as session:
            self.index = build_search_index(session)
            self.kid = session.scalar(select(KnowledgeChunk.id).where(KnowledgeChunk.article_no == "第五百八十五条"))
            self.owner = session.get(Contract, self.cid).owner_id

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.engine.dispose()
        self.directory.cleanup()

    def client(self, ids):
        def handler(request):
            message = json.loads(request.content)["messages"][1]["content"]
            candidates = json.loads(message)["candidate_evidence"]
            self.assertIn(self.kid, [c["chunk_id"] for c in candidates])
            output = {"applicable": True, "has_issue": True, "risk_level": "medium", "description": "未设置违约金累计上限。",
                      "reason": "用于验证相同输入的两条路径。", "suggestion": "协商明确累计上限。",
                      "evidence_ids": ids, "support_note": "第585条支持违约金调整的核查。"}
            return httpx.Response(200, json={"id": "mock", "object": "chat.completion", "created": 0,
                "model": "deepseek-chat", "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(output, ensure_ascii=False)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})
        sdk = OpenAI(api_key="mock-not-real", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        client = LLMClient(self.sf, sdk_client=sdk, emit_metrics=lambda _: None)
        self.clients.append(client)
        return client

    def test_same_preview_and_worker_inputs_keep_valid_585_and_explain_empty_or_fake_ids(self):
        self.diagnostic_examples = []
        with self.sf.begin() as session:
            for clause in session.execute(select(Clause).where(Clause.contract_id == self.cid)).scalars():
                clause.content = CLAUSE if clause.seq == 1 else "设备说明与其他信息。"
        for label, ids, expected in (("valid", [self.kid], "supported"), ("empty", [], "insufficient"), ("fake", ["k_fake"], "insufficient")):
            with self.subTest(case=label):
                application = FastAPI()
                application.include_router(judge_router)
                application.state.risk_judge = RiskJudge(self.index, self.sf, self.client(ids))
                stream = StringIO()
                with redirect_stdout(stream), TestClient(application) as http:
                    preview = http.post("/judge/preview", json={"clause_text": CLAUSE, "stance": "party_a", "rule_id": "liability"}).json()
                task = create_review(self.sf, self.cid, rules=["liability"])["task_id"]
                def factory(index, task_id, snapshot):
                    return RiskJudge(index, self.sf, self.client(ids))
                worker = ReviewWorker(self.sf, Path(self.directory.name) / "worker.lock", judge_factory=factory)
                claimed = worker.claim()
                self.assertEqual(claimed, task)
                with redirect_stdout(stream):
                    worker.execute(task)
                with self.sf() as session:
                    item = session.scalar(select(ReviewItem).where(ReviewItem.review_id == task, ReviewItem.check_status != "not_applicable"))
                    result = json.loads(item.machine_result)
                self.assertEqual(preview["check_status"], expected)
                self.assertEqual(result["check_status"], expected)
                for field in ("candidate_ids", "model_evidence_ids", "validation_trace", "input_digest"):
                    self.assertEqual(preview[field], result[field])
                if label == "valid":
                    self.assertEqual([s["chunk_id"] for s in result["evidence"]], [self.kid])
                elif label == "empty":
                    self.assertEqual(result["discarded_evidence_ids"], [])
                    self.assertEqual(result["validation_trace"]["steps"][-1]["reason"], "MODEL_RETURNED_NO_EVIDENCE_IDS")
                else:
                    self.assertEqual(result["validation_trace"]["steps"][1]["reason"], "NOT_IN_CANDIDATES")
                self.assertIn('"event": "evidence_validation"', stream.getvalue())
                self.assertIn('"path": "worker"', stream.getvalue())
                self.assertIn('"path": "preview"', stream.getvalue())
                self.diagnostic_examples.append({"case": label, "source": "SDK_MockTransport_not_real_model",
                    "events": [json.loads(line) for line in stream.getvalue().splitlines()
                               if line.startswith("{") and json.loads(line).get("event") == "evidence_validation"]})

    def test_each_db_rejection_is_identified_without_weakening_validation(self):
        candidates = self.index.search(CLAUSE + " 违约金 违约责任", 5)
        output = RiskOutput(applicable=True, has_issue=True, risk_level="medium", description="测试问题", reason="校验测试", suggestion="人工核对", evidence_ids=[self.kid], support_note="测试")
        for field, value, code in (("enabled", 0, "SOURCE_DISABLED"), ("verified", 0, "SOURCE_UNVERIFIED"),
                                   ("superseded", 1, "SOURCE_SUPERSEDED"), ("content", "修改后的正文", "SNAPSHOT_MISMATCH"),
                                   ("article_no", "其他条号", "SNAPSHOT_MISMATCH"), ("source_file", "changed.txt", "SNAPSHOT_MISMATCH")):
            with self.subTest(field=field), self.sf() as session:
                row = session.get(KnowledgeChunk, self.kid)
                original = getattr(row, field)
                setattr(row, field, value)
                session.flush()
                result = validate_evidence(output, candidates, session)
                self.assertEqual(result["check_status"], "insufficient")
                rejection = next(step for step in result["validation_trace"]["steps"] if step.get("decision") == "rejected")
                self.assertIn(code, rejection.get("reasons", [rejection.get("reason")]))
                if code == "SNAPSHOT_MISMATCH": self.assertEqual(rejection["changed_fields"], [field])
                session.rollback()
        with self.sf() as session:
            session.delete(session.get(KnowledgeChunk, self.kid))
            session.flush()
            result = validate_evidence(output, candidates, session)
            self.assertIn("CHUNK_NOT_FOUND", result["validation_trace"]["steps"][2]["reasons"])
            session.rollback()

    def test_api_default_and_null_review_all_twelve_but_explicit_five_stays_limited(self):
        self.assertIsNone(ReviewRequest().max_clauses)
        application = FastAPI()
        application.include_router(review_router)
        application.dependency_overrides[current_user] = lambda: Principal(self.owner, "test-owner", "reviewer", 1)
        with patch("app.api.reviews.db.SessionLocal", self.sf), TestClient(application) as client:
            response = client.post("/contracts/" + self.cid + "/review", json={"rules": ["liability"]})
        self.assertEqual(response.status_code, 202)
        with self.sf() as session:
            snapshot = json.loads(session.get(ReviewTask, response.json()["task_id"]).input_snapshot)
        self.assertIsNone(snapshot["max_clauses"])
        self.assertEqual(len(snapshot["clauses"]), 12)
        ids = {u["clause_id"] for u in snapshot["plan"]}
        self.assertTrue({c["id"] for c in snapshot["all_clauses"] if c["seq"] in (3, 10, 11)}.issubset(ids))
        data = [{"id": str(i), "seq": i, "content": "违约付款争议"} for i in range(1, 251)]
        self.assertEqual(len(build_check_plan(data, ["liability"])["units"]), 250)
        self.assertEqual(len(build_check_plan(data, ["liability"], 5)["units"]), 5)

    def test_full_review_budget_covers_more_than_fifteen_units(self):
        with self.sf.begin() as session:
            for clause in session.execute(select(Clause).where(Clause.contract_id == self.cid)).scalars():
                clause.content = "违约付款争议"
        task = create_review(self.sf, self.cid)["task_id"]
        with self.sf() as session:
            snapshot = json.loads(session.get(ReviewTask, task).input_snapshot)
        self.assertEqual(len(snapshot["plan"]), 36)
        self.assertGreaterEqual(snapshot["call_limit"], 36 * 6)
