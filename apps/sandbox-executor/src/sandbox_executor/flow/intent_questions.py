from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sandbox_executor.intent_payload_rules import check_intent_payload

_OPEN_STATUSES = ("open", "unresolved")


@dataclass(frozen=True)
class QuestionKind:
    kind: str
    field: str
    text_keys: tuple[str, ...]
    matches_id: bool
    default_status: str
    marked_label: str
    missing_label: str
    entry_label: str

    @property
    def string_label(self) -> str:
        return f"unanswered {self.kind}"

    def item_text(self, item: dict[str, Any], index: int) -> Any:
        for key in self.text_keys:
            if text := item.get(key):
                return text
        return f"{self.kind}_{index}"

    def item_label(self, status: str) -> str:
        if is_marked_open(status):
            return self.marked_label.format(status=status)
        return self.missing_label


QUESTION_KINDS: tuple[QuestionKind, ...] = (
    QuestionKind(
        kind="open_question",
        field="open_questions",
        text_keys=("question", "id"),
        matches_id=True,
        default_status="open",
        marked_label="unresolved open_question",
        missing_label="missing answer",
        entry_label="unresolved open_question",
    ),
    QuestionKind(
        kind="question",
        field="questions",
        text_keys=("question", "id"),
        matches_id=True,
        default_status="",
        marked_label="marked as {status}",
        missing_label="unanswered question",
        entry_label="unanswered question",
    ),
    QuestionKind(
        kind="clarification",
        field="clarifications",
        text_keys=("question", "clarification"),
        matches_id=False,
        default_status="",
        marked_label="unresolved clarification",
        missing_label="unanswered clarification",
        entry_label="unresolved clarification",
    ),
)


def has_inline_answer(item: dict[str, Any]) -> bool:
    return item.get("answer") not in (None, "")


def answered_by_text(text: Any, index: int, answers: dict[str, Any]) -> bool:
    if text in answers and answers[text] not in (None, ""):
        return True
    return str(index) in answers and answers[str(index)] not in (None, "")


def answered_by_id(item: dict[str, Any], index: int, answers: dict[str, Any]) -> bool:
    return answered_by_text(item.get("id", ""), index, answers)


def is_marked_open(status: str) -> bool:
    return status in _OPEN_STATUSES


def _answers_from_list(items: list[Any]) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    for index, item in enumerate(items):
        if isinstance(item, dict):
            answers[item.get("question") or item.get("id") or str(index)] = item.get("answer", "")
        else:
            answers[str(index)] = item
    return answers


def _build_answers_map(intent_data: dict[str, Any]) -> dict[str, Any]:
    answers_raw = intent_data.get("answers") or intent_data.get("resolved_questions") or {}
    if isinstance(answers_raw, dict):
        return dict(answers_raw)
    if isinstance(answers_raw, list):
        return _answers_from_list(answers_raw)
    return {}


@dataclass(frozen=True)
class IntentQuestions:
    intent_data: dict[str, Any]
    answers: dict[str, Any]

    @classmethod
    def from_intent(cls, intent_data: dict[str, Any]) -> IntentQuestions:
        return cls(intent_data, _build_answers_map(intent_data))

    def unresolved(self) -> list[str]:
        found: list[str] = []
        for kind in QUESTION_KINDS:
            found.extend(self._unresolved_in_group(kind, self.intent_data.get(kind.field)))
        return found

    def _unresolved_in_group(self, kind: QuestionKind, group: Any) -> list[str]:
        if isinstance(group, list):
            return [text for index, item in enumerate(group) if (text := self._describe_item(kind, item, index))]
        if isinstance(group, dict):
            return [
                f"{key} ({kind.entry_label})"
                for key, value in group.items()
                if value in (None, "") and not answered_by_text(key, 0, self.answers)
            ]
        return []

    def _describe_item(self, kind: QuestionKind, item: Any, index: int) -> str | None:
        if isinstance(item, dict):
            return self._describe_dict_item(kind, item, index)
        if isinstance(item, str) and not answered_by_text(item, index, self.answers):
            return f"{item} ({kind.string_label})"
        return None

    def _describe_dict_item(self, kind: QuestionKind, item: dict[str, Any], index: int) -> str | None:
        text = kind.item_text(item, index)
        if self._is_answered(kind, item, text, index):
            return None
        status = str(item.get("status", kind.default_status)).lower()
        return f"{text} ({kind.item_label(status)})"

    def _is_answered(self, kind: QuestionKind, item: dict[str, Any], text: Any, index: int) -> bool:
        if has_inline_answer(item) or answered_by_text(text, index, self.answers):
            return True
        return kind.matches_id and answered_by_id(item, index, self.answers)


def validate_intent_questions(intent_data: dict[str, Any]) -> tuple[bool, list[str]]:
    # why: this check receives no worktree to probe, so a bare existing-directory entry is left to intent-check.
    rule_violations = check_intent_payload(intent_data).violations
    unresolved = [
        *IntentQuestions.from_intent(intent_data).unresolved(),
        *(f"{violation} (unresolved intent rule)" for violation in rule_violations),
    ]
    return (len(unresolved) == 0, unresolved)
