from __future__ import annotations

from dataclasses import dataclass
from typing import Any

HIGH_RISK_SCORE_THRESHOLD = 6

_REQUIRED_RISK_FIELDS = ("likelihood", "testability", "score", "response")
_HIGH_SCORE_LABELS = frozenset({"high", "critical"})
_UNTESTABLE_LABELS = frozenset({"untestable", "none", "false"})
_FORBIDDEN_PR_QUESTION_IMPACTS = frozenset({"low", "medium"})
_ASSUMPTION_KEY_FIELDS = ("id", "assumption", "statement", "text")
_ENTRY_KEY_FIELDS = ("assumption_id", "assumption", "id")
_VALIDATION_CONTENT_FIELDS = ("validation", "method", "check", "how", "test")


@dataclass(frozen=True)
class _ParsedAssumption:
    label: str
    keys: tuple[str, ...]
    impact: str
    mitigation: str
    pr_question: str


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _parse_assumption_dict(item: dict[str, Any], index: int) -> tuple[_ParsedAssumption | None, str | None]:
    keys: list[str] = []
    for field_name in _ASSUMPTION_KEY_FIELDS:
        candidate = _clean_text(item.get(field_name))
        if candidate and candidate not in keys:
            keys.append(candidate)
    if not keys:
        return None, f"assumptions[{index}] is empty; provide an assumption id or statement"
    return (
        _ParsedAssumption(
            label=keys[0],
            keys=tuple(keys),
            impact=_clean_text(item.get("impact")).lower(),
            mitigation=_clean_text(item.get("mitigation")),
            pr_question=_clean_text(item.get("pr_question")),
        ),
        None,
    )


def _extract_assumptions(raw: Any) -> tuple[list[_ParsedAssumption], list[str]]:
    if not isinstance(raw, list) or not raw:
        return [], ["intent has no assumptions list; declare each assumption before Stage 1"]
    parsed: list[_ParsedAssumption] = []
    violations: list[str] = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            text = item.strip()
            if not text:
                violations.append(f"assumptions[{index}] is blank")
            else:
                parsed.append(_ParsedAssumption(label=text, keys=(text,), impact="", mitigation="", pr_question=""))
        elif isinstance(item, dict):
            entry, error = _parse_assumption_dict(item, index)
            if error:
                violations.append(error)
            elif entry is not None:
                parsed.append(entry)
        else:
            violations.append(f"assumptions[{index}] must be a string or mapping")
    return parsed, violations


def _has_validation_content(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        if any(_clean_text(value.get(name)) for name in _VALIDATION_CONTENT_FIELDS):
            return True
        return any(key not in _ENTRY_KEY_FIELDS and _has_validation_content(val) for key, val in value.items())
    if isinstance(value, list):
        return any(_has_validation_content(item) for item in value)
    return value is not None and not isinstance(value, bool)


def _index_validations(raw: Any) -> tuple[set[str], list[str]]:
    if isinstance(raw, dict) and raw:
        keys = {str(key).strip() for key, val in raw.items() if str(key).strip() and _has_validation_content(val)}
        return keys, []
    if isinstance(raw, list) and raw:
        keys: set[str] = set()
        for item in raw:
            if not isinstance(item, dict) or not _has_validation_content(item):
                continue
            for field_name in _ENTRY_KEY_FIELDS:
                candidate = _clean_text(item.get(field_name))
                if candidate:
                    keys.add(candidate)
        return keys, []
    return set(), ["intent has no assumption_validation; map every assumption to how it is validated"]


def _index_risks(raw: Any) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            cleaned = str(key).strip()
            if cleaned and isinstance(value, dict):
                indexed[cleaned] = value
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            for field_name in _ENTRY_KEY_FIELDS:
                candidate = _clean_text(item.get(field_name))
                if candidate:
                    indexed[candidate] = item
    return indexed


def _is_present_risk_field(field_name: str, value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if field_name == "testability" and isinstance(value, bool):
        return True
    return not isinstance(value, bool)


def _is_high_score(score: Any) -> bool:
    if isinstance(score, bool):
        return False
    if isinstance(score, (int, float)):
        return score >= HIGH_RISK_SCORE_THRESHOLD
    if isinstance(score, str):
        normalized = score.strip().lower()
        if normalized in _HIGH_SCORE_LABELS:
            return True
        try:
            return float(normalized) >= HIGH_RISK_SCORE_THRESHOLD
        except ValueError:
            return False
    return False


def _is_untestable(testability: Any) -> bool:
    if testability is False:
        return True
    if isinstance(testability, str):
        return testability.strip().lower() in _UNTESTABLE_LABELS
    return False


def _lookup_risk(assumption: _ParsedAssumption, risk_index: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for key in assumption.keys:
        if key in risk_index:
            return risk_index[key]
    return None


def _check_assumption_risk(assumption: _ParsedAssumption, risk_entry: dict[str, Any] | None) -> list[str]:
    if risk_entry is None:
        return [f"assumption '{assumption.label}' has no assumption_risk entry"]
    violations: list[str] = []
    missing = [name for name in _REQUIRED_RISK_FIELDS if not _is_present_risk_field(name, risk_entry.get(name))]
    if missing:
        violations.append(
            f"assumption '{assumption.label}' assumption_risk is missing required field(s): {', '.join(missing)}"
        )
    if _is_high_score(risk_entry.get("score")) and _is_untestable(risk_entry.get("testability")):
        mitigation = _clean_text(risk_entry.get("mitigation")) or assumption.mitigation
        if not mitigation:
            violations.append(f"high-score untestable assumption '{assumption.label}' requires a mitigation")
    return violations


def _check_pr_question_impact(assumption: _ParsedAssumption, risk_entry: dict[str, Any] | None) -> list[str]:
    risk_question = _clean_text(risk_entry.get("pr_question")) if risk_entry else ""
    if not (assumption.pr_question or risk_question):
        return []
    risk_impact = _clean_text(risk_entry.get("impact")).lower() if risk_entry else ""
    impact = assumption.impact or risk_impact
    if impact in _FORBIDDEN_PR_QUESTION_IMPACTS:
        return [
            f"assumption '{assumption.label}' has pr_question on {impact} impact; "
            "pr_question is not permitted on low or medium impact assumptions"
        ]
    return []


def assumption_violations(payload: dict[str, Any]) -> list[str]:
    assumptions, violations = _extract_assumptions(payload.get("assumptions"))
    validation_keys, validation_errors = _index_validations(payload.get("assumption_validation"))
    violations.extend(validation_errors)
    risk_index = _index_risks(payload.get("assumption_risk"))
    for assumption in assumptions:
        if not validation_errors and not any(key in validation_keys for key in assumption.keys):
            violations.append(f"assumption '{assumption.label}' has no assumption_validation entry")
        risk_entry = _lookup_risk(assumption, risk_index)
        violations.extend(_check_assumption_risk(assumption, risk_entry))
        violations.extend(_check_pr_question_impact(assumption, risk_entry))
    return violations


def default_assumptions_fields(statement: str, validation: str) -> dict[str, Any]:
    return {
        "assumptions": [{"id": "A1", "statement": statement, "impact": "low"}],
        "assumption_validation": {"A1": validation},
        "assumption_risk": {
            "A1": {
                "likelihood": "low",
                "testability": "unit",
                "score": 1,
                "response": "verify in unit tests",
            }
        },
    }
