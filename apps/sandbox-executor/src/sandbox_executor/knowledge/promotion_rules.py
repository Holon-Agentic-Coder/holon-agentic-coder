from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class LadderEvidence:
    successes: int
    failures: int
    wilson_lower: float


@dataclass(frozen=True)
class LadderRule:
    state: str
    applies: Callable[[LadderEvidence], bool]


FAILURE_LADDER_RULES: tuple[LadderRule, ...] = (
    LadderRule("none", lambda e: e.failures == 0),
    LadderRule("observed", lambda e: e.failures == 1 and e.successes == 0),
    LadderRule("repeated", lambda e: e.failures == 2 and e.successes == 0),
    LadderRule("proven-fail", lambda e: e.failures >= 3 and e.successes == 0),
    LadderRule("contested", lambda e: e.successes > 0 and e.failures > 0),
)
FAILURE_LADDER_DEFAULT = "observed"

SUCCESS_LADDER_RULES: tuple[LadderRule, ...] = (
    LadderRule("none", lambda e: e.successes == 0),
    LadderRule("observed", lambda e: e.successes == 1),
    LadderRule("proven-success", lambda e: e.successes >= 10 and e.wilson_lower >= 0.85),
    LadderRule("likely-success", lambda e: e.successes >= 5 and e.wilson_lower >= 0.70),
    LadderRule("repeated", lambda e: e.successes >= 2),
)
SUCCESS_LADDER_DEFAULT = "observed"

VETO_STATES = frozenset({"repeated", "proven-fail"})
ADVISORY_STATES = frozenset({"repeated", "likely-success", "proven-success"})

_VETO_DECAY_PER_HOUR = 0.00412
_VETO_DECAY_MIN_WEIGHT = 0.01
_VETO_DECAY_DEFAULT_WEIGHT = 1.0
_SECONDS_PER_HOUR = 3600.0

REVIEW_ACTION_ID_PREFIXES = ("ACT-REV-", "ACT-FIND-")
_INTENT_SEGMENT = re.compile(r"(?:^|/)(I-\d+(?:-[^/]*)?)")


def classify(rules: tuple[LadderRule, ...], evidence: LadderEvidence, default: str) -> str:
    return next((rule.state for rule in rules if rule.applies(evidence)), default)


def veto_decay_weight(updated_at: str | None, now: datetime | None = None) -> float:
    if not updated_at:
        return _VETO_DECAY_DEFAULT_WEIGHT
    try:
        last_up = datetime.fromisoformat(updated_at)
        if last_up.tzinfo is None:
            last_up = last_up.replace(tzinfo=UTC)
        age_hours = ((now or datetime.now(UTC)) - last_up).total_seconds() / _SECONDS_PER_HOUR
        return max(_VETO_DECAY_MIN_WEIGHT, math.exp(-_VETO_DECAY_PER_HOUR * age_hours))
    except Exception:
        return _VETO_DECAY_DEFAULT_WEIGHT


def _flow_key(target: Any) -> str:
    text = str(target or "").strip()
    match = _INTENT_SEGMENT.search(text)
    return match.group(1) if match else text


def is_single_flow_review_chatter(records: Sequence[Mapping[str, Any]]) -> bool:
    if not records or not all(str(r["record_id"] or "").startswith(REVIEW_ACTION_ID_PREFIXES) for r in records):
        return False
    return len({_flow_key(r["target"]) for r in records}) == 1


def is_review_chatter_context(cur: Any, signature: Mapping[str, Any], failure_ladder_state: str) -> bool:
    if failure_ladder_state not in ("repeated", "proven-fail"):
        return False
    cur.execute(
        "SELECT record_id, target FROM action_records WHERE context_fingerprint = ?;",
        (signature["context_fingerprint"],),
    )
    return is_single_flow_review_chatter(cur.fetchall())
