"""Role-scoped dashboard. All counts are computed on the server."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app import db
from app.models import Contract, ReviewTask, Ticket
from app.services.access import contract_visibility, ticket_visibility
from app.services.auth import current_user
from app.services.ticket_title import ticket_title

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
SHANGHAI = timezone(timedelta(hours=8))


def ticket_rows(session, user, needs_human=False):
    query = select(Ticket, Contract, ReviewTask).join(Contract, Ticket.contract_id == Contract.id).outerjoin(
        ReviewTask, Ticket.review_task_id == ReviewTask.id).where(ticket_visibility(user))
    if needs_human:
        query = query.where(Ticket.status == "needs_human")
    return [{"id": t.id, "title": ticket_title(c), "status": t.status,
             "high_risk_count": t.high_risk_count, "created_at": t.created_at,
             "review_task_id": t.review_task_id, "done_items": task.done_items if task else 0,
             "total_items": task.total_items if task else 0,
             "task_status": task.status if task else None}
            for t, c, task in session.execute(query.order_by(Ticket.created_at.desc(), Ticket.id.desc()).limit(20))]


@router.get("/summary")
def summary(user=Depends(current_user)):
    today = datetime.now(SHANGHAI).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end = (today + timedelta(days=1)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with db.SessionLocal() as s:
        ticket_filter = ticket_visibility(user)
        result = {
            "role": user.role,
            "contracts": s.scalar(select(func.count()).select_from(Contract).where(contract_visibility(user))),
            "tickets": s.scalar(select(func.count()).select_from(Ticket).where(ticket_filter)),
            "tickets_needs_human": s.scalar(select(func.count()).select_from(Ticket).where(ticket_filter, Ticket.status == "needs_human")),
            # Count ticket-associated high risks once, using the worker's saved
            # counter. Merged review items and unrelated contracts cannot leak.
            "high_risk_total": s.scalar(select(func.coalesce(func.sum(Ticket.high_risk_count), 0)).where(ticket_filter)),
            "today_done": s.scalar(select(func.count()).select_from(Ticket).where(
                ticket_filter, Ticket.status == "done", func.datetime(Ticket.updated_at) >= start,
                func.datetime(Ticket.updated_at) < end)),
            "needs_human": [], "submissions": [], "recent_activity": [],
        }
        if user.role == "reviewer":
            result["needs_human"] = ticket_rows(s, user, needs_human=True)
        elif user.role == "requester":
            result["submissions"] = ticket_rows(s, user)
        else:
            activities = [{"kind": "ticket", "id": t["id"], "title": t["title"], "created_at": t["created_at"]}
                          for t in ticket_rows(s, user)]
            activities += [{"kind": "contract", "id": c.id, "title": c.title, "created_at": c.created_at}
                           for c in s.scalars(select(Contract).where(contract_visibility(user)).order_by(Contract.created_at.desc()).limit(20))]
            def timestamp(item):
                value = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
                return value.replace(tzinfo=timezone.utc).timestamp() if value.tzinfo is None else value.timestamp()
            result["recent_activity"] = sorted(activities, key=timestamp, reverse=True)[:20]
        return result
