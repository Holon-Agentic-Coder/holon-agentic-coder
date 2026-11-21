"""Wall-clock budgets for runner turns and pipeline stages.

A single agent turn on a local model could previously run unbounded, and a stage had no ceiling at all. This module is
the single source of truth for the limits and their configuration:

- a *turn* budget (``HOLON_TURN_TIMEOUT_SECONDS``, default 3600 s) bounds a single assistant response, observed as the
  interval between artefact-progress events of a runner process (see :mod:`sandbox_executor.turns`), never the
  lifetime of the runner process itself;
- a *stall* budget (``HOLON_TURN_STALL_SECONDS``, default 90 s; ``0`` disables it) bounds silence, meaning no streamed
  output and no growth of the artefact being written, within a response that has streamed output; a non-streaming
  runner, which cannot signal liveness mid-response, is allowed ``max(stall, turn)`` of silence instead;
- a *stage* budget bounds a whole stage (intent 10 min, plan 35, execute 45, each review iteration 120, calibration 10),
  and is the only wall-clock limit on a runner process as a whole.

Every stage-minute variable, ``HOLON_STAGE_BUDGET_SECONDS`` and ``HOLON_TURN_TIMEOUT_SECONDS`` also accept the
case-insensitive sentinel ``unlimited`` (see :mod:`sandbox_executor.budget_limits`), which removes that ceiling. "No
ceiling" is carried as ``None``, never as ``inf``; an unset or invalid value still means the default.

The defaults are the real measurements recorded during the artefact, turn and model-envelope work; they are
deliberately not re-derived here.
The module also holds the one shared *entropy* budget default and its resolver, used by both the planner and the
executor, together with the bounds on automatic re-planning and sub-intent nesting.
Clocks are injected so control flow can be tested without sleeping; tests never make performance claims.
"""

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

ENV_TURN_TIMEOUT = "HOLON_TURN_TIMEOUT_SECONDS"
ENV_TURN_STALL = "HOLON_TURN_STALL_SECONDS"
# Absolute ceiling, in seconds, for the stage an in-container entrypoint is running. Takes precedence over the
# per-stage minute variables below. The host sets it per container: while a host stage timer is active (see
# active_stage_timer), run_docker_container exports the stage's remaining time minus a safety margin here
# (stage_budget_handdown_env), so the container's own ceiling fires before the host's.
ENV_STAGE_BUDGET_SECONDS = "HOLON_STAGE_BUDGET_SECONDS"
ENV_STAGE_BUDGET_MINUTES = {
    "intent": "HOLON_STAGE_BUDGET_INTENT_MINUTES",
    "plan": "HOLON_STAGE_BUDGET_PLAN_MINUTES",
    "execute": "HOLON_STAGE_BUDGET_EXECUTE_MINUTES",
    "review_iteration": "HOLON_STAGE_BUDGET_REVIEW_ITERATION_MINUTES",
    "calibration": "HOLON_STAGE_BUDGET_CALIBRATION_MINUTES",
}

# Wall-clock budget, in seconds, for waiting on pending CI checks after the reviewers ran; separate from the review
# iteration budget above, so a slow CI runner never consumes reviewer time.
ENV_REVIEW_CI_WAIT = "HOLON_REVIEW_CI_WAIT_SECONDS"

DEFAULT_REVIEW_CI_WAIT_SECONDS = 1800.0
DEFAULT_TURN_SECONDS = 3600.0
DEFAULT_STALL_SECONDS = 90.0
# Largest budget accepted from the environment, in seconds (7 days), checked after any minute -> second conversion.
# A larger value is rejected like a non-finite one, so no budget can reach arithmetic or a container env as inf, and
# the floored hand-down value always fits an int.
MAX_BUDGET_SECONDS = 7 * 24 * 3600.0

# Pipeline stage names (flow.FlowStage values) mapped onto budget names.
_STAGE_ALIASES = {"calibrate": "calibration", "review": "review_iteration"}


@dataclass(frozen=True)
class StageBudgets:
    """Per-stage wall-clock ceilings in seconds; ``None`` means the stage is ``unlimited``."""

    intent: float | None = 600.0
    plan: float | None = 2100.0
    execute: float | None = 2700.0
    review_iteration: float | None = 7200.0
    calibration: float | None = 600.0

    def for_stage(self, stage: str) -> float | None:
        """Ceiling for a stage name (``None`` when unlimited); accepts pipeline names such as ``calibrate``."""
        name = _STAGE_ALIASES.get(stage, stage)
        if name not in ENV_STAGE_BUDGET_MINUTES:
            raise KeyError(f"Unknown budget stage '{stage}'")
        value = getattr(self, name)
        return None if value is None else float(value)


@dataclass(frozen=True)
class TurnBudgets:
    """Limits for one runner turn, in seconds.

    ``stall_seconds == 0`` disables stall detection; ``turn_seconds is None`` means the turn timeout is ``unlimited``.
    """

    turn_seconds: float | None = DEFAULT_TURN_SECONDS
    stall_seconds: float = DEFAULT_STALL_SECONDS


@dataclass(frozen=True)
class Budgets:
    """Everything :func:`load_budgets` resolved from the environment."""

    turn: TurnBudgets = field(default_factory=TurnBudgets)
    stages: StageBudgets = field(default_factory=StageBudgets)
    stage_override_seconds: float | None = None
    # HOLON_STAGE_BUDGET_SECONDS=unlimited: a third state next to "unset" (override None) and "a number".
    stage_override_unlimited: bool = False

    def unlimited_stages(self) -> list[str]:
        """Budget stage names that run with no wall-clock ceiling."""
        return [name for name in ENV_STAGE_BUDGET_MINUTES if self.stage_seconds(name) is None]

    def stage_seconds(self, stage: str) -> float | None:
        """Effective ceiling for ``stage``, ``None`` when unlimited.

        An explicit ``HOLON_STAGE_BUDGET_SECONDS`` (a number or ``unlimited``) wins over the per-stage value.
        """
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
    """Positive finite float from ``env[name]`` times ``scale``; ``default`` when unset or invalid (with a warning).

    With ``allow_unlimited`` the sentinel ``unlimited`` (any case, trimmed) returns ``None`` with a warning naming the
    variable; without it the sentinel is "not a number" like any other word.

    Invalid means not a number, non-finite, negative (or zero unless ``allow_zero``), or above
    ``MAX_BUDGET_SECONDS`` once multiplied by ``scale`` (60 for the per-stage minute variables), so ``1e308`` minutes
    cannot overflow to ``inf`` seconds. ``default`` is returned unscaled. The warning names the variable, never the
    value (#96), so a misplaced credential cannot reach the log.

    ``fallback`` describes what happens instead in the warning; it defaults to "keeping the default of <default>".
    """
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
    """``(seconds, "")`` for a valid budget ``text`` times ``scale``, else ``(None, <why it is invalid>)``."""
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
    """Resolve turn, stall and stage budgets from ``env``; invalid values log a warning and keep the default.

    ``unlimited`` resolves to ``None`` (no ceiling) for the turn timeout and every stage variable. When the turn timeout
    and stall detection are both off and some stage is unlimited, an ERROR names the unsupervised stages.
    """
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
            fallback=f"keeping the default of {getattr(defaults, name) / 60.0:g} min",
            scale=60.0,
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
    """CI wait budget from ``HOLON_REVIEW_CI_WAIT_SECONDS``; an invalid value logs a warning and keeps 1800 s."""
    return _read_positive(env, ENV_REVIEW_CI_WAIT, DEFAULT_REVIEW_CI_WAIT_SECONDS)


Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class StageTimer:
    """Tracks elapsed and remaining time of one stage against its ceiling using an injected clock.

    A ``None`` budget is ``unlimited``: the timer never breaches and has no remaining time to report.
    """

    def __init__(self, stage: str, budget_seconds: float | None, clock: Clock = time.monotonic) -> None:
        self.stage = stage
        self.budget_seconds = None if budget_seconds is None else float(budget_seconds)
        self._clock = clock
        self._start = clock()
        self._stopped_at: float | None = None

    def now(self) -> float:
        """Current reading of the injected clock."""
        return self._clock()

    def elapsed(self) -> float:
        """Seconds since the timer started (frozen once :meth:`stop` is called)."""
        end = self._stopped_at if self._stopped_at is not None else self._clock()
        return max(0.0, end - self._start)

    @property
    def unlimited(self) -> bool:
        """True when the stage has no wall-clock ceiling."""
        return self.budget_seconds is None

    def remaining(self) -> float | None:
        """Seconds left before the ceiling, never negative; ``None`` when unlimited."""
        if self.budget_seconds is None:
            return None
        return max(0.0, self.budget_seconds - self.elapsed())

    def has_time_left(self) -> bool:
        """True while the stage may still start work: always when unlimited, else while time remains."""
        remaining = self.remaining()
        return remaining is None or remaining > 0

    def remaining_capped(self, cap: float) -> float:
        """The smaller of ``cap`` and the remaining time (never negative); ``cap`` when unlimited."""
        remaining = self.remaining()
        return max(0.0, cap) if remaining is None else min(cap, remaining)

    def breached(self) -> bool:
        """True once elapsed time has reached the ceiling; never for an unlimited stage."""
        return self.budget_seconds is not None and self.elapsed() >= self.budget_seconds

    def credit(self, seconds: float) -> None:
        """Exclude ``seconds`` already spent (a CI wait that has its own budget) from the elapsed time."""
        self._start += max(0.0, seconds)

    def stop(self) -> float:
        """Freeze the elapsed time and return it."""
        if self._stopped_at is None:
            self._stopped_at = self._clock()
        return self.elapsed()

    def breach(self) -> "BudgetBreach":
        """A :class:`BudgetBreach` describing this stage ceiling (only reachable once :meth:`breached`)."""
        assert self.budget_seconds is not None, "an unlimited stage never breaches"
        return BudgetBreach(
            stage=self.stage,
            budget_name="stage",
            budget_seconds=self.budget_seconds,
            elapsed_seconds=self.elapsed(),
        )

    def summary(self, status: str) -> str:
        """``format_stage_summary`` for this timer."""
        return format_stage_summary(self.stage, status, self.elapsed(), self.budget_seconds)

    def ledger_fields(self) -> dict[str, object]:
        """Ledger row budget fields: ``budget_seconds`` (``None`` when unlimited), plus ``budget`` when unlimited."""
        fields: dict[str, object] = {"budget_seconds": self.budget_seconds}
        if self.unlimited:
            fields["budget"] = UNLIMITED
        return fields


def describe_budgets(budgets: Budgets) -> list[str]:
    """The ``holon doctor`` budget lines: ``budgets ...``, plus a ``WARNING:`` when supervision is unbounded."""
    stage_minutes = " ".join(
        f"{name}={format_budget_minutes(budgets.stage_seconds(name))}"
        for name in ("intent", "plan", "execute", "review_iteration", "calibration")
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
    """Format a duration in seconds as minutes (see :func:`sandbox_executor.budget_limits.minutes_text`)."""
    return minutes_text(seconds)


def format_stage_summary(stage: str, status: str, elapsed_s: float, budget_s: float | None) -> str:
    """Stage summary line, for example ``plan: ok (4.1 min / 35 min)`` or ``execute: ok (73.2 min / unlimited)``.

    Elapsed minutes always carry one decimal; whole-minute budgets are printed as integers.
    """
    budget = UNLIMITED if budget_s is None else f"{minutes_text(budget_s)} min"
    return f"{stage}: {status} ({elapsed_s / 60.0:.1f} min / {budget})"


class BudgetBreach(Exception):
    """A turn, stall or stage budget fired."""

    def __init__(self, stage: str, budget_name: str, budget_seconds: float, elapsed_seconds: float) -> None:
        self.stage = stage
        self.budget_name = budget_name
        self.budget_seconds = float(budget_seconds)
        self.elapsed_seconds = float(elapsed_seconds)
        super().__init__(self.describe())

    def describe(self) -> str:
        """Human-readable description used in logs and ledger error context."""
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
    """Make ``timer`` the host stage timer for the enclosed block (restored on exit; nests)."""
    token = _ACTIVE_STAGE_TIMER.set(timer)
    try:
        yield timer
    finally:
        _ACTIVE_STAGE_TIMER.reset(token)


def current_stage_timer() -> StageTimer | None:
    """The host stage timer set by :func:`active_stage_timer`, or None outside a timed stage."""
    return _ACTIVE_STAGE_TIMER.get()


def stage_budget_handdown_env(
    timer: StageTimer | None = None, margin_seconds: float = CONTAINER_OVERHEAD_AND_HOST_CHECKS_MARGIN_SECONDS
) -> dict[str, str]:
    """``{HOLON_STAGE_BUDGET_SECONDS: <remaining - margin, floored>}`` for a role container, or ``{}`` with no timer.

    An unlimited timer hands down ``{HOLON_STAGE_BUDGET_SECONDS: "unlimited"}``.

    ``timer`` defaults to :func:`current_stage_timer`. The margin is ``margin_seconds`` capped at a tenth of the stage
    budget, so a deliberately short budget is not consumed by it. The value is at least 1 second, because zero or a
    negative value would be ignored inside the container (falling back to the full per-stage budget); a container
    started with no time left therefore stops at once instead of outliving the host ceiling.
    """
    timer = timer if timer is not None else current_stage_timer()
    if timer is None:
        return {}
    if timer.budget_seconds is None:
        # The container honours the sentinel too, so its own per-stage default cannot fire under an unlimited stage.
        return {ENV_STAGE_BUDGET_SECONDS: UNLIMITED}
    margin = min(margin_seconds, timer.budget_seconds / 10.0)
    left = max(0.0, timer.budget_seconds - timer.elapsed()) - margin
    if not math.isfinite(left):
        # Only a timer built in code can get here (load_budgets never yields a non-finite budget); hand nothing down
        # rather than raise on floor(inf), so the container keeps its own per-stage ceiling.
        logger.warning("Not handing down %s: the %s stage budget is not finite.", ENV_STAGE_BUDGET_SECONDS, timer.stage)
        return {}
    seconds = max(1, math.floor(left))
    return {ENV_STAGE_BUDGET_SECONDS: str(seconds)}


# --- Entropy budgets ---------------------------------------------------------------------------------------------

# Default entropy budget for a plan when neither the plan row nor the intent row carries one. The planner shows this
# value to the planning agent and persists it on the plans.jsonl row; the executor enforces the same value.
DEFAULT_ENTROPY_BUDGET = 15.0
# Number of times the flow sends an over-budget plan back to Stage 2 before failing the Execute stage.
MAX_REPLAN_ATTEMPTS = 2
# Host environment variable that carries the flow's re-plan feedback into the planner container (HOLON_* variables are
# forwarded). The feedback is transient: it is never written to a ledger.
ENV_REPLAN_FEEDBACK = "HOLON_REPLAN_FEEDBACK"
# Deepest nesting of sub-intents the flow will drive; a child of a child at this depth fails rather than recursing.
MAX_SUB_INTENT_DEPTH = 2


def _entropy_budget_from(row: Mapping[str, object] | None) -> float | None:
    """Return a usable ``entropy_budget`` from a ledger row, or ``None`` when it is missing or invalid."""
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
    """Resolve the entropy budget for a plan.

    Resolution order: a finite, non-negative ``entropy_budget`` on the plan row; else the same on the intent row; else
    :data:`DEFAULT_ENTROPY_BUDGET`. Missing, ``None``, non-numeric, NaN, infinite or negative values fall through to
    the next source, so historic plan rows without the field keep working.
    """
    for row in (plan_row, intent_row):
        value = _entropy_budget_from(row)
        if value is not None:
            return value
    return DEFAULT_ENTROPY_BUDGET
