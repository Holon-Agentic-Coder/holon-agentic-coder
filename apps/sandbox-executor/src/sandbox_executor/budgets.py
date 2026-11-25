import contextlib
import contextvars
import logging
import math
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Literal, overload

from sandbox_executor.budget_limits import (
    UNLIMITED,
    format_budget_minutes,
    is_unlimited,
    minutes_text,
    unbounded_supervision_warning,
)

logger = logging.getLogger(__name__)

_SECONDS_PER_MINUTE = 60.0
_HANDDOWN_MARGIN_DIVISOR = 10.0
_MIN_HANDDOWN_SECONDS = 1
_ORDERED_STAGE_NAMES = ("intent", "plan", "execute", "review_iteration", "calibration")

ENV_TURN_TIMEOUT = "HOLON_TURN_TIMEOUT_SECONDS"
ENV_TURN_STALL = "HOLON_TURN_STALL_SECONDS"
ENV_STAGE_BUDGET_SECONDS = "HOLON_STAGE_BUDGET_SECONDS"
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


Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class StageTimer:
    def __init__(self, stage: str, budget_seconds: float | None, clock: Clock = time.monotonic) -> None:
        self.stage = stage
        self.budget_seconds = None if budget_seconds is None else float(budget_seconds)
        self._clock = clock
        self._start = clock()
        self._stopped_at: float | None = None

    def now(self) -> float:
        return self._clock()

    def elapsed(self) -> float:
        end = self._stopped_at if self._stopped_at is not None else self._clock()
        return max(0.0, end - self._start)

    @property
    def unlimited(self) -> bool:
        return self.budget_seconds is None

    def remaining(self) -> float | None:
        if self.budget_seconds is None:
            return None
        return max(0.0, self.budget_seconds - self.elapsed())

    def has_time_left(self) -> bool:
        remaining = self.remaining()
        return remaining is None or remaining > 0

    def remaining_capped(self, cap: float) -> float:
        remaining = self.remaining()
        return max(0.0, cap) if remaining is None else min(cap, remaining)

    def breached(self) -> bool:
        return self.budget_seconds is not None and self.elapsed() >= self.budget_seconds

    def credit(self, seconds: float) -> None:
        self._start += max(0.0, seconds)

    def stop(self) -> float:
        if self._stopped_at is None:
            self._stopped_at = self._clock()
        return self.elapsed()

    def breach(self) -> "BudgetBreach":
        assert self.budget_seconds is not None, "an unlimited stage never breaches"
        return BudgetBreach(
            stage=self.stage,
            budget_name="stage",
            budget_seconds=self.budget_seconds,
            elapsed_seconds=self.elapsed(),
        )

    def summary(self, status: str) -> str:
        return format_stage_summary(self.stage, status, self.elapsed(), self.budget_seconds)

    def ledger_fields(self) -> dict[str, object]:
        fields: dict[str, object] = {"budget_seconds": self.budget_seconds}
        if self.unlimited:
            fields["budget"] = UNLIMITED
        return fields


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


def format_minutes(seconds: float) -> str:
    return minutes_text(seconds)


def format_stage_summary(stage: str, status: str, elapsed_s: float, budget_s: float | None) -> str:
    budget = UNLIMITED if budget_s is None else f"{minutes_text(budget_s)} min"
    return f"{stage}: {status} ({elapsed_s / _SECONDS_PER_MINUTE:.1f} min / {budget})"


class BudgetBreach(Exception):
    def __init__(self, stage: str, budget_name: str, budget_seconds: float, elapsed_seconds: float) -> None:
        self.stage = stage
        self.budget_name = budget_name
        self.budget_seconds = float(budget_seconds)
        self.elapsed_seconds = float(elapsed_seconds)
        super().__init__(self.describe())

    def describe(self) -> str:
        return (
            f"{self.stage} {self.budget_name} budget of {self.budget_seconds:.0f} s exceeded "
            f"after {self.elapsed_seconds:.1f} s"
        )


CONTAINER_OVERHEAD_AND_HOST_CHECKS_MARGIN_SECONDS = 60.0

_ACTIVE_STAGE_TIMER: contextvars.ContextVar[StageTimer | None] = contextvars.ContextVar(
    "holon_active_stage_timer", default=None
)


@contextlib.contextmanager
def active_stage_timer(timer: StageTimer) -> Iterator[StageTimer]:
    token = _ACTIVE_STAGE_TIMER.set(timer)
    try:
        yield timer
    finally:
        _ACTIVE_STAGE_TIMER.reset(token)


def current_stage_timer() -> StageTimer | None:
    return _ACTIVE_STAGE_TIMER.get()


def stage_budget_handdown_env(
    timer: StageTimer | None = None, margin_seconds: float = CONTAINER_OVERHEAD_AND_HOST_CHECKS_MARGIN_SECONDS
) -> dict[str, str]:
    timer = timer if timer is not None else current_stage_timer()
    if timer is None:
        return {}
    if timer.budget_seconds is None:
        return {ENV_STAGE_BUDGET_SECONDS: UNLIMITED}
    margin = min(margin_seconds, timer.budget_seconds / _HANDDOWN_MARGIN_DIVISOR)
    left = max(0.0, timer.budget_seconds - timer.elapsed()) - margin
    if not math.isfinite(left):
        logger.warning("Not handing down %s: the %s stage budget is not finite.", ENV_STAGE_BUDGET_SECONDS, timer.stage)
        return {}
    seconds = max(_MIN_HANDDOWN_SECONDS, math.floor(left))
    return {ENV_STAGE_BUDGET_SECONDS: str(seconds)}


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
