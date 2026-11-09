"""One paid A task, <=15 persisted attempts, print all 36 audit units."""
import argparse
import json
import sqlite3
from pathlib import Path

from sqlalchemy import func, select

from app import db
from app.models import Clause, Contract, LLMLog, ReviewItem, ReviewTask
from app.services.report import generate_report
from app.services.review_tasks import create_review, get_task
from tests.parse_helpers import ROOT
from tests.review_helpers import start_worker, stop_process_tree


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    path = ROOT / "work/model-applicability-live-results.json"
    if path.exists():
        raise RuntimeError("已有验收状态，不自动创建第二个付费任务")
    db.init_db()
    previous = json.loads((ROOT / "work/model-applicability-pause.json").read_text(encoding="utf-8"))
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ReviewTask).where(ReviewTask.status.in_(["queued", "running"]))) == 0
        before = session.scalar(select(func.count()).select_from(LLMLog))
        assert before == previous["llm_logs_before"], "有其他调用发生，先核对预算"
        baseline_rows = session.execute(select(ReviewItem.check_status).where(ReviewItem.review_id == previous["baseline_task_id"])).scalars().all()
        baseline_counts = {status: baseline_rows.count(status) for status in set(baseline_rows)}
        contract = session.get(Contract, previous["contract_id"])
        assert contract.filename == "contract_a.txt" or len(session.execute(select(Clause.id).where(Clause.contract_id == contract.id)).all()) == 12
    backup = ROOT / "work/backups/law-before-model-applicability.db"
    if backup.exists():
        raise RuntimeError("已有本轮备份，不能盲目重新验收")
    with sqlite3.connect(str(ROOT / "law.db")) as source, sqlite3.connect(str(backup)) as target:
        source.backup(target)
    state = {"mode": "live", "baseline_task_id": previous["baseline_task_id"], "baseline_counts": baseline_counts,
             "contract_id": previous["contract_id"], "llm_logs_before": before, "call_limit": 15}
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    created = create_review(db.SessionLocal, state["contract_id"], rules=["liability", "payment", "dispute_resolution"])
    assert not created["deduplicated"]
    state["task_id"] = created["task_id"]
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    with db.SessionLocal.begin() as session:
        task = session.get(ReviewTask, state["task_id"])
        snapshot = json.loads(task.input_snapshot)
        assert len(snapshot["clauses"]) == 12 and len(snapshot["plan"]) == 11 and len(snapshot["not_applicable"]) == 25
        snapshot["call_limit"] = 15
        task.input_snapshot = json.dumps(snapshot, ensure_ascii=False)
    worker = None
    try:
        worker = start_worker(ROOT / "law.db", ROOT / "work/model-applicability-worker-live.log", mock=False)
        assert worker.wait(timeout=900) == 0
        state["final"] = get_task(db.SessionLocal, state["task_id"])
        with db.SessionLocal() as session:
            clauses = {c.id: c for c in session.execute(select(Clause).where(Clause.contract_id == state["contract_id"])).scalars()}
            rows = session.execute(select(ReviewItem).where(ReviewItem.review_id == state["task_id"])).scalars().all()
            logs = session.execute(select(LLMLog).where(LLMLog.review_id == state["task_id"]).order_by(LLMLog.created_at)).scalars().all()
            assert len(logs) <= 15
            state["units"] = []
            for item in sorted(rows, key=lambda item: (clauses[item.clause_id].seq, item.rule_id)):
                result = json.loads(item.machine_result)
                unit = {"seq": clauses[item.clause_id].seq, "article_no": clauses[item.clause_id].original_no,
                        "rule_id": item.rule_id, "applicable": result.get("applicable"), "has_issue": result.get("has_issue"),
                        "check_status": item.check_status, "evidence_count": len(result.get("evidence", [])),
                        "source": result.get("applicability_source"), "result": result}
                state["units"].append(unit)
                print(json.dumps({k: v for k, v in unit.items() if k != "result"}, ensure_ascii=False), flush=True)
            state["counts"] = {s: sum(item.check_status == s for item in rows) for s in set(item.check_status for item in rows)}
            state["calls"] = [{"item_id": log.item_id, "prompt_tokens": log.prompt_tokens, "completion_tokens": log.completion_tokens,
                               "duration_ms": log.duration_ms, "error_code": log.error_code} for log in logs]
            after = session.scalar(select(func.count()).select_from(LLMLog))
            assert after - before == len(logs) and after - before <= 15
        state["llm_logs_after"] = after
        state["insufficient_reduced"] = state["counts"].get("insufficient", 0) < baseline_counts.get("insufficient", 0)
        state["checks"] = {
            "all_36_units_saved": len(state["units"]) == 36,
            "clause_10_liability_applicable": next(u["applicable"] for u in state["units"] if u["seq"] == 10 and u["rule_id"] == "liability") is True,
            "clause_11_liability_applicable": next(u["applicable"] for u in state["units"] if u["seq"] == 11 and u["rule_id"] == "liability") is True,
            "clause_12_liability_not_applicable": next(u["applicable"] for u in state["units"] if u["seq"] == 12 and u["rule_id"] == "liability") is False,
            "within_15_attempts": len(state["calls"]) <= 15,
        }
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        (ROOT / "work/model-applicability-live-report.md").write_text(generate_report(db.SessionLocal, state["task_id"]), encoding="utf-8")
        print(json.dumps({"final": state["final"], "counts": state["counts"], "checks": state["checks"], "insufficient_reduced": state["insufficient_reduced"]}, ensure_ascii=False), flush=True)
        assert all(state["checks"].values())
        assert state["final"]["status"] == "completed"
    finally:
        if worker: stop_process_tree(worker)
        db.engine.dispose()


if __name__ == "__main__":
    main()
