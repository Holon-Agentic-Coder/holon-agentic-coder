import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, overload

from sandbox_executor.budget_limits import (
    UNLIMITED,
    format_budget_minutes,
    is_unlimited,
    unbounded_supervision_warning,
)
from sandbox_executor.budgets_timer import _ACTIVE_STAGE_TIMER as _ACTIVE_STAGE_TIMER
from sandbox_executor.budgets_timer import _HANDDOWN_MARGIN_DIVISOR as _HANDDOWN_MARGIN_DIVISOR
from sandbox_executor.budgets_timer import _MIN_HANDDOWN_SECONDS as _MIN_HANDDOWN_SECONDS
from sandbox_executor.budgets_timer import _SECONDS_PER_MINUTE, ENV_STAGE_BUDGET_SECONDS
from sandbox_executor.budgets_timer import (
    CONTAINER_OVERHEAD_AND_HOST_CHECKS_MARGIN_SECONDS as CONTAINER_OVERHEAD_AND_HOST_CHECKS_MARGIN_SECONDS,
)
from sandbox_executor.budgets_timer import BudgetBreach as BudgetBreach
from sandbox_executor.budgets_timer import Clock as Clock
from sandbox_executor.budgets_timer import Sleeper as Sleeper
from sandbox_executor.budgets_timer import StageTimer as StageTimer
from sandbox_executor.budgets_timer import active_stage_timer as active_stage_timer
from sandbox_executor.budgets_timer import current_stage_timer as current_stage_timer
from sandbox_executor.budgets_timer import format_minutes as format_minutes
from sandbox_executor.budgets_timer import format_stage_summary as format_stage_summary
from sandbox_executor.budgets_timer import stage_budget_handdown_env as stage_budget_handdown_env

logger = logging.getLogger(__name__)

_ORDERED_STAGE_NAMES = ("intent", "plan", "execute", "review_iteration", "calibration")

ENV_TURN_TIMEOUT = "HOLON_TURN_TIMEOUT_SECONDS"
ENV_TURN_STALL = "HOLON_TURN_STALL_SECONDS"
ENV_STAGE_BUDGET_MINUTES = {
    "intent": "HOLON_STAGE_BUDGET_INTENT_MINUTES",
    "plan": "HOLON_STAGE_BUDGET_PLAN_MINUTES",
    "execute": "HOLON_STAGE_BUDGET_EXECUTE_MINUTES",
    "review_iteration": "HOLON_STAGE_BUDGET_REVIEW_ITERATION_MINUTES",
    "calibration": "HOLON_STAGE_BUDGET_CALIBRATION_MINUTES",
}

ENV_REVIEW_CI_WAIT = "HOLON_REVIEW_CI_WAIT_SECONDS"

DEFAULT_REVIEW_CI_WAIT_SECONDS = 1800.0
DEFAULT_TURN_SECONDS = 3600.0
DEFAULT_STALL_SECONDS = 90.0
MAX_BUDGET_SECONDS = 7 * 24 * 3600.0

_STAGE_ALIASES = {"calibrate": "calibration", "review": "review_iteration"}


@dataclass(frozen=True)
class StageBudgets:
    intent: float | None = 600.0
    plan: float | None = 2100.0
    execute: float | None = 2700.0
    review_iteration: float | None = 7200.0
    calibration: float | None = 600.0

    def for_stage(self, stage: str) -> float | None:
        name = _STAGE_ALIASES.get(stage, stage)
        if name not in ENV_STAGE_BUDGET_MINUTES:
            raise KeyError(f"Unknown budget stage '{stage}'")
        value = getattr(self, name)
        return None if value is None else float(value)


@dataclass(frozen=True)
class TurnBudgets:
    turn_seconds: float | None = DEFAULT_TURN_SECONDS
    stall_seconds: float = DEFAULT_STALL_SECONDS


@dataclass(frozen=True)
class Budgets:
    turn: TurnBudgets = field(default_factory=TurnBudgets)
    stages: StageBudgets = field(default_factory=StageBudgets)
    stage_override_seconds: float | None = None
    stage_override_unlimited: bool = False

    def unlimited_stages(self) -> list[str]:
        return [name for name in ENV_STAGE_BUDGET_MINUTES if self.stage_seconds(name) is None]

    def stage_seconds(self, stage: str) -> float | None:
        if self.stage_override_unlimited:
            return None
        if self.stage_override_seconds is not None:
            return self.stage_override_seconds
        return self.stages.for_stage(stage)


@overload
def _read_positive(
    env: Mapping[str, str],
    name: str,
    default: float,
    *,
    allow_zero: bool = False,
    fallback: str | None = None,
    scale: float = 1.0,
    allow_unlimited: Literal[False] = False,
) -> float: ...


@overload
def _read_positive(
    env: Mapping[str, str],
    name: str,
    default: float | None,
    *,
    allow_zero: bool = False,
    fallback: str | None = None,
    scale: float = 1.0,
    allow_unlimited: bool = False,
) -> float | None: ...


def _read_positive(  # noqa: PLR0913
    env: Mapping[str, str],
    name: str,
    default: float | None,
    *,
    allow_zero: bool = False,
    fallback: str | None = None,
    scale: float = 1.0,
    allow_unlimited: bool = False,
) -> float | None:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    instead = fallback or f"keeping the default of {default}"
    if allow_unlimited and is_unlimited(raw):
        logger.warning("%s is '%s': that budget has no ceiling.", name, UNLIMITED)
        return None
    seconds, problem = _parse_seconds(str(raw).strip(), allow_zero=allow_zero, scale=scale)
    if seconds is None:
        logger.warning("Ignoring %s: %s; %s", name, problem, instead)
        return default
    return seconds


def _parse_seconds(text: str, *, allow_zero: bool, scale: float) -> tuple[float | None, str]:
    try:
        value = float(text)
    except ValueError:
        return None, "not a number"
    if value != value or value in (float("inf"), float("-inf")):
        return None, "not a finite number"
    if value < 0 or (value == 0 and not allow_zero):
        return None, "must be positive"
    seconds = value * scale
    if not math.isfinite(seconds) or seconds > MAX_BUDGET_SECONDS:
        return None, f"above the {MAX_BUDGET_SECONDS:g} s maximum"
    return seconds, ""


def load_budgets(env: Mapping[str, str]) -> Budgets:
    turn = TurnBudgets(
        turn_seconds=_read_positive(env, ENV_TURN_TIMEOUT, DEFAULT_TURN_SECONDS, allow_unlimited=True),
        stall_seconds=_read_positive(env, ENV_TURN_STALL, DEFAULT_STALL_SECONDS, allow_zero=True),
    )
    defaults = StageBudgets()
    stage_values = {
        name: _read_positive(
            env,
            var,
            float(getattr(defaults, name)),
            fallback=f"keeping the default of {getattr(defaults, name) / _SECONDS_PER_MINUTE:g} min",
            scale=_SECONDS_PER_MINUTE,
            allow_unlimited=True,
        )
        for name, var in ENV_STAGE_BUDGET_MINUTES.items()
    }
    override = _read_positive(
        env,
        ENV_STAGE_BUDGET_SECONDS,
        None,
        fallback="no override is set and the per-stage budgets apply",
        allow_unlimited=True,
    )
    budgets = Budgets(
        turn=turn,
        stages=StageBudgets(**stage_values),
        stage_override_seconds=override,
        stage_override_unlimited=is_unlimited(env.get(ENV_STAGE_BUDGET_SECONDS)),
    )
    unbounded = unbounded_supervision_warning(turn.turn_seconds, turn.stall_seconds, budgets.unlimited_stages())
    if unbounded is not None:
        logger.error(unbounded)
    return budgets


def resolve_review_ci_wait_seconds(env: Mapping[str, str]) -> float:
    return _read_positive(env, ENV_REVIEW_CI_WAIT, DEFAULT_REVIEW_CI_WAIT_SECONDS)


def describe_budgets(budgets: Budgets) -> list[str]:
    stage_minutes = " ".join(
        f"{name}={format_budget_minutes(budgets.stage_seconds(name))}" for name in _ORDERED_STAGE_NAMES
    )
    turn = UNLIMITED if budgets.turn.turn_seconds is None else f"{budgets.turn.turn_seconds:g}s"
    override = f" stage_override={UNLIMITED}" if budgets.stage_override_unlimited else ""
    lines = [f"budgets turn={turn} stall={budgets.turn.stall_seconds:g}s {stage_minutes}{override}"]
    unbounded = unbounded_supervision_warning(
        budgets.turn.turn_seconds, budgets.turn.stall_seconds, budgets.unlimited_stages()
    )
    if unbounded is not None:
        lines.append(f"WARNING: {unbounded}")
    return lines


DEFAULT_ENTROPY_BUDGET = 15.0
MAX_REPLAN_ATTEMPTS = 2
ENV_REPLAN_FEEDBACK = "HOLON_REPLAN_FEEDBACK"
MAX_SUB_INTENT_DEPTH = 2


def _entropy_budget_from(row: Mapping[str, object] | None) -> float | None:
    if not isinstance(row, Mapping):
        return None
    raw = row.get("entropy_budget")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def resolve_entropy_budget(plan_row: Mapping[str, object] | None, intent_row: Mapping[str, object] | None) -> float:
    for row in (plan_row, intent_row):
        value = _entropy_budget_from(row)
        if value is not None:
            return value
    return DEFAULT_ENTROPY_BUDGET
