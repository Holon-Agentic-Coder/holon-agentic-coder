from __future__ import annotations

from sandbox_executor.agent_defaults import get_model_capabilities, normalise_model_and_effort

# why: canonical model names embed the family, so a substring match ranks every version of a family together.
_FAMILY_RANKS = ("haiku", "sonnet", "opus")


def _family_rank(model: str) -> int | None:
    name = model.lower()
    return next((rank for rank, family in enumerate(_FAMILY_RANKS) if family in name), None)


def tier_ladder(runner: str, configured_model: str) -> tuple[str, ...]:
    ranked = [
        (rank, cap.canonical_model)
        for cap in get_model_capabilities(runner)
        if (rank := _family_rank(cap.canonical_model)) is not None
    ]
    ladder = tuple(dict.fromkeys(model for _, model in sorted(ranked, key=lambda item: item[0])))
    configured = normalise_model_and_effort(runner, configured_model)[0]
    # why: a runner with no ranked family still needs one tier, so an unranked configured model is the last rung.
    if _family_rank(configured) is None and configured not in ladder:
        ladder = (*ladder, configured)
    return ladder


def next_tier(ladder: tuple[str, ...], current: str | None) -> str | None:
    if current not in ladder:
        return None
    index = ladder.index(current) + 1
    return ladder[index] if index < len(ladder) else None


def start_tier(runner: str, configured_model: str, ladder: tuple[str, ...]) -> str | None:
    configured = normalise_model_and_effort(runner, configured_model)[0]
    return configured if configured in ladder else (ladder[0] if ladder else None)
