"""Read-only BM25 snapshots, shared Chinese tokenization and raw scores."""

from dataclasses import dataclass
from functools import lru_cache
import re
import os
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set

import jieba
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import PROJECT_ROOT
from app.models import KnowledgeChunk
from app.services.clause_splitter import NUMERALS


TOKENIZER_VERSION = "jieba-search-hmm-off-domain-article-preserving-v1"
ARTICLE = re.compile(r"第[" + NUMERALS + r"]+条(?:之[" + NUMERALS + r"]+)?")
WORD = re.compile(r"[\u4e00-\u9fffa-z0-9]+")
STOP_WORDS = frozenset({"的", "了", "吗", "呢", "啊", "请问", "怎么", "怎么办", "怎么样", "如何", "我", "你"})
DOMAIN_WORDS = ("违约金", "过高", "过低")


@lru_cache(maxsize=1)
def _get_tokenizer() -> jieba.Tokenizer:
    # jieba's default cache location is not writable on every Windows machine.
    cache_directory = Path(os.environ.get("JIEBA_CACHE_DIR", str(PROJECT_ROOT / "work" / "jieba-cache")))
    cache_directory.mkdir(parents=True, exist_ok=True)
    tokenizer = jieba.Tokenizer()
    tokenizer.tmp_dir = str(cache_directory)
    tokenizer.initialize()
    for word in DOMAIN_WORDS:
        tokenizer.add_word(word, freq=100000)
    return tokenizer


def tokenize(text: str) -> List[str]:
    tokenizer = _get_tokenizer()
    tokens = []  # type: List[str]

    def ordinary(fragment: str) -> None:
        for token in tokenizer.cut_for_search(fragment.lower(), HMM=False):
            token = token.strip()
            if WORD.fullmatch(token) and token not in STOP_WORDS:
                tokens.append(token)

    start = 0
    for match in ARTICLE.finditer(text):
        ordinary(text[start:match.start()])
        tokens.append(match.group())
        start = match.end()
    ordinary(text[start:])
    return tokens


def eligibility_conditions():
    return (KnowledgeChunk.enabled == 1, KnowledgeChunk.verified == 1, KnowledgeChunk.superseded == 0)


def eligible_chunk_ids(session: Session) -> Set[str]:
    return set(session.execute(select(KnowledgeChunk.id).where(*eligibility_conditions())).scalars())


@dataclass(frozen=True)
class SearchDocument:
    chunk_id: str
    source_file: Optional[str]
    article_no: Optional[str]
    content: str
    terms: FrozenSet[str]


class BM25Index:
    """An immutable document snapshot; no automatic or incremental rebuilds."""

    def __init__(self, rows: List[KnowledgeChunk]) -> None:
        documents = []  # type: List[SearchDocument]
        corpus = []  # type: List[List[str]]
        for row in rows:
            terms = tokenize(row.content)
            if not terms:
                continue
            documents.append(SearchDocument(row.id, row.source_file, row.article_no, row.content, frozenset(terms)))
            corpus.append(terms)
        self.documents = tuple(documents)
        # Empty knowledge bases and punctuation-only texts must not cause /0.
        self.bm25 = BM25Okapi(corpus) if corpus else None

    def search(self, query: str, top_k: int = 5, allowed_ids: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
        if not 1 <= top_k <= 50:
            raise ValueError("top_k 必须在 1 到 50 之间")
        if self.bm25 is None:
            return []
        # Repeated query terms do not get arbitrary extra weight.
        terms = list(dict.fromkeys(tokenize(query)))
        if not terms:
            return []
        scores = self.bm25.get_scores(terms)
        results = []
        for index, document in enumerate(self.documents):
            if allowed_ids is not None and document.chunk_id not in allowed_ids:
                continue
            matched = [term for term in terms if term in document.terms]
            # This is a lexical match check, not a relevance score threshold.
            # Matched documents with zero or negative raw scores are retained.
            if not matched:
                continue
            results.append({
                "chunk_id": document.chunk_id, "source_file": document.source_file,
                "article_no": document.article_no, "content": document.content,
                "score": float(scores[index]), "matched_terms": matched,
            })
        # Python's stable sort preserves the source/version/chunk order on ties.
        results.sort(key=lambda item: -item["score"])
        return results[:top_k]


def build_search_index(session: Session) -> BM25Index:
    rows = session.execute(select(KnowledgeChunk).where(*eligibility_conditions()).order_by(
        KnowledgeChunk.source_id, KnowledgeChunk.source_version, KnowledgeChunk.chunk_index,
    )).scalars().all()
    return BM25Index(rows)
