"""Explicit paid acceptance: formal database, real worker, 45 attempts maximum."""
import json
from pathlib import Path
from sqlalchemy import select, func
from app import db
from app.config import load_environment
from app.models import Contract, ReviewTask, LLMLog, ReviewItem, Clause
from app.services.review_tasks import create_review, get_task
from app.services.review_rules import CLAUSE_RULES
from app.services.review_worker import ReviewWorker, WorkerLock

ROOT = Path(__file__).resolve().parents[1]
MARKER = ROOT / 'work/ten-rules-acceptance-task.json'
OUTPUT_PREFIX = 'ten-rules'


def export(task_id):
    state = get_task(db.SessionLocal, task_id)
    with db.SessionLocal() as session:
        task = session.get(ReviewTask, task_id)
        snapshot = json.loads(task.input_snapshot)
        clauses = {c['id']: c for c in snapshot['clauses']}
        rows = []
        for item in session.scalars(select(ReviewItem).where(ReviewItem.review_id == task_id)):
            result = json.loads(item.machine_result or '{}')
            clause = clauses.get(item.clause_id, {})
            rows.append({'seq': clause.get('seq'), 'clause': clause.get('original_no'),
                         'rule': item.rule_id, 'check_status': item.check_status,
                         'risk_level': item.risk_level or 'none',
                         'reason': result.get('reason') or result.get('description', ''),
                         'description': result.get('description'), 'suggestion': result.get('suggestion'),
                         'evidence': result.get('evidence', []),
                         'model_output': result.get('model_output'),
                         'merged_into': result.get('merged_into'),
                         'merged_from': result.get('merged_from', []),
                         'original_check_status': result.get('original_check_status'),
                         'candidate_ids': result.get('candidate_ids', []),
                         'validation_trace': result.get('validation_trace'),
                         'applicable': result.get('applicable')})
    rows.sort(key=lambda r: (r['seq'] or 0, list(CLAUSE_RULES).index(r['rule'])))
    counts = {status: sum(r['check_status'] == status for r in rows) for status in
              ['supported','insufficient','no_issue_detected','not_applicable','failed','merged']}
    result = {'state': state, 'stats': counts, 'items': rows}
    (ROOT/'work'/ (OUTPUT_PREFIX+'-results.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    lines = ['# 坑合同10规则真实验收', '', 'task_id: '+task_id, '',
             json.dumps(state['metrics'],ensure_ascii=False), '', json.dumps(counts,ensure_ascii=False), '',
             '| 条款 | 规则 | check_status | risk_level | 理由 |', '|---|---|---|---|---|']
    for row in rows:
        reason = row['reason'].replace('\n',' ').replace('|','／')
        lines.append('| {} | {} | {} | {} | {} |'.format(row['clause'],row['rule'],row['check_status'],row['risk_level'],reason))
    (ROOT/'work'/ (OUTPUT_PREFIX+'-results.md')).write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'task_id':task_id,'status':state['status'],'stats':counts,'metrics':state['metrics']},ensure_ascii=False),flush=True)


def main():
    load_environment()
    assert Path(db.engine.url.database).resolve() == ROOT/'law.db', 'Must use formal law.db'
    db.init_db()
    worker = ReviewWorker(db.SessionLocal, (ROOT/'law.db').with_suffix('.worker.lock'))
    with WorkerLock(worker.lock_path):
        if MARKER.exists():
            task_id = json.loads(MARKER.read_text(encoding='utf-8'))['task_id']
        else:
            with db.SessionLocal() as session:
                assert not session.scalar(select(ReviewTask.id).where(ReviewTask.status.in_(['queued','running'])))
                contract = session.get(Contract, 'c0220fbfcb4449f1ae7ffc802de04a75')
                assert contract.filename == 'contract_c_bad.txt' and contract.party_stance == 'party_b'
                before = session.scalar(select(func.count()).select_from(LLMLog))
            task_id = create_review(db.SessionLocal, contract.id, None, list(CLAUSE_RULES), [])['task_id']
            with db.SessionLocal.begin() as session:
                task = session.get(ReviewTask, task_id)
                snapshot = json.loads(task.input_snapshot)
                assert len(snapshot['plan']) <= 45
                snapshot['call_limit'] = 45
                task.input_snapshot = json.dumps(snapshot,ensure_ascii=False)
            MARKER.write_text(json.dumps({'task_id':task_id,'baseline_logs':before}),encoding='utf-8')
        worker.recover()
        state = get_task(db.SessionLocal, task_id)
        if state['status'] == 'queued':
            assert worker.claim() == task_id
            worker.execute(task_id)
        export(task_id)


if __name__ == '__main__':
    main()
