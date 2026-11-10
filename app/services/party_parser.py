"""Deterministic party extraction and organization matching, without an LLM."""
import re
import unicodedata
from typing import Dict, Optional


SPACE = r"[^\S\r\n]*"
ALIASES = {
    "party_a": [r"甲" + SPACE + r"方(?:\s*[（(][^）)\r\n]*[）)])?",
                r"(?:委托方|卖方)\s*[（(]甲" + SPACE + r"方[）)]", r"卖方"],
    "party_b": [r"乙" + SPACE + r"方(?:\s*[（(][^）)\r\n]*[）)])?",
                r"(?:受托方|买方)\s*[（(]乙" + SPACE + r"方[）)]", r"买方"],
}


def parse_parties(text: str) -> Dict[str, Optional[str]]:
    result = {"party_a_name": None, "party_b_name": None}
    # Explicit A/B labels win over bare seller/buyer aliases. The latter use
    # seller=A, buyer=B as a display convention, always requiring confirmation.
    lines = text.splitlines()[:30]
    for side, patterns in ALIASES.items():
        for pattern in patterns:
            names = set()
            regex = re.compile(r"(?:^|[;；,，\t])\s*(?:" + pattern + r")\s*[:：]\s*(.*)")
            for line in lines:
                for match in regex.finditer(line):
                    raw = match.group(1)
                    name = []
                    for char in raw:
                        if unicodedata.category(char).startswith("P"):
                            break
                        name.append(char)
                    value = "".join(name).strip()
                    if 2 <= len(value) <= 50:
                        names.add(value)
            if names:
                result[side + "_name"] = next(iter(names)) if len(names) == 1 else None
                break
    return result


def normalize_name(value: Optional[str]) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", value or "")
                   if not c.isspace() and not unicodedata.category(c).startswith("P")).casefold()


def suggest_stance(org_name: Optional[str], parties: Dict[str, Optional[str]]) -> Optional[str]:
    org = normalize_name(org_name)
    if not org:
        return None
    matches = []
    for side in ("party_a", "party_b"):
        name = normalize_name(parties.get(side + "_name"))
        if name and (org in name or name in org):
            matches.append(side)
    return matches[0] if len(matches) == 1 else None


def party_detection(text: str, org_name: Optional[str] = None) -> dict:
    parties = parse_parties(text)
    stance = suggest_stance(org_name, parties)
    return {"detected_parties": parties, "suggested_stance": stance,
            "stance_reason": ("甲方" if stance == "party_a" else "乙方") + "名称与我方组织匹配"
            if stance else "未匹配到我方组织，请手动选择"}
