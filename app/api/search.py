"""Read-only retrieval over the index built during application startup."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.search import eligible_chunk_ids


router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000, strict=True)
    top_k: int = Field(default=5, ge=1, le=50, strict=True)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query 不能为空白")
        return value.strip()


class SearchResult(BaseModel):
    chunk_id: str
    source_file: Optional[str]
    article_no: Optional[str]
    content: str
    score: float
    matched_terms: List[str]


class SearchResponse(BaseModel):
    query: str
    results: List[SearchResult]


@router.post("/search", response_model=SearchResponse)
def search(body: SearchRequest, request: Request, session: Session = Depends(get_db)):
    # Revoked/superseded rows stop appearing immediately. Newly enabled rows
    # still require restart; the original corpus and raw IDF values stay fixed.
    allowed_ids = eligible_chunk_ids(session)
    results = request.app.state.search_index.search(body.query, body.top_k, allowed_ids)
    return {"query": body.query, "results": results}
