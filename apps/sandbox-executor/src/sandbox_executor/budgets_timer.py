import contextlib
import contextvars
import logging
import math
import time
from collections.abc import Callable, Iterator

from sandbox_executor.budget_limits import UNLIMITED, minutes_text

# why: log filters select the hand-down warning by the budgets logger it was always emitted on
logger = logging.getLogger("sandbox_executor.budgets")

_SECONDS_PER_MINUTE = 60.0
_HANDDOWN_MARGIN_DIVISOR = 10.0
_MIN_HANDDOWN_SECONDS = 1
ENV_STAGE_BUDGET_SECONDS = "HOLON_STAGE_BUDGET_SECONDS"


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
