"""DeepSeek chat client: explicit retries, per-attempt metrics and DB logs."""

from dataclasses import dataclass, field
import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

from openai import APIConnectionError, APIError, APIStatusError, DefaultHttpxClient, OpenAI
from sqlalchemy import func, select, update

from app.models import LLMLog
from app.config import load_environment


BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"
TIMEOUT_SECONDS = 60.0
MAX_RETRIES = 2


class LLMConfigurationError(Exception):
    pass


class LLMBudgetExceeded(Exception):
    pass


class LLMLoggingError(Exception):
    pass


@dataclass
class CallBudget:
    limit: int
    used: int = 0
    lock: Any = field(default_factory=threading.Lock, repr=False)

    def reserve(self) -> None:
        with self.lock:
            if self.used >= self.limit:
                raise LLMBudgetExceeded("本次验收调用预算已用完")
            self.used += 1


@dataclass
class CallContext:
    request_id: str
    review_id: Optional[str] = None
    item_id: Optional[str] = None
    attempt_no: int = 0
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    active_log_id: Optional[str] = None
    purpose: str = "risk_judge"


class PersistentTaskBudget:
    """Used only under the single-worker process lock; survives restarts."""
    def __init__(self, session_factory, task_id: str, limit: int = 20) -> None:
        self.session_factory, self.task_id, self.limit = session_factory, task_id, limit

    def reserve(self) -> None:
        with self.session_factory() as session:
            used = session.scalar(select(func.count()).select_from(LLMLog).where(LLMLog.review_id == self.task_id))
        if used >= self.limit:
            raise LLMBudgetExceeded("该任务累计模型尝试已达到预算上限")


def print_metrics(record: Dict[str, Any]) -> None:
    print(json.dumps({"event": "llm_call", **record}, ensure_ascii=False), flush=True)


def aggregate_usage(attempts: List[Dict[str, Any]]) -> Dict[str, Any]:
    complete = all(item["prompt_tokens"] is not None and item["completion_tokens"] is not None for item in attempts)
    known_prompt = sum(item["prompt_tokens"] or 0 for item in attempts)
    known_completion = sum(item["completion_tokens"] or 0 for item in attempts)
    return {"prompt_tokens": known_prompt if complete else None,
            "completion_tokens": known_completion if complete else None,
            "total_tokens": known_prompt + known_completion if complete else None,
            "known_prompt_tokens": known_prompt, "known_completion_tokens": known_completion,
            "usage_complete": complete, "attempt_count": len(attempts)}


class LLMClient:
    def __init__(self, session_factory, sdk_client: Optional[Any] = None,
                 budget: Optional[CallBudget] = None, sleep: Callable[[float], None] = time.sleep,
                 emit_metrics: Callable[[Dict[str, Any]], None] = print_metrics) -> None:
        self.session_factory = session_factory
        self.sdk_client = sdk_client
        self.budget = budget
        self.sleep = sleep
        self.emit_metrics = emit_metrics
        self.lock = threading.Lock()

    def _get_client(self):
        with self.lock:
            if self.sdk_client is None:
                load_environment()
                key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
                if not key:
                    raise LLMConfigurationError("未配置 DEEPSEEK_API_KEY")
                # Disable the SDK's retries so 4xx and the attempt limit are
                # controlled once, here, rather than multiplied by two layers.
                trust_env = os.environ.get("DEEPSEEK_TRUST_ENV", "false").lower() in {"1", "true", "yes"}
                self.sdk_client = OpenAI(api_key=key, base_url=BASE_URL,
                                         timeout=TIMEOUT_SECONDS, max_retries=0,
                                         http_client=DefaultHttpxClient(trust_env=trust_env, timeout=TIMEOUT_SECONDS))
            return self.sdk_client

    def close(self) -> None:
        if self.sdk_client is not None:
            self.sdk_client.close()

    def mark_invalid_output(self, context: CallContext) -> None:
        context.attempts[-1]["error_code"] = "INVALID_MODEL_OUTPUT"
        try:
            with self.session_factory.begin() as session:
                session.execute(update(LLMLog).where(
                    LLMLog.request_id == context.request_id,
                    LLMLog.attempt_no == context.attempt_no,
                ).values(error_code="INVALID_MODEL_OUTPUT"))
        except Exception as exc:
            raise LLMLoggingError("模型格式错误分类写入失败") from exc

    def _begin_attempt(self, context: CallContext) -> None:
        if context.review_id is None:
            return
        log_id = uuid.uuid4().hex
        try:
            with self.session_factory.begin() as session:
                session.add(LLMLog(id=log_id, request_id=context.request_id,
                                   review_id=context.review_id, item_id=context.item_id,
                                   purpose=context.purpose, provider="deepseek", model=MODEL,
                                   attempt_no=context.attempt_no, error_code="CALL_IN_PROGRESS"))
        except Exception as exc:
            raise LLMLoggingError("请求前调用日志预记失败，未发出模型请求") from exc
        context.active_log_id = log_id

    def _record(self, context: CallContext, usage: Dict[str, Any], duration_ms: int,
                error_code: Optional[str], model: str) -> None:
        record = {"request_id": context.request_id, "attempt_no": context.attempt_no,
                  "requested_model": MODEL, "model": model, "prompt_tokens": usage.get("prompt_tokens"),
                  "completion_tokens": usage.get("completion_tokens"),
                  "duration_ms": duration_ms, "error_code": error_code}
        context.attempts.append(record)
        self.emit_metrics(record)
        try:
            with self.session_factory.begin() as session:
                values = dict(request_id=context.request_id,
                                   review_id=context.review_id, item_id=context.item_id,
                                   purpose=context.purpose, provider="deepseek", model=model,
                                   prompt_tokens=usage.get("prompt_tokens"),
                                   completion_tokens=usage.get("completion_tokens"),
                                   duration_ms=duration_ms, attempt_no=context.attempt_no,
                                   error_code=error_code)
                if context.active_log_id is not None:
                    row = session.get(LLMLog, context.active_log_id)
                    if row is None:
                        raise LLMLoggingError("预记的调用日志丢失")
                    for name, value in values.items():
                        setattr(row, name, value)
                else:
                    session.add(LLMLog(id=uuid.uuid4().hex, **values))
            context.active_log_id = None
        except Exception as exc:
            # A logging failure must never trigger another billed model call.
            raise LLMLoggingError("模型尝试已发生，但调用日志写入失败") from exc

    def complete(self, messages: List[Dict[str, str]], context: CallContext,
                 max_retries: Optional[int] = None) -> Tuple[str, Dict[str, Any], int]:
        client = self._get_client()
        retries = MAX_RETRIES if max_retries is None else max_retries
        if not 0 <= retries <= MAX_RETRIES:
            raise ValueError("Invalid retry limit")
        for retry in range(retries + 1):
            if self.budget is not None:
                self.budget.reserve()
            context.attempt_no += 1
            self._begin_attempt(context)
            started = time.perf_counter()
            error = None
            usage = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
            content = ""
            model = MODEL
            try:
                response = client.chat.completions.create(
                    model=MODEL, messages=messages, temperature=0,
                    response_format={"type": "json_object"}, max_tokens=1536,
                    stream=False,
                )
                model = getattr(response, "model", None) or MODEL
                raw_usage = getattr(response, "usage", None)
                if raw_usage is not None:
                    for name in usage:
                        usage[name] = raw_usage.get(name) if isinstance(raw_usage, dict) else getattr(raw_usage, name, None)
                choices = getattr(response, "choices", None) or []
                if choices:
                    value = getattr(choices[0].message, "content", None)
                    content = value if isinstance(value, str) else ""
            except APIError as exc:
                error = exc
            duration_ms = max(0, round((time.perf_counter() - started) * 1000))
            error_code = None
            if isinstance(error, APIStatusError):
                error_code = "HTTP_{}".format(error.status_code)
            elif error is not None:
                error_code = type(error).__name__
            self._record(context, usage, duration_ms, error_code, model)
            if error is not None:
                retryable = (isinstance(error, APIConnectionError) or
                             isinstance(error, APIStatusError) and error.status_code >= 500)
                if retryable and retry < retries:
                    self.sleep(float(2 ** retry))
                    continue
                raise error
            return content, usage, duration_ms
        raise AssertionError("Unreachable retry state")
