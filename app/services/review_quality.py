from sqlalchemy import select
from app.models import ReviewItem

def all_clauses_insufficient(session,task):
    if task.status not in {'completed','partial_failed'}:return False
    rows=session.execute(select(ReviewItem.execution_status,ReviewItem.check_status).where(
        ReviewItem.review_id==task.id,ReviewItem.clause_id.is_not(None))).all()
    relevant=[(execution,status) for execution,status in rows if status not in {'not_applicable','merged'}]
    return bool(relevant) and all(execution=='done' and status=='insufficient' for execution,status in relevant)
