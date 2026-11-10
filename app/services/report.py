"""Markdown reports from persisted checkpoints and immutable contract inputs."""

import html
import json
import re

from sqlalchemy import select

from app.models import ReviewItem, ReviewTask
from app.services.review_tasks import ReviewError, elapsed_ms, task_metrics
from app.services.report_evidence import compact_evidence


def _inline(value) -> str:
    text = html.escape(str(value)).replace("\n", " ")
    return re.sub(r"([\\`*_{}\[\]()#+!|])", r"\\\1", text)


def _block(value) -> str:
    text = str(value or "（无）")
    longest = max([len(match.group()) for match in re.finditer(r"`+", text)] or [0])
    fence = "`" * max(3, longest + 1)
    return fence + "text\n" + text + "\n" + fence


def generate_report(session_factory, task_id: str) -> str:
    with session_factory() as session:
        task = session.get(ReviewTask, task_id)
        if task is None:
            raise ReviewError("TASK_NOT_FOUND", "任务不存在", 404)
        if task.review_status == "confirmed" and task.frozen_report is not None:
            return task.frozen_report
        snapshot = json.loads(task.input_snapshot)
        items = session.execute(select(ReviewItem).where(ReviewItem.review_id == task_id)).scalars().all()
        order = {unit["item_key"]: i for i, unit in enumerate(snapshot["plan"])}
        items.sort(key=lambda item: (order.get(item.item_key, len(order)), item.item_key))
        clauses = {clause["id"]: clause for clause in snapshot["clauses"]}
        counts = {"high": 0, "medium": 0, "low": 0, "insufficient": 0, "failed": 0, "no_issue_detected": 0, "not_applicable": 0, "merged": 0}
        for item in items:
            key = item.risk_level if item.check_status == "supported" else item.check_status
            if item.execution_status == "done" and key in counts:
                counts[key] += 1
        metrics = task_metrics(session, task_id)
        contract = snapshot["contract"]
        lines = ["# 合同辅助初审报告（草稿）", "", "## 合同信息", "",
                 "- 合同：" + _inline(contract["title"]),
                 "- 合同版本：{}；审查版本：{}".format(contract["version"], task.review_version),
                 "- 我方立场：" + _inline(contract["stance"]), "- 任务：" + _inline(task.id),
                 "- 状态：" + _inline(task.status),
                 "- 检查范围：前 {} / {} 个父条款；规则 {}；可执行单元 {}，不适用记录 {}。".format(
                     len(snapshot["clauses"]), snapshot["all_parent_count"], ", ".join(snapshot["rules"]) or "未选择条款级规则", len(snapshot["plan"]), counts["not_applicable"]),
                 "- 合同级规则：" + _inline(", ".join(snapshot.get("missing_rules", [])) or "未选择"),
                 "- 缺失检查使用全合同条款的条号和前80字，不受条款级 max_clauses 限制。",
                 "- 本报告仅覆盖这次受限计划，未覆盖条款不视为已审查。", "",
                 "## 统计", "", "| 高 | 中 | 低 | 依据不足 | 失败 | 未发现问题 | 不适用 | 未完成 |",
                 "|---|---|---|---|---|---|---|---|",
                 "| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                     counts["high"], counts["medium"], counts["low"], counts["insufficient"],
                     counts["failed"], counts["no_issue_detected"], counts["not_applicable"],
                     max(0, task.total_items - task.done_items - task.failed_items - counts["not_applicable"])), "",
                 "模型尝试：{}；prompt_tokens：{}；completion_tokens：{}；总 token：{}；模型累计耗时：{} ms。".format(
                     metrics["model_calls"], metrics["prompt_tokens"], metrics["completion_tokens"],
                     metrics["total_tokens"], metrics["model_duration_ms"]),
                 "未知用量保留为 None，不按零费用处理；模型耗时不包含停机和排队。", "",
                 "任务总耗时（包含排队、暂停与恢复）：{} ms。".format(elapsed_ms(task)), "",
                 "## 逐项意见", ""]

        def add_item(item):
            clause = clauses.get(item.clause_id, {})
            result = json.loads(item.machine_result) if item.machine_result else {}
            label = clause.get("original_no") or ("合同级检查" if item.clause_id is None else item.clause_id)
            risk_label = "none" if item.check_status == "no_issue_detected" else item.risk_level or "尚未判定"
            lines.extend(["### {} · {}".format(_inline(label), _inline(item.rule_id)), "",
                          "检查单元：" + _inline(item.item_key), "",
                          "状态：{}；风险：{}。".format(item.check_status or "pending", risk_label), ""])
            if "gap_description" in result and item.clause_id is None:
                lines.extend(["has_gap：" + str(result.get("has_gap")), "",
                              "缺什么（或已有约定）：", "", _block(result["gap_description"]), "",
                              "缺了会怎样（影响）：", "", _block(result.get("impact")), "",
                              "建议补什么：", "", _block(result.get("suggestion")), ""])
            else:
                lines.extend(["条款原文：", "", _block(result.get("clause_text") or clause.get("content", "合同级缺失检查，无对应单条原文")), "",
                              "问题：", "", _block(result.get("description", "尚未执行")), "",
                              "理由：", "", _block(result.get("reason")), "",
                              "建议：", "", _block(result.get("suggestion")), ""])
            if item.clause_id is None:
                lines.extend(["覆盖判断：" + _inline(result.get("assessment", "未完成")), "",
                              "本节依据为合同条款摘要，不是法律法规引文；摘要无法支持的缺失判断需人工核查。", ""])
                for finding in result.get("missing_items", []) if "gap_description" not in result else []:
                    lines.extend(["待补充：" + _inline(finding["name"]), "", _block(finding["importance"]), ""])
            evidence = compact_evidence(result, snapshot.get("all_clauses", snapshot["clauses"]),
                                        item.rule_id, item.clause_id is None)
            lines.extend(["依据（{} 条）：".format(len(evidence)), ""])
            if not evidence:
                lines.extend(["无已通过校验的引用。", ""])
            for source in evidence:
                label = source.get("title") or "{}，{}".format(
                    source.get("source_name") or source.get("source_file"), source.get("article_no"))
                lines.append("- {} —— {}".format(_inline(label), _inline(source["explanation"])))
            lines.append("")

        for item in items:
            if item.clause_id is not None and item.check_status not in {"not_applicable", "merged"}:
                add_item(item)
        lines.extend(["## 已合并检查项", "", "已合并 {} 项，不重复计入风险。".format(counts['merged']), ""])
        for item in items:
            if item.check_status == 'merged':
                result = json.loads(item.machine_result)
                lines.append('- {} → {}（保留最高等级，原始模型结果保留供审计）'.format(
                    _inline(item.item_key), _inline(result['merged_into'])))
        lines.append('')
        lines.extend(["## 合同级缺失", ""])
        missing = [item for item in items if item.clause_id is None]
        if not missing:
            lines.extend(["本任务未选择合同级缺失检查。", ""])
        for item in missing:
            add_item(item)
        lines.extend(["## 不适用清单", ""])
        skipped = [item for item in items if item.check_status == "not_applicable"]
        for item in skipped:
            clause = clauses.get(item.clause_id, {})
            result = json.loads(item.machine_result or "{}")
            via_model = result.get("applicability_source") == "model" or item.item_key in order
            note = ("模型判断本条不涉及本规则：" + result.get("reason", "需核对检查范围。")) if via_model else "未达到适用关键词条件，未调用模型。"
            lines.append("- {} / {}：{}".format(
                _inline(clause.get("original_no") or item.clause_id), _inline(item.rule_id), _inline(note)))
        if not skipped:
            lines.append("无。")
        lines.append("")
        lines.extend(["## 未发现问题清单", ""])
        clean = [item for item in items if item.check_status == "no_issue_detected"]
        lines.extend(["- " + _inline(item.item_key) for item in clean] or ["无。"])
        lines.extend(["", "未发现问题仅表示当前规则与资料范围内未检出；报告尚未经人工确认。", ""])
        return "\n".join(lines)
