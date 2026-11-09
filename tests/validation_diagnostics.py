"""Print stored-task diagnosis plus paired offline validation; no paid calls."""
import argparse
import json

from sqlalchemy import func, select

from app import db
from app.models import KnowledgeChunk, LLMLog, ReviewItem, ReviewTask
from app.services.review_planner import build_check_plan
from app.services.search import BM25Index, build_search_index, eligible_chunk_ids
from app.services.validator import RiskOutput, validate_evidence
from tests.parse_helpers import ROOT
from tests.test_validation_paths import ValidationPathTests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="4388fa77baf1403b9f9f922ceca3e37f")
    args = parser.parse_args()
    report = {"task_id": args.task, "real_model_calls": 0, "historical_items": []}
    with db.SessionLocal() as session:
        before = session.scalar(select(func.count()).select_from(LLMLog))
        task = session.get(ReviewTask, args.task)
        if task is None:
            raise ValueError("任务不存在")
        snapshot = json.loads(task.input_snapshot)
        snapshot_index = BM25Index([KnowledgeChunk(**row) for row in snapshot["knowledge"]])
        live_index = build_search_index(session)
        allowed = eligible_chunk_ids(session)
        clauses = {c["id"]: c for c in snapshot["clauses"]}
        rows = session.execute(select(ReviewItem).where(ReviewItem.review_id == args.task,
            ReviewItem.clause_id.is_not(None), ReviewItem.check_status != "not_applicable")).scalars()
        for row in rows:
            stored = json.loads(row.machine_result)
            clause = clauses[row.clause_id]
            query = clause["content"] + (" 违约金 违约责任" if row.rule_id == "liability" else "")
            worker_candidates = snapshot_index.search(query, 5, allowed)
            preview_candidates = live_index.search(query, 5, allowed)
            entry = {"clause_no": clause.get("original_no"), "rule_id": row.rule_id,
                     "stored_status": row.check_status, "stored_candidate_ids": stored.get("candidate_ids"),
                     "stored_discarded_ids": stored.get("discarded_evidence_ids"),
                     "stored_evidence_ids": [s["chunk_id"] for s in stored.get("evidence", [])],
                     "same_candidates_under_same_current_input": worker_candidates == preview_candidates,
                     "candidates": [{"id": c["chunk_id"], "article_no": c["article_no"]} for c in worker_candidates]}
            ids = stored.get("model_evidence_ids")
            entry["model_id_origin"] = "recorded" if ids is not None else "unavailable"
            if ids is None and row.check_status == "insufficient" and stored.get("evidence") == [] and stored.get("discarded_evidence_ids") == []:
                # In the legacy validator, every requested unique ID is either
                # accepted or discarded. Both empty proves the model chose [].
                ids = []
                entry["model_id_origin"] = "inferred_from_legacy_validator_invariant_not_raw_response"
            entry["model_evidence_ids"] = ids
            if ids is not None:
                placeholder = RiskOutput(applicable=True, has_issue=True, risk_level="medium", description="历史ID行为回放占位，非原始模型文案。",
                    reason="只回放ID校验；历史模型完整输出未保存。", suggestion="查看校验轨迹。", evidence_ids=ids, support_note="")
                entry["id_validation_replay"] = validate_evidence(placeholder, worker_candidates, session)["validation_trace"]
            report["historical_items"].append(entry)
            print(json.dumps({"event": "historical_id_replay", **entry}, ensure_ascii=False))
        plan = build_check_plan(snapshot.get("all_clauses", snapshot["clauses"]), snapshot["rules"])
        report["old_selected_clauses"] = len(snapshot["clauses"])
        report["new_default_selected_clauses"] = snapshot["all_parent_count"]
        report["new_default_executable_units"] = len(plan["units"])
        report["new_default_skipped_units"] = len(plan["not_applicable"])
    comparison = ValidationPathTests("test_same_preview_and_worker_inputs_keep_valid_585_and_explain_empty_or_fake_ids")
    comparison.setUp()
    try:
        comparison.test_same_preview_and_worker_inputs_keep_valid_585_and_explain_empty_or_fake_ids()
        report["offline_comparisons"] = comparison.diagnostic_examples
        for example in comparison.diagnostic_examples:
            print(json.dumps(example, ensure_ascii=False))
    finally:
        comparison.tearDown()
    with db.SessionLocal() as session:
        after = session.scalar(select(func.count()).select_from(LLMLog))
    assert before == after
    report.update({"root_llm_logs_before": before, "root_llm_logs_after": after, "paired_paths_passed": True})
    path = ROOT / "work/validation-diagnostics.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"old_selected": report["old_selected_clauses"], "new_selected": report["new_default_selected_clauses"],
                      "new_executable_units": report["new_default_executable_units"], "real_model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
