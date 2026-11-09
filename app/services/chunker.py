"""Article-first law chunking; source headers never become a statute clause."""

from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath
from typing import List, Optional

from app.services.clause_splitter import PRIMARY
from app.services.parser import normalize_newlines, paragraph_ranges


@dataclass(frozen=True)
class SourceInfo:
    source_id: str
    name: str
    source_version: int = 1
    source_locator: Optional[str] = None
    publisher: Optional[str] = None
    effective_date: Optional[str] = None
    source_kind: str = "law"


@dataclass(frozen=True)
class LawChunk:
    source: SourceInfo
    source_file: str
    article_no: Optional[str]
    chunk_index: int
    content: str
    content_hash: str


def chunk_law(text: str, filename: str, source: SourceInfo) -> List[LawChunk]:
    if not source.source_id.strip() or not source.name.strip():
        raise ValueError("source_id 和法规名称不能为空")
    if source.source_version < 1:
        raise ValueError("source_version 必须为正整数")
    if source.source_kind not in {"law", "internal_rule"}:
        raise ValueError("不支持的来源类型")
    source_file = PurePosixPath(filename.replace("\\", "/")).name
    if not source_file:
        raise ValueError("文件名不能为空")
    normalized = normalize_newlines(text.lstrip("\ufeff"))
    headings = list(PRIMARY.finditer(normalized))
    pieces = []
    if headings:
        for i, heading in enumerate(headings):
            end = headings[i + 1].start() if i + 1 < len(headings) else len(normalized)
            pieces.append((heading.group("number"), normalized[heading.start():end].strip()))
    else:
        for start, end in paragraph_ranges(normalized):
            paragraph = normalized[start:end].strip()
            # Numbered articles stay whole; the 500-character cap is for fallback.
            for offset in range(0, len(paragraph), 500):
                content = paragraph[offset:offset + 500].strip()
                if content:
                    pieces.append((None, content))
    return [
        LawChunk(source, source_file, article, index, content,
                 hashlib.sha256(content.encode("utf-8")).hexdigest())
        for index, (article, content) in enumerate(pieces)
    ]
