"""Validation of the clarifying questions an intent may carry before Stage 1 accepts it."""

from __future__ import annotations

from typing import Any


def _build_answers_map(intent_data: dict[str, Any]) -> dict[str, Any]:
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
    if q_key in answers_map and answers_map[q_key] not in (None, ""):
        return True
    return str(q_idx) in answers_map and answers_map[str(q_idx)] not in (None, "")


def _check_dict_question(
    q: dict[str, Any],
    idx: int,
    kind: str,
    answers_map: dict[str, Any],
) -> str | None:
    fallback_id = f"{kind}_{idx}"
    q_text = q.get("question") or q.get("id") or q.get("clarification") or fallback_id
    q_status = str(q.get("status", "open" if kind == "open_question" else "")).lower()
    has_ans = (
        (q.get("answer") not in (None, ""))
        or _is_answered(str(q_text), idx, answers_map)
        or _is_answered(str(q.get("id", "")), idx, answers_map)
    )
    if q_status in ("open", "unresolved") and not has_ans:
        label = f"unresolved {kind}" if kind in ("open_question", "clarification") else f"marked as {q_status}"
        return f"{q_text} ({label})"
    if not has_ans:
        label = "missing answer" if kind == "open_question" else f"unanswered {kind}"
        return f"{q_text} ({label})"
    return None


def _check_questions_list(
    raw_list: list[Any],
    kind: str,
    answers_map: dict[str, Any],
) -> list[str]:
    unresolved: list[str] = []
    for idx, q in enumerate(raw_list):
        if isinstance(q, dict):
            err = _check_dict_question(q, idx, kind, answers_map)
            if err:
                unresolved.append(err)
        elif isinstance(q, str):
            if not _is_answered(q, idx, answers_map):
                label = f"unanswered {kind}" if kind != "open_question" else "unanswered open_question"
                unresolved.append(f"{q} ({label})")
    return unresolved


def _check_questions_dict(
    raw_dict: dict[str, Any],
    kind: str,
    answers_map: dict[str, Any],
) -> list[str]:
    unresolved: list[str] = []
    for k, v in raw_dict.items():
        if v in (None, "") and not _is_answered(k, 0, answers_map):
            label = f"unresolved {kind}" if kind == "open_question" else f"unanswered {kind}"
            unresolved.append(f"{k} ({label})")
    return unresolved


def _check_questions_group(
    raw_questions: Any,
    kind: str,
    answers_map: dict[str, Any],
) -> list[str]:
    if isinstance(raw_questions, list):
        return _check_questions_list(raw_questions, kind, answers_map)
    if isinstance(raw_questions, dict):
        return _check_questions_dict(raw_questions, kind, answers_map)
    return []


def validate_intent_questions(intent_data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate that an intent payload has no unresolved open questions."""
    answers_map = _build_answers_map(intent_data)
    unresolved: list[str] = []
    unresolved.extend(_check_questions_group(intent_data.get("open_questions"), "open_question", answers_map))
    unresolved.extend(_check_questions_group(intent_data.get("questions"), "question", answers_map))
    unresolved.extend(_check_questions_group(intent_data.get("clarifications"), "clarification", answers_map))
    return (len(unresolved) == 0, unresolved)
