from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from openai import AuthenticationError, OpenAI
from sqlalchemy import func, inspect, select, update
from sqlalchemy.orm import sessionmaker

from app.api.judge import router
from app.db import Base, create_db_engine
from app.migrations import allow_preview_llm_logs
from app.models import KnowledgeChunk, LLMLog
from app.prompts.risk_judge import build_messages
from app.services.chunker import SourceInfo
from app.services.knowledge import enable_source, import_law
from app.services.llm import (CallBudget, CallContext, LLMBudgetExceeded,
                              LLMClient, LLMConfigurationError, LLMLoggingError)
from app.services.risk_judge import RiskJudge
from app.services.search import build_search_index
from app.services.validator import OutputFormatError, parse_model_output
from tests.parse_helpers import ROOT


CLAUSE = "甲方逾期付款，每日按合同价款的千分之一支付违约金，未约定累计上限。"


def output(evidence_ids, **updates):
    result = {"applicable": True, "has_issue": True, "risk_level": "medium", "description": "我方违约金累计未设上限。",
              "reason": "我方付款责任可能持续累积，需协商并核对可调整的违约金约定。",
              "suggestion": "明确累计上限和计算基础。", "evidence_ids": evidence_ids,
              "support_note": "候选依据支持违约金调整问题，商业上限仍需协商。"}
    result.update(updates)
    return result


def response(content, tokens=100):
    return httpx.Response(200, json={
        "id": "chatcmpl-mock", "object": "chat.completion", "created": 0, "model": "deepseek-chat",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": tokens, "completion_tokens": 20, "total_tokens": tokens + 20},
    })


class JudgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_db_engine("sqlite:///" + (Path(self.directory.name) / "judge.db").as_posix())
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        for filename, source_id in (("civil_code_liability.txt", "liability"), ("civil_code_termination.txt", "termination")):
            with self.Session() as session:
                import_law(session, (ROOT / "samples/laws" / filename).read_text(encoding="utf-8"),
                           filename, SourceInfo(source_id, source_id))
            with self.Session() as session:
                enable_source(session, source_id)
        with self.Session() as session:
            self.index = build_search_index(session)
            self.evidence_id = session.execute(select(KnowledgeChunk.id).where(
                KnowledgeChunk.article_no == "第五百八十五条")).scalar_one()
        self.clients = []
        self.requests = []
        self.metrics = []
        self.sleeps = []

    def tearDown(self) -> None:
        for client in self.clients:
            client.close()
        self.engine.dispose()
        self.directory.cleanup()

    def client(self, handler, budget=None):
        def wrapped(request):
            body = json.loads(request.content)
            self.requests.append(body)
            self.assertEqual(str(request.url), "https://api.deepseek.com/chat/completions")
            self.assertEqual(body["model"], "deepseek-chat")
            self.assertEqual(body["temperature"], 0)
            self.assertEqual(body["response_format"], {"type": "json_object"})
            self.assertNotIn("tools", body)
            return handler(request)
        sdk = OpenAI(api_key="mock-only-not-a-real-key", base_url="https://api.deepseek.com", timeout=60,
                     max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(wrapped)))
        client = LLMClient(self.Session, sdk_client=sdk, budget=budget,
                           sleep=self.sleeps.append, emit_metrics=self.metrics.append)
        self.clients.append(client)
        return client

    def judge(self, client, clause=CLAUSE):
        return RiskJudge(self.index, self.Session, client).judge_clause(clause, "party_a", "liability")

    def test_supported_uses_database_quotes_and_logs_tokens(self) -> None:
        client = self.client(lambda request: response(json.dumps(output([self.evidence_id]), ensure_ascii=False)))
        result = self.judge(client)
        self.assertEqual(result["check_status"], "supported")
        self.assertEqual(result["risk_level"], "medium")
        self.assertEqual(result["evidence"][0]["article_no"], "第五百八十五条")
        with self.Session() as session:
            original = session.get(KnowledgeChunk, self.evidence_id)
            log = session.execute(select(LLMLog)).scalar_one()
            self.assertEqual(result["evidence"][0]["content"], original.content)
            self.assertEqual(result["evidence"][0]["source_locator"], original.source_locator)
            self.assertIsNone(log.review_id)
            self.assertEqual(log.prompt_tokens, 100)
            self.assertEqual(log.completion_tokens, 20)
            self.assertEqual(log.request_id, result["request_id"])
        self.assertEqual(result["usage"]["total_tokens"], 120)
        self.assertEqual(len(result["calls"]), 1)
        self.assertGreaterEqual(result["calls"][0]["duration_ms"], 0)

    def test_fake_id_is_dropped_and_no_conclusion_survives(self) -> None:
        client = self.client(lambda request: response(json.dumps(output(["k_fabricated"]))))
        result = self.judge(client)
        self.assertEqual((result["check_status"], result["risk_level"]), ("insufficient", "unknown"))
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["discarded_evidence_ids"], ["k_fabricated"])
        self.assertNotEqual(result["description"], "我方违约金累计未设上限。")

    def test_mixed_ids_keep_only_valid_candidate_and_deduplicate(self) -> None:
        client = self.client(lambda request: response(json.dumps(output(["fake", self.evidence_id, self.evidence_id]))))
        result = self.judge(client)
        self.assertEqual(result["check_status"], "supported")
        self.assertEqual([e["chunk_id"] for e in result["evidence"]], [self.evidence_id])
        self.assertEqual(result["discarded_evidence_ids"], ["fake"])

    def test_existing_but_not_candidate_id_is_rejected(self) -> None:
        candidates = self.index.search(CLAUSE + " 违约金 违约责任", 5)
        candidate_ids = {row["chunk_id"] for row in candidates}
        unrelated = next(document.chunk_id for document in self.index.documents if document.chunk_id not in candidate_ids)
        client = self.client(lambda request: response(json.dumps(output([unrelated]))))
        result = self.judge(client)
        self.assertEqual(result["check_status"], "insufficient")
        self.assertEqual(result["discarded_evidence_ids"], [unrelated])

    def test_disabled_or_changed_evidence_during_call_is_rejected(self) -> None:
        def handler(request):
            with self.Session.begin() as session:
                session.execute(update(KnowledgeChunk).where(KnowledgeChunk.id == self.evidence_id).values(enabled=0))
            return response(json.dumps(output([self.evidence_id])))
        result = self.judge(self.client(handler))
        self.assertEqual(result["check_status"], "insufficient")

    def test_no_candidates_short_circuits_without_key_or_model(self) -> None:
        client = LLMClient(self.Session, emit_metrics=self.metrics.append)
        self.clients.append(client)
        result = RiskJudge(self.index, self.Session, client).judge_clause("数据出境", "party_a", "data_export")
        self.assertEqual((result["check_status"], result["risk_level"]), ("insufficient", "unknown"))
        self.assertEqual(result["calls"], [])
        self.assertEqual(result["usage"]["total_tokens"], 0)

    def test_one_json_repair_and_all_attempt_usage(self) -> None:
        payloads = iter(["not JSON", json.dumps(output([self.evidence_id]))])
        result = self.judge(self.client(lambda request: response(next(payloads))))
        self.assertEqual(result["check_status"], "supported")
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(result["usage"]["prompt_tokens"], 200)
        self.assertEqual(result["usage"]["completion_tokens"], 40)
        with self.Session() as session:
            logs = session.execute(select(LLMLog).order_by(LLMLog.attempt_no)).scalars().all()
            self.assertEqual([log.error_code for log in logs], ["INVALID_MODEL_OUTPUT", None])

    def test_two_bad_outputs_end_failed(self) -> None:
        result = self.judge(self.client(lambda request: response('{"has_issue":true}')))
        self.assertEqual(result["check_status"], "failed")
        self.assertEqual(result["risk_level"], "unknown")
        self.assertEqual(result["error_code"], "INVALID_MODEL_OUTPUT")
        self.assertEqual(len(self.requests), 2)

    def test_no_issue_with_empty_evidence_is_not_forced_to_insufficient(self) -> None:
        payload = output([], has_issue=False, risk_level="none", description="本次未发现问题。", support_note="")
        result = self.judge(self.client(lambda request: response(json.dumps(payload))))
        self.assertEqual((result["check_status"], result["risk_level"]), ("no_issue_detected", "none"))

    def test_5xx_retries_twice_with_exponential_backoff_and_logs_unknown_usage(self) -> None:
        statuses = iter([500, 503, 200])
        def handler(request):
            status = next(statuses)
            return response(json.dumps(output([self.evidence_id]))) if status == 200 else httpx.Response(status, json={"error": {"message": "temporary"}})
        result = self.judge(self.client(handler))
        self.assertEqual(result["check_status"], "supported")
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.sleeps, [1.0, 2.0])
        self.assertEqual([c["error_code"] for c in result["calls"]], ["HTTP_500", "HTTP_503", None])
        self.assertFalse(result["usage"]["usage_complete"])
        self.assertIsNone(result["usage"]["prompt_tokens"])
        self.assertEqual(result["usage"]["known_prompt_tokens"], 100)

    def test_all_4xx_including_429_are_not_retried(self) -> None:
        for status in (400, 401, 403, 404, 429):
            with self.subTest(status=status):
                before = len(self.requests)
                client = self.client(lambda request, status=status: httpx.Response(status, json={"error": {"message": "private upstream detail"}}))
                result = self.judge(client)
                self.assertEqual(result["check_status"], "failed")
                self.assertEqual(len(self.requests) - before, 1)
                self.assertNotIn("private upstream detail", json.dumps(result))
                self.assertEqual(self.sleeps, [])

    def test_client_rethrows_authentication_error(self) -> None:
        client = self.client(lambda request: httpx.Response(401, json={"error": {"message": "unauthorized"}}))
        with self.assertRaises(AuthenticationError):
            client.complete(build_messages(CLAUSE, "party_a", "liability", []), CallContext(uuid.uuid4().hex))
        self.assertEqual(len(self.requests), 1)

    def test_timeout_maximum_three_attempts(self) -> None:
        def timeout(request):
            raise httpx.ReadTimeout("timed out", request=request)
        result = self.judge(self.client(timeout))
        self.assertEqual(result["check_status"], "failed")
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.sleeps, [1.0, 2.0])

    def test_budget_stops_before_another_network_attempt(self) -> None:
        budget = CallBudget(1)
        client = self.client(lambda request: httpx.Response(500, json={"error": {"message": "temporary"}}), budget)
        with self.assertRaises(LLMBudgetExceeded):
            client.complete([], CallContext(uuid.uuid4().hex))
        self.assertEqual(budget.used, 1)
        self.assertEqual(len(self.requests), 1)

    def test_missing_key_has_zero_attempts(self) -> None:
        client = LLMClient(self.Session, emit_metrics=self.metrics.append)
        self.clients.append(client)
        with patch.dict(os.environ, {}, clear=True), patch("app.services.llm.load_environment"):
            with self.assertRaises(LLMConfigurationError):
                client.complete([], CallContext(uuid.uuid4().hex))
        self.assertEqual(self.metrics, [])

    def test_logging_failure_does_not_repeat_billed_request(self) -> None:
        client = self.client(lambda request: response(json.dumps(output([self.evidence_id]))))
        class BrokenFactory:
            def begin(self):
                raise OSError("test log storage failure")
        client.session_factory = BrokenFactory()
        result = self.judge(client)
        self.assertEqual(result["error_code"], "LLM_LOGGING_FAILED")
        self.assertEqual(len(self.requests), 1)

    def test_api_request_validation_and_normal_result(self) -> None:
        client = self.client(lambda request: response(json.dumps(output([self.evidence_id]))))
        app = FastAPI()
        app.include_router(router)
        app.state.risk_judge = RiskJudge(self.index, self.Session, client)
        with TestClient(app) as http:
            result = http.post("/judge/preview", json={"clause_text": CLAUSE, "stance": "party_a", "rule_id": "liability"})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["check_status"], "supported")
            self.assertEqual(http.post("/judge/preview", json={"clause_text": CLAUSE, "stance": "other", "rule_id": "liability"}).status_code, 422)


class StrictFormatTests(unittest.TestCase):
    def test_strict_json_and_field_types(self) -> None:
        for content in ("```json\n{}\n```", "[]", '{"has_issue":true,"has_issue":false}',
                        json.dumps(output([], has_issue="true")),
                        json.dumps(output([], fabricated_quote="fake")),
                        json.dumps(output([], has_issue=False, risk_level="high"))):
            with self.subTest(content=content):
                with self.assertRaises(OutputFormatError):
                    parse_model_output(content)


class ConfigTests(unittest.TestCase):
    def test_project_dotenv_loads_without_overriding_existing_environment(self) -> None:
        from app.config import load_environment
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("DEEPSEEK_API_KEY=unit-test-placeholder\n", encoding="utf-8")
            with patch("app.config.PROJECT_ENV", path), patch.dict(os.environ, {}, clear=True):
                load_environment()
                self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "unit-test-placeholder")
            with patch("app.config.PROJECT_ENV", path), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "process-wins"}):
                load_environment()
                self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "process-wins")


class LogMigrationTests(unittest.TestCase):
    def test_existing_schema_migrates_without_losing_logs_or_foreign_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            with sqlite3.connect(str(path)) as connection:
                connection.executescript((ROOT / "tests/schema_v6.sql").read_text(encoding="utf-8"))
                connection.execute("INSERT INTO users(id,username,password_hash,role) VALUES ('u','u','x','admin')")
                connection.execute("INSERT INTO contracts(id,series_id,contract_version,title,filename,stored_path,file_hash,party_stance,parse_status,owner_id) VALUES ('c','s',1,'t','f','p','h','party_a','parsed','u')")
                connection.execute("INSERT INTO review_tasks(id,contract_id,review_version,status,input_snapshot,created_by) VALUES ('r','c',1,'completed','{}','u')")
                connection.execute("INSERT INTO llm_logs(id,request_id,review_id,purpose,provider,model,attempt_no,prompt_tokens) VALUES ('l','request','r','risk_judge','deepseek','deepseek-chat',1,123)")
            engine = create_db_engine("sqlite:///" + path.as_posix())
            try:
                allow_preview_llm_logs(engine)
                allow_preview_llm_logs(engine)
                self.assertEqual(len(inspect(engine).get_table_names()), 7)
                self.assertTrue(next(c for c in inspect(engine).get_columns("llm_logs") if c["name"] == "review_id")["nullable"])
                with engine.begin() as connection:
                    self.assertEqual(connection.exec_driver_sql("SELECT prompt_tokens FROM llm_logs WHERE id='l'").scalar_one(), 123)
                    connection.exec_driver_sql("INSERT INTO llm_logs(id,request_id,purpose,provider,model,attempt_no) VALUES ('preview','p','risk_judge','deepseek','deepseek-chat',1)")
                    self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_key_check").all(), [])
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
