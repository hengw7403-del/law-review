"""Mock acceptance by default; --live explicitly allows <=20 real attempts."""

import argparse
import json
import os
from pathlib import Path
import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.api.judge import router
from app.config import load_environment
from app.db import Base, create_db_engine
from app.models import KnowledgeChunk
from app.services.chunker import SourceInfo
from app.services.knowledge import enable_source, import_law
from app.services.llm import CallBudget, LLMClient
from app.services.risk_judge import RiskJudge
from app.services.search import build_search_index
from tests.parse_helpers import ROOT, PreviewServer


def mock_acceptance():
    with tempfile.TemporaryDirectory() as directory:
        engine = create_db_engine("sqlite:///" + (Path(directory) / "mock.db").as_posix())
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        sdk = None
        try:
            for filename, source_id in (("civil_code_liability.txt", "liability"), ("civil_code_termination.txt", "termination")):
                with Session() as session:
                    import_law(session, (ROOT / "samples/laws" / filename).read_text(encoding="utf-8"), filename, SourceInfo(source_id, source_id))
                with Session() as session:
                    enable_source(session, source_id)
            with Session() as session:
                index = build_search_index(session)
                valid_id = session.execute(select(KnowledgeChunk.id).where(KnowledgeChunk.article_no == "第五百八十五条")).scalar_one()
            mode = {"fake": False}

            def handler(request):
                sent = json.loads(request.content)
                data_export = json.loads(sent["messages"][1]["content"])["rule_id"] == "data_export"
                payload = {"applicable": True, "has_issue": True, "risk_level": "medium", "description": "我方逾期付款责任采用同一口径且未设累计上限。",
                           "reason": "无上限责任存在持续累积的商业风险，不直接等同违法。",
                           "suggestion": "协商我方累计责任上限与计算基础。",
                           "evidence_ids": ["k_fake_not_in_candidates" if mode["fake"] else valid_id],
                           "support_note": "所列候选支持违约金可调整的问题，具体合同上限仍需协商。"}
                if data_export:
                    payload["evidence_ids"] = []
                    payload["support_note"] = "候选只有一般合同依据，不足判断专门的数据出境要求。"
                return httpx.Response(200, json={"id": "mock-only", "object": "chat.completion", "created": 0,
                    "model": "deepseek-chat", "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168}})

            sdk = OpenAI(api_key="mock-only-not-a-real-key", base_url="https://api.deepseek.com", max_retries=0,
                         timeout=60, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
            llm = LLMClient(Session, sdk_client=sdk, budget=CallBudget(20))
            application = FastAPI()
            application.include_router(router)
            application.state.risk_judge = RiskJudge(index, Session, llm)
            cases = {}
            with TestClient(application) as client:
                for name, filename, rule in (("uncapped", "liability_uncapped.txt", "liability"), ("data_export", "data_export.txt", "data_export")):
                    result = client.post("/judge/preview", json={"clause_text": (ROOT / "samples/judge" / filename).read_text(encoding="utf-8"), "stance": "party_a", "rule_id": rule})
                    cases[name] = {"http_status": result.status_code, "result": result.json()}
                mode["fake"] = True
                result = client.post("/judge/preview", json={"clause_text": (ROOT / "samples/judge/liability_uncapped.txt").read_text(encoding="utf-8"), "stance": "party_a", "rule_id": "liability"})
                cases["fake_evidence"] = {"http_status": result.status_code, "result": result.json()}
            assert cases["uncapped"]["result"]["check_status"] == "supported"
            assert cases["uncapped"]["result"]["risk_level"] == "medium"
            assert cases["data_export"]["result"]["check_status"] == "insufficient"
            assert cases["fake_evidence"]["result"]["risk_level"] == "unknown"
            assert cases["fake_evidence"]["result"]["discarded_evidence_ids"] == ["k_fake_not_in_candidates"]
            return cases
        finally:
            if sdk is not None:
                sdk.close()
            engine.dispose()


def live_acceptance(remaining: int):
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return {"status": "blocked", "reason": "MISSING_API_KEY", "real_attempts": 0, "cases": {}}
    if remaining <= 0:
        return {"status": "blocked", "reason": "CALL_BUDGET_EXCEEDED", "real_attempts": 0, "cases": {}}
    server = PreviewServer()
    server.start(ROOT / "law.db", {"LLM_TEST_CALL_LIMIT": str(remaining)})
    cases = {}
    try:
        for name, filename, rule in (("uncapped", "liability_uncapped.txt", "liability"), ("data_export", "data_export.txt", "data_export")):
            status, result = server.post_json("/judge/preview", {
                "clause_text": (ROOT / "samples/judge" / filename).read_text(encoding="utf-8"),
                "stance": "party_a", "rule_id": rule,
            }, timeout=400)
            cases[name] = {"http_status": status, "result": result}
            if result.get("error_code"):
                # Do not spend another request on a known configuration failure.
                break
        calls = [call for case in cases.values() for call in case["result"].get("calls", [])]
        assert len(calls) <= remaining
        first = cases.get("uncapped", {}).get("result", {})
        second = cases.get("data_export", {}).get("result", {})
        passed = (first.get("check_status") == "supported" and first.get("risk_level") in ("high", "medium")
                  and any(term in first.get("description", "") for term in ("上限", "过高"))
                  and second.get("check_status") == "insufficient" and second.get("risk_level") == "unknown")
        return {"status": "passed" if passed else "not_passed", "real_attempts": len(calls), "cases": cases}
    finally:
        server.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="显式调用 DeepSeek；本次进程总尝试不超过 20")
    parser.add_argument("--render-only", action="store_true", help="只从已存 JSON 重建报告，不发起任何模型调用")
    args = parser.parse_args()
    folder = ROOT / "work"
    folder.mkdir(exist_ok=True)
    previous_path = folder / "judge-acceptance-results.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else {}
    history = previous.get("live_history")
    if history is None:
        last = previous.get("live", {})
        history = [last] if last.get("real_attempts", 0) else []
    used_before = sum(run["real_attempts"] for run in history)
    if args.render_only:
        mock, live = previous["mock"], previous["live"]
        total_used = used_before
    else:
        # This path is always local and cannot make outbound model requests.
        mock = mock_acceptance()
        if args.live:
            load_environment()
        live = live_acceptance(20 - used_before) if args.live else {"status": "not_run", "real_attempts": 0, "cases": {}}
        if live["real_attempts"]:
            history.append(live)
        total_used = sum(run["real_attempts"] for run in history)
        assert total_used <= 20
        data = {"mock": mock, "live": live, "live_history": history, "real_attempts_total": total_used}
        (folder / "judge-acceptance-results.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 单条款 LLM 与依据校验 · 验收记录", "", "环境：Python 3.9.6；openai SDK 1.109.1。", "",
             "模型配置固定 deepseek-chat / https://api.deepseek.com / temperature=0；网络超时 60 秒，最多两次网络重试。", "",
             "合同 A 原文已有 10% 上限，未修改；验收使用 samples/judge/liability_uncapped.txt 派生变体。", "",
             "## 本地 mock（没有真实 API 调用）", "", "| 用例 | check_status | risk_level | 模拟尝试 |", "|---|---|---|---|"]
    for name, case in mock.items():
        result = case["result"]
        lines.append("| {} | {} | {} | {} |".format(name, result["check_status"], result["risk_level"], len(result["calls"])))
    lines += ["", "mock 的 token 数来自合成响应，耗时为本地处理时间；不能据此声称真实模型效果或实际 API 消耗。", "",
              "## 真实 DeepSeek 验收", "", "最新状态：`{}`；本次真实尝试：{}；累计真实尝试：{} / 20。".format(live["status"], live["real_attempts"], total_used), ""]
    if live["status"] in {"not_run", "blocked"}:
        lines += ["真实模型尚未验收。当前未提供可读的 DEEPSEEK_API_KEY；不能以 mock 结果替代真实模型效果。", ""]
    lines += ["| 模式/用例 | 次数 | prompt_tokens | completion_tokens | duration_ms | error_code |", "|---|---|---|---|---|---|"]
    details = []
    modes = [("mock", mock)] + [("live#{}".format(i + 1), run["cases"]) for i, run in enumerate(history)]
    for kind, cases in modes:
        for name, case in cases.items():
            result = case["result"]
            for call in result.get("calls", []):
                lines.append("| {}/{} | {} | {} | {} | {} | {} |".format(kind, name, call["attempt_no"], call["prompt_tokens"], call["completion_tokens"], call["duration_ms"], call["error_code"]))
            if kind.startswith("live"):
                details += ["", "### " + kind + "/" + name, "", "```json", json.dumps(result, ensure_ascii=False, indent=2), "```", ""]
    response_models = sorted({call["model"] for run in history for case in run["cases"].values()
                              for call in case["result"].get("calls", []) if not call["error_code"]})
    lines += ["", "请求 model 始终为 deepseek-chat；成功响应实际回报 model：`{}`。这不是客户端自动替换模型。".format(
        ", ".join(response_models) or "尚无成功响应"), ""]
    lines += details
    lines += ["", "引用由服务端从候选 ID 与数据库交叉核对并填充，模型不提供引文原文。缺依据降级后不保留确定风险结论。", "",
              "预览不创建合同或整份审查任务；仅记录 llm_logs，通过 request_id 关联。review_id 允许为空，迁移保留原日志、外键和索引。", "",
              "格式错误最多重新生成一次；单次逻辑生成的网络重试最多两次，所以单条预览最坏 6 次网络尝试。SDK 内部重试关闭。所有 4xx（含 429）不重试。", "",
              "技术校验通过不等于法律结论已经由人工核对；support_note 仍是模型给出的支持关系说明。", ""]
    report = "\n".join(lines)
    (folder / "judge-acceptance.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
