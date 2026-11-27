from __future__ import annotations

import pytest
from sandbox_executor.flow.intent_questions import (
    QUESTION_KINDS,
    IntentQuestions,
    answered_by_id,
    answered_by_text,
    has_inline_answer,
    is_marked_open,
    validate_intent_questions,
)
from sandbox_executor.harness_reference_patterns import find_references, sample_reference
from sandbox_executor.intent_payload_rules import check_intent_payload


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


KINDS = {kind.kind: kind for kind in QUESTION_KINDS}


@pytest.mark.parametrize(
    ("item", "expected"),
    [({"answer": "Postgres"}, True), ({"answer": ""}, False), ({"answer": None}, False), ({}, False)],
)
def test_has_inline_answer(item, expected):
    assert has_inline_answer(item) is expected


@pytest.mark.parametrize(
    ("text", "index", "answers", "expected"),
    [
        ("Which DB?", 0, {"Which DB?": "Postgres"}, True),
        ("Which DB?", 2, {"2": "Postgres"}, True),
        ("Which DB?", 0, {"Which DB?": ""}, False),
        ("Which DB?", 0, {"Which DB?": None, "0": None}, False),
        ("Which DB?", 1, {"0": "Postgres"}, False),
    ],
)
def test_answered_by_text_matches_the_text_or_the_index(text, index, answers, expected):
    assert answered_by_text(text, index, answers) is expected


@pytest.mark.parametrize(
    ("item", "answers", "expected"),
    [
        ({"id": "q1"}, {"q1": "yes"}, True),
        ({"id": "q1"}, {"q2": "yes"}, False),
        ({"id": "q1"}, {"3": "yes"}, True),
        ({}, {"": "yes"}, True),
    ],
)
def test_answered_by_id_matches_the_id_or_the_index(item, answers, expected):
    assert answered_by_id(item, 3, answers) is expected


@pytest.mark.parametrize(("status", "expected"), [("open", True), ("unresolved", True), ("closed", False), ("", False)])
def test_is_marked_open(status, expected):
    assert is_marked_open(status) is expected


@pytest.mark.parametrize(
    ("kind", "status", "label"),
    [
        ("open_question", "open", "unresolved open_question"),
        ("open_question", "closed", "missing answer"),
        ("question", "unresolved", "marked as unresolved"),
        ("question", "", "unanswered question"),
        ("clarification", "open", "unresolved clarification"),
        ("clarification", "", "unanswered clarification"),
    ],
)
def test_question_kind_labels_an_unanswered_item_by_status(kind, status, label):
    assert KINDS[kind].item_label(status) == label


@pytest.mark.parametrize(
    ("kind", "string_label", "entry_label"),
    [
        ("open_question", "unanswered open_question", "unresolved open_question"),
        ("question", "unanswered question", "unanswered question"),
        ("clarification", "unanswered clarification", "unresolved clarification"),
    ],
)
def test_question_kind_labels_string_items_and_mapping_entries(kind, string_label, entry_label):
    assert KINDS[kind].string_label == string_label
    assert KINDS[kind].entry_label == entry_label


def test_question_kinds_are_evaluated_in_order_and_only_clarifications_ignore_ids():
    assert [kind.field for kind in QUESTION_KINDS] == ["open_questions", "questions", "clarifications"]
    assert [kind.matches_id for kind in QUESTION_KINDS] == [True, True, False]
    assert [kind.default_status for kind in QUESTION_KINDS] == ["open", "", ""]


def test_intent_questions_reads_answers_from_the_resolved_questions_list():
    intent = {"resolved_questions": [{"id": "q1", "answer": "yes"}, "Postgres"]}
    assert IntentQuestions.from_intent(intent).answers == {"q1": "yes", "1": "Postgres"}


def test_intent_questions_reports_groups_in_order_and_ignores_other_shapes():
    intent = {
        "clarifications": ["Which DB?", 3],
        "questions": "not a group",
        "open_questions": [{"question": "Scope?"}],
    }
    assert IntentQuestions.from_intent(intent).unresolved() == [
        "Scope? (unresolved open_question)",
        "Which DB? (unanswered clarification)",
    ]


def _clean_intent() -> dict[str, object]:
    return {
        "goal": "Reject directory scope entries.",
        "scope": ["src/pkg/core.py", "tests/test_core.py (new)"],
        "acceptance_criteria": ["core.py rejects a directory entry."],
    }


def test_a_glob_scope_entry_is_an_unresolved_intent_rule():
    valid, items = validate_intent_questions({**_clean_intent(), "scope": ["src/pkg/*.py"]})
    assert not valid
    assert len(items) == 1
    assert items[0].startswith("scope entry 'src/pkg/*.py' contains a glob character")
    assert items[0].endswith(" (unresolved intent rule)")


def test_a_harness_reference_in_the_rationale_is_refused_without_echoing_it():
    sample = sample_reference("scratch directory")
    valid, items = validate_intent_questions({**_clean_intent(), "rationale": f"Copied from {sample}"})
    assert not valid
    assert items == [
        "intent field 'rationale' names a control-plane scratch directory; the intent text is copied into the "
        "append-only intents ledger, so remove it (unresolved intent rule)"
    ]
    assert find_references(items[0]) == []


def test_intent_rules_follow_unresolved_questions():
    intent = {**_clean_intent(), "scope": ["src/pkg/"], "questions": {"Which DB?": None}}
    valid, items = validate_intent_questions(intent)
    assert not valid
    assert items[0] == "Which DB? (unanswered question)"
    assert items[1].endswith(
        "ends in a slash: list each file on its own; a directory or glob widens the touch set "
        "and blocks every other flow at Stage 3 admission (unresolved intent rule)"
    )


def test_a_clean_intent_still_passes():
    assert validate_intent_questions(_clean_intent()) == (True, [])


def test_hub_file_warnings_do_not_make_an_intent_unresolved():
    assert validate_intent_questions({**_clean_intent(), "scope": ["tests/conftest.py"]}) == (True, [])


def test_stage_one_leaves_a_bare_existing_directory_to_intent_check(tmp_path):
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    intent = {**_clean_intent(), "scope": ["src/pkg"]}
    assert check_intent_payload(intent, str(tmp_path)).violations
    assert validate_intent_questions(intent) == (True, [])
