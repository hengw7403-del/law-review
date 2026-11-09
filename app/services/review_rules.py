"""Explicit versioned applicability definitions; no model routing."""
from copy import deepcopy

RULES_VERSION = "applicability-v4-ten-rules"
CLAUSE_RULES = {
    "liability": {"rule_id": "liability", "name": "违约责任", "scope": "clause", "applicable_when": {"keywords": ["违约", "违反", "不履行", "未履行", "未按期", "未按时", "延迟", "迟延", "逾期", "超时", "赔偿", "损失", "责任", "罚"], "min_hits": 1}},
    "payment": {"rule_id": "payment", "name": "付款条件", "scope": "clause", "applicable_when": {"keywords": ["价款", "付款", "支付", "款项", "发票", "结算", "账期"], "min_hits": 1}},
    "dispute_resolution": {"rule_id": "dispute_resolution", "name": "争议解决", "scope": "clause", "applicable_when": {"keywords": ["争议", "仲裁", "诉讼", "法院", "管辖", "协商"], "min_hits": 1}},
}
MISSING_RULES = {
    "payment_default": {"rule_id": "payment_default", "name": "逾期付款违约责任缺失", "scope": "contract", "goal": "全合同是否约定甲方逾期付款的违约金或赔偿责任。跨条款的统一违约责任也算覆盖，不可只看付款条款。", "impact_hint": "约定不明确可能增加损失金额、因果关系的举证负担及追偿时间成本；不要断言依法只能主张某一种救济。"},
    "delivery_default": {"rule_id": "delivery_default", "name": "交付违约责任缺失", "scope": "contract", "goal": "分别检查乙方逾期交付、交付不合格的违约金、赔偿或整改责任。已约定逾期责任不等于不合格交付责任也完整；说明具体覆盖和缺口。", "impact_hint": "缺少明确责任可能增加整改、赔偿金额和责任承担方式的争议，使甲方追偿及恢复项目进度更困难。"},
    "dispute_resolution": {"rule_id": "dispute_resolution", "name": "争议解决", "scope": "contract", "goal": "是否明确协商不成后的法院、管辖或仲裁机构；仅说日后另定方式属于内容不完整。"},
    "acceptance": {"rule_id": "acceptance", "name": "验收标准", "scope": "contract", "goal": "是否说明验收通过的判断标准、需求清单或测试用例。"},
    "confidentiality": {"rule_id": "confidentiality", "name": "保密义务", "scope": "contract", "goal": "是否约定保密义务、保密对象或禁止未经授权披露等内容。"},
}

def _definition(rule_id, name, keywords, points, positive, negative):
    return {"rule_id": rule_id, "name": name, "scope": "clause",
            "applicable_when": {"keywords": keywords, "min_hits": 1},
            "check_points": points, "positive_examples": positive, "negative_examples": negative}


CLAUSE_RULES.update({r["rule_id"]: r for r in [
    _definition("service_scope", "服务范围与交付物", ["服务范围", "交付物", "工作范围", "模块", "功能", "需求清单", "服务内容", "交付清单"],
                ["交付物名称、数量和版本是否明确", "需求清单及排除范围是否明确", "范围与验收边界能否对应", "最终确认需求等开口表述是否有确认机制"],
                ["交付物以最终确认需求为准", "开发范围包括账户模块和报表功能"], ["提供六个月缺陷修复服务", "违约金按价款计算"]),
    _definition("milestones", "里程碑与进度", ["进度", "里程碑", "周期", "工作日", "自然日", "节点", "完成期限", "工期", "完成时间"],
                ["起算点及各阶段期限是否确定", "对方配合义务及延误影响是否明确", "延期是否有通知和确认机制", "各节点与交付成果能否对应"],
                ["项目周期六十个自然日", "甲方延误资料提交时工期顺延"], ["成果权利归属甲方", "争议提交法院处理"]),
    _definition("acceptance", "验收标准与期限", ["验收", "测试", "验收标准", "验收确认", "验收通过", "整改", "测试用例"],
                ["标准是否可量化并对应需求", "验收及整改期限是否确定", "不回复是否视为通过及条件是否合理", "复验流程及不合格处理是否明确"],
                ["按测试用例验收", "收到交付物十日内验收，未回复视为通过"], ["双方确认收款账户", "违约金累计不超过价款百分之十"]),
    _definition("change_control", "需求变更机制", ["变更", "调整", "新增", "需求修改", "功能修改", "需求变化", "变更单"],
                ["变更是否需双方书面确认", "费用及付款安排如何调整", "工期及交付范围如何调整", "缺陷修复与新增需求是否区分"],
                ["新增模块应签署变更单", "需求变化后费用工期另行书面确认"], ["法院依法调整违约金", "保密资料按约删除"]),
    _definition("intellectual_property", "知识产权归属", ["知识产权", "著作权", "专利", "源码", "源代码", "归属", "授权", "既有工具", "许可", "通用组件"],
                ["成果权利归属和使用权是否明确", "乙方既有工具及通用组件是否保留", "第三方组件许可及侵权责任是否明确", "源码交付与授权范围是否一致"],
                ["成果著作权归甲方", "既有工具按许可范围提供使用"], ["授权代表协商争议", "支付合同总价百分之三十"]),
    _definition("confidentiality_data", "保密与数据处理", ["保密", "机密", "个人信息", "数据", "安全", "披露", "泄露", "泄密"],
                ["保密对象、例外及期限是否清楚", "个人信息处理目的和权限是否明确", "安全措施及泄露通知是否明确", "返还删除及违反保密后的责任是否约定"],
                ["双方不得披露商业秘密", "个人信息仅用于本项目并按约删除"], ["项目每十日召开会议", "争议由法院管辖"]),
    _definition("termination", "解除与终止", ["解除", "终止", "退出", "中止", "单方", "随时"],
                ["解除权及适用条件是否对等", "是否允许无条件单方解除及补偿", "通知、催告及补救期限是否明确", "终止后的费用成果资料交接是否明确"],
                ["甲方有权随时单方解除", "终止时结清费用并交接成果"], ["验收十日内完成", "成果知识产权归甲方"]),
]})

for _id, _points, _positive, _negative in [
    ("liability", ["违约触发条件是否明确", "违约金计算及累计上限是否清楚", "责任分配是否对等且符合我方立场", "损失赔偿及减损责任如何衔接"], ["逾期交付每日承担违约金", "严重违反义务经催告不改可解除"], ["仅约定法院管辖", "仅约定项目进度节点"]),
    ("payment", ["支付条件及节点是否明确", "账期及付款前提是否合理", "价款计算及结算是否清楚", "发票和验收前提是否可操作"], ["验收后180日内支付全部款项", "收到发票十日内支付首款"], ["仅以价款为基数计算违约金", "仅约定违反付款义务可解除"]),
    ("dispute_resolution", ["诉讼或仲裁方式是否确定", "法院或仲裁机构是否明确", "适用法律是否明确", "约定是否冲突或留待另行决定"], ["争议由合同签订地人民法院管辖", "争议提交明确的仲裁委员会"], ["终止后争议条款继续有效", "仅协商服务范围变更"]),
]:
    CLAUSE_RULES[_id].update(check_points=_points, positive_examples=_positive, negative_examples=_negative)

# 精确排除仅描述催告期限或条款存续的语境，不删除通用关键词。
CLAUSE_RULES["milestones"]["applicable_when"]["exclude_patterns"] = [r"催告后[零一二三四五六七八九十百0-9]+个工作日"]
CLAUSE_RULES["intellectual_property"]["applicable_when"]["exclude_patterns"] = [r"知识产权[^。；\n]{0,30}继续有效"]

# 旧任务快照继续使用原ID；新计划只枚举以下五个规范ID。
_legacy_missing = {key: MISSING_RULES[key] for key in ("dispute_resolution", "acceptance", "confidentiality")}
class MissingRuleRegistry(dict):
    def __missing__(self, key):
        if key in _legacy_missing:
            return _legacy_missing[key]
        raise KeyError(key)

    def __contains__(self, key):
        return super().__contains__(key) or key in _legacy_missing

MISSING_RULES = MissingRuleRegistry({key: MISSING_RULES[key] for key in ("payment_default", "delivery_default")})
for _id, _name, _goal, _impact in [
    ("dispute_resolution_gap", "争议解决方式缺失", "检查全合同是否完全没有诉讼、仲裁或其他争议解决方式；已有方式但管辖不明不等于完全缺失。", "发生争议后可能先就诉讼还是仲裁及受理地点产生争执，增加程序沟通、异地处理和时间成本；仍可依法确定管辖。"),
    ("acceptance_gap", "验收标准和期限缺失", "分别检查全合同是否约定验收标准和验收期限；只缺一项应说明部分缺口，不能称全无约定。", "双方难以统一认定交付是否合格及何时结束验收，可能反复整改并拖延尾款结算和项目关闭。"),
    ("confidentiality_gap", "保密义务缺失", "检查全合同是否完全未约定保密对象、保密义务或禁止未经许可披露；已有义务但不完善不等于完全缺失。", "双方难以明确资料可披露范围和履约人员的保密要求，泄露后对违约义务、责任及补救措施更易产生举证争议。"),
]:
    MISSING_RULES[_id] = {"rule_id": _id, "name": _name, "scope": "contract", "goal": _goal, "impact": _impact, "impact_hint": _impact}
for _rule in MISSING_RULES.values():
    _rule.setdefault("impact", _rule["impact_hint"])


def resolve_clause_rule(rule):
    value = deepcopy(CLAUSE_RULES[rule]) if isinstance(rule, str) and rule in CLAUSE_RULES else deepcopy(rule)
    if not isinstance(value, dict) or value.get("scope") != "clause" or value.get("rule_id") not in CLAUSE_RULES:
        raise ValueError("不支持的条款规则；支持：" + ",".join(CLAUSE_RULES))
    condition = value.get("applicable_when", {})
    keywords, minimum = condition.get("keywords", []), condition.get("min_hits")
    if (not keywords or any(not isinstance(k, str) or not k.strip() for k in keywords)
            or type(minimum) is not int or not 1 <= minimum <= len(set(keywords))):
        raise ValueError("keywords 必须非空，min_hits 必须为有效正整数")
    return value
