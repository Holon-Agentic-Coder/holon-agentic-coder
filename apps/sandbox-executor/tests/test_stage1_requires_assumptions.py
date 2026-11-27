from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.flow.context import FlowContext, StageStatus
from sandbox_executor.flow.intent_questions import validate_intent_questions
from sandbox_executor.flow.stages.intent import run_intent_stage
from sandbox_executor.harness_reference_patterns import sample_reference
from sandbox_executor.intent_payload_rules import check_intent_payload


def _valid_intent() -> dict[str, Any]:
    return {
        "slug": "stage1-assumptions-check",
        "target_branch": "main",
        "description": "Enforce explicit assumptions before planning.",
        "goal": "Validate assumptions, validation plans, risks, mitigations, and pr_question impact at Stage 1.",
        "rationale": "Untested assumptions should be visible before code is written.",
        "scope": [
            "apps/sandbox-executor/src/sandbox_executor/intent_assumption_rules.py",
            "apps/sandbox-executor/src/sandbox_executor/intent_payload_rules.py",
        ],
        "acceptance_criteria": [
            "intent_payload_rules.py refuses intents missing assumptions.",
            "intent_questions.py surfaces assumption violations at Stage 1.",
        ],
        "assumptions": [
            {
                "id": "A1",
                "statement": "All callers pass a dict intent payload into Stage 1.",
                "impact": "medium",
            }
        ],
        "assumption_validation": {
            "A1": "Unit tests invoke validate_intent_questions and check_intent_payload with real intent payloads."
        },
        "assumption_risk": {
            "A1": {
                "likelihood": "low",
                "testability": "unit",
                "score": 2,
                "response": "Verify via Stage 1 unit tests.",
            }
        },
    }


def _assert_refused_everywhere(payload: dict[str, Any], tmp_path: Path, expected_substring: str) -> None:
    findings = check_intent_payload(payload)
    assert findings.refused
    assert any(expected_substring in message for message in findings.violations)

    valid, unresolved = validate_intent_questions(payload)
    assert not valid
    assert any(expected_substring in item and item.endswith("(unresolved intent rule)") for item in unresolved)

    ctx = FlowContext(repo_dir=str(tmp_path), intent_data=deepcopy(payload), dry_run=True)
    result = run_intent_stage(ctx)
    assert result.status == StageStatus.FAILED
    assert expected_substring in (result.error or "")


@pytest.mark.parametrize("assumptions_value", [None, [], "not-a-list"])
def test_intent_without_assumptions_is_refused_at_stage1(tmp_path: Path, assumptions_value: Any) -> None:
    payload = _valid_intent()
    if assumptions_value is None:
        payload.pop("assumptions")
    else:
        payload["assumptions"] = assumptions_value

    _assert_refused_everywhere(payload, tmp_path, "no assumptions list")


@pytest.mark.parametrize("validation_value", [None, {}, []])
def test_intent_without_assumption_validation_is_refused_at_stage1(tmp_path: Path, validation_value: Any) -> None:
    payload = _valid_intent()
    if validation_value is None:
        payload.pop("assumption_validation")
    else:
        payload["assumption_validation"] = validation_value

    _assert_refused_everywhere(payload, tmp_path, "no assumption_validation")


def test_assumption_without_matching_validation_entry_is_refused_at_stage1(tmp_path: Path) -> None:
    payload = _valid_intent()
    payload["assumptions"].append(
        {"id": "A2", "statement": "Secondary assumption without validation.", "impact": "low"}
    )
    payload["assumption_risk"]["A2"] = {
        "likelihood": "low",
        "testability": "unit",
        "score": 1,
        "response": "Verify in tests.",
    }

    _assert_refused_everywhere(payload, tmp_path, "assumption 'A2' has no assumption_validation entry")


def test_assumption_without_risk_entry_is_refused_at_stage1(tmp_path: Path) -> None:
    payload = _valid_intent()
    payload["assumption_risk"] = {}

    _assert_refused_everywhere(payload, tmp_path, "assumption 'A1' has no assumption_risk entry")


@pytest.mark.parametrize("missing_field", ["likelihood", "testability", "score", "response"])
def test_assumption_risk_missing_required_field_is_refused_at_stage1(tmp_path: Path, missing_field: str) -> None:
    payload = _valid_intent()
    payload["assumption_risk"]["A1"].pop(missing_field)

    _assert_refused_everywhere(payload, tmp_path, missing_field)


def test_assumption_risk_rejects_boolean_score(tmp_path: Path) -> None:
    payload = _valid_intent()
    payload["assumption_risk"]["A1"]["score"] = True

    _assert_refused_everywhere(payload, tmp_path, "score")


@pytest.mark.parametrize(
    ("score", "testability"),
    [(6, "untestable"), (9, False), ("high", "none"), ("critical", "false")],
)
def test_high_score_untestable_assumption_without_mitigation_is_refused_at_stage1(
    tmp_path: Path, score: Any, testability: Any
) -> None:
    payload = _valid_intent()
    payload["assumptions"] = [{"id": "A1", "statement": "External API rate limits remain unchanged.", "impact": "high"}]
    payload["assumption_risk"]["A1"] = {
        "likelihood": "high",
        "testability": testability,
        "score": score,
        "response": "Observe in production.",
    }

    _assert_refused_everywhere(payload, tmp_path, "requires a mitigation")


def test_high_score_untestable_high_impact_assumption_with_mitigation_and_pr_question_is_accepted(
    tmp_path: Path,
) -> None:
    payload = _valid_intent()
    payload["assumptions"] = [
        {
            "id": "A1",
            "statement": "External API rate limits remain unchanged.",
            "impact": "high",
            "mitigation": "Add exponential backoff and circuit breaker on 429 responses.",
            "pr_question": "Should we confirm the upstream rate-limit contract before merging?",
        }
    ]
    payload["assumption_risk"]["A1"] = {
        "likelihood": "high",
        "testability": "untestable",
        "score": 9,
        "response": "Mitigate with backoff and confirm in PR review.",
    }

    findings = check_intent_payload(payload)
    assert not findings.refused
    assert findings.violations == []

    assert validate_intent_questions(payload) == (True, [])

    ctx = FlowContext(repo_dir=str(tmp_path), intent_data=deepcopy(payload), dry_run=True)
    result = run_intent_stage(ctx)
    assert result.status == StageStatus.SUCCESS


@pytest.mark.parametrize("impact", ["low", "medium"])
def test_pr_question_on_low_or_medium_impact_assumption_is_refused_at_stage1(tmp_path: Path, impact: str) -> None:
    payload = _valid_intent()
    payload["assumptions"] = [
        {
            "id": "A1",
            "statement": "Minor formatting preference.",
            "impact": impact,
            "pr_question": "Does this minor detail look okay?",
        }
    ]

    _assert_refused_everywhere(payload, tmp_path, "pr_question is not permitted on low or medium impact assumptions")


def test_list_shaped_validation_and_risk_entries_are_accepted() -> None:
    payload = _valid_intent()
    payload["assumption_validation"] = [{"assumption_id": "A1", "method": "Unit test validation."}]
    payload["assumption_risk"] = [
        {
            "assumption_id": "A1",
            "likelihood": "low",
            "testability": "unit",
            "score": 2,
            "response": "Unit test covers this.",
        }
    ]

    assert check_intent_payload(payload).violations == []
    assert validate_intent_questions(payload) == (True, [])


def test_existing_payload_and_question_rules_stay_unchanged_with_valid_assumptions() -> None:
    sample = sample_reference("task id")
    payload = {
        **_valid_intent(),
        "scope": ["src/pkg/*.py", "tests/conftest.py"],
        "rationale": f"Tracks {sample}.",
        "questions": {"Which DB?": None},
    }

    findings = check_intent_payload(payload)
    assert len(findings.violations) == 2
    assert findings.violations[0].startswith("scope entry 'src/pkg/*.py' contains a glob character")
    assert "intent field 'rationale' names a control-plane task id" in findings.violations[1]
    assert len(findings.warnings) == 1
    assert findings.warnings[0].startswith("tests/conftest.py: hub file")

    valid, unresolved = validate_intent_questions(payload)
    assert not valid
    assert unresolved[0] == "Which DB? (unanswered question)"
    assert len(unresolved) == 3
