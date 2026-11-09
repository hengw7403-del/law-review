"""Apply contract visibility before returning any contract/task/report data."""
from fastapi import HTTPException
from sqlalchemy import or_, select
from app.models import Contract, ReviewTask


def visibility(user):
    return True if user.role == "admin" else or_(Contract.owner_id == user.id, Contract.reviewer_id == user.id)


def visible_contract(session, contract_id, user):
    row = session.scalar(select(Contract).where(Contract.id == contract_id, visibility(user)))
    if row is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    return row


def authorize_contract(session_factory, contract_id, user):
    with session_factory() as session:
        visible_contract(session, contract_id, user)


def authorize_task(session_factory, task_id, user):
    with session_factory() as session:
        row = session.scalar(select(ReviewTask.id).join(Contract, ReviewTask.contract_id == Contract.id).where(
            ReviewTask.id == task_id, visibility(user)))
        if row is None:
            raise HTTPException(status_code=404, detail="资源不存在")
