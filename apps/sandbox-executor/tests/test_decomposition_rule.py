"""Tests for the neutral decomposition rule module and its plan-row serialisation."""

import ast
import inspect

import pytest
from sandbox_executor import decomposition_rule
from sandbox_executor.decomposition_rule import (
    DECISION_DECOMPOSED,
    DECISION_EXECUTE,
    DECISION_OVER_BUDGET,
    DecompositionDecision,
    InvalidPlanDecisionError,
    should_decompose,
)
from sandbox_executor.entrypoint import executor

_LISTED_PLAN = "# Plan\n\n## Sub-intent list\n\n- Add the parser\n  with its tests\n- Wire the parser into Stage 3\n"


@pytest.mark.parametrize(
    ("plan_row", "plan_content", "expected"),
    [
        ({"entropy": 4.0, "entropy_budget": 10.0}, "# Plan\n", DECISION_EXECUTE),
        ({"entropy": 12.5, "entropy_budget": 10.0}, "# Plan\n", DECISION_OVER_BUDGET),
        ({"entropy": 12.5, "entropy_budget": 10.0}, _LISTED_PLAN, DECISION_DECOMPOSED),
        ({"entropy": 2.0}, _LISTED_PLAN, DECISION_DECOMPOSED),
        ({}, "# Plan\n", DECISION_EXECUTE),
    ],
)
def test_plan_fields_round_trip_every_decision(plan_row, plan_content, expected):
    """``from_plan_row(to_plan_fields())`` rebuilds every decision ``should_decompose`` can return."""
    decision = should_decompose(plan_row, plan_content, None)
    assert decision.decision == expected
    fields = decision.to_plan_fields()
    assert fields["decomposition_decision"] == expected
    assert ("sub_intents" in fields) == (expected == DECISION_DECOMPOSED)
    assert DecompositionDecision.from_plan_row(fields) == decision
    # Recorded on top of a full plan row, the planner's other fields do not change the decision read back.
    assert DecompositionDecision.from_plan_row({"plan_id": "P-1", **plan_row, **fields}) == decision


def test_to_plan_fields_copies_only_the_recorded_sub_intent_keys():
    """The recorded items are ``{slug, description, goal}`` copies, never the decision's own dicts."""
    decision = should_decompose({"entropy": 1.0}, _LISTED_PLAN, None)
    fields = decision.to_plan_fields()
    assert fields["sub_intents"] == decision.sub_intents
    assert fields["sub_intents"][0] is not decision.sub_intents[0]
    assert all(set(item) == {"slug", "description", "goal"} for item in fields["sub_intents"])
    assert fields["sub_intents"][0]["description"] == "Add the parser\nwith its tests"


def test_from_plan_row_returns_none_for_an_older_planner_row():
    """A row without ``decomposition_decision`` predates the field: the caller applies the rule itself."""
    assert DecompositionDecision.from_plan_row({"plan_id": "P-1", "entropy": 30.0}) is None


@pytest.mark.parametrize(
    "row",
    [
        {"decomposition_decision": "split"},
        {"decomposition_decision": None},
        {"decomposition_decision": ["execute"]},
        {"decomposition_decision": DECISION_DECOMPOSED},
        {"decomposition_decision": DECISION_DECOMPOSED, "sub_intents": []},
        {"decomposition_decision": DECISION_DECOMPOSED, "sub_intents": "add the parser"},
        {"decomposition_decision": DECISION_DECOMPOSED, "sub_intents": ["add the parser"]},
        {"decomposition_decision": DECISION_DECOMPOSED, "sub_intents": [{"slug": "a", "description": "A"}]},
        {"decomposition_decision": DECISION_DECOMPOSED, "sub_intents": [{"slug": "a", "description": "A", "goal": 1}]},
        {"decomposition_decision": DECISION_EXECUTE, "sub_intents": [{"slug": "a", "description": "A", "goal": "A"}]},
        {"decomposition_decision": DECISION_OVER_BUDGET, "sub_intents": []},
    ],
)
def test_from_plan_row_rejects_a_corrupt_record(row):
    """A recorded decision that ``should_decompose`` could not have produced is never silently re-decided."""
    with pytest.raises(InvalidPlanDecisionError):
        DecompositionDecision.from_plan_row(row)


def test_invalid_plan_decision_error_is_a_value_error():
    """Callers that already handle ``ValueError`` treat a corrupt record as invalid input."""
    assert issubclass(InvalidPlanDecisionError, ValueError)


def test_rule_module_imports_nothing_from_the_entrypoints():
    """The flow engine imports the rule, so the rule must not depend on a container entrypoint."""
    tree = ast.parse(inspect.getsource(decomposition_rule))
    modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    modules += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert not [name for name in modules if name.startswith("sandbox_executor.entrypoint")]


def test_executor_re_exports_the_rule():
    """Existing importers of ``entrypoint.executor`` keep getting the one rule, not a copy of it."""
    assert executor.should_decompose is decomposition_rule.should_decompose
    assert executor.parse_sub_intent_list is decomposition_rule.parse_sub_intent_list
    assert executor.is_placeholder_item is decomposition_rule.is_placeholder_item
    assert executor.DecompositionDecision is decomposition_rule.DecompositionDecision
    assert (executor.DECISION_EXECUTE, executor.DECISION_DECOMPOSED, executor.DECISION_OVER_BUDGET) == (
        DECISION_EXECUTE,
        DECISION_DECOMPOSED,
        DECISION_OVER_BUDGET,
    )
