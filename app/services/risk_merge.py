"""Conservative deterministic duplicate groups; raw model output is retained."""
import json
import re
from sqlalchemy import select
from app.models import ReviewItem

MERGE_VERSION = 'same-clause-risk-v1'
# Require the same concrete topic, not merely the same clause or evidence IDs.
TOPICS = {
    'unequal_termination': (r'解除权|单方解除|任意解除|随时解除', r'不对等|不平等|无权|单方|仅甲方|甲方.*乙方'),
    'uncapped_liability': (r'违约金', r'不设上限|无上限|未设上限|没有上限'),
    'long_payment_term': (r'账期|付款周期|付款期限|回款周期', r'180|一百八十|过长|偏长'),
}


def signature(result):
    description = result.get('description', '')
    topics = tuple(key for key, patterns in TOPICS.items() if all(re.search(p, description) for p in patterns))
    # Exact normalized descriptions can also match; uncertain paraphrases stay separate.
    return ('topics', topics) if topics else ('exact', re.sub(r'\W+', '', description)) if description.strip() else None


def merge_task_items(session, task_id):
    rows = list(session.scalars(select(ReviewItem).where(
        ReviewItem.review_id == task_id, ReviewItem.clause_id.is_not(None),
        ReviewItem.execution_status == 'done', ReviewItem.check_status.in_(['supported', 'merged']))))
    groups = {}
    for row in rows:
        result = json.loads(row.machine_result)
        # Recompute from all saved findings so a later, higher risk can replace the winner.
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
