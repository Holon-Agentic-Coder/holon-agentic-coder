"""Promotion ladder rule tables and veto decay: pure functions of a signature's evidence, no database access.

Each ladder is an ordered first-match table, so rule order is part of the contract (``proven-success`` must be tried
before ``likely-success``). ``tests/test_promotion_rules.py`` pins both tables against the original ``if/elif`` chains.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class LadderEvidence:
    """The counts a ladder classifies: successes, failures and the Wilson 95% lower bound."""

    successes: int
    failures: int
    wilson_lower: float


@dataclass(frozen=True)
class LadderRule:
    """One ladder rung: the state a signature takes when ``applies`` holds for its evidence."""

    state: str
    applies: Callable[[LadderEvidence], bool]


# Failure veto ladder: deterministic, driven by uncontested failure counts.
FAILURE_LADDER_RULES: tuple[LadderRule, ...] = (
    LadderRule("none", lambda e: e.failures == 0),
    LadderRule("observed", lambda e: e.failures == 1 and e.successes == 0),
    LadderRule("repeated", lambda e: e.failures == 2 and e.successes == 0),
    LadderRule("proven-fail", lambda e: e.failures >= 3 and e.successes == 0),
    LadderRule("contested", lambda e: e.successes > 0 and e.failures > 0),
)
FAILURE_LADDER_DEFAULT = "observed"

# Success evidence ladder: statistical, gated on the Wilson lower bound.
SUCCESS_LADDER_RULES: tuple[LadderRule, ...] = (
    LadderRule("none", lambda e: e.successes == 0),
    LadderRule("observed", lambda e: e.successes == 1),
    LadderRule("proven-success", lambda e: e.successes >= 10 and e.wilson_lower >= 0.85),
    LadderRule("likely-success", lambda e: e.successes >= 5 and e.wilson_lower >= 0.70),
    LadderRule("repeated", lambda e: e.successes >= 2),
)
SUCCESS_LADDER_DEFAULT = "observed"

# Failure ladder states that raise (or refresh) an active veto.
VETO_STATES = frozenset({"repeated", "proven-fail"})
# Success ladder states that warrant a non-imperative advisory.
ADVISORY_STATES = frozenset({"repeated", "likely-success", "proven-success"})

# Exponential decay rate per hour, a half-life of about 7 days.
_VETO_DECAY_PER_HOUR = 0.00412


def classify(rules: tuple[LadderRule, ...], evidence: LadderEvidence, default: str) -> str:
    """Return the state of the first rule that applies to ``evidence``, or ``default`` when none does."""
    return next((rule.state for rule in rules if rule.applies(evidence)), default)


def veto_decay_weight(updated_at: str | None, now: datetime | None = None) -> float:
    """Return the decay weight of a veto last updated at ``updated_at`` (ISO text; naive is read as UTC).

    The weight halves about every 7 days with a floor of 0.01; it is 1.0 without a timestamp or when it cannot be
    parsed.
    """
    if not updated_at:
        return 1.0
    try:
        last_up = datetime.fromisoformat(updated_at)
        if last_up.tzinfo is None:
            last_up = last_up.replace(tzinfo=UTC)
        age_hours = ((now or datetime.now(UTC)) - last_up).total_seconds() / 3600.0
        return max(0.01, math.exp(-_VETO_DECAY_PER_HOUR * age_hours))
    except Exception:
        return 1.0
