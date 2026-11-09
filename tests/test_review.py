from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.api.reviews import router
from app.db import Base, create_db_engine
from app.models import Clause, LLMLog, ReviewItem, ReviewTask
from app.services.chunker import SourceInfo
from app.services.knowledge import enable_source, import_law
from app.services.report import generate_report
from app.services.review_planner import build_plan
from app.services.review_tasks import create_review, get_task
from app.services.review_worker import ReviewWorker
from tests.parse_helpers import ROOT
from tests.review_helpers import seed_contract_a, start_worker, stop_process_tree, wait_progress
from app.services.auth import current_user, Principal
from app.models import Contract


class PlannerTests(unittest.TestCase):
    def test_parent_only_cross_product_deduplicates_rules_and_limits(self):
        clauses = [{"id": str(i), "seq": i, "content": "违约付款"} for i in range(1, 13)]
        clauses.append({"id": "child", "seq": 0, "parent_clause_id": "1"})
        plan = build_plan(clauses, ["liability", "payment", "liability"], 5)
        self.assertEqual(len(plan), 10)
        self.assertEqual(plan[0]["item_key"], "clause:1:liability")
        self.assertNotIn("child", [unit["clause_id"] for unit in plan])
        with self.assertRaises(ValueError):
            build_plan(clauses, ["liability"], 201)
        with self.assertRaises(ValueError):
            build_plan(clauses, ["unknown"])


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name).resolve() / "review.db"
        self.engine = create_db_engine("sqlite:///" + self.path.as_posix())
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        self.contract_id = seed_contract_a(self.Session)
        # Lifecycle tests intentionally use five applicable clauses. Actual A
        # keyword filtering has separate tests and is not modified on disk.
        with self.Session.begin() as session:
            for clause in session.execute(select(Clause).where(Clause.contract_id == self.contract_id)).scalars():
                clause.content += "\n本条涉及违约责任及损失。"
        for filename, source_id in (("civil_code_liability.txt", "law1"), ("civil_code_termination.txt", "law2")):
            with self.Session() as session:
                import_law(session, (ROOT / "samples/laws" / filename).read_text(encoding="utf-8"), filename, SourceInfo(source_id, source_id))
            with self.Session() as session:
                enable_source(session, source_id)
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            stop_process_tree(process)
        self.engine.dispose()
        self.directory.cleanup()

    def task(self, rules=None):
        return create_review(self.Session, self.contract_id, 5, rules or ["liability"])["task_id"]

    def worker(self, delay=0, fail_seq=None):
        log = Path(self.directory.name) / ("worker-{}.log".format(len(self.processes)))
        process = start_worker(self.path, log, delay=delay, fail_seq=fail_seq)
        self.processes.append(process)
        return process, log

    def test_create_and_concurrent_duplicate_returns_one_task(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: create_review(self.Session, self.contract_id, 5, ["liability"]), range(3)))
        self.assertEqual(len({result["task_id"] for result in results}), 1)
        state = get_task(self.Session, results[0]["task_id"])
        self.assertEqual(state["status"], "queued")
        self.assertEqual((state["total_items"], state["pending_items"]), (5, 5))
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ReviewTask)), 1)

    def test_worker_persists_each_item_and_report_contains_all_five(self):
        task_id = self.task()
        process, log = self.worker()
        self.assertEqual(process.wait(timeout=30), 0, log.read_text(encoding="utf-8"))
        state = get_task(self.Session, task_id)
        self.assertEqual((state["status"], state["done_items"], state["failed_items"]), ("completed", 5, 0))
        self.assertEqual(state["metrics"]["model_calls"], 5)
        self.assertEqual(state["metrics"]["total_tokens"], 600)
        with self.Session() as session:
            items = session.execute(select(ReviewItem).where(ReviewItem.review_id == task_id)).scalars().all()
            self.assertEqual(len(items), 5)
            self.assertTrue(all(item.machine_result for item in items))
            self.assertTrue(all(json.loads(item.machine_result)["clause_text"] for item in items))
        report = generate_report(self.Session, task_id)
        for number in ("第一条", "第二条", "第三条", "第四条", "第五条"):
            self.assertIn(number, report)
        self.assertIn("前 5 / 12", report)

    def test_kill_restart_skips_saved_units_and_lock_blocks_second_worker(self):
        task_id = self.task()
        process, _ = self.worker(delay=4)
        state = wait_progress(self.Session, task_id, 2, process)
        self.assertEqual(state["status"], "running")
        with self.Session() as session:
            completed_ids = list(session.execute(select(ReviewItem.id).where(
                ReviewItem.review_id == task_id, ReviewItem.execution_status == "done")).scalars())
            before = session.scalar(select(func.count()).select_from(LLMLog).where(LLMLog.item_id.in_(completed_ids)))
        second, log = self.worker()
        self.assertEqual(second.wait(timeout=10), 2, log.read_text(encoding="utf-8"))
        self.assertEqual(get_task(self.Session, task_id)["status"], "running")
        stop_process_tree(process)
        restarted, log = self.worker()
        self.assertEqual(restarted.wait(timeout=30), 0, log.read_text(encoding="utf-8"))
        state = get_task(self.Session, task_id)
        self.assertEqual((state["status"], state["done_items"]), ("completed", 5))
        self.assertEqual(state["metrics"]["model_calls"], 5)
        with self.Session() as session:
            after = session.scalar(select(func.count()).select_from(LLMLog).where(LLMLog.item_id.in_(completed_ids)))
            self.assertEqual(before, after)
            self.assertEqual(session.scalar(select(func.count()).select_from(ReviewItem).where(ReviewItem.review_id == task_id)), 5)

    def test_single_unit_failure_does_not_stop_following_units(self):
        task_id = self.task()
        process, log = self.worker(fail_seq=2)
        self.assertEqual(process.wait(timeout=30), 0, log.read_text(encoding="utf-8"))
        state = get_task(self.Session, task_id)
        self.assertEqual((state["status"], state["done_items"], state["failed_items"]), ("partial_failed", 4, 1))
        self.assertEqual(state["processed_items"], 5)
        self.assertEqual(state["metrics"]["model_calls"], 5)
        self.assertFalse(state["metrics"]["usage_complete"])

    def test_persistent_budget_survives_restart_and_limits_actual_attempts(self):
        task_id = self.task()
        with self.Session.begin() as session:
            task = session.get(ReviewTask, task_id)
            snapshot = json.loads(task.input_snapshot)
            snapshot["call_limit"] = 2
            task.input_snapshot = json.dumps(snapshot, ensure_ascii=False)
        process, log = self.worker()
        self.assertEqual(process.wait(timeout=30), 0, log.read_text(encoding="utf-8"))
        state = get_task(self.Session, task_id)
        self.assertEqual(state["metrics"]["model_calls"], 2)
        self.assertEqual((state["done_items"], state["failed_items"]), (2, 3))

    def test_snapshot_and_task_versions_are_preserved(self):
        task_id = self.task()
        with self.Session.begin() as session:
            first = session.execute(select(Clause).where(Clause.contract_id == self.contract_id).order_by(Clause.seq)).scalars().first()
            first.content = "模拟后来修改，不能影响本次原文"
        process, log = self.worker()
        self.assertEqual(process.wait(timeout=30), 0, log.read_text(encoding="utf-8"))
        self.assertNotIn("模拟后来修改", generate_report(self.Session, task_id))
        second_id = self.task()
        self.assertNotEqual(second_id, task_id)
        with self.Session() as session:
            self.assertEqual(session.get(ReviewTask, second_id).review_version, 2)

    def test_interrupted_inflight_attempt_is_preserved_and_counted_after_recovery(self):
        task_id = self.task()
        worker = ReviewWorker(self.Session, self.path.with_suffix(".worker.lock"))
        self.assertEqual(worker.claim(), task_id)
        worker._prepare(task_id)
        with self.Session.begin() as session:
            first = session.execute(select(ReviewItem).where(ReviewItem.review_id == task_id).order_by(ReviewItem.item_key)).scalars().first()
            session.add(LLMLog(id="interrupted", request_id="interrupted", review_id=task_id, item_id=first.id,
                               purpose="risk_judge", provider="deepseek", model="deepseek-chat",
                               attempt_no=1, error_code="CALL_IN_PROGRESS"))
        process, log = self.worker()
        self.assertEqual(process.wait(timeout=30), 0, log.read_text(encoding="utf-8"))
        state = get_task(self.Session, task_id)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["metrics"]["model_calls"], 6)
        self.assertFalse(state["metrics"]["usage_complete"])
        with self.Session() as session:
            self.assertEqual(session.get(LLMLog, "interrupted").error_code, "INTERRUPTED_UNKNOWN_USAGE")

    def test_task_api_and_markdown(self):
        application = FastAPI()
        application.include_router(router)
        with self.Session() as session:
            owner = session.get(Contract, self.contract_id).owner_id
        application.dependency_overrides[current_user] = lambda: Principal(owner, "test-owner", "reviewer", 1)
        with patch("app.api.reviews.db.SessionLocal", self.Session), TestClient(application) as client:
            payload = {"max_clauses": 5, "rules": ["liability"]}
            created = client.post("/contracts/{}/review".format(self.contract_id), json=payload)
            self.assertEqual(created.status_code, 202)
            task_id = created.json()["task_id"]
            self.assertEqual(client.post("/contracts/{}/review".format(self.contract_id), json=payload).json()["task_id"], task_id)
            self.assertEqual(client.get("/tasks/" + task_id).json()["status"], "queued")
            self.assertEqual(client.post("/contracts/{}/review".format(self.contract_id), json={"max_clauses": 201}).status_code, 422)
            self.assertEqual(client.get("/tasks/missing").status_code, 404)
            process, log = self.worker()
            self.assertEqual(process.wait(timeout=30), 0, log.read_text(encoding="utf-8"))
            report = client.get("/reviews/{}/report".format(task_id))
            self.assertEqual(report.status_code, 200)
            self.assertIn("text/markdown", report.headers["content-type"])
            self.assertIn("第五条", report.text)


if __name__ == "__main__":
    unittest.main()
