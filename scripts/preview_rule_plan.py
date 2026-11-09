"""Offline plan only: no database writes, worker or model invocation."""
import json
from pathlib import Path
from app.services.clause_splitter import split_clauses
from app.services.review_planner import build_check_plan
from app.services.review_rules import CLAUSE_RULES, MISSING_RULES


def preview():
    root = Path(__file__).resolve().parents[1]
    clauses = split_clauses((root / 'samples/contract_a.txt').read_text(encoding='utf-8'))
    for clause in clauses:
        clause['id'] = str(clause['seq'])
    plan = build_check_plan(clauses, list(CLAUSE_RULES), missing_rules=list(MISSING_RULES))
    rows = []
    for clause in clauses:
        hits = [u for u in plan['units'] if u['clause_id'] == clause['id']]
        rows.append({'clause': clause['original_no'], 'count': len(hits),
                     'rules': {u['rule_id']: u['matched_keywords'] for u in hits}})
    result = {'raw_units': len(clauses)*len(CLAUSE_RULES),
              'clause_units': sum(r['count'] for r in rows),
              'not_applicable': len(plan['not_applicable']), 'contract_units': len(MISSING_RULES),
              'model_calls': 0, 'rows': rows}
    return result


if __name__ == '__main__':
    result = preview()
    root = Path(__file__).resolve().parents[1]
    out = root / 'work/rule-plan-v4.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 合同 A 检查计划（待确认，未执行）', '',
             '120 个候选单元 → 45 个条款级待检查单元；75 个不适用。另有 5 个合同级缺失检查。模型调用：0。', '',
             '关键词命中仅表示进入待检查清单，不代表模型已确认适用或存在风险。每条最多命中5个规则。', '',
             '| 条款 | 数量 | 命中规则（关键词） |', '|---|---:|---|']
    for row in result['rows']:
        names = ['{}（{}）'.format(CLAUSE_RULES[k]['name'], '、'.join(v)) for k, v in row['rules'].items()]
        lines.append('| {} | {} | {} |'.format(row['clause'], row['count'], '；'.join(names)))
    lines += ['', '## 边界与待确认', '',
              '- 通用“服务、确认、通过、修改、标准”收为服务范围、验收确认、验收通过、需求修改等语境词，避免日常表述触发。',
              '- 第十一条：催告期限不触发项目进度；仅知识产权条款继续有效不触发知识产权归属。混合条款仍保留真实安排的命中。',
              '- 原有规则保留初筛语义：第九条“协商”命中争议规则，第十条“价款/付款”命中付款规则，第十一条存续表述仍命中争议规则。此处不是模型适用结论。',
              '- 第一条数据库脚本、第五条数据丢失、第七条组件披露也会初筛命中保密与数据规则，留给人工确认是否进一步收紧。',
              '- 默认审查仍保持原来的3个规则，不启动worker、不建正式任务、不修改正式库。旧缺失规则ID只保留历史兼容，新计划列5个规范ID。', '', '## 五个合同级缺失规则', '']
    for rule in MISSING_RULES.values():
        lines += ['- {} / {}：{} 影响：{}'.format(rule['rule_id'], rule['name'], rule['goal'], rule['impact'])]
    lines += ['', '## 新增条款规则定义', '']
    for rule in list(CLAUSE_RULES.values())[3:]:
        lines += ['### {} {}'.format(rule['rule_id'], rule['name']), '',
                  '关键词：' + '、'.join(rule['applicable_when']['keywords']), '',
                  '检查要点：' + '；'.join(rule['check_points']), '',
                  '适用正例：' + '；'.join(rule['positive_examples']), '',
                  '不适用反例（语义判断，不保证关键词初筛全部排除）：' + '；'.join(rule['negative_examples']), '']
    (root / 'work/rule-plan-v4.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
