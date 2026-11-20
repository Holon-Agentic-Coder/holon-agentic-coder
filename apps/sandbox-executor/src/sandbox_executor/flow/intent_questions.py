"""Validation of the clarifying questions an intent may carry before Stage 1 accepts it."""

from __future__ import annotations

from typing import Any


def _build_answers_map(intent_data: dict[str, Any]) -> dict[str, Any]:
    """Extract answers mapping from intent data."""
    answers_map: dict[str, Any] = {}
    answers_raw = intent_data.get("answers") or intent_data.get("resolved_questions") or {}
    if isinstance(answers_raw, dict):
        answers_map.update(answers_raw)
    elif isinstance(answers_raw, list):
        for idx, item in enumerate(answers_raw):
            if isinstance(item, dict):
                q_key = item.get("question") or item.get("id") or str(idx)
                answers_map[q_key] = item.get("answer", "")
            else:
                answers_map[str(idx)] = item
    return answers_map


def _is_answered(q_key: str, q_idx: int, answers_map: dict[str, Any]) -> bool:
    """Check whether a question key or index has a non-empty answer in answers_map."""
    if q_key in answers_map and answers_map[q_key] not in (None, ""):
        return True
    return str(q_idx) in answers_map and answers_map[str(q_idx)] not in (None, "")


def _check_dict_question(
    q: dict[str, Any],
    idx: int,
    answers_map: dict[str, Any],
    label: str,
) -> str | None:
    """Check a single dictionary question item for unresolved status or missing answer."""
    default_text = f"{label}_{idx}"
    q_text = q.get("question") or q.get("clarification") or q.get("id") or default_text
    q_status = str(q.get("status", "open" if label == "open_question" else "")).lower()

    has_ans = (
        (q.get("answer") not in (None, ""))
        or _is_answered(q_text, idx, answers_map)
        or _is_answered(q.get("id", ""), idx, answers_map)
    )
    if has_ans:
        return None

    if q_status in ("open", "unresolved"):
        reason = f"marked as {q_status}" if label == "question" else f"unresolved {label}"
    elif label == "open_question":
        reason = "missing answer"
    else:
        reason = f"unanswered {label}"

    return f"{q_text} ({reason})"


def _check_dict_mapping(items: dict[str, Any], answers_map: dict[str, Any], label: str) -> list[str]:
    unresolved: list[str] = []
    suffix = "unresolved open_question" if label == "open_question" else f"unanswered {label}"
    for k, v in items.items():
        if v in (None, "") and not _is_answered(k, 0, answers_map):
            unresolved.append(f"{k} ({suffix})")
    return unresolved


def _check_list_items(items: list[Any], answers_map: dict[str, Any], label: str) -> list[str]:
    unresolved: list[str] = []
    suffix = f"unanswered {label}"
    for idx, item in enumerate(items):
        if isinstance(item, dict):
            desc = _check_dict_question(item, idx, answers_map, label)
            if desc is not None:
                unresolved.append(desc)
        elif isinstance(item, str) and not _is_answered(item, idx, answers_map):
            unresolved.append(f"{item} ({suffix})")
    return unresolved


def _check_question_items(items: Any, answers_map: dict[str, Any], label: str) -> list[str]:
    """Check a questions, open_questions, or clarifications field for unresolved items."""
    if isinstance(items, list):
        return _check_list_items(items, answers_map, label)
    if isinstance(items, dict):
        return _check_dict_mapping(items, answers_map, label)
    return []


def validate_intent_questions(intent_data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate that an intent payload has no unresolved open questions.

    Checks `questions`, `open_questions`, and `clarifications` fields in `intent_data`.
    """
    answers_map = _build_answers_map(intent_data)
    unresolved: list[str] = []

    unresolved.extend(_check_question_items(intent_data.get("open_questions"), answers_map, "open_question"))
    unresolved.extend(_check_question_items(intent_data.get("questions"), answers_map, "question"))
    unresolved.extend(_check_question_items(intent_data.get("clarifications"), answers_map, "clarification"))

    return (len(unresolved) == 0, unresolved)
