"""Compare frozen pre-fix merge with clause merge on disposable SQLite files.

120 items, 12 clauses x 10 rules. Publish ten results then merge once, as
requested. Only merge+flush is timed/counted, not fixture insertion/model IO.
Reports real SQL UPDATE rowcounts separately from ORM dirty rows. No LLM.
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker
from app.db import Base, create_db_engine
from app.models import User, Contract, Clause, ReviewTask, ReviewItem
from app.services.risk_merge import MERGE_VERSION, signature, merge_clause_items, merge_task_items
from app.services.review_rules import CLAUSE_RULES


def legacy_merge_task_items(session, task_id):
    """Frozen pre-fix implementation, intentionally not a production fallback."""
    rows = list(session.scalars(select(ReviewItem).where(
        ReviewItem.review_id == task_id, ReviewItem.clause_id.is_not(None),
        ReviewItem.execution_status == 'done', ReviewItem.check_status.in_(['supported', 'merged']))))
    groups = {}
    for row in rows:
        result = json.loads(row.machine_result)
        result['check_status'] = 'supported'
        for key in ('merged_into', 'merge_reason', 'merge_version', 'merged_from'):
            result.pop(key, None)
        row.check_status = 'supported'
        key = signature(result)
        if key is not None:
            groups.setdefault((row.clause_id, key), []).append((row, result))
        row.machine_result = json.dumps(result, ensure_ascii=False)
    for entries in groups.values():
        if len(entries) < 2:
            continue
        entries.sort(key=lambda pair: ({'high':0,'medium':1,'low':2}.get(pair[0].risk_level,3),
                                       0 if pair[0].rule_id == 'termination' else 1, pair[0].item_key))
        winner, winning_result = entries[0]
        winning_result['merged_from'] = [row.item_key for row, _ in entries[1:]]
        winner.machine_result = json.dumps(winning_result, ensure_ascii=False)
        for row, result in entries[1:]:
            result.update(check_status='merged', original_check_status='supported',
                          merged_into=winner.item_key, merge_version=MERGE_VERSION,
                          merge_reason='同条款同一风险点，保留最高等级；同级按稳定优先级保留。')
            row.check_status = 'merged'
            row.machine_result = json.dumps(result, ensure_ascii=False)


def seed(engine, payload):
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False)
    with sf.begin() as s:
        s.add(User(id='u',username='perf-disabled',role='reviewer',password_hash='!',active=0));s.flush()
        s.add(Contract(id='c',series_id='c',contract_version=1,title='merge fixture',filename='fixture.txt',
                       stored_path='unused',file_hash='fixture',party_stance='party_b',parse_status='parsed',owner_id='u'));s.flush()
        for number in range(12):
            s.add(Clause(id='c'+str(number),contract_id='c',seq=number+1,content='单方解除条款',char_start=number*10,char_end=number*10+8,location_json='{}'))
        s.flush()
        s.add(ReviewTask(id='t',contract_id='c',review_version=1,status='running',total_items=120,input_snapshot='{}',created_by='u'));s.flush()
        for number in range(12):
            for rule in CLAUSE_RULES:
                key='clause:c{}:{}'.format(number,rule)
                s.add(ReviewItem(id=key,item_key=key,review_id='t',clause_id='c'+str(number),rule_id=rule))
    return sf


def run(path, mode, payload):
    engine = create_db_engine('sqlite:///' + path.as_posix())
    sf = seed(engine,payload)
    counts = {'active':False,'updates':0}
    def count(conn,cursor,statement,parameters,context,executemany):
        if counts['active'] and statement.lstrip().upper().startswith('UPDATE REVIEW_ITEMS '):
            counts['updates'] += cursor.rowcount
    event.listen(engine,'after_cursor_execute',count)
    samples=[]
    try:
        for number in range(12):
            with sf.begin() as s:
                for row in s.scalars(select(ReviewItem).where(ReviewItem.review_id=='t',ReviewItem.clause_id=='c'+str(number))):
                    row.execution_status='done';row.check_status='supported'
                    row.risk_level='high' if row.rule_id=='termination' else 'medium'
                    row.machine_result=json.dumps(payload,ensure_ascii=False)
                s.flush()  # Result writes are identical and not merge UPDATEs.
                counts.update(active=True,updates=0)
                start=time.perf_counter()
                if mode=='before':legacy_merge_task_items(s,'t')
                else:merge_clause_items(s,'t','c'+str(number))
                dirty=len(s.dirty)
                s.flush()
                elapsed=(time.perf_counter()-start)*1000
                counts['active']=False
                samples.append({'after_units':(number+1)*10,'merge_ms':round(elapsed,4),
                                'dirty_rows':dirty,'updated_rows':counts['updates']})
        # Final correction is included in after's total time, rather than hidden.
        final_ms=0;final_updates=0
        if mode=='after':
            with sf.begin() as s:
                counts.update(active=True,updates=0);start=time.perf_counter()
                merge_task_items(s,'t');s.flush()
                final_ms=(time.perf_counter()-start)*1000;final_updates=counts['updates'];counts['active']=False
        with sf() as s:
            result={row.item_key:(row.check_status,row.risk_level,json.loads(row.machine_result))
                    for row in s.scalars(select(ReviewItem).where(ReviewItem.review_id=='t'))}
        return {'samples':samples,'total_ms':sum(v['merge_ms'] for v in samples)+final_ms,
                'final_correction_ms':final_ms,'final_correction_updates':final_updates,
                'total_updates':sum(v['updated_rows'] for v in samples)+final_updates},result
    finally:
        engine.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds',type=int,default=5)
    parser.add_argument('--context-repeat',type=int,default=60,help='Retained raw result context size; reported explicitly')
    parser.add_argument('--output',default='work/merge-perf-report.json')
    args=parser.parse_args()
    if args.rounds<1 or args.context_repeat<0:parser.error('invalid benchmark size')
    description='甲方可随时单方解除，乙方无权单方解除，解除权不对等。'
    payload={'check_status':'supported','description':description,'reason':'以本条自身约定判断不对等解除权。',
             'suggestion':'双方约定同等解除条件。','evidence':[],
             'model_output':{'description':description,'reason':'合同原文及候选依据。'*args.context_repeat},
             'clause_text':'合同终止后双方应交接成果并结算已完成工作。'*args.context_repeat}
    before=[];after=[]
    with tempfile.TemporaryDirectory() as directory:
        for number in range(args.rounds):
            # Alternate order across rounds to reduce systematic cache bias.
            outputs={}
            for mode in (['before','after'] if number%2==0 else ['after','before']):
                metrics,output=run(Path(directory)/('{}-{}.db'.format(number,mode)),mode,payload)
                (before if mode=='before' else after).append(metrics);outputs[mode]=output
            assert outputs['before']==outputs['after'],'Merge semantics changed'
    old=statistics.median(r['total_ms'] for r in before);new=statistics.median(r['total_ms'] for r in after)
    report={'items':120,'clauses':12,'rules_per_clause':10,'merge_every_units':10,'rounds':args.rounds,
            'payload_utf8_bytes':len(json.dumps(payload,ensure_ascii=False).encode('utf-8')),
            'before_median_ms':old,'after_median_ms':new,'speedup':old/new,
            'max_after_updates':max(v['updated_rows'] for r in after for v in r['samples']),
            'semantics_equal':True,'before':before,'after':after}
    report['passed']=report['max_after_updates']<=10 and report['speedup']>=10
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'before','after'}},ensure_ascii=False))
    for i in range(12):
        print(json.dumps({'units':(i+1)*10,'before':before[0]['samples'][i],'after':after[0]['samples'][i]}))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
