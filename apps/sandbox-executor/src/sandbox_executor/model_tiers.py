"""The model tier ladder shared by the conflict resolver and the test failure loop: which model to try first for a
runner, and which comes next.

The ladder is the runner's models from the capability table (``agent_defaults``), cheapest family first. A model whose
family is not ranked is dropped from the ranked part. A configured model whose family is not ranked is the last tier,
so a partly ranked runner (pi-agent configured for codex) still tries the configured model, and a runner with no
ranked model (antigravity, local runners) has the configured model alone: the resolver always has one tier. The
configured model is not a cap: a ranked ladder starts at the cheapest family even when the flow runs on a deeper model.
"""

from __future__ import annotations

from sandbox_executor.agent_defaults import get_model_capabilities, normalise_model_and_effort

# Model families, cheapest first, matched as substrings of the canonical model name.
_FAMILY_RANKS = ("haiku", "sonnet", "opus")


def _family_rank(model: str) -> int | None:
    name = model.lower()
    return next((rank for rank, family in enumerate(_FAMILY_RANKS) if family in name), None)


def tier_ladder(runner: str, configured_model: str) -> tuple[str, ...]:
    """The runner's ranked models, cheapest family first and without duplicates, then an unranked configured model."""
    ranked = [
        (rank, cap.canonical_model)
        for cap in get_model_capabilities(runner)
        if (rank := _family_rank(cap.canonical_model)) is not None
    ]
    ladder = tuple(dict.fromkeys(model for _, model in sorted(ranked, key=lambda item: item[0])))
    configured = normalise_model_and_effort(runner, configured_model)[0]
    if _family_rank(configured) is None and configured not in ladder:
        ladder = (*ladder, configured)
    return ladder


def next_tier(ladder: tuple[str, ...], current: str | None) -> str | None:
    """The tier after ``current`` in ``ladder``; ``None`` at the top or for a model not on the ladder."""
    if current not in ladder:
        return None
    index = ladder.index(current) + 1
    return ladder[index] if index < len(ladder) else None


def start_tier(runner: str, configured_model: str, ladder: tuple[str, ...]) -> str | None:
    """The configured model's canonical name when it is on ``ladder``, else the cheapest tier."""
    configured = normalise_model_and_effort(runner, configured_model)[0]
    return configured if configured in ladder else (ladder[0] if ladder else None)
