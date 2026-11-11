"""Import legacy JSON once, keep every source file; no model calls.

python -m scripts.migrate_agent_runs --directory /data/agent-runs
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from app import db
from app.models import User
from app.services.agent_store import AgentStore


def migrate(directory,session_factory=None):
    sf=session_factory or db.SessionLocal;store=AgentStore(session_factory=sf)
    report={'scanned':0,'imported':0,'skipped':0,'failed':0,'errors':[]}
    root=Path(directory)
    if not root.is_dir():raise ValueError('历史目录不存在：'+str(root))
    for path in sorted(root.glob('*.json')):
        report['scanned']+=1
        try:
            run=json.loads(path.read_text(encoding='utf-8-sig'))
            if run.get('run_id')!=path.stem:raise ValueError('文件名与run_id不一致')
            with sf() as s:
                if s.get(User,run.get('user_id')) is None:raise ValueError('创建用户不存在，不伪造用户')
            # Missing timestamps have no trustworthy ordering; flag for repair.
            if not run.get('created_at'):raise ValueError('缺少created_at')
            from app.services.agent import costs
            run['cost']=costs(sf,run['run_id'])
            report['imported' if store.save(run,insert_only=True) else 'skipped']+=1
        except Exception as exc:
            report['failed']+=1;report['errors'].append({'file':path.name,'error':type(exc).__name__+': '+str(exc)[:200]})
    # Historical handoffs may have obtained a task after their JSON froze.
    # Fill only missing relational links, never replace imported run content.
    from sqlalchemy import select,update,func
    from app.models import Ticket,AgentStep
    with sf.begin() as s:
        tickets=list(s.scalars(select(Ticket).where(Ticket.agent_run_id.isnot(None),Ticket.review_task_id.isnot(None))))
        for ticket in tickets:
            s.execute(update(AgentStep).where(AgentStep.run_id==ticket.agent_run_id,AgentStep.tool=='review_contract',
                AgentStep.review_task_id.is_(None),func.json_extract(AgentStep.args,'$.contract_id')==ticket.source_contract_id
            ).values(review_task_id=ticket.review_task_id))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',default=os.environ.get('AGENT_RUN_DIR','/data/agent-runs'))
    args=parser.parse_args();db.init_db()
    report=migrate(args.directory);print(json.dumps(report,ensure_ascii=False,indent=2))
    return 1 if report['failed'] else 0


if __name__=='__main__':raise SystemExit(main())
