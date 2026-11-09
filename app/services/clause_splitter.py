"""Heading-first clause splitting with exact offsets into extracted text."""

import re
from typing import List, Optional, TypedDict

from app.services.parser import TextSpan, paragraph_ranges


NUMERALS = "零〇一二三四五六七八九十百千万两壹贰叁肆伍陆柒捌玖拾佰仟0-9０-９"
PRIMARY = re.compile(
    r"^[ \t\u3000]*(?P<number>第[" + NUMERALS + r"]+条(?:之[" + NUMERALS + r"]+)?)",
    re.MULTILINE,
)
SECONDARY = re.compile(
    r"^[ \t\u3000]*(?:"
    r"(?P<simple>[" + NUMERALS + r"]+、|[0-9]+[.．](?![0-9]))"
    r"|(?P<hierarchical>[0-9]+(?:\.[0-9]+)+[.、．]?)(?![0-9.])"
    r"|(?P<bracket>[（(][" + NUMERALS + r"]+[）)]))",
    re.MULTILINE,
)


class ClausePreview(TypedDict):
    seq: int
    original_no: Optional[str]
    content: str
    char_start: int
    char_end: int
    location: str


def locate(start: int, end: int, page_map: List[TextSpan]) -> str:
    locations = []  # type: List[str]
    for span in page_map:
        if span["char_start"] < end and span["char_end"] > start:
            if span["location"] not in locations:
                locations.append(span["location"])
    if not locations:
        return "unknown"
    if len(locations) == 1:
        return locations[0]
    return "{}-{}".format(locations[0], locations[-1])


def split_clauses(text: str, page_map: Optional[List[TextSpan]] = None) -> List[ClausePreview]:
    mapping = page_map if page_map is not None else [
        {"char_start": start, "char_end": end, "location": "para{}".format(number)}
        for number, (start, end) in enumerate(paragraph_ranges(text), 1)
    ]
    headings = list(PRIMARY.finditer(text))
    numbers = []  # type: List[Optional[str]]
    if headings:
        numbers = [match.group("number") for match in headings]
    else:
        candidates = list(SECONDARY.finditer(text))
        # Outer-level mixed Chinese/Arabic markers win over their child lists.
        simple = [match for match in candidates if match.group("simple")]
        hierarchical = [match for match in candidates if match.group("hierarchical")]
        if simple:
            headings = simple
        elif hierarchical:
            minimum_depth = min(match.group("hierarchical").rstrip(".、．").count(".") for match in hierarchical)
            headings = [match for match in hierarchical if
                        match.group("hierarchical").rstrip(".、．").count(".") == minimum_depth]
        else:
            headings = [match for match in candidates if match.group("bracket")]
        numbers = [match.group(match.lastgroup) for match in headings]

    if headings:
        # Exclude preamble, preserving offsets into the full extracted text.
        starts = [match.start() for match in headings]
        ranges = list(zip(starts, starts[1:] + [len(text)]))
    else:
        ranges = paragraph_ranges(text)
        numbers = [None] * len(ranges)

    clauses = []  # type: List[ClausePreview]
    for (start, end), original_no in zip(ranges, numbers):
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start == end:
            continue
        clauses.append({
            "seq": len(clauses) + 1,
            "original_no": original_no,
            "content": text[start:end],
            "char_start": start,
            "char_end": end,
            "location": locate(start, end, mapping),
        })
    return clauses


def split_document(text: str, page_map: Optional[List[TextSpan]] = None):
    clauses = split_clauses(text, page_map)
    preamble = text[:clauses[0]["char_start"]].strip() if clauses and clauses[0]["original_no"] else ""
    return {"preamble": preamble, "clauses": clauses}
