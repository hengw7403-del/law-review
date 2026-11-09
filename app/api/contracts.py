import json
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app import db
from app.models import Clause, Contract, ReviewTask
from app.services.access import visibility, visible_contract
from app.services.auth import current_user
from app.services.contracts import create_contract
from app.services.parser import MAX_FILE_SIZE, ParseError

router = APIRouter(prefix="/contracts", tags=["contracts"])


def contract_summary(contract, clause_count=0):
    return {"contract_id": contract.id, "title": contract.title, "filename": contract.filename,
            "owner_id": contract.owner_id, "reviewer_id": contract.reviewer_id,
            "version": contract.contract_version, "parse_status": contract.parse_status,
            "party_stance": contract.party_stance, "created_at": contract.created_at,
            "clause_count": clause_count}


def latest_task(session, contract_id):
    task = session.scalar(select(ReviewTask).where(ReviewTask.contract_id == contract_id).order_by(
        ReviewTask.review_version.desc()).limit(1))
    return {"task_id": task.id, "status": task.status} if task else None


@router.get("")
def list_contracts(user=Depends(current_user)):
    with db.SessionLocal() as session:
        rows = session.execute(select(Contract, func.count(Clause.id)).outerjoin(Clause, Clause.contract_id == Contract.id).where(
            visibility(user)).group_by(Contract.id).order_by(Contract.created_at.desc(), Contract.id.desc())).all()
        return {"contracts": [{**contract_summary(row, count), "latest_task": latest_task(session, row.id)} for row, count in rows]}


@router.post("", status_code=201)
async def upload_contract(file: UploadFile = File(...), stance: Literal["party_a", "party_b"] = Form("party_a"),
                          title: Optional[str] = Form(None, max_length=200), user=Depends(current_user)):
    try:
        data = bytearray()
        while True:
            chunk = await file.read(64 * 1024)
            if not chunk:
                break
            if len(data) + len(chunk) > MAX_FILE_SIZE:
                raise ParseError("FILE_TOO_LARGE", "单文件不得超过20MB", 413)
            data.extend(chunk)
        return await run_in_threadpool(create_contract, db.SessionLocal, user, file.filename or "", bytes(data), stance, title)
    except ParseError as error:
        return JSONResponse(status_code=error.status_code, content={"error": {"code": error.code, "message": error.message}})
    finally:
        await file.close()


@router.get("/{contract_id}")
def read_contract(contract_id: str, user=Depends(current_user)):
    with db.SessionLocal() as session:
        contract = visible_contract(session, contract_id, user)
        clauses = session.execute(select(Clause).where(Clause.contract_id == contract.id).order_by(Clause.seq)).scalars()
        count = session.scalar(select(func.count()).select_from(Clause).where(Clause.contract_id == contract.id))
        return {**contract_summary(contract, count), "raw_text": contract.raw_text, "preamble": contract.preamble,
                "parse_error": contract.parse_error, "parse_warnings": json.loads(contract.parse_warnings or "[]"),
                "latest_task": latest_task(session, contract.id),
                "clauses": [{"clause_id": c.id, "seq": c.seq, "original_no": c.original_no,
                             "content": c.content, "location": json.loads(c.location_json)} for c in clauses]}


@router.get("/{contract_id}/clauses")
def list_clauses(contract_id: str, page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100),
                 user=Depends(current_user)):
    with db.SessionLocal() as session:
        visible_contract(session, contract_id, user)
        total = session.scalar(select(func.count()).select_from(Clause).where(Clause.contract_id == contract_id))
        rows = session.execute(select(Clause).where(Clause.contract_id == contract_id).order_by(Clause.seq).offset(
            (page - 1) * page_size).limit(page_size)).scalars()
        return {"page": page, "page_size": page_size, "total": total,
                "clauses": [{"clause_id": c.id, "seq": c.seq, "original_no": c.original_no, "content": c.content,
                             "location": json.loads(c.location_json)} for c in rows]}
