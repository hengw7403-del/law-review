"""Compact, deterministic references; never turn candidates into citations."""
def compact_evidence(result, clauses, rule_id, contract_scope=False):
    if not contract_scope:
        seen, selected = set(), []
        for source in result.get("evidence", []):
            key = source.get("chunk_id") or (source.get("source_id"), source.get("article_no"))
            if key in seen:
                continue
            seen.add(key)
            selected.append({**source, "explanation": source.get("explanation") or
                             (result.get("support_note") or "该法条为本项实际引用依据，用于核对判断理由，仍需人工确认支持关系。")[:180]})
        return selected[:5]
    topics = {
        "payment_default": ("付款", "违约"),
        "delivery_default": ("验收", "质保", "违约"),
        "acceptance": ("验收",), "confidentiality": ("保密",),
        "dispute_resolution": ("争议",),
    }.get(rule_id, ())
    existing = {source.get("clause_id") for source in result.get("evidence", [])}
    selected = []
    for clause in clauses:
        text = clause["content"]
        heading = text.split("\n", 1)[0]
        # Title/topic matching is for choosing references, not adjudicating gaps.
        if not any(topic in heading for topic in topics):
            continue
        if rule_id not in {"payment_default", "delivery_default"} and clause["id"] not in existing:
            continue
        if "验收" in heading:
            explanation = "列有验收标准、流程或整改安排；需结合本项核对整改失败后的责任，不能将整改义务视为不存在。" if "整改" in text else "用于核对验收通过标准及流程是否已有约定，标题本身不能证明内容完整。"
        elif "质保" in heading:
            explanation = "列有缺陷修复及响应安排；用于区分修复义务与违约金、赔偿等责任是否完整。"
        elif "违约" in heading:
            if rule_id == "payment_default":
                explanation = "该条包含逾期付款表述，须与付款节点结合核对责任计算口径。" if "逾期付款" in text else "核对统一违约责任是否覆盖付款迟延，不能只看付款条款。"
            else:
                explanation = "该条约定逾期交付责任；用于核对其是否同时覆盖质量不合格的后果。" if "逾期交付" in text else "核对统一违约责任对交付迟延及质量问题的覆盖范围。"
        elif "付款" in heading:
            explanation = "该条约定付款条件或节点；责任是否缺失还须结合统一违约条款判断。"
        else:
            explanation = "该条涉及本项检查主题，用于核对已有约定及尚待明确的要素。"
        selected.append({"clause_id": clause["id"], "article_no": clause.get("original_no"),
                         "title": heading, "explanation": explanation, "kind": "contract_outline"})
    return selected[:5]
