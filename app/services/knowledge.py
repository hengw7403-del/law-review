"""Transactional, idempotent law import and explicit source activation."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk
from app.services.chunker import SourceInfo, chunk_law


class KnowledgeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def import_law(session: Session, text: str, filename: str, source: SourceInfo) -> Dict[str, Any]:
    chunks = chunk_law(text, filename, source)
    if not chunks:
        raise KnowledgeError("EMPTY_TEXT", "法规正文为空，未导入任何记录")
    rows = [dict(
        source_id=source.source_id, source_name=source.name, source_kind=source.source_kind,
        source_file=chunk.source_file, source_locator=source.source_locator or chunk.source_file,
        publisher=source.publisher, source_version=source.source_version,
        article_no=chunk.article_no, chunk_index=chunk.chunk_index,
        content=chunk.content, content_hash=chunk.content_hash, effective_date=source.effective_date,
    ) for chunk in chunks]
    try:
        with session.begin():
            existing = session.execute(select(KnowledgeChunk).where(
                KnowledgeChunk.source_id == source.source_id,
                KnowledgeChunk.source_version == source.source_version,
            ).order_by(KnowledgeChunk.chunk_index)).scalars().all()
            if existing:
                same = len(existing) == len(rows) and all(
                    all(getattr(old, key) == value for key, value in row.items())
                    for old, row in zip(existing, rows)
                )
                if not same:
                    raise KnowledgeError(
                        "SOURCE_VERSION_CONFLICT", "该来源版本已有不同内容或元数据，请增加 --source-version，不会覆盖旧块"
                    )
                return {"source_id": source.source_id, "source_version": source.source_version,
                        "chunk_count": len(rows), "imported": 0, "skipped": len(rows)}
            session.add_all([
                KnowledgeChunk(id=uuid.uuid4().hex, verified=0, enabled=0, superseded=0, **row)
                for row in rows
            ])
    except IntegrityError as exc:
        raise KnowledgeError("IMPORT_CONFLICT", "来源版本并发导入冲突，请重试；此次写入已回滚") from exc
    return {"source_id": source.source_id, "source_version": source.source_version,
            "chunk_count": len(rows), "imported": len(rows), "skipped": 0}


def enable_source(session: Session, source_id: str, source_version: Optional[int] = None) -> Dict[str, Any]:
    if not source_id.strip():
        raise ValueError("source_id 不能为空")
    if source_version is not None and source_version < 1:
        raise ValueError("source_version 必须为正整数")
    conditions = [KnowledgeChunk.source_id == source_id]
    if source_version is not None:
        conditions.append(KnowledgeChunk.source_version == source_version)
    with session.begin():
        count = len(session.execute(select(KnowledgeChunk.id).where(*conditions)).all())
        if not count:
            raise KnowledgeError("SOURCE_NOT_FOUND", "找不到该来源或版本，未修改任何记录")
        session.execute(update(KnowledgeChunk).where(*conditions).values(
            verified=1, enabled=1,
            verified_at=datetime.now(timezone.utc).isoformat(),
        ))
    # superseded is intentionally unchanged: retired data must stay out of search.
    return {"source_id": source_id, "source_version": source_version,
            "affected_chunks": count, "verified": 1, "enabled": 1,
            "restart_required": True}
