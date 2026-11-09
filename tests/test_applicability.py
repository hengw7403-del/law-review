import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import httpx
from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db import Base, create_db_engine
from app.migrations import add_applicability_and_preamble, allow_preview_llm_logs
from app.models import Clause, Contract, LLMLog, ReviewItem, ReviewTask
from app.prompts.missing_judge import build_messages
from app.services.chunker import SourceInfo
from app.services.clause_splitter import split_document
from app.services.contract_reparse import reparse_contract
from app.services.knowledge import enable_source, import_law
from app.services.llm import LLMClient
from app.services.missing_judge import MissingJudge, MissingOutput, validate_missing
from app.services.parser import parse_document
from app.services.report import generate_report
from app.services.review_planner import build_check_plan
from app.services.review_rules import MISSING_RULES
from app.services.review_tasks import create_review, get_task
from tests.parse_helpers import ROOT
from tests.review_helpers import seed_contract_a, start_worker, stop_process_tree

ORIGINAL_MISSING_RULES = ["dispute_resolution", "acceptance", "confidentiality"]


def sample_clauses():
    path = ROOT / "samples/contract_a.txt"
    text, mapping = parse_document(path.name, path.read_bytes())
    document = split_document(text, mapping)
    return text, document, [{"id": str(c["seq"]), **c} for c in document["clauses"]]


class ApplicabilityTests(unittest.TestCase):
    def test_sample_liability_covers_delay_and_breach_but_skips_unrelated_clauses(self):
        _, _, clauses = sample_clauses()
        result = build_check_plan(clauses, ["liability"], 12)
        self.assertEqual([u["clause_id"] for u in result["units"]], ["3", "10", "11"])
        skipped = {u["clause_id"] for u in result["not_applicable"]}
        self.assertEqual(len(skipped), 9)
        self.assertTrue({"1", "6", "7", "12"}.issubset(skipped))

    def test_liability_keyword_variants_are_applicable(self):
        for word in ["违约", "违反", "不履行", "未履行", "未按期", "未按时", "延迟", "迟延", "逾期", "超时", "赔偿", "损失", "责任", "罚"]:
            with self.subTest(word=word):
                result = build_check_plan([{"id": "1", "seq": 1, "content": word}], ["liability"])
                self.assertEqual(len(result["units"]), 1)

    def test_distinct_hits_minimum_and_no_model_unit_for_skip(self):
        rule = {"rule_id": "liability", "name": "test", "scope": "clause",
                "applicable_when": {"keywords": ["违约", "赔偿"], "min_hits": 2}}
        result = build_check_plan([{"id": "1", "seq": 1, "content": "违约违约违约"},
                                   {"id": "2", "seq": 2, "content": "违约赔偿"}], [rule], 12)
        self.assertEqual([u["clause_id"] for u in result["units"]], ["2"])
        self.assertEqual([u["clause_id"] for u in result["not_applicable"]], ["1"])

    def test_preamble_keeps_full_text_offsets(self):
        text, document, clauses = sample_clauses()
        self.assertEqual(len(clauses), 12)
        self.assertIn("示例星桥", document["preamble"])
        self.assertTrue(clauses[0]["content"].startswith("第一条"))
        self.assertNotIn("测试样本", clauses[0]["content"])
        self.assertTrue(text.startswith(document["preamble"]))
        for clause in clauses:
            self.assertEqual(text[clause["char_start"]:clause["char_end"]], clause["content"])

    def test_missing_prompt_contains_only_outline_and_eighty_char_prefix(self):
        _, _, clauses = sample_clauses()
        clauses[0]["content"] = "字" * 80 + "SECRET_AFTER_80"
        messages = build_messages(clauses, "party_a", MISSING_RULES["acceptance"])
        data = json.loads(messages[1]["content"])
        self.assertEqual(len(data["clauses"]), 12)
        self.assertTrue(all(len(c["snippet"]) <= 80 for c in data["clauses"]))
        self.assertNotIn("SECRET_AFTER_80", messages[1]["content"])

    def test_false_absence_from_truncation_is_downgraded(self):
        clauses = [{"id": "1", "seq": 1, "original_no": "第一条", "content": "字" * 100 + "双方承担保密义务，不得披露资料。"}]
        raw = MissingOutput(assessment="missing", missing_items=[{"name": "保密", "importance": "保护资料。"}],
                            covered_clause_ids=[], reason="摘要未写保密。")
        result = validate_missing(raw, clauses, "confidentiality")
        self.assertEqual((result["check_status"], result["risk_level"]), ("insufficient", "unknown"))
        self.assertEqual(result["missing_items"], [])

    def test_invalid_outline_id_cannot_establish_presence(self):
        _, _, clauses = sample_clauses()
        raw = MissingOutput(assessment="present", missing_items=[], covered_clause_ids=["fabricated"], reason="存在。")
        self.assertEqual(validate_missing(raw, clauses, "acceptance")["assessment"], "uncertain")


class ApplicabilityDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name).resolve() / "new.db"
        self.engine = create_db_engine("sqlite:///" + self.path.as_posix())
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        self.cid = seed_contract_a(self.Session)
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            stop_process_tree(process)
        self.engine.dispose()
        self.directory.cleanup()

    def run_worker(self):
        log = Path(self.directory.name) / "worker.log"
        process = start_worker(self.path, log)
        self.processes.append(process)
        self.assertEqual(process.wait(timeout=30), 0, log.read_text(encoding="utf-8"))

    def test_all_skipped_is_completed_not_checked_and_no_logs(self):
        with self.Session.begin() as session:
            for row in session.execute(select(Clause).where(Clause.contract_id == self.cid)).scalars():
                row.content = "设备信息与服务说明。"
        task = create_review(self.Session, self.cid, 12, ["liability"])["task_id"]
        self.run_worker()
        state = get_task(self.Session, task)
        self.assertEqual((state["status"], state["done_items"], state["failed_items"], state["not_applicable_items"]),
                         ("completed", 0, 0, 12))
        self.assertEqual(state["metrics"]["model_calls"], 0)
        self.assertEqual(state["progress"], 1)
        self.assertIn("不适用", generate_report(self.Session, task))

    def test_missing_each_once_uses_all_clauses_even_when_clause_limit_is_one(self):
        task = create_review(self.Session, self.cid, 1, ["liability"], ORIGINAL_MISSING_RULES)["task_id"]
        with self.Session() as session:
            snapshot = json.loads(session.get(ReviewTask, task).input_snapshot)
            self.assertEqual(len(snapshot["all_clauses"]), 12)
            self.assertEqual(len(snapshot["plan"]), 3)
            self.assertEqual(snapshot["call_limit"], 15)
        self.run_worker()
        state = get_task(self.Session, task)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["metrics"]["model_calls"], 3)
        self.assertEqual(state["not_applicable_items"], 1)
        with self.Session() as session:
            items = session.execute(select(ReviewItem).where(ReviewItem.review_id == task, ReviewItem.clause_id.is_(None))).scalars().all()
            self.assertEqual({i.item_key for i in items}, {"contract:" + r for r in ORIGINAL_MISSING_RULES})
            for item in items:
                logs = session.execute(select(LLMLog).where(LLMLog.item_id == item.id)).scalars().all()
                self.assertEqual(len(logs), 1)
                self.assertEqual(logs[0].purpose, "missing_judge")
        self.assertIn("## 合同级缺失", generate_report(self.Session, task))

    def test_reparse_creates_new_version_and_preserves_old_snapshot(self):
        with self.Session.begin() as session:
            old = session.get(Contract, self.cid)
            old.preamble = None
            first = session.execute(select(Clause).where(Clause.contract_id == self.cid).order_by(Clause.seq)).scalars().first()
            first.content = old.raw_text[:first.char_end]
            old_content = first.content
        task = create_review(self.Session, self.cid, 12, ["liability"])["task_id"]
        with self.Session() as session:
            old_snapshot = session.get(ReviewTask, task).input_snapshot
        result = reparse_contract(self.Session, self.cid)
        with self.Session() as session:
            new = session.get(Contract, result["contract_id"])
            self.assertEqual(new.previous_version_id, self.cid)
            self.assertEqual(new.contract_version, 2)
            self.assertIn("测试样本", new.preamble)
            first = session.execute(select(Clause).where(Clause.contract_id == new.id).order_by(Clause.seq)).scalars().first()
            self.assertTrue(first.content.startswith("第一条"))
            self.assertEqual(session.get(ReviewTask, task).input_snapshot, old_snapshot)
            old_first = session.execute(select(Clause).where(Clause.contract_id == self.cid).order_by(Clause.seq)).scalars().first()
            self.assertEqual(old_first.content, old_content)

    def test_genuinely_absent_sections_produce_three_missing_findings(self):
        # Separate mock fixture: original contract A already has these sections.
        with self.Session.begin() as session:
            for row in session.execute(select(Clause).where(Clause.contract_id == self.cid)).scalars():
                row.content = "设备配置与接口说明，交付清单另附。"
        task = create_review(self.Session, self.cid, 12, [], ORIGINAL_MISSING_RULES)["task_id"]
        self.run_worker()
        state = get_task(self.Session, task)
        self.assertEqual((state["status"], state["metrics"]["model_calls"]), ("completed", 3))
        with self.Session() as session:
            items = session.execute(select(ReviewItem).where(ReviewItem.review_id == task)).scalars().all()
            self.assertEqual(len(items), 3)
            for item in items:
                result = json.loads(item.machine_result)
                self.assertIsNone(item.clause_id)
                self.assertEqual(result["assessment"], "missing")
                self.assertEqual(len(result["missing_items"]), 1)
                self.assertTrue(result["missing_items"][0]["importance"])

    def test_missing_error_does_not_retry_http_or_format(self):
        for status in (500, 200):
            requests = []
            def handler(request):
                requests.append(request)
                if status == 500:
                    return httpx.Response(500, json={"error": {"message": "mock"}})
                return httpx.Response(200, json={"id": "mock", "object": "chat.completion", "created": 0,
                    "model": "deepseek-chat", "choices": [{"index": 0, "message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
            sdk = OpenAI(api_key="mock-not-real", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
            client = LLMClient(self.Session, sdk_client=sdk, emit_metrics=lambda _: None)
            try:
                _, _, clauses = sample_clauses()
                result = MissingJudge(client).judge(clauses, "party_a", "acceptance", None, None)
                self.assertEqual(result["check_status"], "failed")
                self.assertEqual(len(requests), 1)
            finally:
                client.close()


class ApplicabilityMigrationTests(unittest.TestCase):
    def test_new_enum_and_preamble_preserve_dependent_log_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.db"
            with sqlite3.connect(str(path)) as c:
                c.executescript((ROOT / "tests/schema_v6.sql").read_text(encoding="utf-8"))
                c.execute("INSERT INTO users(id,username,password_hash,role) VALUES ('u','u','x','reviewer')")
                c.execute("INSERT INTO contracts(id,series_id,contract_version,title,filename,stored_path,file_hash,party_stance,parse_status,owner_id) VALUES ('c','s',1,'t','f','p','h','party_a','parsed','u')")
                c.execute("INSERT INTO clauses(id,contract_id,seq,content) VALUES ('cl','c',1,'text')")
                c.execute("INSERT INTO review_tasks(id,contract_id,review_version,status,input_snapshot,created_by) VALUES ('r','c',1,'completed','{}','u')")
                c.execute("INSERT INTO review_items(id,review_id,item_key,clause_id,rule_id,execution_status,check_status,machine_result) VALUES ('i','r','clause:cl:liability','cl','liability','done','no_issue_detected','{}')")
                c.execute("INSERT INTO llm_logs(id,request_id,review_id,item_id,purpose,provider,model,attempt_no) VALUES ('log','req','r','i','risk_judge','deepseek','model',1)")
            engine = create_db_engine("sqlite:///" + path.as_posix())
            try:
                allow_preview_llm_logs(engine)
                add_applicability_and_preamble(engine)
                add_applicability_and_preamble(engine)
                with engine.begin() as c:
                    self.assertEqual(c.exec_driver_sql("SELECT item_id FROM llm_logs").scalar_one(), "i")
                    c.exec_driver_sql("UPDATE review_items SET check_status='not_applicable' WHERE id='i'")
                    self.assertEqual(c.exec_driver_sql("PRAGMA foreign_key_check").all(), [])
                    self.assertEqual(c.exec_driver_sql("PRAGMA foreign_keys").scalar_one(), 1)
                    self.assertEqual(c.exec_driver_sql("SELECT count(*) FROM sqlite_master WHERE type='table'").scalar_one(), 7)
            finally:
                engine.dispose()
