"""Compose the stateless preview pipeline independently of HTTP routing."""

from typing import Any, Dict

from app.services.clause_splitter import split_document
from app.services.parse_quality import assess_quality
from app.services.parser import ParseError, display_filename, parse_document


def build_preview(filename: str, data: bytes) -> Dict[str, Any]:
    text, page_map = parse_document(filename, data)
    document = split_document(text, page_map)
    clauses = document["clauses"]
    ok, error, warnings = assess_quality(text, len(clauses))
    if not ok:
        messages = {
            "EMPTY_TEXT": "正文不足 50 字符，请提供可复制文本的合同；扫描件暂不支持 OCR",
            "GARBLED": "提取文本的替换字符比例过高，请检查文件内容或编码",
        }
        raise ParseError(error or "EMPTY_TEXT", messages[error or "EMPTY_TEXT"])
    return {
        "filename": display_filename(filename),
        "preamble": document["preamble"],
        "text_length": len(text),
        "clause_count": len(clauses),
        "warnings": warnings,
        "clauses": clauses,
    }
