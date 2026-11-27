"""The conflict resolver's model tier ladder, cheapest first per runner."""

from __future__ import annotations

from sandbox_executor.flow.conflict_resolver_tiers import next_tier, tier_ladder


def test_claude_ladder_is_haiku_then_sonnet_then_opus():
    ladder = tier_ladder("claude-agent", "claude-opus-5-5")
    assert ladder == ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-5-5")


def test_configured_model_does_not_cap_the_ladder():
    assert tier_ladder("claude", "haiku") == tier_ladder("claude-agent", "claude-opus-5-5")


def test_unranked_runner_gets_a_one_tier_ladder_of_its_configured_model():
    assert tier_ladder("codex-agent", "codex") == ("gpt-5-codex",)
    assert tier_ladder("local-model", "my-local") == ("my-local",)


def test_partly_ranked_runner_ends_with_its_unranked_configured_model():
    assert tier_ladder("pi-agent", "codex") == ("claude-opus-5-5", "gpt-5-codex")
    assert tier_ladder("pi-agent", "claude-opus-5-5") == ("claude-opus-5-5",)


def test_next_tier_walks_the_ladder_and_stops_at_the_top():
    ladder = tier_ladder("claude-agent", "claude-opus-5-5")
    assert next_tier(ladder, ladder[0]) == ladder[1]
    assert next_tier(ladder, ladder[1]) == ladder[2]
    assert next_tier(ladder, ladder[2]) is None
    assert next_tier(ladder, "not-on-the-ladder") is None


def test_ladder_is_deterministic_and_has_no_duplicates():
    first = tier_ladder("claude-agent", "sonnet")
    assert first == tier_ladder("claude-agent", "sonnet")
    assert len(first) == len(set(first))


def test_start_tier_normalises_an_alias_onto_the_ladder():
    from sandbox_executor.model_tiers import start_tier

    ladder = tier_ladder("claude-agent", "claude-opus-5-5")
    assert start_tier("claude", "opus", ladder) == ladder[-1]
    assert start_tier("claude", "no-such-model", ladder) == ladder[0]
    assert start_tier("claude", "opus", ()) is None
