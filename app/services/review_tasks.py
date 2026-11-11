"""Persistent task creation, immutable inputs and real checkpoint statistics."""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional, Sequence
import uuid

from sqlalchemy import func, select, text

from app.models import Clause, Contract, KnowledgeChunk, LLMLog, ReviewItem, ReviewTask, Ticket
from app.prompts.risk_judge import PROMPT_VERSION
from app.services.llm import MODEL
from app.services.review_planner import build_check_plan, normalize_rules
from app.services.review_rules import RULES_VERSION
from app.prompts.missing_judge import PROMPT_VERSION as MISSING_PROMPT_VERSION
from app.services.search import TOKENIZER_VERSION, eligibility_conditions


class ReviewError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_review(session_factory, contract_id: str, max_clauses: Optional[int] = None,
                  rules: Optional[Sequence[str]] = None, missing_rules: Optional[Sequence[str]] = None,
                  created_by: Optional[str] = None, _on_created=None, party_stance: Optional[str] = None) -> Dict[str, Any]:
    selected_rules = normalize_rules(rules) if rules else ([] if rules == [] else ["liability", "payment", "dispute_resolution"])
    missing_rules = list(missing_rules or [])
    # Validate limits even when a duplicate is submitted.
    build_check_plan([], selected_rules, max_clauses, missing_rules)
    with session_factory.begin() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        contract = session.get(Contract, contract_id)
        if contract is None:
            raise ReviewError("CONTRACT_NOT_FOUND", "合同不存在", 404)
        existing = session.execute(select(ReviewTask).where(
            ReviewTask.contract_id == contract_id, ReviewTask.status.in_(["queued", "running"])
        )).scalar_one_or_none()
        if existing:
            if party_stance and json.loads(existing.input_snapshot)['contract']['stance'] != party_stance:
                raise ReviewError('ACTIVE_STANCE_CONFLICT', '已有不同立场的活动审查任务', 409)
            return {"task_id": existing.id, "deduplicated": True}
        if party_stance is not None and party_stance not in {'party_a','party_b'}:
            raise ValueError('立场必须为party_a或party_b')
        stance = party_stance or contract.party_stance
        if not stance:
            from app.models import User
            from app.services.party_parser import party_detection
            owner = session.get(User, created_by or contract.owner_id)
            stance = party_detection(contract.raw_text or '', owner.org_name if owner else None)['suggested_stance']
        if not stance:
            raise ReviewError('STANCE_REQUIRED', '未匹配到我方组织，请法务明确立场', 409)
        contract.party_stance = stance
        if contract.parse_status != "parsed":
            raise ReviewError("CONTRACT_NOT_PARSED", "合同未解析完成")
        if json.loads(contract.parse_warnings or "[]") and not contract.parse_confirmed_at:
            raise ReviewError("PARSE_CONFIRMATION_REQUIRED", "请先核对解析警告")
        clauses = session.execute(select(Clause).where(
            Clause.contract_id == contract_id, Clause.parent_clause_id.is_(None)
        ).order_by(Clause.seq, Clause.id)).scalars().all()
        parents = [{"id": c.id, "seq": c.seq, "original_no": c.original_no,
                    "content": c.content, "location": json.loads(c.location_json or "{}")}
                   for c in clauses]
        planned = build_check_plan(parents, selected_rules, max_clauses, missing_rules)
        plan, skipped = planned["units"], planned["not_applicable"]
        if not parents:
            raise ReviewError("NO_PARENT_CLAUSES", "合同没有可检查的父条款")
        selected_ids = {clause["id"] for clause in parents[:max_clauses]}
        sources = session.execute(select(KnowledgeChunk).where(*eligibility_conditions()).order_by(
            KnowledgeChunk.source_id, KnowledgeChunk.source_version, KnowledgeChunk.chunk_index
        )).scalars().all()
        snapshot = {
            "schema_version": 2, "max_clauses": max_clauses, "rules": selected_rules,
            "missing_rules": missing_rules, "rules_version": RULES_VERSION,
            "missing_prompt_version": MISSING_PROMPT_VERSION,
            "rule_definitions": planned["rule_definitions"], "missing_rule_definitions": planned["missing_rule_definitions"],
            # Six is the existing worst-case clause retry bound (two JSON
            # generations, three HTTP attempts each); missing checks use one.
            # Cover every planned unit instead of truncating full reviews at
            # the old test-only fifteen-attempt cap. Old snapshots stay frozen.
            "not_applicable": skipped, "call_limit": max(15, sum(
                1 if u.get("scope") == "contract" else 6 for u in plan)), "prompt_version": PROMPT_VERSION,
            "tokenizer_version": TOKENIZER_VERSION, "model": MODEL, "temperature": 0,
            "contract": {"id": contract.id, "title": contract.title, "filename": contract.filename,
                         "version": contract.contract_version, "stance": contract.party_stance,
                         "file_hash": contract.file_hash, "preamble": contract.preamble or ""},
            "all_parent_count": len(parents), "clauses": [c for c in parents if c["id"] in selected_ids],
            "plan": plan,
            "all_clauses": parents,
            "knowledge": [{"id": row.id, "source_file": row.source_file,
                           "article_no": row.article_no, "content": row.content,
                           "content_hash": row.content_hash, "source_id": row.source_id,
                           "source_version": row.source_version} for row in sources],
        }
        version = (session.scalar(select(func.max(ReviewTask.review_version)).where(
            ReviewTask.contract_id == contract_id)) or 0) + 1
        task_id = uuid.uuid4().hex
        session.add(ReviewTask(id=task_id, contract_id=contract_id, review_version=version,
                               status="queued", input_snapshot=json.dumps(snapshot, ensure_ascii=False),
                               total_items=len(plan) + len(skipped), created_by=created_by or contract.owner_id,
                               created_at=utc_now(), updated_at=utc_now()))
        contract.locked_at = contract.locked_at or utc_now()
        if _on_created is not None:
            session.flush()
            _on_created(session, task_id)
        return {"task_id": task_id, "deduplicated": False}


def refresh_progress(session, task: ReviewTask) -> None:
    rows = session.execute(select(ReviewItem.execution_status, ReviewItem.check_status).where(
        ReviewItem.review_id == task.id)).all()
    linked = session.scalar(select(Ticket.id).where(Ticket.review_task_id==task.id).limit(1)) is not None
    task.done_items = sum(execution == "done" for execution, status in rows) if linked else sum(execution == "done" and status not in {"failed", "not_applicable"} for execution, status in rows)
    task.failed_items = sum(execution == "done" and status == "failed" for execution, status in rows)
    task.updated_at = utc_now()


def task_metrics(session, task_id: str) -> Dict[str, Any]:
    logs = session.execute(select(LLMLog).where(LLMLog.review_id == task_id)).scalars().all()
    known_prompt = sum(row.prompt_tokens or 0 for row in logs)
    known_completion = sum(row.completion_tokens or 0 for row in logs)
    complete = all(row.prompt_tokens is not None and row.completion_tokens is not None for row in logs)
    duration_complete = all(row.duration_ms is not None for row in logs)
    return {"model_calls": len(logs), "prompt_tokens": known_prompt if complete else None,
            "completion_tokens": known_completion if complete else None,
            "total_tokens": known_prompt + known_completion if complete else None,
            "known_prompt_tokens": known_prompt, "known_completion_tokens": known_completion,
            "usage_complete": complete, "model_duration_ms": sum(row.duration_ms or 0 for row in logs),
            "duration_complete": duration_complete}


def elapsed_ms(task: ReviewTask) -> int:
    start = datetime.fromisoformat(task.created_at)
    end = datetime.fromisoformat(task.finished_at) if task.finished_at else datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return max(0, round((end - start).total_seconds() * 1000))


def get_task(session_factory, task_id: str) -> Dict[str, Any]:
    with session_factory() as session:
        task = session.get(ReviewTask, task_id)
        if task is None:
            raise ReviewError("TASK_NOT_FOUND", "任务不存在", 404)
        snapshot = json.loads(task.input_snapshot)
        planned_keys = {unit["item_key"] for unit in snapshot["plan"]}
        na_keys = list(session.execute(select(ReviewItem.item_key).where(
            ReviewItem.review_id == task_id, ReviewItem.check_status == "not_applicable")).scalars())
        skipped = len(na_keys)
        model_na = sum(key in planned_keys for key in na_keys)
        done_rows=session.execute(select(ReviewItem.item_key,ReviewItem.check_status).where(ReviewItem.review_id==task_id,ReviewItem.execution_status=='done')).all()
        processed = len(done_rows)
        successful=sum(status not in {'failed','not_applicable','merged'} for _,status in done_rows)
        linked=session.scalar(select(Ticket.id).where(Ticket.review_task_id==task_id).limit(1)) is not None
        from app.services.review_quality import all_clauses_insufficient
        return {"task_id": task.id, "contract_id": task.contract_id, "status": task.status,
                "stage": task.stage, "total_items": task.total_items, "done_items": processed if linked else task.done_items,
                "successful_items":successful,
                "all_clauses_insufficient":all_clauses_insufficient(session,task),
                "clause_items":sum(u.get('scope','clause')=='clause' for u in snapshot['plan']+snapshot.get('not_applicable',[])),
                "contract_items":sum(u.get('scope')=='contract' for u in snapshot['plan']),
                "failed_items": task.failed_items, "processed_items": processed,
                "not_applicable_items": skipped, "planned_model_items": len(snapshot["plan"]),
                "keyword_not_applicable_items": skipped - model_na, "model_not_applicable_items": model_na,
                "executed_items": sum(key in planned_keys for key,_ in done_rows),
                "scope": {"selected_clauses": len(snapshot["clauses"]),
                          "total_parent_clauses": snapshot["all_parent_count"],
                          "max_clauses": snapshot.get("max_clauses")},
                "pending_items": max(0, task.total_items - processed),
                "progress": processed / task.total_items if task.total_items else 0,
                "started_at": task.started_at, "finished_at": task.finished_at,
                "elapsed_ms": elapsed_ms(task),
                "error_code": task.error_code, "error_message": task.error_message,
                "metrics": task_metrics(session, task.id)}
