"""Contract-outline coverage checks, distinct from statute-backed risk judging."""
import json
import time
from typing import List, Literal
import uuid

from openai import APIError, APIStatusError
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator, model_validator

from app.prompts.missing_judge import GAP_RULES, PROMPT_VERSION, build_messages
from app.services.llm import CallContext, LLMConfigurationError, LLMBudgetExceeded, LLMLoggingError, aggregate_usage
from app.services.review_rules import MISSING_RULES
from app.services.risk_judge import failed_result


class MissingItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: StrictStr = Field(min_length=1)
    importance: StrictStr = Field(min_length=1)


class GapOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    has_gap: StrictBool
    gap_description: StrictStr = Field(min_length=1)
    impact: StrictStr = Field(min_length=1)
    suggestion: StrictStr = Field(min_length=1)

    @field_validator("gap_description", "impact", "suggestion")
    @classmethod
    def substantive_text(cls, value):
        value = value.strip()
        if len(value) < 8 or value.rstrip("。！.! ") in {"存在风险", "未约定", "无", "有风险", "需要注意"}:
            raise ValueError("说明、影响和建议必须具体")
        return value


def _responsibility_context(text, rule_id):
    target = "付款" if rule_id == "payment_default" else "交付"
    return target in text and any(k in text for k in ("逾期", "迟延", "延迟", "不合格")) and any(
        k in text for k in ("违约", "赔偿", "责任", "整改", "修复"))


def validate_gap(output, clauses, rule_id):
    # The model sees only 80-character snippets. Full text only prevents false
    # absence claims; it is never silently added to the model input.
    relevant = [c for c in clauses if _responsibility_context(c["content"], rule_id)]
    hidden = [c for c in relevant if not _responsibility_context(c["content"][:80], rule_id)]
    uncertain = output.has_gap and bool(hidden)
    if not output.has_gap and not any(_responsibility_context(c["content"][:80], rule_id) for c in clauses):
        uncertain = True
    description, impact, suggestion = output.gap_description, output.impact, output.suggestion
    if uncertain:
        numbers = "、".join(c.get("original_no") or "无编号条款" for c in relevant)
        description = "摘要不足以确认责任缺失；完整正文{}存在相关责任表述，需人工核对覆盖范围。".format(numbers)
        impact = "不能仅凭前80字认定完全没有责任；忽略跨条款约定会误报缺失并导致重复或冲突的责任安排。"
        suggestion = "核对完整违约责任条款，确认付款、交付迟延和不合格交付分别适用的计算口径及补救方式。"
    status = "insufficient" if uncertain else ("supported" if output.has_gap else "no_issue_detected")
    assessment = "uncertain" if uncertain else ("incomplete" if output.has_gap else "present")
    return {"has_gap": None if uncertain else output.has_gap, "model_has_gap": output.has_gap, "gap_description": description,
            "impact": impact, "suggestion": suggestion, "description": description, "reason": description,
            "check_status": status, "risk_level": "unknown" if uncertain else ("medium" if output.has_gap else "none"),
            "has_issue": None if uncertain else output.has_gap, "assessment": assessment,
            "missing_items": [{"name": MISSING_RULES[rule_id]["name"], "importance": impact,
                               "gap_description": description, "impact": impact, "suggestion": suggestion}] if output.has_gap and not uncertain else [],
            "evidence": [{"kind": "contract_outline", "clause_id": c["id"], "article_no": c.get("original_no"),
                          "source_name": "合同条款摘要（非法律依据）", "content": c["content"][:80]} for c in clauses],
            "basis_type": "contract_outline", "validation_warnings": ["OUTLINE_INSUFFICIENT"] if uncertain else [],
            "coverage_limit": "仅发送每条前80字；全文只用于拦截可能的误报，不能据此确认法律结论。"}


class MissingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    assessment: Literal["present", "missing", "incomplete", "uncertain"]
    missing_items: List[MissingItem] = Field(max_length=1)
    covered_clause_ids: List[StrictStr]
    reason: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def consistency(self):
        if self.assessment == "present" and (self.missing_items or not self.covered_clause_ids):
            raise ValueError("present needs a known clause and no missing items")
        if self.assessment in {"missing", "incomplete"} and len(self.missing_items) != 1:
            raise ValueError("Missing finding needs one explanation")
        if self.assessment == "uncertain" and self.missing_items:
            raise ValueError("Uncertain cannot claim definite missing items")
        return self


def validate_missing(output, clauses, rule_id):
    clause_map = {c["id"]: c for c in clauses}
    ids = list(dict.fromkeys(output.covered_clause_ids))
    warnings = []
    assessment = output.assessment
    if any(cid not in clause_map for cid in ids):
        assessment = "uncertain"
        warnings.append("INVALID_CLAUSE_REFERENCE")
    markers = {"acceptance": ("验收",), "confidentiality": ("保密", "泄露"),
               "dispute_resolution": ("争议", "仲裁", "法院", "管辖", "诉讼", "协商")}[rule_id]
    if assessment == "present" and not any(
            any(marker in clause_map[cid]["content"][:80] for marker in markers) for cid in ids if cid in clause_map):
        assessment = "uncertain"
        warnings.append("INVALID_CLAUSE_SUPPORT")
    # Full text is used only for a conservative server guard, never sent to LLM.
    texts = [c["content"] for c in clauses]
    substantive = {
        "acceptance": any("验收" in t and any(k in t for k in ("标准", "测试用例", "通过")) for t in texts),
        "confidentiality": any("保密" in t and any(k in t for k in ("义务", "资料", "不得", "采取")) for t in texts),
        "dispute_resolution": any(any(k in t for k in ("法院", "仲裁机构", "仲裁委员会", "管辖")) for t in texts),
    }[rule_id]
    if assessment in {"missing", "incomplete"} and substantive:
        assessment = "uncertain"
        warnings.append("FULL_TEXT_HAS_RELATED_TERMS")
    if assessment == "missing" and rule_id == "dispute_resolution" and any("争议" in t for t in texts):
        assessment = "incomplete"
        warnings.append("RELATED_CLAUSE_EXISTS")
    name = MISSING_RULES[rule_id]["name"]
    findings = [{"name": name, "importance": item.importance, "finding_type": assessment}
                for item in output.missing_items] if assessment in {"missing", "incomplete"} else []
    basis = [{"kind": "contract_outline", "chunk_id": c["id"], "clause_id": c["id"],
              "article_no": c.get("original_no"), "source_name": "合同条款摘要（非法律依据）",
              "source_locator": "contract-clause:" + c["id"], "content": c["content"][:80]}
             for c in clauses if not ids or c["id"] in ids]
    if assessment in {"missing", "incomplete"}:
        status, risk = "supported", "medium"
        description = name + ("未检出相关约定（摘要初筛）" if assessment == "missing" else "存在关键要素待补充")
    elif assessment == "present":
        status, risk, description = "no_issue_detected", "none", name + "已有相关约定"
    else:
        status, risk, description = "insufficient", "unknown", "摘要或覆盖信息不足，需人工核查" 
    reason = output.reason
    if any(w in warnings for w in ("FULL_TEXT_HAS_RELATED_TERMS", "INVALID_CLAUSE_REFERENCE", "INVALID_CLAUSE_SUPPORT")):
        reason = "完整正文存在相关约定或摘要引用不可核对，不能给出确定缺失结论，需核对原文。"
    elif "RELATED_CLAUSE_EXISTS" in warnings:
        reason = "原文已有争议处理表述，不能声称完全没有约定；仅需核查具体机构和方式等关键要素。"
    return {"check_status": status, "risk_level": risk, "has_issue": assessment != "present",
            "assessment": assessment, "description": description, "reason": reason,
            "suggestion": findings[0]["importance"] if findings else "核对完整条款后确认。",
            "missing_items": findings, "evidence": basis, "basis_type": "contract_outline",
            "validation_warnings": warnings, "coverage_limit": "仅基于每条前80字初筛；未证明完整法律合规。"}


class MissingJudge:
    def __init__(self, llm_client):
        self.llm_client = llm_client

    def judge(self, clauses, stance, rule_id, review_id, item_id, rule=None):
        rule = rule or MISSING_RULES[rule_id]
        context = CallContext(uuid.uuid4().hex, review_id=review_id, item_id=item_id, purpose="missing_judge")
        started = time.perf_counter()
        try:
            # Exactly one outbound attempt for each contract-level rule.
            content, _, _ = self.llm_client.complete(build_messages(clauses, stance, rule), context, max_retries=0)
            try:
                def pairs(values):
                    result = {}
                    for key, value in values:
                        if key in result:
                            raise ValueError("Duplicate JSON key")
                        result[key] = value
                    return result
                data = json.loads(content, object_pairs_hook=pairs)
                if rule_id in GAP_RULES:
                    result = validate_gap(GapOutput.model_validate(data), clauses, rule_id)
                else:
                    result = validate_missing(MissingOutput.model_validate(data), clauses, rule_id)
            except (ValueError, TypeError):
                self.llm_client.mark_invalid_output(context)
                result = failed_result("INVALID_MISSING_OUTPUT")
        except LLMConfigurationError:
            result = failed_result("MISSING_API_KEY")
        except LLMBudgetExceeded:
            result = failed_result("CALL_BUDGET_EXCEEDED")
        except LLMLoggingError:
            result = failed_result("LLM_LOGGING_FAILED")
        except APIStatusError as error:
            result = failed_result("LLM_HTTP_{}".format(error.status_code))
        except APIError as error:
            result = failed_result(type(error).__name__)
        result.update({"request_id": context.request_id, "prompt_version": PROMPT_VERSION,
                       "calls": context.attempts, "usage": aggregate_usage(context.attempts),
                       "duration_ms": max(0, round((time.perf_counter() - started) * 1000)),
                       "scope": "contract", "rule_id": rule_id})
        return result
