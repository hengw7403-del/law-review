"""Simple, explicit parsing-quality rules; no model calls."""

from typing import List, Optional, Tuple


def assess_quality(text: str, clause_count: int) -> Tuple[bool, Optional[str], List[str]]:
    meaningful_text = text.strip()
    if len(meaningful_text) < 50:
        return False, "EMPTY_TEXT", []
    if meaningful_text.count("\ufffd") / len(meaningful_text) > 0.05:
        return False, "GARBLED", []
    warnings = ["TOO_FEW_CLAUSES"] if clause_count < 3 else []
    return True, None, warnings
