"""Test-only worker entry point using SDK MockTransport, never real API calls."""

import argparse
import json
from pathlib import Path

import httpx
from openai import OpenAI
from sqlalchemy import select, text

from app import db
from app.models import LLMLog
from app.services.llm import LLMClient, PersistentTaskBudget
from app.services.review_worker import ReviewWorker, WorkerAlreadyRunning
from app.services.risk_judge import RiskJudge


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=0)
    parser.add_argument("--fail-seq", type=int)
    args = parser.parse_args()
    db.init_db()

    def factory(index, task_id, snapshot):
        fail_text = snapshot["clauses"][args.fail_seq - 1]["content"] if args.fail_seq else None

        def handler(request):
            body = json.loads(request.content)
            payload = json.loads(body["messages"][1]["content"])
            clause = payload.get("clause_text")
            # This independent write must succeed while the model is called.
            with db.SessionLocal.begin() as session:
                session.execute(text("UPDATE contracts SET revision=revision WHERE id=:id"), {"id": snapshot["contract"]["id"]})
                assert session.execute(select(LLMLog.id).where(
                    LLMLog.review_id == task_id, LLMLog.error_code == "CALL_IN_PROGRESS")).first()
            if fail_text is not None and clause == fail_text:
                return httpx.Response(401, json={"error": {"message": "mock single-unit failure"}})
            result = {"applicable": True, "has_issue": False, "risk_level": "none", "description": "本项未发现问题。",
                      "reason": "已按当前规则检查该条款。", "suggestion": "人工核对。",
                      "evidence_ids": [], "support_note": ""}
            if "rule" in payload:
                rid = payload["rule"]["rule_id"]
                keyword = {"acceptance": "验收", "confidentiality": "保密", "dispute_resolution": "争议",
                           "payment_default": "逾期付款", "delivery_default": "逾期交付"}[rid]
                covered = [c["clause_id"] for c in payload["clauses"] if keyword in c["snippet"]]
                result = {"assessment": "present" if covered else "missing",
                          "missing_items": [] if covered else [{"name": payload["rule"]["name"], "importance": "明确相关义务以减少争议。"}],
                          "covered_clause_ids": covered,
                          "reason": "本地 mock 覆盖结果，不代表真实判断。"}
                if rid in {"payment_default", "delivery_default"}:
                    result = {"has_gap": not bool(covered),
                              "gap_description": "摘要未检出明确的责任计算或补救方式。" if not covered else "第十条已约定逾期交付的违约责任。",
                              "impact": "责任金额与承担方式不明会增加追偿举证负担及项目恢复时间。" if not covered else "已明确迟延的违约金计算方式，有助于减少责任金额争议。",
                              "suggestion": "明确责任触发条件、计算基数和补救期限，并与统一违约条款衔接。"}
            return httpx.Response(200, json={"id": "mock", "object": "chat.completion", "created": 0,
                "model": "deepseek-chat", "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})
        sdk = OpenAI(api_key="test-only-not-a-real-key", base_url="https://api.deepseek.com", max_retries=0,
                     http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        client = LLMClient(db.SessionLocal, sdk_client=sdk,
                           budget=PersistentTaskBudget(db.SessionLocal, task_id, snapshot["call_limit"]))
        return RiskJudge(index, db.SessionLocal, client)
    try:
        ReviewWorker(db.SessionLocal, Path(db.engine.url.database).resolve().with_suffix(".worker.lock"),
                     judge_factory=factory, unit_delay=args.delay).run(once=True)
    except WorkerAlreadyRunning:
        print("WORKER_ALREADY_RUNNING", flush=True)
        return 2
    finally:
        db.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
