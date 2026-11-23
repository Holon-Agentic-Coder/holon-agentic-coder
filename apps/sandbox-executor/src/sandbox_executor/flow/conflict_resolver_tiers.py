"""The model tier ladder of the conflict resolver: which model to try first for a runner, and which comes next.

The ladder is the runner's models from the capability table (``agent_defaults``), cheapest family first. A model whose
family is not ranked is dropped. When none of the runner's models is ranked (antigravity, local runners), the ladder
is the configured model alone, so the resolver always has one tier. The configured model is not a cap: a ranked
ladder starts at the cheapest family even when the flow runs on a deeper model.

The public names are bound by assignment: the flow package re-exports every public definition, and the boundary wiring
that first calls the resolver adds the re-exports.
"""

from __future__ import annotations

from sandbox_executor.agent_defaults import get_model_capabilities, normalise_model_and_effort

# Model families, cheapest first, matched as substrings of the canonical model name.
_FAMILY_RANKS = ("haiku", "sonnet", "opus")


def _family_rank(model: str) -> int | None:
    name = model.lower()
    return next((rank for rank, family in enumerate(_FAMILY_RANKS) if family in name), None)


def _tier_ladder(runner: str, configured_model: str) -> tuple[str, ...]:
    """The runner's models, cheapest family first and without duplicates, else the configured model alone."""
    ranked = [
        (rank, cap.canonical_model)
        for cap in get_model_capabilities(runner)
        if (rank := _family_rank(cap.canonical_model)) is not None
    ]
    ladder = tuple(dict.fromkeys(model for _, model in sorted(ranked, key=lambda item: item[0])))
    return ladder or (normalise_model_and_effort(runner, configured_model)[0],)


def _next_tier(ladder: tuple[str, ...], current: str) -> str | None:
    """The tier after ``current`` in ``ladder``; ``None`` at the top or for a model not on the ladder."""
    if current not in ladder:
        return None
    index = ladder.index(current) + 1
    return ladder[index] if index < len(ladder) else None


tier_ladder = _tier_ladder
next_tier = _next_tier
