import json
from sqlalchemy import select
from app.models import ReviewTask, ReviewItem
from app.services.review_tasks import ReviewError


def review_items(session_factory, task_id):
    with session_factory() as session:
        task=session.get(ReviewTask,task_id)
        if task is None: raise ReviewError('TASK_NOT_FOUND','任务不存在',404)
        snapshot=json.loads(task.input_snapshot)
        clauses={c['id']:c for c in snapshot.get('all_clauses',snapshot['clauses'])}
        stats=dict.fromkeys(['high','medium','low','insufficient','failed','no_issue','not_applicable'],0)
        items=[]
        for row in session.execute(select(ReviewItem).where(ReviewItem.review_id==task_id)).scalars():
            result=json.loads(row.machine_result or '{}');clause=clauses.get(row.clause_id,{})
            status=row.check_status or 'pending'
            key=row.risk_level if status=='supported' else 'no_issue' if status=='no_issue_detected' else status
            if key in stats: stats[key]+=1
            items.append({'item_key':row.item_key,'clause_seq':clause.get('seq'),'clause_no':clause.get('original_no'),
                'clause_content':clause.get('content',''),'scope':'contract' if row.clause_id is None else 'clause',
                'rule_id':row.rule_id,'check_status':status,'risk_level':row.risk_level,
                'description':result.get('description','尚未执行'),'reason':result.get('reason',''),
                'suggestion':result.get('suggestion',''),'evidence':result.get('evidence',[])})
        ranks={'supported':0,'insufficient':1,'failed':2,'pending':3,'not_applicable':4,'no_issue_detected':5}
        items.sort(key=lambda i:(ranks.get(i['check_status'],4),{'high':0,'medium':1,'low':2}.get(i['risk_level'],3),i['clause_seq'] or 0,i['rule_id']))
        return {'task_id':task.id,'status':task.status,'reviewed_at':task.finished_at or task.started_at or task.created_at,
                'contract':{'title':snapshot['contract']['title'],'party_stance':snapshot['contract']['stance'],'contract_id':task.contract_id},
                'stats':stats,'items':items}
