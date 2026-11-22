"""The ``unlimited`` budget sentinel and the operator messages that go with it.

An operator removes a stage or turn ceiling by setting its variable to ``unlimited`` (case-insensitive, whitespace
trimmed). Internally "no ceiling" is always ``None``, never ``float("inf")``, so remaining-time arithmetic, minute
formatting and the container hand-down stay finite. This leaf module is standard-library only and never imports
:mod:`sandbox_executor.budgets`, so both the budget core and its consumers can use it without an import cycle.
"""

from collections.abc import Sequence

UNLIMITED = "unlimited"


def is_unlimited(raw: object) -> bool:
    """True when ``raw`` is exactly the ``unlimited`` sentinel, ignoring case and surrounding whitespace."""
    return raw is not None and str(raw).strip().casefold() == UNLIMITED


def minutes_text(seconds: float) -> str:
    """Format a duration in seconds as minutes: whole minutes as integers, otherwise one decimal.

    For example ``2100`` -> ``"35"``, ``90`` -> ``"1.5"`` and ``20`` -> ``"0.3"``, so a sub-minute budget never prints
    as ``0``.
    """
    minutes = seconds / 60.0
    if abs(minutes - round(minutes)) < 1e-9:
        return str(round(minutes))
    return f"{minutes:.1f}"


def format_budget_minutes(seconds: float | None) -> str:
    """A budget for display: ``"unlimited"`` for ``None``, otherwise ``"<minutes>min"``."""
    return UNLIMITED if seconds is None else f"{minutes_text(seconds)}min"


def unlimited_stage_warning(stage: str) -> str:
    """Warning logged when ``stage`` starts with no wall-clock ceiling."""
    return (
        f"Stage '{stage}' runs with no wall-clock ceiling (budget '{UNLIMITED}'); "
        "only the turn and stall limits can stop it."
    )


def unbounded_supervision_warning(
    turn_seconds: float | None, stall_seconds: float, unlimited_stages: Sequence[str]
) -> str | None:
    """Message when the turn timeout, stall detection and at least one stage ceiling are all disabled, else ``None``.

    In that combination nothing stops a hung agent in those stages, so the caller logs it at ERROR level (and
    ``holon doctor`` prints it). Plain arguments keep this module free of a :mod:`sandbox_executor.budgets` import.
    """
    if turn_seconds is not None or stall_seconds > 0 or not unlimited_stages:
        return None
    return (
        f"Unbounded agent supervision: HOLON_TURN_TIMEOUT_SECONDS is '{UNLIMITED}', HOLON_TURN_STALL_SECONDS is 0 and "
        f"the stage budget is '{UNLIMITED}' for {', '.join(unlimited_stages)}; nothing will stop a hung agent in "
        "those stages."
    )
