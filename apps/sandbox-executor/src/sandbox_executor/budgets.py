"""Wall-clock budgets for runner turns and pipeline stages (Bean 0139).

A single agent turn on a local model could previously run unbounded, and a stage had no ceiling at all. This module is
the single source of truth for the limits and their configuration:

- a *turn* budget (``HOLON_TURN_TIMEOUT_SECONDS``, default 300 s) bounds a single assistant response, observed as the
  interval between artefact-progress events of a runner process (see :mod:`sandbox_executor.turns`), never the
  lifetime of the runner process itself;
- a *stall* budget (``HOLON_TURN_STALL_SECONDS``, default 90 s; ``0`` disables it) bounds silence, meaning no streamed
  output and no growth of the artefact being written;
- a *stage* budget bounds a whole stage (intent 10 min, plan 35, execute 45, each review iteration 15, calibration 10),
  and is the only wall-clock limit on a runner process as a whole.

The defaults are the real measurements recorded in Beans 0138 to 0140; they are deliberately not re-derived here.
Clocks are injected so control flow can be tested without sleeping; tests never make performance claims.
"""

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

ENV_TURN_TIMEOUT = "HOLON_TURN_TIMEOUT_SECONDS"
ENV_TURN_STALL = "HOLON_TURN_STALL_SECONDS"
# Absolute ceiling, in seconds, for the stage an in-container entrypoint is running. Takes precedence over the
# per-stage minute variables below so a host orchestrator can hand down the remainder it has left.
ENV_STAGE_BUDGET_SECONDS = "HOLON_STAGE_BUDGET_SECONDS"
ENV_STAGE_BUDGET_MINUTES = {
    "intent": "HOLON_STAGE_BUDGET_INTENT_MINUTES",
    "plan": "HOLON_STAGE_BUDGET_PLAN_MINUTES",
    "execute": "HOLON_STAGE_BUDGET_EXECUTE_MINUTES",
    "review_iteration": "HOLON_STAGE_BUDGET_REVIEW_ITERATION_MINUTES",
    "calibration": "HOLON_STAGE_BUDGET_CALIBRATION_MINUTES",
}

DEFAULT_TURN_SECONDS = 300.0
DEFAULT_STALL_SECONDS = 90.0

# Pipeline stage names (flow.FlowStage values) mapped onto budget names.
_STAGE_ALIASES = {"calibrate": "calibration", "review": "review_iteration"}


@dataclass(frozen=True)
class StageBudgets:
    """Per-stage wall-clock ceilings in seconds."""

    intent: float = 600.0
    plan: float = 2100.0
    execute: float = 2700.0
    review_iteration: float = 900.0
    calibration: float = 600.0

    def for_stage(self, stage: str) -> float:
        """Ceiling for a stage name; accepts pipeline names such as ``calibrate`` and ``review``."""
        name = _STAGE_ALIASES.get(stage, stage)
        if name not in ENV_STAGE_BUDGET_MINUTES:
            raise KeyError(f"Unknown budget stage '{stage}'")
        return float(getattr(self, name))


@dataclass(frozen=True)
class TurnBudgets:
    """Limits for one runner turn, in seconds. ``stall_seconds == 0`` disables stall detection."""

    turn_seconds: float = DEFAULT_TURN_SECONDS
    stall_seconds: float = DEFAULT_STALL_SECONDS


@dataclass(frozen=True)
class Budgets:
    """Everything :func:`load_budgets` resolved from the environment."""

    turn: TurnBudgets = field(default_factory=TurnBudgets)
    stages: StageBudgets = field(default_factory=StageBudgets)
    stage_override_seconds: float | None = None

    def stage_seconds(self, stage: str) -> float:
        """Effective ceiling for ``stage``: an explicit ``HOLON_STAGE_BUDGET_SECONDS`` wins over the per-stage value."""
        if self.stage_override_seconds is not None:
            return self.stage_override_seconds
        return self.stages.for_stage(stage)


def _read_positive(env: Mapping[str, str], name: str, default: float, *, allow_zero: bool = False) -> float:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except ValueError:
        logger.warning("Ignoring %s=%r: not a number; keeping the default of %s", name, raw, default)
        return default
    if value != value or value in (float("inf"), float("-inf")):
        logger.warning("Ignoring %s=%r: not a finite number; keeping the default of %s", name, raw, default)
        return default
    if value < 0 or (value == 0 and not allow_zero):
        logger.warning("Ignoring %s=%r: must be positive; keeping the default of %s", name, raw, default)
        return default
    return value


def load_budgets(env: Mapping[str, str]) -> Budgets:
    """Resolve turn, stall and stage budgets from ``env``; invalid values log a warning and keep the default."""
    turn = TurnBudgets(
        turn_seconds=_read_positive(env, ENV_TURN_TIMEOUT, DEFAULT_TURN_SECONDS),
        stall_seconds=_read_positive(env, ENV_TURN_STALL, DEFAULT_STALL_SECONDS, allow_zero=True),
    )
    defaults = StageBudgets()
    stage_values = {
        name: _read_positive(env, var, getattr(defaults, name) / 60.0) * 60.0
        for name, var in ENV_STAGE_BUDGET_MINUTES.items()
    }
    override = None
    if env.get(ENV_STAGE_BUDGET_SECONDS, "").strip():
        override = _read_positive(env, ENV_STAGE_BUDGET_SECONDS, -1.0)
        if override <= 0:
            override = None
    return Budgets(turn=turn, stages=StageBudgets(**stage_values), stage_override_seconds=override)


Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class StageTimer:
    """Tracks elapsed and remaining time of one stage against its ceiling using an injected clock."""

    def __init__(self, stage: str, budget_seconds: float, clock: Clock = time.monotonic) -> None:
        self.stage = stage
        self.budget_seconds = float(budget_seconds)
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

    def remaining(self) -> float:
        """Seconds left before the ceiling, never negative."""
        return max(0.0, self.budget_seconds - self.elapsed())

    def breached(self) -> bool:
        """True once elapsed time has reached the ceiling."""
        return self.elapsed() >= self.budget_seconds

    def stop(self) -> float:
        """Freeze the elapsed time and return it."""
        if self._stopped_at is None:
            self._stopped_at = self._clock()
        return self.elapsed()

    def breach(self) -> "BudgetBreach":
        """A :class:`BudgetBreach` describing this stage ceiling."""
        return BudgetBreach(
            stage=self.stage,
            budget_name="stage",
            budget_seconds=self.budget_seconds,
            elapsed_seconds=self.elapsed(),
        )

    def summary(self, status: str) -> str:
        """``format_stage_summary`` for this timer."""
        return format_stage_summary(self.stage, status, self.elapsed(), self.budget_seconds)


def _format_minutes(seconds: float) -> str:
    minutes = seconds / 60.0
    if abs(minutes - round(minutes)) < 1e-9:
        return str(round(minutes))
    return f"{minutes:.1f}"


def format_stage_summary(stage: str, status: str, elapsed_s: float, budget_s: float) -> str:
    """Stage summary line, for example ``plan: ok (4.1 min / 35 min)``.

    Elapsed minutes always carry one decimal; whole-minute budgets are printed as integers.
    """
    return f"{stage}: {status} ({elapsed_s / 60.0:.1f} min / {_format_minutes(budget_s)} min)"


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
