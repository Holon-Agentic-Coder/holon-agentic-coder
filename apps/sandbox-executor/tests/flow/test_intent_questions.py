"""validate_intent_questions labels each unresolved question by its kind, as Stage 1 reports it."""

from __future__ import annotations

import pytest
from sandbox_executor.flow.intent_questions import validate_intent_questions


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        ({"clarifications": {"Which DB?": None}}, ["Which DB? (unresolved clarification)"]),
        ({"questions": {"Which DB?": ""}}, ["Which DB? (unanswered question)"]),
        ({"open_questions": {"Which DB?": None}}, ["Which DB? (unresolved open_question)"]),
        ({"clarifications": [{"id": "c1", "clarification": "Which DB?"}]}, ["Which DB? (unanswered clarification)"]),
        (
            {"clarifications": [{"id": "c1", "clarification": "Which DB?", "status": "open"}]},
            ["Which DB? (unresolved clarification)"],
        ),
        ({"clarifications": [{"id": "c1"}]}, ["clarification_0 (unanswered clarification)"]),
        ({"questions": [{"id": "q1", "status": "open"}]}, ["q1 (marked as open)"]),
        ({"open_questions": [{"question": "Which DB?", "status": "closed"}]}, ["Which DB? (missing answer)"]),
        ({"open_questions": ["Which DB?"]}, ["Which DB? (unanswered open_question)"]),
    ],
)
def test_unresolved_questions_keep_their_per_kind_label_and_text(intent, expected):
    assert validate_intent_questions(intent) == (False, expected)


def test_a_clarification_is_not_answered_through_its_id():
    intent = {"clarifications": [{"id": "c1", "clarification": "Which DB?"}], "answers": {"c1": "Postgres"}}
    assert validate_intent_questions(intent) == (False, ["Which DB? (unanswered clarification)"])


def test_a_question_is_answered_through_its_id_or_text():
    assert validate_intent_questions({"questions": [{"id": "q1"}], "answers": {"q1": "yes"}}) == (True, [])
    intent = {"clarifications": [{"clarification": "Which DB?"}], "answers": [{"question": "Which DB?", "answer": "x"}]}
    assert validate_intent_questions(intent) == (True, [])
