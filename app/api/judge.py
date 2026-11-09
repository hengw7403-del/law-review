"""Single-clause LLM preview, with deterministic orchestration only."""

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator


router = APIRouter(prefix="/judge", tags=["judge"])


class JudgeRequest(BaseModel):
    clause_text: str = Field(min_length=1, max_length=20000, strict=True)
    stance: Literal["party_a", "party_b"]
    rule_id: str = Field(min_length=1, max_length=100, strict=True)

    @field_validator("clause_text", "rule_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("字段不能为空白")
        return value.strip()


@router.post("/preview")
def preview(body: JudgeRequest, request: Request):
    result = request.app.state.risk_judge.judge_clause(body.clause_text, body.stance, body.rule_id)
    code = result.get("error_code", "")
    if code in {"MISSING_API_KEY", "CALL_BUDGET_EXCEEDED", "LLM_LOGGING_FAILED"}:
        return JSONResponse(status_code=503, content=result)
    if code and code != "INVALID_MODEL_OUTPUT":
        return JSONResponse(status_code=502, content=result)
    return result
