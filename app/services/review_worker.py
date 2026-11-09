"""Single-process worker with per-item checkpoints and an OS-owned lock."""

import json
import os
from pathlib import Path
import time
from typing import Optional
import uuid

from sqlalchemy import select, text, update
from sqlalchemy.dialects.sqlite import insert

from app.models import KnowledgeChunk, LLMLog, ReviewItem, ReviewTask
from app.prompts.risk_judge import PROMPT_VERSION
from app.services.llm import LLMClient, MODEL, PersistentTaskBudget
from app.services.review_tasks import get_task, refresh_progress, utc_now
from app.services.risk_judge import RiskJudge, failed_result
from app.services.search import BM25Index, TOKENIZER_VERSION
from app.services.missing_judge import MissingJudge
from app.services.review_rules import RULES_VERSION
from app.prompts.missing_judge import PROMPT_VERSION as MISSING_PROMPT_VERSION


class WorkerAlreadyRunning(Exception):
    pass


class WorkerLock:
    def __init__(self, path: Path) -> None:
        self.path, self.handle = path, None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise WorkerAlreadyRunning("同一数据库已有 worker，未重置任何任务") from exc
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.handle is not None:
            # Closing the handle releases the OS lock, including after a kill.
            self.handle.close()


class ReviewWorker:
    def __init__(self, session_factory, lock_path: Path, judge_factory=None,
                 poll_interval: float = 1, unit_delay: float = 0) -> None:
        self.session_factory, self.lock_path = session_factory, lock_path
        self.judge_factory = judge_factory
        self.poll_interval, self.unit_delay = poll_interval, unit_delay

    def recover(self) -> None:
        with self.session_factory.begin() as session:
            running = list(session.execute(select(ReviewTask.id).where(ReviewTask.status == "running")).scalars())
            if running:
                session.execute(update(LLMLog).where(LLMLog.review_id.in_(running),
                    LLMLog.error_code == "CALL_IN_PROGRESS").values(error_code="INTERRUPTED_UNKNOWN_USAGE"))
                session.execute(update(ReviewTask).where(ReviewTask.id.in_(running)).values(
                    status="queued", stage="recovered", updated_at=utc_now()))
        print(json.dumps({"event": "worker_recovered", "task_count": len(running)}), flush=True)

    def claim(self) -> Optional[str]:
        with self.session_factory.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            task = session.execute(select(ReviewTask).where(ReviewTask.status == "queued").order_by(
                ReviewTask.created_at, ReviewTask.id).limit(1)).scalar_one_or_none()
            if task is None:
                return None
            changed = session.execute(update(ReviewTask).where(
                ReviewTask.id == task.id, ReviewTask.status == "queued").values(
                status="running", stage="planning", started_at=task.started_at or utc_now(), updated_at=utc_now()))
            return task.id if changed.rowcount == 1 else None

    def _prepare(self, task_id: str):
        with self.session_factory.begin() as session:
            task = session.get(ReviewTask, task_id)
            snapshot = json.loads(task.input_snapshot)
            if snapshot.get("missing_rules") and snapshot.get("missing_prompt_version") != MISSING_PROMPT_VERSION:
                raise ValueError("缺失检查提示词版本已变化")
            if (snapshot["prompt_version"] != PROMPT_VERSION or snapshot["tokenizer_version"] != TOKENIZER_VERSION
                    or snapshot["model"] != MODEL or snapshot.get("rules_version", RULES_VERSION) != RULES_VERSION):
                raise ValueError("任务配置版本已变化，请创建新的审查版本")
            for unit in snapshot["plan"]:
                session.execute(insert(ReviewItem).values(
                    id=uuid.uuid4().hex, review_id=task_id, item_key=unit["item_key"],
                    clause_id=unit["clause_id"], rule_id=unit["rule_id"], execution_status="pending",
                ).on_conflict_do_nothing(index_elements=["review_id", "item_key"]))
            clauses = {c["id"]: c for c in snapshot["clauses"]}
            for unit in snapshot.get("not_applicable", []):
                result = {"applicable": False, "has_issue": False, "applicability_source": "keyword",
                          "check_status": "not_applicable", "risk_level": None,
                          "description": "关键词命中数未达到规则适用条件，未调用模型。",
                          "clause_text": clauses[unit["clause_id"]]["content"],
                          "rule_id": unit["rule_id"], "item_key": unit["item_key"],
                          "matched_keywords": unit["matched_keywords"], "evidence": []}
                session.execute(insert(ReviewItem).values(
                    id=uuid.uuid4().hex, review_id=task_id, item_key=unit["item_key"], clause_id=unit["clause_id"],
                    rule_id=unit["rule_id"], execution_status="done", check_status="not_applicable",
                    machine_result=json.dumps(result, ensure_ascii=False),
                ).on_conflict_do_nothing(index_elements=["review_id", "item_key"]))
            refresh_progress(session, task)
            task.stage = "reviewing"
            return snapshot

    def execute(self, task_id: str) -> None:
        judge = None
        client = None
        try:
            snapshot = self._prepare(task_id)
            index = BM25Index([KnowledgeChunk(**row) for row in snapshot["knowledge"]])
            if self.judge_factory is not None:
                judge = self.judge_factory(index, task_id, snapshot)
            else:
                client = LLMClient(self.session_factory, budget=PersistentTaskBudget(
                    self.session_factory, task_id, snapshot["call_limit"]))
                judge = RiskJudge(index, self.session_factory, client)
            clauses = {clause["id"]: clause for clause in snapshot["clauses"]}
            missing = MissingJudge(judge.llm_client)
            missing_rules = {r["rule_id"]: r for r in snapshot.get("missing_rule_definitions", [])}
            for unit in snapshot["plan"]:
                with self.session_factory() as session:
                    item = session.execute(select(ReviewItem).where(
                        ReviewItem.review_id == task_id, ReviewItem.item_key == unit["item_key"])).scalar_one()
                    if item.execution_status == "done":
                        continue
                    item_id = item.id
                # No DB connection or write transaction is held during judging.
                clause = clauses.get(unit["clause_id"])
                try:
                    if unit.get("scope") == "contract":
                        result = missing.judge(snapshot["all_clauses"], snapshot["contract"]["stance"],
                                               unit["rule_id"], task_id, item_id, missing_rules[unit["rule_id"]])
                    else:
                        result = judge.judge_clause(clause["content"], snapshot["contract"]["stance"],
                                                    unit["rule_id"], review_id=task_id, item_id=item_id)
                    if result.get("check_status") not in {"supported", "insufficient", "failed", "no_issue_detected", "not_applicable"}:
                        raise ValueError("Invalid judge result status")
                except Exception:
                    result = failed_result("UNIT_EXECUTION_FAILED")
                result.update({"clause_text": clause["content"] if clause else "合同全部条款摘要清单",
                               "clause_id": unit["clause_id"],
                               "rule_id": unit["rule_id"], "item_key": unit["item_key"]})
                with self.session_factory.begin() as session:
                    item = session.get(ReviewItem, item_id)
                    if item.execution_status == "done":
                        continue
                    item.machine_result = json.dumps(result, ensure_ascii=False)
                    item.check_status = result["check_status"]
                    item.risk_level = None if result["check_status"] in {"no_issue_detected", "not_applicable"} else result.get("risk_level", "unknown")
                    item.execution_status = "done"
                    item.updated_at = utc_now()
                    if result["check_status"] == "failed":
                        session.execute(update(LLMLog).where(
                            LLMLog.item_id == item_id, LLMLog.error_code == "CALL_IN_PROGRESS"
                        ).values(error_code="UNKNOWN_USAGE_AFTER_UNIT_FAILURE"))
                    task = session.get(ReviewTask, task_id)
                    session.flush()
                    refresh_progress(session, task)
                print(json.dumps({"event": "unit_committed", "task_id": task_id,
                                  "item_key": unit["item_key"], "applicable": result.get("applicable"),
                                  "has_issue": result.get("has_issue"), "check_status": result["check_status"],
                                  "evidence_count": len(result.get("evidence", []))}), flush=True)
                if self.unit_delay:
                    time.sleep(self.unit_delay)
            with self.session_factory.begin() as session:
                task = session.get(ReviewTask, task_id)
                refresh_progress(session, task)
                task.status = "partial_failed" if task.failed_items else "completed"
                task.stage = "finished"
                task.finished_at = utc_now()
            print(json.dumps({"event": "task_finished", **get_task(self.session_factory, task_id)}, ensure_ascii=False), flush=True)
        except Exception:
            with self.session_factory.begin() as session:
                task = session.get(ReviewTask, task_id)
                refresh_progress(session, task)
                task.status, task.stage = "failed", "failed"
                task.error_code = "TASK_EXECUTION_FAILED"
                task.error_message = "任务快照、数据库或执行配置异常，请检查后创建新版本；已保存单元保留。"
                task.finished_at = utc_now()
            print(json.dumps({"event": "task_failed", "task_id": task_id}), flush=True)
        finally:
            if client is not None:
                client.close()
            elif judge is not None and hasattr(judge, "llm_client"):
                judge.llm_client.close()

    def run(self, once: bool = False) -> None:
        with WorkerLock(self.lock_path):
            self.recover()
            while True:
                task_id = self.claim()
                if task_id is not None:
                    self.execute(task_id)
                elif not once:
                    time.sleep(self.poll_interval)
                if once:
                    return
