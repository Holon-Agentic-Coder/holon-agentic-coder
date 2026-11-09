"""Validation of the clarifying questions an intent may carry before Stage 1 accepts it."""

from __future__ import annotations

from typing import Any


def validate_intent_questions(intent_data: dict[str, Any]) -> tuple[bool, list[str]]:  # noqa: C901, PLR0912, PLR0915
    """Validate that an intent payload has no unresolved open questions.

    Checks `questions`, `open_questions`, and `clarifications` fields in `intent_data`.
    If any open question exists without a matching answer in `answers` or `resolved_questions`,
    or if a question item is explicitly marked as open/unresolved, this returns False
    along with a list of descriptions of the unresolved questions.

    Args:
        intent_data: Dictionary containing intent metadata and questions.

    Returns:
        tuple[bool, list[str]]: (is_valid, list_of_unresolved_question_descriptions)
    """
    unresolved: list[str] = []

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

    def is_answered(q_key: str, q_idx: int) -> bool:
        if q_key in answers_map and answers_map[q_key] not in (None, ""):
            return True
        return str(q_idx) in answers_map and answers_map[str(q_idx)] not in (None, "")

    # 1. Check open_questions
    open_qs = intent_data.get("open_questions")
    if open_qs:
        if isinstance(open_qs, list):
            for idx, q in enumerate(open_qs):
                if isinstance(q, dict):
                    q_text = q.get("question") or q.get("id") or f"open_question_{idx}"
                    q_status = str(q.get("status", "open")).lower()
                    has_ans = (
                        (q.get("answer") not in (None, ""))
                        or is_answered(q_text, idx)
                        or is_answered(q.get("id", ""), idx)
                    )
                    if q_status in ("open", "unresolved") and not has_ans:
                        unresolved.append(f"{q_text} (unresolved open_question)")
                    elif not has_ans:
                        unresolved.append(f"{q_text} (missing answer)")
                elif isinstance(q, str):
                    if not is_answered(q, idx):
                        unresolved.append(f"{q} (unanswered open_question)")
        elif isinstance(open_qs, dict):
            for k, v in open_qs.items():
                if v in (None, "") and not is_answered(k, 0):
                    unresolved.append(f"{k} (unresolved open_question)")

    # 2. Check questions
    questions = intent_data.get("questions")
    if questions:
        if isinstance(questions, list):
            for idx, q in enumerate(questions):
                if isinstance(q, dict):
                    q_text = q.get("question") or q.get("id") or f"question_{idx}"
                    q_status = str(q.get("status", "")).lower()
                    has_ans = (
                        (q.get("answer") not in (None, ""))
                        or is_answered(q_text, idx)
                        or is_answered(q.get("id", ""), idx)
                    )
                    if q_status in ("open", "unresolved") and not has_ans:
                        unresolved.append(f"{q_text} (marked as {q_status})")
                    elif not has_ans:
                        unresolved.append(f"{q_text} (unanswered question)")
                elif isinstance(q, str):
                    if not is_answered(q, idx):
                        unresolved.append(f"{q} (unanswered question)")
        elif isinstance(questions, dict):
            for k, v in questions.items():
                if v in (None, "") and not is_answered(k, 0):
                    unresolved.append(f"{k} (unanswered question)")

    # 3. Check clarifications
    clarifications = intent_data.get("clarifications")
    if clarifications:
        if isinstance(clarifications, list):
            for idx, c in enumerate(clarifications):
                if isinstance(c, dict):
                    c_text = c.get("question") or c.get("clarification") or f"clarification_{idx}"
                    c_status = str(c.get("status", "")).lower()
                    has_ans = (c.get("answer") not in (None, "")) or is_answered(c_text, idx)
                    if c_status in ("open", "unresolved") and not has_ans:
                        unresolved.append(f"{c_text} (unresolved clarification)")
                    elif not has_ans:
                        unresolved.append(f"{c_text} (unanswered clarification)")
                elif isinstance(c, str):
                    if not is_answered(c, idx):
                        unresolved.append(f"{c} (unanswered clarification)")
        elif isinstance(clarifications, dict):
            for k, v in clarifications.items():
                if v in (None, "") and not is_answered(k, 0):
                    unresolved.append(f"{k} (unresolved clarification)")

    return (len(unresolved) == 0, unresolved)
