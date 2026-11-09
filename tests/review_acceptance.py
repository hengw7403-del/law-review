"""Limited CLI/HTTP/worker acceptance; --live allows one capped real task."""

import argparse
import json
from pathlib import Path
import sqlite3
import time

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app import db
from app.config import load_environment
from app.models import LLMLog, ReviewItem, ReviewTask
from app.services.chunker import SourceInfo
from app.services.knowledge import enable_source, import_law
from app.services.review_tasks import get_task
from tests.parse_helpers import ROOT, PreviewServer
from tests.review_helpers import seed_contract_a, start_worker, stop_process_tree, wait_progress


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    mode = "live" if args.live else "mock"
    work = ROOT / "work"
    work.mkdir(exist_ok=True)
    result_path = work / ("review-acceptance-" + mode + "-results.json")
    if result_path.exists():
        raise RuntimeError("已有该模式验收记录；不自动创建新付费任务或清空记录")
    load_environment()
    database = ROOT / "law.db" if args.live else work / "review-acceptance-mock.db"
    engine = db.create_db_engine("sqlite:///" + database.resolve().as_posix())
    db.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    server = PreviewServer()
    processes = []
    state = {"mode": mode, "database": str(database), "states": []}
    try:
        if not args.live:
            for filename, source_id in (("civil_code_liability.txt", "law1"), ("civil_code_termination.txt", "law2")):
                with Session() as session:
                    import_law(session, (ROOT / "samples/laws" / filename).read_text(encoding="utf-8"), filename, SourceInfo(source_id, source_id))
                with Session() as session:
                    enable_source(session, source_id)
        else:
            with Session() as session:
                if session.scalar(select(func.count()).select_from(ReviewTask).where(
                        ReviewTask.status.in_(["queued", "running"]))):
                    raise RuntimeError("已有活动任务，验收不会付费执行其他排队任务")
            backup = work / "backups/law-before-worker.db"
            backup.parent.mkdir(exist_ok=True)
            if not backup.exists():
                source, target = sqlite3.connect(str(database)), sqlite3.connect(str(backup))
                try:
                    source.backup(target)
                finally:
                    source.close()
                    target.close()
        contract_id = seed_contract_a(Session)
        state["contract_id"] = contract_id
        server.start(database)
        options = {"max_clauses": 5, "rules": ["liability"]}
        status, created = server.post_json("/contracts/{}/review".format(contract_id), options)
        assert status == 202
        task_id = created["task_id"]
        state["task_id"] = task_id
        state["states"].append(get_task(Session, task_id))
        assert server.post_json("/contracts/{}/review".format(contract_id), options)[1]["task_id"] == task_id
        state["duplicate_task_id"] = task_id
        result_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        first_log = work / ("review-worker-" + mode + "-before-kill.log")
        worker = start_worker(database, first_log, mock=not args.live, delay=2)
        processes.append(worker)
        state["states"].append(wait_progress(Session, task_id, 2, worker, timeout=240))
        with Session() as session:
            completed = list(session.execute(select(ReviewItem.id).where(
                ReviewItem.review_id == task_id, ReviewItem.execution_status == "done")).scalars())
            before = session.scalar(select(func.count()).select_from(LLMLog).where(LLMLog.item_id.in_(completed)))
        stop_process_tree(worker)
        assert get_task(Session, task_id)["status"] == "running"
        assert server.post_json("/contracts/{}/review".format(contract_id), options)[1]["task_id"] == task_id
        second_log = work / ("review-worker-" + mode + "-after-restart.log")
        restarted = start_worker(database, second_log, mock=not args.live)
        processes.append(restarted)
        deadline = time.monotonic() + 400
        while restarted.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        assert restarted.poll() == 0, "worker 没有正常完成；保留检查点，禁止自动重建任务"
        final = get_task(Session, task_id)
        state["states"].append(final)
        assert final["status"] == "completed", final
        assert (final["total_items"], final["done_items"], final["failed_items"]) == (5, 5, 0)
        with Session() as session:
            after = session.scalar(select(func.count()).select_from(LLMLog).where(LLMLog.item_id.in_(completed)))
            items = session.execute(select(ReviewItem).where(ReviewItem.review_id == task_id)).scalars().all()
            assert len(items) == 5 and all(item.machine_result for item in items)
            logs = session.execute(select(LLMLog).where(LLMLog.review_id == task_id).order_by(LLMLog.created_at, LLMLog.id)).scalars().all()
            state["calls"] = [{"item_id": row.item_id, "request_id": row.request_id, "attempt_no": row.attempt_no,
                               "prompt_tokens": row.prompt_tokens, "completion_tokens": row.completion_tokens,
                               "duration_ms": row.duration_ms, "error_code": row.error_code} for row in logs]
        assert before == after
        assert final["metrics"]["model_calls"] <= 20
        state["completed_item_calls_before"] = before
        state["completed_item_calls_after"] = after
        with server.opener.open(server.url + "/tasks/" + task_id, timeout=10) as response:
            assert json.load(response)["status"] == "completed"
        with server.opener.open(server.url + "/reviews/" + task_id + "/report", timeout=10) as response:
            report = response.read().decode("utf-8")
        for no in ("第一条", "第二条", "第三条", "第四条", "第五条"):
            assert no in report
        (work / ("review-" + mode + "-report.md")).write_text(report, encoding="utf-8")
        state["passed"] = True
        result_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(state, ensure_ascii=False, indent=2))
    finally:
        for process in processes:
            stop_process_tree(process)
        if hasattr(server, "process"):
            server.stop()
        engine.dispose()


if __name__ == "__main__":
    main()
