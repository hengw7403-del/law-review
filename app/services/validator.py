"""Strict output parsing and DB-authoritative candidate citation validation."""

import json
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk


class OutputFormatError(Exception):
    pass


class RiskOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    applicable: StrictBool
    has_issue: StrictBool
    risk_level: Literal["high", "medium", "low", "none"]
    description: StrictStr
    reason: StrictStr = Field(min_length=1)
    suggestion: StrictStr
    evidence_ids: List[StrictStr] = Field(max_length=20)
    support_note: StrictStr

    @model_validator(mode="after")
    def consistent_risk(self):
        if not self.applicable and self.has_issue:
            raise ValueError("不适用时has_issue必须为false")
        if self.has_issue == (self.risk_level == "none"):
            raise ValueError("has_issue 与 risk_level 不一致")
        return self


def parse_model_output(content: str) -> RiskOutput:
    try:
        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("JSON 字段重复")
                result[key] = value
            return result

        def reject_constant(value):
            raise ValueError("非法 JSON 常量：" + value)

        data = json.loads(content, object_pairs_hook=unique_keys,
                          parse_constant=reject_constant)
        return RiskOutput.model_validate(data)
    except (ValueError, TypeError, ValidationError) as exc:
        raise OutputFormatError("输出不是所要求的严格八字段JSON（含applicable）") from exc


def insufficient_result() -> Dict[str, Any]:
    return {"applicable": None, "has_issue": True, "check_status": "insufficient", "risk_level": "unknown",
            "description": "当前知识库依据不足，需人工核查。",
            "reason": "本次候选依据未提供可验证的支持，不能形成确定风险结论。",
            "suggestion": "补充适用依据并由人工复核。", "evidence": [], "support_note": ""}


def validate_evidence(output: RiskOutput, candidates: List[Dict[str, Any]], session: Session) -> Dict[str, Any]:
    candidate_map = {item["chunk_id"]: item for item in candidates}
    if not output.applicable:
        # Applicability precedes legal support. No database evidence is needed
        # and any accidental citations are ignored, not used as a risk finding.
        return {"applicable": False, "has_issue": False, "check_status": "not_applicable", "risk_level": "none",
                "applicability_source": "model", "description": output.description, "reason": output.reason,
                "suggestion": output.suggestion, "evidence": [], "support_note": "",
                "model_evidence_ids": list(output.evidence_ids), "model_output": output.model_dump(),
                "discarded_evidence_ids": [],
                "validation_warnings": ["EVIDENCE_IGNORED_NOT_APPLICABLE"] if output.evidence_ids else [],
                "validation_trace": {"candidate_ids": list(candidate_map), "model_evidence_ids": list(output.evidence_ids),
                    "accepted_ids": [], "steps": [{"stage": "model_output", "evidence_ids": list(output.evidence_ids)},
                    {"stage": "applicability", "applicable": False, "decision": "not_applicable"},
                    {"stage": "final", "decision": "not_applicable", "reason": "RULE_NOT_APPLICABLE", "accepted_ids": []}]}}
    requested = list(dict.fromkeys(output.evidence_ids))
    allowed = [chunk_id for chunk_id in requested if chunk_id in candidate_map]
    # Read current rows before applying eligibility so the audit can distinguish
    # a missing ID from disabled/unverified/superseded data. Acceptance remains
    # strict: all three eligibility flags and the snapshot fields must match.
    rows = session.execute(select(KnowledgeChunk).where(
        KnowledgeChunk.id.in_(allowed),
    )).scalars().all() if allowed else []
    row_map = {row.id: row for row in rows}
    evidence = []
    discarded = []
    steps = [{"stage": "model_output", "evidence_ids": list(output.evidence_ids)}]
    for chunk_id in requested:
        candidate = candidate_map.get(chunk_id)
        if candidate is None:
            steps.append({"stage": "candidate_membership", "id": chunk_id, "decision": "rejected", "reason": "NOT_IN_CANDIDATES"})
            discarded.append(chunk_id)
            continue
        steps.append({"stage": "candidate_membership", "id": chunk_id, "decision": "accepted"})
        row = row_map.get(chunk_id)
        reasons = (["CHUNK_NOT_FOUND"] if row is None else
                   (["SOURCE_DISABLED"] if row.enabled != 1 else []) +
                   (["SOURCE_UNVERIFIED"] if row.verified != 1 else []) +
                   (["SOURCE_SUPERSEDED"] if row.superseded != 0 else []))
        if reasons:
            steps.append({"stage": "database_eligibility", "id": chunk_id, "decision": "rejected", "reasons": reasons})
            discarded.append(chunk_id)
            continue
        steps.append({"stage": "database_eligibility", "id": chunk_id, "decision": "accepted"})
        changed_fields = [name for name in ("content", "article_no", "source_file") if getattr(row, name) != candidate.get(name)]
        if changed_fields:
            steps.append({"stage": "snapshot_match", "id": chunk_id, "decision": "rejected",
                          "reason": "SNAPSHOT_MISMATCH", "changed_fields": changed_fields})
            discarded.append(chunk_id)
            continue
        steps.append({"stage": "snapshot_match", "id": chunk_id, "decision": "accepted"})
        evidence.append({"chunk_id": row.id, "source_id": row.source_id,
                         "source_name": row.source_name, "source_file": row.source_file,
                         "source_version": row.source_version, "source_locator": row.source_locator,
                         "article_no": row.article_no, "content": row.content,
                         "content_hash": row.content_hash, "score": candidate["score"],
                         "matched_terms": candidate["matched_terms"]})
    if output.has_issue and not evidence:
        result = insufficient_result()
    else:
        result = {"has_issue": output.has_issue,
                  "check_status": "supported" if output.has_issue else "no_issue_detected",
                  "risk_level": output.risk_level, "description": output.description,
                  "reason": output.reason, "suggestion": output.suggestion,
                  "evidence": evidence, "support_note": output.support_note}
    result["applicable"] = True
    result["applicability_source"] = "model"
    result["discarded_evidence_ids"] = discarded
    result["validation_warnings"] = ["INVALID_EVIDENCE_IDS"] if discarded else []
    empty_reason = "MODEL_RETURNED_NO_EVIDENCE_IDS" if not requested else "ALL_EVIDENCE_REJECTED" if not evidence else None
    if output.has_issue and not requested:
        result["validation_warnings"].append("MODEL_RETURNED_NO_EVIDENCE_IDS")
    steps.append({"stage": "final", "decision": result["check_status"], "reason": empty_reason,
                  "accepted_ids": [source["chunk_id"] for source in evidence]})
    result["model_evidence_ids"] = list(output.evidence_ids)
    # Raw model conclusions are retained for audit only. Reports continue using
    # the validated/downgraded fields above, never this unvalidated output.
    result["model_output"] = output.model_dump()
    result["validation_trace"] = {"candidate_ids": list(candidate_map), "model_evidence_ids": list(output.evidence_ids),
                                  "accepted_ids": [source["chunk_id"] for source in evidence], "steps": steps}
    return result
