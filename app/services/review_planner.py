"""Applicable executable units, plus separate skipped audit records."""

from typing import Any, Dict, List, Optional, Sequence
import re


from app.services.review_rules import CLAUSE_RULES, MISSING_RULES, resolve_clause_rule
ALLOWED_RULES = tuple(CLAUSE_RULES)
MAX_TEST_CLAUSES = 200


def normalize_rules(rules: Sequence[str]) -> List[str]:
    result = list(dict.fromkeys(rule.strip() for rule in rules))
    if not result or any(rule not in ALLOWED_RULES for rule in result):
        raise ValueError("规则不能为空，支持：" + ",".join(ALLOWED_RULES))
    return result


def build_check_plan(parent_clauses, rules, max_clauses=None, missing_rules=()):
    if max_clauses is not None and (type(max_clauses) is not int or not 1 <= max_clauses <= MAX_TEST_CLAUSES):
        raise ValueError("max_clauses 仅允许 null（全部）或1–200")
    definitions = []
    for rule in rules:
        resolved = resolve_clause_rule(rule)
        if resolved["rule_id"] not in [item["rule_id"] for item in definitions]:
            definitions.append(resolved)
    if not definitions and not missing_rules:
        raise ValueError("至少选择一项规则")
    parents = [clause for clause in parent_clauses if clause.get("parent_clause_id") is None]
    parents.sort(key=lambda clause: (clause["seq"], clause["id"]))
    selected = parents[:max_clauses]
    ids = [clause["id"] for clause in selected]
    if len(ids) != len(set(ids)):
        raise ValueError("父条款 ID 重复")
    units, skipped = [], []
    for clause in selected:
        for rule in definitions:
            keywords = list(dict.fromkeys(rule["applicable_when"]["keywords"]))
            match_text = clause.get("content", "").casefold()
            for pattern in rule["applicable_when"].get("exclude_patterns", []):
                match_text = re.sub(pattern, "", match_text)
            hits = [word for word in keywords if word.casefold() in match_text]
            unit = {"item_key": "clause:{}:{}".format(clause["id"], rule["rule_id"]), "scope": "clause",
                    "clause_id": clause["id"], "rule_id": rule["rule_id"], "matched_keywords": hits}
            (units if len(hits) >= rule["applicable_when"]["min_hits"] else skipped).append(unit)
    missing_definitions = []
    for rule_id in dict.fromkeys(missing_rules):
        if rule_id not in MISSING_RULES:
            raise ValueError("不支持的缺失规则：" + str(rule_id))
        missing_definitions.append(MISSING_RULES[rule_id])
        units.append({"item_key": "contract:" + rule_id, "scope": "contract", "clause_id": None, "rule_id": rule_id})
    return {"units": units, "not_applicable": skipped, "rule_definitions": definitions,
            "missing_rule_definitions": missing_definitions}


def build_plan(parent_clauses: Sequence[Dict[str, Any]], rules, max_clauses: Optional[int] = None) -> List[Dict[str, Any]]:
    return build_check_plan(parent_clauses, rules, max_clauses)["units"]
