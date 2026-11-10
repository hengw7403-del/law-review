"""Apply contract visibility before returning any contract/task/report data."""
# 前端隐藏菜单/按钮只是体验，不能构成授权。后端必须在每个资源入口
# 再检查角色和数据可见性；无权限资源返回404，角色不足返回403。
from fastapi import HTTPException
from sqlalchemy import select
from app.models import Contract, ReviewTask, Ticket


def contract_visibility(user):
    return True if user.role in {"admin", "reviewer"} else Contract.owner_id == user.id


def ticket_visibility(user):
    return True if user.role in {"admin", "reviewer"} else Ticket.requester_id == user.id


# Compatibility for existing contract list/conversation callers.
visibility = contract_visibility


def visible_contract(session, contract_id, user):
    row = session.scalar(select(Contract).where(Contract.id == contract_id, contract_visibility(user)))
    if row is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    return row


def authorize_contract(session_factory, contract_id, user):
    with session_factory() as session:
        visible_contract(session, contract_id, user)


def authorize_task(session_factory, task_id, user):
    with session_factory() as session:
        row = session.scalar(select(ReviewTask.id).join(Contract, ReviewTask.contract_id == Contract.id).where(
            ReviewTask.id == task_id, contract_visibility(user)))
        if row is None:
            raise HTTPException(status_code=404, detail="资源不存在")
