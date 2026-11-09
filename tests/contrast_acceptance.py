"""Two isolated production-worker runs sharing <=25 real attempts. No reruns."""
import argparse
import json
from pathlib import Path

from sqlalchemy import select,func
from sqlalchemy.orm import sessionmaker

from app import db
from app.models import KnowledgeChunk, User, ReviewTask, ReviewItem, Clause, LLMLog
from app.services.auth import Principal
from app.services.contracts import create_contract
from app.services.review_tasks import create_review,get_task
from app.services.report import generate_report
from tests.parse_helpers import ROOT
from tests.review_helpers import start_worker,stop_process_tree


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--live',action='store_true',required=True); parser.parse_args()
    path=ROOT/'work/contrast-live-results.json'
    database=ROOT/'work/contrast-live.db'
    if path.exists() or database.exists(): raise RuntimeError('已有付费记录/测试库，不能自动重复运行')
    engine=db.create_db_engine('sqlite:///'+database.as_posix()); db.Base.metadata.create_all(engine)
    sf=sessionmaker(bind=engine,expire_on_commit=False)
    with db.SessionLocal() as source:
        knowledge=[{c.name:getattr(row,c.name) for c in KnowledgeChunk.__table__.columns}
                   for row in source.execute(select(KnowledgeChunk)).scalars()]
    # Clear only optional user attribution FKs, not content, IDs or source state.
    with sf.begin() as session:
        session.add(User(id='contrast-user',username='contrast-user',password_hash='!disabled',role='reviewer',active=0))
        for row in knowledge:
            row['verified_by']=None;session.add(KnowledgeChunk(**row))
    actor=Principal('contrast-user','contrast-user','reviewer',1)
    state={'stance':'party_b','total_limit':25,'runs':{}}
    def save():path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    save()
    try:
        for label,limit in [('bad',14),('clean',None)]:
            with sf() as session:used=session.scalar(select(func.count()).select_from(LLMLog))
            remaining=25-used
            if remaining<11:raise RuntimeError('预算不足，不再调用')
            filename='contract_c_'+label+'.txt'
            cid=create_contract(sf,actor,filename,(ROOT/'samples'/filename).read_bytes(),'party_b')['contract_id']
            tid=create_review(sf,cid)['task_id']
            with sf.begin() as session:
                task=session.get(ReviewTask,tid);snapshot=json.loads(task.input_snapshot)
                assert len(snapshot['clauses'])==12 and len(snapshot['plan'])==11
                snapshot['call_limit']=min(limit,remaining) if limit else remaining
                task.input_snapshot=json.dumps(snapshot,ensure_ascii=False)
            state['runs'][label]={'contract_id':cid,'task_id':tid,'attempt_limit':snapshot['call_limit']};save()
            worker=start_worker(database,ROOT/('work/contrast-'+label+'-worker.log'),mock=False)
            try:assert worker.wait(timeout=900)==0
            finally:stop_process_tree(worker)
            result=state['runs'][label];result['final']=get_task(sf,tid)
            with sf() as session:
                clauses={c.id:c for c in session.execute(select(Clause).where(Clause.contract_id==cid)).scalars()}
                items=session.execute(select(ReviewItem).where(ReviewItem.review_id==tid)).scalars().all()
                result['units']=[]
                for item in sorted(items,key=lambda i:(clauses[i.clause_id].seq,i.rule_id)):
                    raw=json.loads(item.machine_result)
                    unit={'seq':clauses[item.clause_id].seq,'article_no':clauses[item.clause_id].original_no,'rule_id':item.rule_id,
                          'check_status':item.check_status,'risk_level':raw.get('risk_level'),'evidence_count':len(raw.get('evidence',[])),
                          'applicable':raw.get('applicable'),'result':raw}
                    result['units'].append(unit)
                    print(json.dumps({'contract':label,**{k:v for k,v in unit.items() if k!='result'}},ensure_ascii=False),flush=True)
                result['counts']={s:sum(i.check_status==s for i in items) for s in set(i.check_status for i in items)}
                result['calls']=[{'prompt_tokens':log.prompt_tokens,'completion_tokens':log.completion_tokens,'duration_ms':log.duration_ms,'error_code':log.error_code}
                                 for log in session.execute(select(LLMLog).where(LLMLog.review_id==tid).order_by(LLMLog.created_at)).scalars()]
                state['total_calls']=session.scalar(select(func.count()).select_from(LLMLog))
            assert state['total_calls']<=25
            (ROOT/('work/contrast-'+label+'-report.md')).write_text(generate_report(sf,tid),encoding='utf-8')
            save()
        def unit(label,seq,rule):return next(u for u in state['runs'][label]['units'] if u['seq']==seq and u['rule_id']==rule)
        state['checks']={
          'bad_supported_at_least_two':state['runs']['bad']['counts'].get('supported',0)>=2,
          'bad_uncapped_flagged':unit('bad',10,'liability')['check_status']=='supported' or unit('bad',10,'liability')['risk_level'] in ['high','medium','low'],
          'bad_long_payment_flagged':unit('bad',2,'payment')['result'].get('has_issue') is True,
          'bad_unequal_termination_flagged':unit('bad',11,'liability')['result'].get('has_issue') is True,
          'clean_supported_zero':state['runs']['clean']['counts'].get('supported',0)==0,
          'both_clause_11_dispute_not_applicable':all(unit(label,11,'dispute_resolution')['check_status']=='not_applicable' for label in ['bad','clean']),
          'within_25_calls':state['total_calls']<=25}
        lines=['# 对照审查：乙方立场','','两份各只运行一次；没有根据结果重改提示词或重复付费。','','| 条款 | 规则 | 坑合同状态 | 风险 | 依据数 | 干净合同状态 | 风险 | 依据数 |','|---|---|---|---|---:|---|---|---:|']
        for bad in state['runs']['bad']['units']:
            clean=unit('clean',bad['seq'],bad['rule_id'])
            lines.append('| {} | {} | {} | {} | {} | {} | {} | {} |'.format(bad['article_no'],bad['rule_id'],bad['check_status'],bad['risk_level'],bad['evidence_count'],clean['check_status'],clean['risk_level'],clean['evidence_count']))
        lines+=['','## 检查结果','',*['- {}：{}'.format(k,v) for k,v in state['checks'].items()],'','总模型尝试：{} / 25。'.format(state['total_calls'])]
        (ROOT/'work/contrast-comparison.md').write_text('\n'.join(lines),encoding='utf-8');save()
        print(json.dumps({'counts':{k:v['counts'] for k,v in state['runs'].items()},'checks':state['checks'],'total_calls':state['total_calls']},ensure_ascii=False),flush=True)
    finally:engine.dispose()


if __name__=='__main__':main()
