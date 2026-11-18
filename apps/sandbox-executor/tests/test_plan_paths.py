"""Model codes, the three-generation stage-id grammar, collision handling and intent-keyed plan paths."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.agent_defaults import MissingAgentTickerError
from sandbox_executor.plan_paths import (
    DuplicateStageNameError,
    StageIdParts,
    calibration_report_rel,
    execution_notes_rel,
    legacy_calibration_report_rel,
    legacy_plan_file_rel,
    model_code,
    new_stage_id,
    parse_stage_id,
    plan_file_rel,
    uncollided_stage_id,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# A git ref component: no space, control, '~', '^', ':', '?', '*', '[', '\\', no '..', no leading/trailing dot.
_REF_COMPONENT_OK = re.compile(r"[0-9A-Za-z][0-9A-Za-z.]*")


def _is_valid_ref_component(text: str) -> bool:
    if not text or ".." in text or text.startswith(".") or text.endswith(".lock"):
        return False
    return (
        subprocess.run(
            ["git", "check-ref-format", "--allow-onelevel", text],
            capture_output=True,
        ).returncode
        == 0
    )


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-sonnet-5-5", "CS5.5"),
        ("claude-fable-5", "CF5"),
        ("claude-opus-5-5", "CO5.5"),
        ("gemini-3.8-flash-medium", "G3.8FM"),
        ("gemini-4-argon", "G4A"),
        ("gpt-5-codex", "G5C"),
        ("anthropic/claude-opus-5-5", "CO5.5"),
    ],
)
def test_required_model_codes(model, expected):
    assert model_code(model) == expected


@pytest.mark.parametrize(
    "model",
    [
        "qwen2.5-coder:32b",
        "llama3.1:8b-instruct-q4_K_M",
        "local-model",
        "mistral",
        "x/y/z-1.2.3",
        "weird--name---with----dashes",
        "UPPER-Case-Model-7B",
    ],
)
def test_fuzzed_model_names_yield_valid_ref_components(model):
    code = model_code(model)
    assert code
    assert _REF_COMPONENT_OK.fullmatch(code), code
    assert _is_valid_ref_component(code), code


def test_historical_ledger_model_names_yield_valid_ref_components():
    """Every model label ever recorded in plans.jsonl maps to a non-empty, valid ref component."""
    ledger = REPO_ROOT / "holon-knowledge" / "ledger" / "plans.jsonl"
    models = set()
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            if record.get("model"):
                models.add(str(record["model"]))
    assert models
    for model in models:
        code = model_code(model)
        assert code and _is_valid_ref_component(code), f"{model!r} -> {code!r}"


def test_new_stage_ids_have_no_hex_and_carry_ticker_and_code():
    assert new_stage_id("P", 1794567531, "claude-agent", "claude-sonnet-5-5") == "P-1794567531-CLA-CS5.5"
    assert new_stage_id("E", 1794567598, "pi-agent", "claude-opus-5-5") == "E-1794567598-PIA-CO5.5"
    assert new_stage_id("P", 1, "antigravity-agent", "gemini-3.8-flash-medium") == "P-1-ANA-G3.8FM"
    with pytest.raises(MissingAgentTickerError):
        new_stage_id("P", 1, "codex-agent", "gpt-5-codex")


@pytest.mark.parametrize(
    ("identifier", "kind", "epoch", "generation"),
    [
        ("P-1787048480-antigravity-agent-gemini-3.5-flash", "P", 1787048480, 1),
        ("E-1792765484-claude-agent-claude-opus-5-5", "E", 1792765484, 1),
        ("P-1794976507-654a2c7f-claude-agent-claude-fable-5", "P", 1794976507, 2),
        ("E-1794982952-ad01b9b8-claude-agent-claude-fable-5", "E", 1794982952, 2),
        ("P-1794567531-CLA-CS5.5", "P", 1794567531, 3),
        ("E-1794567598-ANA-G3.8FM", "E", 1794567598, 3),
    ],
)
def test_parser_classifies_all_three_generations(identifier, kind, epoch, generation):
    parts = parse_stage_id(identifier)
    assert isinstance(parts, StageIdParts)
    assert (parts.kind, parts.epoch, parts.generation, parts.raw) == (kind, epoch, generation, identifier)
    if generation == 2:
        assert parts.disambiguator and re.fullmatch(r"[0-9a-f]{8}", parts.disambiguator)
    if generation == 3:
        assert parts.ticker and re.fullmatch(r"[A-Z]{3}", parts.ticker)
        assert parts.code


def test_every_historical_ledger_id_parses_as_generation_one_or_two():
    """100% of real plan and execution ids classify into generation 1 or 2 without error."""
    checked = 0
    for name, key in (("plans.jsonl", "plan_id"), ("executions.jsonl", "execution_id")):
        path = REPO_ROOT / "holon-knowledge" / "ledger" / name
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            identifier = record.get(key)
            if not isinstance(identifier, str) or not re.match(r"[PE]-\d", identifier):
                continue
            parts = parse_stage_id(identifier)
            assert parts.generation in (1, 2), identifier
            checked += 1
    assert checked > 100


@pytest.mark.parametrize("bad", ["", "X-1-a", "P-abc-CLA-CS5.5", "plan", "P-1"])
def test_parser_rejects_non_stage_identifiers(bad):
    with pytest.raises(ValueError):
        parse_stage_id(bad)


def test_same_second_collision_bumps_the_epoch_deterministically():
    taken = {"P-100-CLA-CF5", "P-101-CLA-CF5"}
    assert uncollided_stage_id("P", 100, "claude-agent", "claude-fable-5", taken.__contains__) == "P-102-CLA-CF5"


def test_exhausted_collision_bumps_fail_clearly():
    with pytest.raises(DuplicateStageNameError) as caught:
        uncollided_stage_id("P", 100, "claude-agent", "claude-fable-5", lambda _: True)
    assert "P-100-CLA-CF5" in str(caught.value)


def test_plan_paths_mirror_the_branch_for_all_three_nesting_shapes():
    root_plan = "I-1794567515-sleepfix/P-1794567531-CLA-CS5.5"
    child_plan = "I-1-root/P-2-CLA-CF5--1-child-slug/P-3-ANA-G3.8FM"
    plan_under_plan = "I-1-root/P-2-CLA-CF5/P-3-CLA-CF5"
    for bare in (root_plan, child_plan, plan_under_plan):
        for given in (bare, f"{bare}/_"):
            assert plan_file_rel(given) == f"plans/{bare}.md"
            assert calibration_report_rel(given) == f"plans/{bare}_calibration.md"


def test_plan_path_equals_branch_with_suffixes_stripped():
    branch = "refs/heads/I-1-root/P-2-CLA-CF5/calibrated".removeprefix("refs/heads/").removesuffix("/calibrated")
    assert plan_file_rel(branch) == f"plans/{branch}.md"


def test_execution_notes_path_is_keyed_on_the_full_branch():
    assert (
        execution_notes_rel("I-1-root/P-2-CLA-CF5/E-3-CLA-CF5/_")
        == "executions/I-1-root/P-2-CLA-CF5/E-3-CLA-CF5.md"
    )


def test_legacy_flat_paths_stay_readable():
    assert legacy_plan_file_rel("P-1-a") == "plans/P-1-a.md"
    assert legacy_calibration_report_rel("P-1-a") == "plans/P-1-a_calibration.md"
