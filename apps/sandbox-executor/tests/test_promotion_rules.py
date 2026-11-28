"""Pin the promotion ladder rule tables against the original ``if/elif`` chains, kept here as the specification."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest
from sandbox_executor.knowledge.promotion_rules import (
    FAILURE_LADDER_DEFAULT,
    FAILURE_LADDER_RULES,
    SUCCESS_LADDER_DEFAULT,
    SUCCESS_LADDER_RULES,
    LadderEvidence,
    classify,
    veto_decay_weight,
)

WILSON_LOWER_GRID = (0.0, 0.69, 0.70, 0.84, 0.85, 1.0)


def expected_failure_ladder(successes: int, failures: int) -> str:
    """The failure ladder exactly as ``evaluate_action_promotion`` wrote it before the rule table."""
    if failures == 0:
        return "none"
    elif failures == 1 and successes == 0:
        return "observed"
    elif failures >= 2 and successes == 0:
        return "repeated" if failures == 2 else "proven-fail"
    elif successes > 0 and failures > 0:
        return "contested"
    else:
        return "observed"


def expected_success_ladder(successes: int, wilson_lower: float) -> str:
    """The success ladder exactly as ``evaluate_action_promotion`` wrote it before the rule table."""
    if successes == 0:
        return "none"
    elif successes == 1:
        return "observed"
    elif successes >= 10 and wilson_lower >= 0.85:
        return "proven-success"
    elif successes >= 5 and wilson_lower >= 0.70:
        return "likely-success"
    elif successes >= 2:
        return "repeated"
    else:
        return "observed"


GRID = list(itertools.product(range(13), range(5), WILSON_LOWER_GRID))


def test_failure_ladder_table_matches_original_chain() -> None:
    for successes, failures, wilson_lower in GRID:
        evidence = LadderEvidence(successes=successes, failures=failures, wilson_lower=wilson_lower)
        assert classify(FAILURE_LADDER_RULES, evidence, FAILURE_LADDER_DEFAULT) == expected_failure_ladder(
            successes, failures
        ), evidence


def test_success_ladder_table_matches_original_chain() -> None:
    for successes, failures, wilson_lower in GRID:
        evidence = LadderEvidence(successes=successes, failures=failures, wilson_lower=wilson_lower)
        assert classify(SUCCESS_LADDER_RULES, evidence, SUCCESS_LADDER_DEFAULT) == expected_success_ladder(
            successes, wilson_lower
        ), evidence


@pytest.mark.parametrize(("successes", "failures"), [(-1, 1), (0, -1), (-3, 0)])
def test_unreachable_fallbacks_still_default_to_observed(successes: int, failures: int) -> None:
    evidence = LadderEvidence(successes=successes, failures=failures, wilson_lower=0.0)
    assert classify(FAILURE_LADDER_RULES, evidence, FAILURE_LADDER_DEFAULT) == expected_failure_ladder(
        successes, failures
    )
    assert classify(SUCCESS_LADDER_RULES, evidence, SUCCESS_LADDER_DEFAULT) == expected_success_ladder(successes, 0.0)


def test_veto_decay_weight_halves_weekly_and_reads_naive_as_utc() -> None:
    now = datetime(2026, 11, 21, tzinfo=UTC)
    week_ago = now - timedelta(days=7)

    assert veto_decay_weight(week_ago.isoformat(), now) == pytest.approx(0.5, abs=0.01)
    assert veto_decay_weight(week_ago.replace(tzinfo=None).isoformat(), now) == veto_decay_weight(
        week_ago.isoformat(), now
    )
    assert veto_decay_weight((now - timedelta(days=365)).isoformat(), now) == 0.01


@pytest.mark.parametrize("updated_at", [None, "", "not-a-timestamp"])
def test_veto_decay_weight_is_full_without_a_usable_timestamp(updated_at: str | None) -> None:
    assert veto_decay_weight(updated_at) == 1.0


def test_veto_decay_weight_handles_type_and_overflow_errors() -> None:
    now = datetime(2026, 11, 21, tzinfo=UTC)
    invalid_input: object = 12345
    assert veto_decay_weight(invalid_input, now) == 1.0  # type: ignore[arg-type]
    far_future = datetime(2100, 1, 1, tzinfo=UTC)
    assert veto_decay_weight(far_future.isoformat(), now) == 1.0


def test_veto_decay_weight_unexpected_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 11, 21, tzinfo=UTC)

    def broken_exp(_value: float) -> float:
        raise RuntimeError("unexpected math bug")

    monkeypatch.setattr("sandbox_executor.knowledge.promotion_rules.math.exp", broken_exp)
    with pytest.raises(RuntimeError, match="unexpected math bug"):
        veto_decay_weight(now.isoformat(), now)
