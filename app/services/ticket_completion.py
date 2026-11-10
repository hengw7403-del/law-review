"""Terminal task -> linked tickets; participates in the caller's transaction."""
from sqlalchemy import select,func
from app.models import Ticket,ReviewItem
from app.services.review_tasks import utc_now

def complete_tickets(session, task):
    rows=list(session.scalars(select(Ticket).where(Ticket.review_task_id==task.id,Ticket.status=='reviewing')))
    if not rows:return
    high_count=session.scalar(select(func.count()).select_from(ReviewItem).where(
        ReviewItem.review_id==task.id,ReviewItem.execution_status=='done',ReviewItem.check_status=='supported',ReviewItem.risk_level=='high')) or 0
    for row in rows:
        row.high_risk_count=high_count;row.updated_at=utc_now()
        if task.status=='failed':
            row.status='pending';row.review_error=task.error_message or '审查失败，请重试';continue
        row.status='needs_human' if high_count else 'done'
        row.needs_human_reason='发现 {} 处高风险，需要人工确认。'.format(high_count) if high_count else None
        row.review_error=None
