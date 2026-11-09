"""Deterministic single-clause retrieval -> model -> citation validation."""

import time
import hashlib
import json
from typing import Any, Dict, Optional
import uuid

from openai import APIError, APIStatusError

from app import db
from app.prompts.risk_judge import PROMPT_VERSION, build_messages
from app.services.llm import (CallContext, LLMClient, LLMBudgetExceeded,
                              LLMConfigurationError, LLMLoggingError, aggregate_usage)
from app.services.search import BM25Index, build_search_index, eligible_chunk_ids
from app.services.validator import (OutputFormatError, insufficient_result,
                                    parse_model_output, validate_evidence)


def failed_result(code: str) -> Dict[str, Any]:
    return {"applicable": None, "has_issue": True, "check_status": "failed", "risk_level": "unknown",
            "description": "本次辅助初审未完成，需人工复核。", "reason": "模型调用或结构校验失败。",
            "suggestion": "检查错误原因后重试，或交由人工处理。", "evidence": [],
            "support_note": "", "discarded_evidence_ids": [], "validation_warnings": [],
            "error_code": code}


class RiskJudge:
    def __init__(self, search_index: BM25Index, session_factory, llm_client: LLMClient) -> None:
        self.search_index = search_index
        self.session_factory = session_factory
        self.llm_client = llm_client

    def judge_clause(self, clause_text: str, stance: str, rule_id: str,
                     request_id: Optional[str] = None, review_id: Optional[str] = None,
                     item_id: Optional[str] = None) -> Dict[str, Any]:
        if stance not in {"party_a", "party_b"}:
            raise ValueError("stance 必须为 party_a 或 party_b")
        if not clause_text.strip() or not rule_id.strip():
            raise ValueError("条款和 rule_id 不能为空")
        started = time.perf_counter()
        context = CallContext(request_id or uuid.uuid4().hex, review_id=review_id, item_id=item_id)
        # End the read transaction before the potentially slow network call.
        with self.session_factory() as session:
            # Rule-level query expansion is lexical and deterministic; it does
            # not boost any article number or impose a relevance threshold.
            expansion = {"liability": " 违约金 违约责任", "dispute_resolution": " 争议解决方式 协议管辖 管辖法院"}
            query = clause_text + expansion.get(rule_id, "")
            candidates = self.search_index.search(query, 5, eligible_chunk_ids(session))

        def finish(result):
            input_digest = hashlib.sha256(json.dumps({"clause_text": clause_text, "stance": stance, "rule_id": rule_id},
                                                      ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            result.update({"request_id": context.request_id, "stance": stance, "rule_id": rule_id,
                           "input_digest": input_digest,
                           "prompt_version": PROMPT_VERSION,
                           "candidate_ids": [item["chunk_id"] for item in candidates],
                           "usage": aggregate_usage(context.attempts), "calls": context.attempts,
                           "duration_ms": max(0, round((time.perf_counter() - started) * 1000))})
            print(json.dumps({"event": "evidence_validation", "path": "worker" if review_id else "preview",
                              "request_id": context.request_id, "review_id": review_id, "item_id": item_id,
                              "input_digest": input_digest, "rule_id": rule_id, "candidate_ids": result["candidate_ids"],
                              "model_evidence_ids": result.get("model_evidence_ids"),
                              "applicable": result.get("applicable"), "has_issue": result.get("has_issue"),
                              "trace": result.get("validation_trace"), "check_status": result["check_status"],
                              "error_code": result.get("error_code")}, ensure_ascii=False), flush=True)
            return result

        if not candidates:
            result = insufficient_result()
            result.update({"discarded_evidence_ids": [], "validation_warnings": ["NO_CANDIDATES"]})
            return finish(result)
        try:
            for format_attempt in range(2):
                messages = build_messages(clause_text, stance, rule_id, candidates, format_retry=bool(format_attempt))
                content, _, _ = self.llm_client.complete(messages, context)
                try:
                    output = parse_model_output(content)
                except OutputFormatError:
                    self.llm_client.mark_invalid_output(context)
                    if format_attempt == 0:
                        continue
                    return finish(failed_result("INVALID_MODEL_OUTPUT"))
                with self.session_factory() as session:
                    return finish(validate_evidence(output, candidates, session))
        except LLMConfigurationError:
            return finish(failed_result("MISSING_API_KEY"))
        except LLMBudgetExceeded:
            return finish(failed_result("CALL_BUDGET_EXCEEDED"))
        except LLMLoggingError:
            return finish(failed_result("LLM_LOGGING_FAILED"))
        except APIStatusError as exc:
            code = "LLM_AUTHENTICATION" if exc.status_code in (401, 403) else "LLM_HTTP_{}".format(exc.status_code)
            return finish(failed_result(code))
        except APIError as exc:
            return finish(failed_result(type(exc).__name__))
        raise AssertionError("Unreachable format-retry state")


def judge_clause(clause_text: str, stance: str, rule_id: str) -> Dict[str, Any]:
    """One-shot Python entry point. The HTTP service reuses its startup index."""
    with db.SessionLocal() as session:
        index = build_search_index(session)
    client = LLMClient(db.SessionLocal)
    try:
        return RiskJudge(index, db.SessionLocal, client).judge_clause(clause_text, stance, rule_id)
    finally:
        client.close()
