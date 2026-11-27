from __future__ import annotations

from typing import Any

import pytest
from sandbox_executor.flow.intent_questions import validate_intent_questions
from sandbox_executor.harness_reference_patterns import sample_reference
from sandbox_executor.intent_payload_rules import check_intent_payload


def _real_intent(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "slug": "stage1-requires-assumptions",
        "target_branch": "main",
        "description": "Stage 1 refuses intents that omit explicit assumptions and risk entries.",
        "goal": "Enforce Stage 1 assumption validation rules on every intent payload.",
        "rationale": "Explicit assumptions prevent silent requirement drift before planning.",
        "scope": [
            "apps/sandbox-executor/src/sandbox_executor/intent_assumption_rules.py",
            "apps/sandbox-executor/src/sandbox_executor/intent_payload_rules.py",
            "apps/sandbox-executor/tests/test_stage1_requires_assumptions.py",
        ],
        "acceptance_criteria": [
            "intent_assumption_rules.py refuses missing assumptions and missing risk fields.",
            "test_stage1_requires_assumptions.py passes.",
        ],
        "assumptions": [
            {
                "id": "A1",
                "statement": "All Stage 1 entrypoints invoke check_intent_payload.",
                "impact": "high",
            }
        ],
        "assumption_validation": [
            {
                "assumption_id": "A1",
                "method": "Run unit tests driving validate_intent_questions and check_intent_payload.",
            }
        ],
        "assumption_risk": [
            {
                "assumption_id": "A1",
                "likelihood": "low",
                "testability": "testable",
                "score": 2,
                "response": "fail-fast in Stage 1 tests",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _assert_refused(payload: dict[str, Any], expected_fragment: str) -> None:
    findings = check_intent_payload(payload)
    assert findings.refused
    assert any(expected_fragment in violation for violation in findings.violations), findings.violations

    valid, items = validate_intent_questions(payload)
    assert not valid
    assert any(expected_fragment in item and item.endswith(" (unresolved intent rule)") for item in items), items


def _assert_accepted(payload: dict[str, Any]) -> None:
    findings = check_intent_payload(payload)
    assert not findings.refused, findings.violations
    assert findings.violations == []
    assert validate_intent_questions(payload) == (True, [])


def test_valid_real_intent_with_assumptions_is_accepted() -> None:
    _assert_accepted(_real_intent())


@pytest.mark.parametrize("missing_value", [None, [], "not-a-list"])
def test_intent_without_assumptions_is_refused(missing_value: Any) -> None:
    payload = _real_intent()
    if missing_value is None:
        payload.pop("assumptions")
    else:
        payload["assumptions"] = missing_value
    _assert_refused(payload, "non-empty 'assumptions' list")


@pytest.mark.parametrize("missing_value", [None, [], {}])
def test_intent_without_assumption_validation_is_refused(missing_value: Any) -> None:
    payload = _real_intent()
    if missing_value is None:
        payload.pop("assumption_validation")
    else:
        payload["assumption_validation"] = missing_value
    _assert_refused(payload, "non-empty 'assumption_validation'")


def test_assumption_without_validation_entry_is_refused() -> None:
    payload = _real_intent(
        assumptions=[
            {"id": "A1", "statement": "First assumption.", "impact": "medium"},
            {"id": "A2", "statement": "Second assumption without validation.", "impact": "medium"},
        ],
        assumption_validation=[{"assumption_id": "A1", "method": "Unit test"}],
        assumption_risk=[
            {
                "assumption_id": "A1",
                "likelihood": "low",
                "testability": "testable",
                "score": 2,
                "response": "unit test",
            },
            {
                "assumption_id": "A2",
                "likelihood": "low",
                "testability": "testable",
                "score": 2,
                "response": "unit test",
            },
        ],
    )
    _assert_refused(payload, "assumption 'A2' has no 'assumption_validation' entry")


def test_assumption_without_assumption_risk_entry_is_refused() -> None:
    payload = _real_intent(assumption_risk=[])
    _assert_refused(payload, "assumption 'A1' has no 'assumption_risk' entry")


@pytest.mark.parametrize("omitted_field", ["likelihood", "testability", "score", "response"])
def test_assumption_risk_missing_required_field_is_refused(omitted_field: str) -> None:
    risk = {
        "assumption_id": "A1",
        "likelihood": "medium",
        "testability": "testable",
        "score": 4,
        "response": "add regression test",
    }
    risk.pop(omitted_field)
    payload = _real_intent(assumption_risk=[risk])
    _assert_refused(payload, omitted_field)


def test_assumption_risk_rejects_boolean_score() -> None:
    payload = _real_intent(
        assumption_risk=[
            {
                "assumption_id": "A1",
                "likelihood": "medium",
                "testability": "testable",
                "score": True,
                "response": "add regression test",
            }
        ]
    )
    _assert_refused(payload, "score")


def test_high_score_untestable_assumption_without_mitigation_is_refused() -> None:
    payload = _real_intent(
        assumptions=[
            {
                "id": "A1",
                "statement": "External API rate limits remain unchanged in production.",
                "impact": "high",
            }
        ],
        assumption_risk=[
            {
                "assumption_id": "A1",
                "likelihood": "high",
                "testability": "untestable",
                "score": 9,
                "response": "monitor in production",
            }
        ],
    )
    _assert_refused(payload, "requires a 'mitigation'")


def test_high_score_untestable_assumption_with_mitigation_and_pr_question_is_accepted() -> None:
    payload = _real_intent(
        assumptions=[
            {
                "id": "A1",
                "statement": "External API rate limits remain unchanged in production.",
                "impact": "high",
                "pr_question": "Can we confirm the production rate-limit ceiling before merging?",
            }
        ],
        assumption_risk=[
            {
                "assumption_id": "A1",
                "likelihood": "high",
                "testability": "untestable",
                "score": 9,
                "response": "monitor and throttle",
                "mitigation": "Add exponential backoff and circuit breaker around API calls.",
            }
        ],
    )
    _assert_accepted(payload)


@pytest.mark.parametrize("impact", ["low", "medium"])
@pytest.mark.parametrize("location", ["assumption", "risk"])
def test_pr_question_on_low_or_medium_impact_assumption_is_refused(impact: str, location: str) -> None:
    assumption: dict[str, Any] = {
        "id": "A1",
        "statement": "Config defaults match staging.",
        "impact": impact,
    }
    risk: dict[str, Any] = {
        "assumption_id": "A1",
        "likelihood": "low",
        "testability": "testable",
        "score": 2,
        "response": "fallback to defaults",
    }
    if location == "assumption":
        assumption["pr_question"] = "Should we change the default timeout?"
    else:
        risk["pr_question"] = "Should we change the default timeout?"
    payload = _real_intent(assumptions=[assumption], assumption_risk=[risk])
    _assert_refused(payload, f"cannot use 'pr_question' with '{impact}' impact")


def test_dict_keyed_validation_and_risk_maps_are_accepted() -> None:
    payload = _real_intent(
        assumptions=["All Stage 1 entrypoints invoke check_intent_payload."],
        assumption_validation={
            "All Stage 1 entrypoints invoke check_intent_payload.": "Run Stage 1 unit tests.",
        },
        assumption_risk={
            "All Stage 1 entrypoints invoke check_intent_payload.": {
                "likelihood": "low",
                "testability": "testable",
                "score": 2,
                "response": "fail-fast in tests",
            }
        },
    )
    _assert_accepted(payload)


def test_existing_rules_remain_unchanged_alongside_valid_assumptions() -> None:
    glob_payload = _real_intent(scope=["apps/sandbox-executor/src/*.py"])
    _assert_refused(glob_payload, "contains a glob character")

    harness_payload = _real_intent(rationale=f"See {sample_reference('scratch directory')}")
    _assert_refused(harness_payload, "names a control-plane scratch directory")

    question_payload = _real_intent(open_questions=["Which serializer should we use?"])
    assert not check_intent_payload(question_payload).refused
    assert validate_intent_questions(question_payload) == (
        False,
        ["Which serializer should we use? (unresolved open_question)"],
    )
