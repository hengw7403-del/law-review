"""Readiness every 500 ms while writing or resuming a real task (no LLM calls).

Run with the same database path as the API. Use --task-id to watch a worker;
otherwise a second process performs short writes for --seconds (default 30).
The report includes failures/timeouts as failures, never as successful probes.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
from pathlib import Path
import sqlite3
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def connection(path):
    db = sqlite3.connect(str(path), timeout=30)
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    return db


def write_loop(path, seconds):
    db = connection(path)
    try:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            # Touch an existing row without altering business values. Sleep only
            # after commit; this is intentionally not a 30-second transaction.
            with db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE users SET active=active WHERE id=(SELECT id FROM users LIMIT 1)")
            time.sleep(1)
    finally:
        db.close()


def probe(url, token=None):
    started = time.monotonic()
    try:
        headers = {"Authorization": "Bearer " + token} if token else {}
        with urlopen(Request(url, headers=headers), timeout=35) as response:
            response.read()
            status, error = response.status, None
    except HTTPError as exc:
        status, error = exc.code, "HTTP_{}".format(exc.code)
    except Exception as exc:
        status, error = None, type(exc).__name__
    return {"status": status, "error": error,
            "latency_ms": round((time.monotonic() - started) * 1000, 2)}


def watch(args):
    report_path = Path(args.output)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    db = connection(args.database)
    token = Path(args.token_file).read_text(encoding="utf-8").strip() if args.token_file else None
    protected = []
    baseline_logs = db.execute("SELECT COUNT(*) FROM llm_logs").fetchone()[0]
    baseline_ids = [r[0] for r in db.execute("SELECT id FROM llm_logs")]
    if args.task_id:
        protected = [r[0] for r in db.execute(
            "SELECT id FROM review_items WHERE review_id=? AND execution_status='done'", (args.task_id,))]
    report = {"mode": "task" if args.task_id else "simulation", "task_id": args.task_id,
              "baseline_done": len(protected), "baseline_log_count": baseline_logs,
              "baseline_log_ids": baseline_ids, "protected_item_ids": protected,
              "health": [], "tickets": [], "violations": [], "finished": False}
    writer = None
    if not args.task_id:
        writer = multiprocessing.Process(target=write_loop, args=(args.database, args.seconds))
        writer.start()
    started = last_progress = next_probe = time.monotonic()
    previous_done = len(protected)
    pending = []
    pool = ThreadPoolExecutor(max_workers=80)
    try:
        while True:
            now = time.monotonic()
            if now >= next_probe:
                pending.append(("health", pool.submit(probe, args.base_url.rstrip('/') + '/health/ready')))
                if token:
                    pending.append(("tickets", pool.submit(probe, args.base_url.rstrip('/') + '/tickets', token)))
                next_probe += .5
            for kind, future in pending[:]:
                if future.done():
                    row = future.result()
                    report[kind].append(row)
                    pending.remove((kind, future))
                    if row['status'] != 200:
                        report['violations'].append(kind + ':' + str(row['status']) + ':' + str(row['error']))
            if args.task_id:
                task = db.execute("SELECT status,done_items,total_items FROM review_tasks WHERE id=?", (args.task_id,)).fetchone()
                if task is None:
                    raise ValueError("Task not found")
                report['task'] = dict(zip(('status', 'done_items', 'total_items'), task))
                done = db.execute("SELECT COUNT(*) FROM review_items WHERE review_id=? AND execution_status='done'", (args.task_id,)).fetchone()[0]
                if done != previous_done:
                    previous_done, last_progress = done, now
                    print(json.dumps({'event': 'progress', 'done': done, 'total': task[2]}), flush=True)
                if protected:
                    placeholders = ','.join('?' for _ in protected)
                    new_calls = db.execute("SELECT item_id FROM llm_logs WHERE item_id IN (" + placeholders + ") AND id NOT IN (" + ','.join('?' for _ in baseline_ids) + ")", protected + baseline_ids).fetchall() if baseline_ids else db.execute("SELECT item_id FROM llm_logs WHERE item_id IN (" + placeholders + ")", protected).fetchall()
                    if new_calls:
                        report['violations'].append('repeated_completed_item_call')
                if now - last_progress > 180:
                    report['violations'].append('stalled_180_seconds')
                terminal = task[0] in {'completed', 'partial_failed', 'failed', 'cancelled'}
                if terminal:
                    report['finished'] = True
                    if task[0] != 'completed':
                        report['violations'].append('task_not_completed:' + task[0])
            else:
                terminal = now - started >= args.seconds
                if terminal:
                    report['finished'] = True
            report['503_count'] = sum(r['status'] == 503 for r in report['health'])
            report['new_model_calls'] = db.execute("SELECT COUNT(*) FROM llm_logs").fetchone()[0] - baseline_logs
            report['elapsed_seconds'] = round(now - started, 2)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            if terminal or report['violations']:
                break
            if now - started > args.max_seconds:
                report['violations'].append('maximum_duration_exceeded')
                break
            time.sleep(.05)
    except Exception as exc:
        report['violations'].append(type(exc).__name__ + ':' + str(exc))
    finally:
        for kind, future in pending:
            row = future.result()
            report[kind].append(row)
            if row['status'] != 200:
                report['violations'].append(kind + ':' + str(row['status']))
        pool.shutdown()
        if writer:
            writer.join(timeout=35)
            if writer.is_alive():
                writer.terminate()
                writer.join()
            if writer.exitcode != 0:
                report['violations'].append('writer_failed')
        report['503_count'] = sum(r['status'] == 503 for r in report['health'])
        report['passed'] = report['finished'] and not report['violations']
        report['max_health_latency_ms'] = max((r['latency_ms'] for r in report['health']), default=None)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        db.close()
    print(json.dumps({k: v for k, v in report.items() if k not in {'health', 'tickets', 'baseline_log_ids', 'protected_item_ids'}}, ensure_ascii=False), flush=True)
    return 0 if report['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--task-id')
    parser.add_argument('--token-file')
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--max-seconds', type=float, default=1800)
    parser.add_argument('--output', default='work/db-concurrency-report.json')
    return watch(parser.parse_args())


if __name__ == '__main__':
    raise SystemExit(main())
