from __future__ import annotations

from dataclasses import dataclass
from typing import Any

HIGH_RISK_SCORE_THRESHOLD = 6
_UNTESTABLE_VALUES = frozenset({"untestable"})
_LOW_OR_MEDIUM_IMPACTS = frozenset({"low", "medium"})
_REQUIRED_RISK_FIELDS = ("likelihood", "testability", "score", "response")


@dataclass(frozen=True)
class _AssumptionEntry:
    identifier: str
    raw: dict[str, Any]


def _non_empty_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _is_numeric_score(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _extract_assumption_id(item: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(item, str):
        return _non_empty_text(item), {}
    if isinstance(item, dict):
        identifier = (
            _non_empty_text(item.get("id"))
            or _non_empty_text(item.get("statement"))
            or _non_empty_text(item.get("assumption"))
        )
        return identifier, item
    return "", {}


def _parse_assumptions(payload: dict[str, Any]) -> tuple[list[_AssumptionEntry], list[str]]:
    raw_items = payload.get("assumptions")
    if not isinstance(raw_items, list) or not raw_items:
        return [], ["intent must declare a non-empty 'assumptions' list"]
    entries: list[_AssumptionEntry] = []
    violations: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_items, start=1):
        identifier, raw = _extract_assumption_id(item)
        if not identifier:
            violations.append(f"assumption entry {index} must be a non-empty string or dict with an id or statement")
            continue
        if identifier in seen:
            violations.append(f"duplicate assumption '{identifier}'")
            continue
        seen.add(identifier)
        entries.append(_AssumptionEntry(identifier=identifier, raw=raw))
    return entries, violations


def _index_by_assumption_id(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return {key.strip(): value for key, value in raw.items() if isinstance(key, str) and key.strip()}
    if not isinstance(raw, list):
        return {}
    indexed: dict[str, Any] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = _non_empty_text(item.get("assumption_id")) or _non_empty_text(item.get("id"))
        if key:
            indexed[key] = item
    return indexed


def _has_validation_content(entry: Any) -> bool:
    if isinstance(entry, str):
        return bool(entry.strip())
    if isinstance(entry, dict):
        for key, value in entry.items():
            if key in ("assumption_id", "id"):
                continue
            if isinstance(value, str) and value.strip():
                return True
            if not isinstance(value, str) and value not in (None, False, [], {}):
                return True
        return False
    return bool(entry)


def _validation_violations(payload: dict[str, Any], entries: list[_AssumptionEntry]) -> list[str]:
    raw_validations = payload.get("assumption_validation")
    indexed = _index_by_assumption_id(raw_validations)
    if not indexed:
        return ["intent must declare a non-empty 'assumption_validation' entry for its assumptions"]
    return [
        f"assumption '{entry.identifier}' has no 'assumption_validation' entry"
        for entry in entries
        if not _has_validation_content(indexed.get(entry.identifier))
    ]


def _missing_risk_fields(risk: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field_name in _REQUIRED_RISK_FIELDS:
        value = risk.get(field_name)
        if field_name == "score":
            if not _is_numeric_score(value):
                missing.append(field_name)
        elif field_name == "likelihood":
            if not (_non_empty_text(value) or _is_numeric_score(value)):
                missing.append(field_name)
        elif not _non_empty_text(value):
            missing.append(field_name)
    return missing


def _pr_question_violation(entry: _AssumptionEntry, risk: dict[str, Any] | None) -> str | None:
    pr_question = _non_empty_text(entry.raw.get("pr_question")) or (
        _non_empty_text(risk.get("pr_question")) if isinstance(risk, dict) else ""
    )
    if not pr_question:
        return None
    impact = _non_empty_text(entry.raw.get("impact")).lower() or (
        _non_empty_text(risk.get("impact")).lower() if isinstance(risk, dict) else ""
    )
    if impact in _LOW_OR_MEDIUM_IMPACTS:
        return f"assumption '{entry.identifier}' cannot use 'pr_question' with '{impact}' impact"
    return None


def _risk_violations_for_entry(entry: _AssumptionEntry, risk: Any) -> list[str]:
    violations: list[str] = []
    if not isinstance(risk, dict):
        violations.append(f"assumption '{entry.identifier}' has no 'assumption_risk' entry")
        if pr_err := _pr_question_violation(entry, None):
            violations.append(pr_err)
        return violations
    missing = _missing_risk_fields(risk)
    if missing:
        violations.append(
            f"assumption '{entry.identifier}' assumption_risk is missing required field(s): {', '.join(missing)}"
        )
    score = risk.get("score")
    testability = _non_empty_text(risk.get("testability")).lower()
    mitigation = _non_empty_text(risk.get("mitigation")) or _non_empty_text(entry.raw.get("mitigation"))
    if (
        _is_numeric_score(score)
        and score >= HIGH_RISK_SCORE_THRESHOLD
        and testability in _UNTESTABLE_VALUES
        and not mitigation
    ):
        violations.append(f"assumption '{entry.identifier}' is high-score and untestable and requires a 'mitigation'")
    if pr_err := _pr_question_violation(entry, risk):
        violations.append(pr_err)
    return violations


def assumption_violations(payload: dict[str, Any]) -> list[str]:
    entries, violations = _parse_assumptions(payload)
    if not entries:
        return violations
    violations.extend(_validation_violations(payload, entries))
    risk_index = _index_by_assumption_id(payload.get("assumption_risk"))
    for entry in entries:
        violations.extend(_risk_violations_for_entry(entry, risk_index.get(entry.identifier)))
    return violations


def default_assumptions_fields() -> dict[str, Any]:
    return {
        "assumptions": [
            {
                "id": "A1",
                "statement": "Target repository matches the declared intent scope.",
                "impact": "medium",
            }
        ],
        "assumption_validation": [
            {
                "assumption_id": "A1",
                "method": "Verify against repository state and test suite.",
            }
        ],
        "assumption_risk": [
            {
                "assumption_id": "A1",
                "likelihood": "low",
                "testability": "testable",
                "score": 2,
                "response": "fail-fast in tests",
            }
        ],
    }
