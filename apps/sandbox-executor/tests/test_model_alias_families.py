"""Tests verifying that every model capability alias resolves to the matching model family."""

from __future__ import annotations

from collections.abc import Iterable

import pytest
from sandbox_executor.agent_defaults import (
    RUNNER_CAPABILITIES,
    ModelCapability,
    get_model_capabilities,
    normalise_model_and_effort,
)

FAMILY_WORDS: tuple[str, ...] = ("fable", "opus", "sonnet", "haiku", "flash")


def find_alias_family_mismatches(capabilities: Iterable[ModelCapability]) -> list[str]:
    """Return human-readable mismatch messages for any alias that resolves to a different model family."""
    mismatches: list[str] = []
    for cap in capabilities:
        canonical_lower = cap.canonical_model.lower()
        for alias in cap.aliases:
            alias_lower = alias.lower()
            for family in FAMILY_WORDS:
                if family in alias_lower and family not in canonical_lower:
                    mismatches.append(
                        f"{cap.runner}: alias '{alias}' contains family word '{family}' "
                        f"but maps to canonical model '{cap.canonical_model}'"
                    )
    return mismatches


def test_every_capability_alias_resolves_to_its_canonical_model_and_family() -> None:
    """Every alias in the capability table resolves to its canonical model and preserves its family word."""
    capabilities = get_model_capabilities()
    assert capabilities == list(RUNNER_CAPABILITIES)
    assert find_alias_family_mismatches(capabilities) == []

    for cap in capabilities:
        for alias in cap.aliases:
            resolved_model, _ = normalise_model_and_effort(cap.runner, alias)
            assert resolved_model == cap.canonical_model
            for family in FAMILY_WORDS:
                if family in alias.lower():
                    assert family in resolved_model.lower(), (
                        f"Alias '{alias}' for {cap.runner} resolved to '{resolved_model}' "
                        f"which does not contain family word '{family}'"
                    )


@pytest.mark.parametrize(
    ("alias", "canonical_model", "expected_family"),
    [
        ("fable", "claude-opus-5-5", "fable"),
        ("claude-fable", "claude-opus-5-5", "fable"),
        ("opus", "claude-sonnet-4-6", "opus"),
        ("sonnet", "claude-haiku-4-5", "sonnet"),
        ("haiku", "claude-opus-5-5", "haiku"),
        ("flash", "claude-opus-5-5", "flash"),
    ],
)
def test_family_mismatch_detector_catches_cross_family_alias(
    alias: str, canonical_model: str, expected_family: str
) -> None:
    """The family invariant check fails when an alias containing a family word maps to another family."""
    bad_cap = ModelCapability(
        runner="claude-agent",
        canonical_model=canonical_model,
        aliases=[alias],
        effort_levels=[],
        fallback_tier="Primary (Cloud/CLI)",
    )
    mismatches = find_alias_family_mismatches([bad_cap])
    assert len(mismatches) == 1
    assert expected_family in mismatches[0]
    assert canonical_model in mismatches[0]
