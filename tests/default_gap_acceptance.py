"""Explicit one-shot two-rule acceptance; no automatic paid reruns."""
import argparse
import json
from pathlib import Path
import sqlite3

from sqlalchemy import func, select

from app import db
from app.models import LLMLog, ReviewItem, ReviewTask
from app.services.review_tasks import get_task
from tests.parse_helpers import ROOT, PreviewServer
from tests.review_helpers import start_worker, stop_process_tree


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    path = ROOT / "work/default-gap-live-results.json"
    if path.exists():
        raise RuntimeError("已有付费验收记录，不自动重跑")
    db.init_db()
    with db.SessionLocal() as session:
        if session.scalar(select(func.count()).select_from(ReviewTask).where(ReviewTask.status.in_(["queued", "running"]))):
            raise RuntimeError("有活动任务，不能执行其他付费任务")
    backup = ROOT / "work/backups/law-before-default-gap.db"
    if backup.exists():
        raise RuntimeError("已有本轮备份，请核对记录，不能自动重建任务")
    with sqlite3.connect(str(ROOT / "law.db")) as source, sqlite3.connect(str(backup)) as target:
        source.backup(target)
    previous = json.loads((ROOT / "work/applicability-live-results.json").read_text(encoding="utf-8"))
    cid = previous["reparsed"]["contract_id"]
    server, worker = PreviewServer(), None
    try:
        server.start(ROOT / "law.db")
        status, created = server.post_json("/contracts/{}/review".format(cid), {
            "max_clauses": 12, "rules": [], "missing_rules": ["payment_default", "delivery_default"]})
        assert status == 202 and not created["deduplicated"]
        task_id = created["task_id"]
        state = {"mode": "live", "task_id": task_id, "contract_id": cid}
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        with db.SessionLocal.begin() as session:
            task = session.get(ReviewTask, task_id)
            snapshot = json.loads(task.input_snapshot)
            assert len(snapshot["plan"]) == 2 and not snapshot["not_applicable"]
            snapshot["call_limit"] = 3
            task.input_snapshot = json.dumps(snapshot, ensure_ascii=False)
        worker = start_worker(ROOT / "law.db", ROOT / "work/default-gap-worker-live.log", mock=False)
        assert worker.wait(timeout=180) == 0
        with server.opener.open(server.url + "/tasks/" + task_id, timeout=10) as response:
            state["final"] = json.load(response)
        with db.SessionLocal() as session:
            items = session.execute(select(ReviewItem).where(ReviewItem.review_id == task_id)).scalars().all()
            logs = session.execute(select(LLMLog).where(LLMLog.review_id == task_id).order_by(LLMLog.created_at)).scalars().all()
            state["items"] = []
            for item in items:
                assert item.clause_id is None and item.item_key == "contract:" + item.rule_id
                count = sum(log.item_id == item.id for log in logs)
                assert count == 1
                state["items"].append({"rule_id": item.rule_id, "clause_id": item.clause_id,
                                       "calls": count, "result": json.loads(item.machine_result)})
            state["calls"] = [{"purpose": log.purpose, "prompt_tokens": log.prompt_tokens,
                               "completion_tokens": log.completion_tokens, "duration_ms": log.duration_ms,
                               "error_code": log.error_code} for log in logs]
            assert len(logs) <= 3
        with server.opener.open(server.url + "/reviews/" + task_id + "/report", timeout=10) as response:
            report = response.read().decode("utf-8")
        assert "## 合同级缺失" in report and "缺了会怎样（影响）" in report
        (ROOT / "work/default-gap-live-report.md").write_text(report, encoding="utf-8")
        state["execution_passed"] = state["final"]["status"] == "completed"
        state["payment_expected_true"] = next(i["result"].get("has_gap") for i in state["items"] if i["rule_id"] == "payment_default") is True
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(state, ensure_ascii=False, indent=2))
        assert state["execution_passed"]
    finally:
        if worker:
            stop_process_tree(worker)
        if hasattr(server, "process"):
            server.stop()
        db.engine.dispose()


if __name__ == "__main__":
    main()
