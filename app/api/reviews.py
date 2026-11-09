"""Task creation, progress polling and Markdown export for local development."""

from typing import List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app import db
from app.services.report import generate_report
from app.services.review_tasks import ReviewError, create_review, get_task
from app.services.auth import current_user
from app.services.access import authorize_contract, authorize_task
from app.services.results import review_items


router = APIRouter(tags=["reviews"])


@router.get("/reviews/{task_id}/items")
def items(task_id: str, user=Depends(current_user)):
    authorize_task(db.SessionLocal, task_id, user)
    return review_items(db.SessionLocal, task_id)


class ReviewRequest(BaseModel):
    max_clauses: Optional[int] = Field(default=None, ge=1, le=200, strict=True)
    rules: List[str] = Field(default_factory=lambda: ["liability", "payment", "dispute_resolution"], max_length=3)
    missing_rules: List[str] = Field(default_factory=list, max_length=5)


def error_response(error: ReviewError):
    return JSONResponse(status_code=error.status_code, content={"error": {"code": error.code, "message": error.message}})


@router.post("/contracts/{contract_id}/review")
def start_review(contract_id: str, body: Optional[ReviewRequest] = None, user=Depends(current_user)):
    body = body or ReviewRequest()
    try:
        authorize_contract(db.SessionLocal, contract_id, user)
        result = create_review(db.SessionLocal, contract_id, body.max_clauses, body.rules, body.missing_rules, created_by=user.id)
        return JSONResponse(status_code=202, content=result)
    except ReviewError as error:
        return error_response(error)
    except ValueError as error:
        return JSONResponse(status_code=422, content={"error": {"code": "INVALID_REVIEW_OPTIONS", "message": str(error)}})


@router.get("/tasks/{task_id}")
def task_progress(task_id: str, user=Depends(current_user)):
    try:
        authorize_task(db.SessionLocal, task_id, user)
        return get_task(db.SessionLocal, task_id)
    except ReviewError as error:
        return error_response(error)


@router.get("/reviews/{task_id}/report")
def report(task_id: str, user=Depends(current_user)):
    try:
        authorize_task(db.SessionLocal, task_id, user)
        content = generate_report(db.SessionLocal, task_id)
        return Response(content=content, media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": 'inline; filename="review-{}.md"'.format(task_id)})
    except ReviewError as error:
        return error_response(error)
