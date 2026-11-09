"""Explicit one-shot applicability/missing acceptance; never repeat paid runs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app import db
from app.config import load_environment
from app.models import Clause, Contract, LLMLog, ReviewItem, ReviewTask
from app.services.chunker import SourceInfo
from app.services.knowledge import import_law, enable_source
from app.services.report import generate_report
from app.services.review_tasks import get_task
from tests.parse_helpers import ROOT, PreviewServer
from tests.review_helpers import seed_contract_a, start_worker, stop_process_tree


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    mode = "live" if args.live else "mock"
    result_path = ROOT / "work" / ("applicability-" + mode + "-results.json")
    if result_path.exists():
        raise RuntimeError("已有验收记录，不自动重建或重复付费")
    load_environment()
    database = ROOT / "law.db" if args.live else ROOT / "work/applicability-mock.db"
    engine = db.create_db_engine("sqlite:///" + database.resolve().as_posix())
    db.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    server, worker = PreviewServer(), None
    state = {"mode": mode}
    try:
        with Session() as session:
            if session.scalar(select(func.count()).select_from(ReviewTask).where(ReviewTask.status.in_(["queued", "running"]))):
                raise RuntimeError("已有活动任务，不能误执行其他付费任务")
        if args.live:
            prior = json.loads((ROOT / "work/review-acceptance-live-results.json").read_text(encoding="utf-8"))
            old_id = prior["contract_id"]
        else:
            old_id = seed_contract_a(Session)
            for filename, source_id in (("civil_code_liability.txt", "law1"), ("civil_code_termination.txt", "law2")):
                with Session() as session:
                    import_law(session, (ROOT / "samples/laws" / filename).read_text(encoding="utf-8"), filename, SourceInfo(source_id, source_id))
                with Session() as session:
                    enable_source(session, source_id)
        environment = os.environ.copy()
        environment["DATABASE_URL"] = "sqlite:///" + database.resolve().as_posix()
        command = subprocess.run([sys.executable, "-m", "app.cli", "reparse-contract", "--contract", old_id],
                                 cwd=str(ROOT), env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30)
        if command.returncode:
            raise RuntimeError(command.stderr)
        reparsed = json.loads(command.stdout)
        state["reparsed"] = reparsed
        cid = reparsed["contract_id"]
        server.start(database)
        status, preview = server.upload("contract_a.txt", (ROOT / "samples/contract_a.txt").read_bytes())
        assert status == 200 and preview["clause_count"] == 12
        assert preview["clauses"][0]["content"].startswith("第一条")
        assert "测试样本" not in preview["clauses"][0]["content"]
        with Session() as session:
            contract = session.get(Contract, cid)
            assert contract.preamble == preview["preamble"]
            assert contract.raw_text.startswith(contract.preamble)
            state["preamble"] = contract.preamble
            state["first_clause"] = session.execute(select(Clause.content).where(Clause.contract_id == cid).order_by(Clause.seq)).scalars().first()
            assert session.get(Contract, old_id).raw_text == contract.raw_text
        status, created = server.post_json("/contracts/{}/review".format(cid), {
            "max_clauses": 12, "rules": ["liability"],
            "missing_rules": ["dispute_resolution", "acceptance", "confidentiality"]})
        assert status == 202
        task_id = created["task_id"]
        state["task_id"] = task_id
        result_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        with Session() as session:
            snapshot = json.loads(session.get(ReviewTask, task_id).input_snapshot)
            clause_units = [u for u in snapshot["plan"] if u["scope"] == "clause"]
            state["old_clause_units"] = 12
            state["new_clause_units"] = len(clause_units)
            state["skipped_clause_rules"] = len(snapshot["not_applicable"])
            assert len(clause_units) == 3 and len(snapshot["not_applicable"]) == 9
        log = ROOT / "work" / ("applicability-worker-" + mode + ".log")
        worker = start_worker(database, log, mock=not args.live)
        assert worker.wait(timeout=300) == 0, "worker 未正常退出，保留任务及调用记录，不自动重跑"
        final = get_task(Session, task_id)
        state["final"] = final
        with Session() as session:
            items = session.execute(select(ReviewItem).where(ReviewItem.review_id == task_id)).scalars().all()
            logs = session.execute(select(LLMLog).where(LLMLog.review_id == task_id).order_by(LLMLog.created_at, LLMLog.id)).scalars().all()
            state["calls"] = [{"item_id": row.item_id, "purpose": row.purpose, "prompt_tokens": row.prompt_tokens,
                               "completion_tokens": row.completion_tokens, "duration_ms": row.duration_ms,
                               "error_code": row.error_code} for row in logs]
            state["missing"] = {}
            for item in items:
                if item.clause_id is None:
                    result = json.loads(item.machine_result)
                    count = sum(row.item_id == item.id for row in logs)
                    assert count == 1
                    state["missing"][item.rule_id] = {"calls": count, "result": result}
            clause_calls = sum(row.purpose == "risk_judge" for row in logs)
            state["actual_clause_calls"] = clause_calls
        assert len(state["calls"]) <= 15
        state["passed"] = final["status"] == "completed" and len(state["missing"]) == 3
        result_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        (ROOT / "work" / ("applicability-" + mode + "-report.md")).write_text(generate_report(Session, task_id), encoding="utf-8")
        print(json.dumps(state, ensure_ascii=False, indent=2))
        if not state["passed"]:
            raise AssertionError("验收未通过，已保留实际结果，不自动重新调用")
    finally:
        if worker is not None:
            stop_process_tree(worker)
        if hasattr(server, "process"):
            server.stop()
        engine.dispose()


if __name__ == "__main__":
    main()
