"""Agent tickers, the pure model-code rule and the short plan/execution identifiers."""

from __future__ import annotations

import argparse
import io
import re
from unittest.mock import patch

import pytest
from sandbox_executor import cli, naming
from sandbox_executor.agent_defaults import AGENT_TICKERS
from sandbox_executor.cli import doctor as cli_doctor
from sandbox_executor.flow.context import FlowContext, StageStatus
from sandbox_executor.flow.stages.intent import run_intent_stage
from sandbox_executor.intent_assumption_rules import default_assumptions_fields
from sandbox_executor.naming import (
    GENERATION_LEGACY_HEX,
    GENERATION_LEGACY_LABELS,
    GENERATION_TICKER,
    MissingTickerError,
    StageIdCollisionError,
    agent_ticker,
    distinct_stage_id,
    is_new_generation,
    model_code,
    parse_stage_id,
    stage_id,
    ticker_for,
)

# Characters and shapes a single git ref component must satisfy (git-check-ref-format rules).
_REF_COMPONENT_OK = re.compile(r"[A-Za-z0-9._-]+")


def _is_valid_ref_component(text: str) -> bool:
    return (
        bool(_REF_COMPONENT_OK.fullmatch(text))
        and ".." not in text
        and not text.startswith(".")
        and not text.endswith(".")
        and not text.endswith(".lock")
    )


REQUIRED_MODEL_CODES = {
    "claude-sonnet-5-5": "CS5.5",
    "claude-fable-5": "CF5",
    "claude-opus-5-5": "CO5.5",
    "gemini-3.8-flash-medium": "G3.8FM",
    "gemini-4-argon": "G4A",
    "gpt-5-codex": "G5C",
    "anthropic/claude-opus-5-5": "CO5.5",
}


@pytest.mark.parametrize(("model", "expected"), sorted(REQUIRED_MODEL_CODES.items()))
def test_model_code_required_results(model, expected):
    assert model_code(model) == expected


def test_local_model_name_yields_a_valid_ref_component():
    code = model_code("qwen2.5-coder:32b")
    assert code
    assert _is_valid_ref_component(code)


def test_every_model_code_is_a_valid_ref_component():
    for model in (*REQUIRED_MODEL_CODES, "local-model", "qwen2.5-coder:32b", "a/b/claude-opus-5-5"):
        assert _is_valid_ref_component(model_code(model))


def test_declared_tickers_are_exactly_the_flow_runners():
    assert AGENT_TICKERS == {"claude": "CLA", "antigravity": "ANA", "pi": "PIA"}


def test_tickers_are_three_uppercase_letters_and_never_shared():
    for ticker in AGENT_TICKERS.values():
        assert re.fullmatch(r"[A-Z]{3}", ticker)
    assert len(set(AGENT_TICKERS.values())) == len(AGENT_TICKERS), "two runners share a ticker"


@pytest.mark.parametrize(
    ("agent", "ticker"), [("claude-agent", "CLA"), ("antigravity-agent", "ANA"), ("pi-agent", "PIA"), ("Pi", "PIA")]
)
def test_ticker_for_resolves_declared_runners(agent, ticker):
    assert ticker_for(agent) == ticker
    assert agent_ticker(agent) == ticker


@pytest.mark.parametrize("agent", ["codex-agent", "opencode-agent", "local-model"])
def test_a_runner_without_a_ticker_is_rejected_naming_the_runner(agent):
    assert agent_ticker(agent) is None
    with pytest.raises(MissingTickerError) as caught:
        ticker_for(agent)
    message = str(caught.value)
    assert naming.canonical_agent_name(agent) in message or agent in message
    assert "no fallback" in message


def test_new_stage_ids_carry_ticker_and_model_code_and_no_hex():
    plan = stage_id("P", 1794567531, "claude-agent", "claude-sonnet-5-5")
    execution = stage_id("E", 1794567598, "claude-agent", "claude-sonnet-5-5")
    assert plan == "P-1794567531-CLA-CS5.5"
    assert execution == "E-1794567598-CLA-CS5.5"
    assert not re.search(r"-[0-9a-f]{8}-", plan)


def test_stage_id_rejects_unknown_kind_and_tickerless_runner():
    with pytest.raises(ValueError):
        stage_id("X", 1, "claude-agent", "claude-opus-5-5")
    with pytest.raises(MissingTickerError):
        stage_id("P", 1, "codex-agent", "gpt-5-codex")


def test_parse_disambiguates_all_three_generations():
    new = parse_stage_id("P-1794567531-CLA-CS5.5")
    assert new is not None and new.generation == GENERATION_TICKER
    assert (new.kind, new.epoch, new.ticker, new.model_code) == ("P", 1794567531, "CLA", "CS5.5")

    hexed = parse_stage_id("E-1794182857-12345678-antigravity-agent-gemini-3.8-flash-medium")
    assert hexed is not None and hexed.generation == GENERATION_LEGACY_HEX
    assert hexed.disambiguator == "12345678"
    assert hexed.labels == "antigravity-agent-gemini-3.8-flash-medium"

    hexless = parse_stage_id("P-1787048480-antigravity-agent-gemini-3.5-flash")
    assert hexless is not None and hexless.generation == GENERATION_LEGACY_LABELS
    assert hexless.labels == "antigravity-agent-gemini-3.5-flash"


def test_parse_round_trips_new_ids_and_reads_variants():
    identifier = stage_id("P", 1794567531, "pi-agent", "gpt-5-codex")
    parsed = parse_stage_id(identifier)
    assert parsed is not None
    assert f"{parsed.kind}-{parsed.epoch}-{parsed.ticker}-{parsed.model_code}" == identifier
    variant = parse_stage_id(f"{identifier}-2")
    assert variant is not None and variant.variant == 2
    assert is_new_generation(identifier)
    assert not is_new_generation("P-1-abcdef12-agent-model")


def test_distinct_stage_id_gives_the_second_writer_a_variant_and_eventually_fails():
    base = "P-1794567531-CLA-CS5.5"
    assert distinct_stage_id(base, set()) == base
    assert distinct_stage_id(base, {base}) == f"{base}-2"
    taken = {base, *(f"{base}-{n}" for n in range(2, naming.MAX_SAME_SECOND_VARIANTS + 1))}
    with pytest.raises(StageIdCollisionError):
        distinct_stage_id(base, taken)


def _real_ledger_ids() -> set[str]:
    """Every distinct plan and execution id recorded in the repository's real ledgers."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    ids: set[str] = set()
    for name, key in (("plans.jsonl", "plan_id"), ("executions.jsonl", "execution_id")):
        path = root / "holon-knowledge" / "ledger" / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            value = row.get(key)
            if isinstance(value, str) and value[:2] in ("P-", "E-"):
                ids.add(value)
    return ids


def test_every_real_ledger_id_parses_and_old_ids_classify_as_legacy():
    ids = _real_ledger_ids()
    assert ids, "the real ledgers must provide sample ids"
    for identifier in ids:
        parsed = parse_stage_id(identifier)
        assert parsed is not None, identifier
        if not is_new_generation(identifier):
            assert parsed.generation in (GENERATION_LEGACY_HEX, GENERATION_LEGACY_LABELS), identifier


def test_doctor_rejects_a_runner_without_a_ticker():
    args = argparse.Namespace(agent="codex", model="gpt-5-codex", json=False, timeout=1, target_worktree=".")
    err = io.StringIO()
    with (
        patch.object(cli_doctor, "report_budgets_and_envelope", return_value=0),
        patch.object(cli_doctor, "run_preflight_probe", side_effect=AssertionError("probe must not run")),
        patch("sys.stderr", err),
    ):
        code = cli.run_doctor(args)
    assert code == 1
    assert "codex-agent" in err.getvalue()
    assert "ticker" in err.getvalue()


def test_intent_open_rejects_a_runner_without_a_ticker_before_any_branch(tmp_path):
    ctx = FlowContext(
        target_worktree=str(tmp_path),
        dry_run=True,
        agent="codex-agent",
        intent_data={**default_assumptions_fields(), "goal": "g", "slug": "x"},
    )
    result = run_intent_stage(ctx)
    assert result.status == StageStatus.FAILED
    assert "codex-agent" in result.error and "ticker" in result.error
    assert not ctx.intent_branch
    assert not (tmp_path / ".git").exists()
