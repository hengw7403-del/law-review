from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from app import db
from app.models import Contract, User
from app.services.auth import public_user, require_admin

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/users")
def users():
    with db.SessionLocal() as session:
        return {"users": [{**public_user(u), "active": bool(u.active)} for u in session.execute(select(User).order_by(User.username)).scalars()]}


class ReviewerAssignment(BaseModel):
    reviewer_id: str


@router.patch("/contracts/{contract_id}/reviewer")
def assign_reviewer(contract_id: str, body: ReviewerAssignment):
    with db.SessionLocal.begin() as session:
        contract = session.get(Contract, contract_id)
        reviewer = session.get(User, body.reviewer_id)
        if not contract or not reviewer or reviewer.role != "reviewer" or reviewer.active != 1:
            raise HTTPException(status_code=404, detail="资源不存在")
        contract.reviewer_id = reviewer.id
        return {"contract_id": contract.id, "reviewer_id": reviewer.id}
