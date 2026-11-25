from collections.abc import Sequence

UNLIMITED = "unlimited"
_SECONDS_PER_MINUTE = 60.0
_WHOLE_MINUTE_TOLERANCE = 1e-9


def is_unlimited(raw: object) -> bool:
    return raw is not None and str(raw).strip().casefold() == UNLIMITED


def minutes_text(seconds: float) -> str:
    minutes = seconds / _SECONDS_PER_MINUTE
    if abs(minutes - round(minutes)) < _WHOLE_MINUTE_TOLERANCE:
        return str(round(minutes))
    return f"{minutes:.1f}"


def format_budget_minutes(seconds: float | None) -> str:
    return UNLIMITED if seconds is None else f"{minutes_text(seconds)}min"


def unlimited_stage_warning(stage: str) -> str:
    return (
        f"Stage '{stage}' runs with no wall-clock ceiling (budget '{UNLIMITED}'); "
        "only the turn and stall limits can stop it."
    )


def unbounded_supervision_warning(
    turn_seconds: float | None, stall_seconds: float, unlimited_stages: Sequence[str]
) -> str | None:
    if turn_seconds is not None or stall_seconds > 0 or not unlimited_stages:
        return None
    return (
        f"Unbounded agent supervision: HOLON_TURN_TIMEOUT_SECONDS is '{UNLIMITED}', HOLON_TURN_STALL_SECONDS is 0 and "
        f"the stage budget is '{UNLIMITED}' for {', '.join(unlimited_stages)}; nothing will stop a hung agent in "
        "those stages."
    )
