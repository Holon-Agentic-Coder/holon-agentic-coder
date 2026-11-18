"""Generation-3 stage names, the three-generation ID grammar and nested plan-file paths."""

from __future__ import annotations

import subprocess

import pytest
from sandbox_executor.agent_defaults import MissingAgentTickerError
from sandbox_executor.plan_paths import (
    StageIdParts,
    branch_dir,
    build_stage_name,
    calibration_report_rel,
    find_calibration_report,
    find_plan_file,
    legacy_calibration_report_rel,
    legacy_plan_file_rel,
    model_code,
    parse_stage_id,
    plan_file_rel,
    plan_parent_dir,
    resolve_calibration_report_rel,
    stage_label,
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
def test_model_code_required_mappings(model, expected):
    assert model_code(model) == expected


@pytest.mark.parametrize(
    "model",
    [
        "qwen2.5-coder:32b",
        "gemini-3.5-flash",
        "local-model",
        "llama3:8b-instruct-q4_K_M",
        "mixtral-8x7b",
        "claude-haiku-4-5",
        "gpt-4.1",
        "provider/om-1",
    ],
)
def test_model_code_of_arbitrary_names_is_a_valid_ref_component(model):
    code = model_code(model)
    assert code
    check = subprocess.run(
        ["git", "check-ref-format", "--allow-onelevel", code],
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, f"'{code}' from '{model}' is not a valid ref component: {check.stderr}"


def test_model_code_with_no_usable_characters_raises():
    with pytest.raises(ValueError, match="model-code"):
        model_code("---")


def test_stage_label_combines_ticker_and_model_code():
    assert stage_label("claude-agent", "claude-sonnet-5-5") == "CLA-CS5.5"
    assert stage_label("antigravity-agent", "gemini-3.8-flash-medium") == "ANA-G3.8FM"
    with pytest.raises(MissingAgentTickerError):
        stage_label("codex-agent", "gpt-5-codex")


def test_build_stage_name_has_no_hex():
    name = build_stage_name("P", 1794567531, "claude-agent", "claude-sonnet-5-5")
    assert name == "P-1794567531-CLA-CS5.5"
    assert build_stage_name("E", 1794567598, "pi-agent", "claude-opus-5-5") == "E-1794567598-PIA-CO5.5"


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        (
            "P-1787048480-antigravity-agent-gemini-3.5-flash",
            StageIdParts("P", 1787048480, 1, "antigravity-agent-gemini-3.5-flash"),
        ),
        (
            "E-1794294214-claude-agent-claude-opus-5-5",
            StageIdParts("E", 1794294214, 1, "claude-agent-claude-opus-5-5"),
        ),
        (
            "P-1794294214-0a2285cf-claude-agent-claude-opus-5-5",
            StageIdParts(
                "P",
                1794294214,
                2,
                "0a2285cf-claude-agent-claude-opus-5-5",
                disambiguator="0a2285cf",
            ),
        ),
        (
            "E-1794476365-334a8266-claude-agent-claude-sonnet-5-5",
            StageIdParts(
                "E",
                1794476365,
                2,
                "334a8266-claude-agent-claude-sonnet-5-5",
                disambiguator="334a8266",
            ),
        ),
        ("P-1794567531-CLA-CS5.5", StageIdParts("P", 1794567531, 3, "CLA-CS5.5", ticker="CLA", model_code="CS5.5")),
        ("E-1794567598-ANA-G3.8FM", StageIdParts("E", 1794567598, 3, "ANA-G3.8FM", ticker="ANA", model_code="G3.8FM")),
    ],
)
def test_parse_stage_id_classifies_all_three_generations(identifier, expected):
    assert parse_stage_id(identifier) == expected


def test_every_historical_ledger_id_parses_as_generation_one_or_two():
    import json
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[3]
    for ledger, key in (("plans.jsonl", "plan_id"), ("executions.jsonl", "execution_id")):
        path = repo_root / "holon-knowledge" / "ledger" / ledger
        if not path.exists():
            pytest.skip(f"no {ledger} in this checkout")
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            identifier = row.get(key)
            if not isinstance(identifier, str) or not identifier.startswith(("P-", "E-")):
                continue
            parsed = parse_stage_id(identifier)
            assert parsed.generation in (1, 2), f"{identifier} classified as generation {parsed.generation}"


def test_parse_stage_id_rejects_non_stage_ids():
    for bad in ("I-123-slug", "P-notanumber-x", "plans/P-1.md", ""):
        with pytest.raises(ValueError):
            parse_stage_id(bad)


def test_branch_dir_normalises_refs_and_suffixes():
    assert branch_dir("I-1-x/_") == "I-1-x"
    assert branch_dir("refs/heads/I-1-x/P-2-CLA-CO5.5/_") == "I-1-x/P-2-CLA-CO5.5"
    assert branch_dir("origin/I-1-x") == "I-1-x"


def test_plan_paths_mirror_the_branch_nesting():
    # Root intent.
    assert plan_file_rel("I-1794567515-sleepfix/_", "P-1794567531-CLA-CS5.5") == (
        "plans/I-1794567515-sleepfix/P-1794567531-CLA-CS5.5.md"
    )
    assert calibration_report_rel("I-1794567515-sleepfix", "P-1794567531-CLA-CS5.5") == (
        "plans/I-1794567515-sleepfix/P-1794567531-CLA-CS5.5_calibration.md"
    )
    # Child intent ({parent}--{index}-{slug}).
    child = "I-1-root/P-2-CLA-CO5.5--1-kid"
    assert plan_file_rel(child, "P-3-CLA-CO5.5") == f"plans/{child}/P-3-CLA-CO5.5.md"
    # A plan nested under another plan (I-.../P-.../P-...).
    nested = "I-1-root/P-2-CLA-CO5.5"
    assert plan_file_rel(nested, "P-3-ANA-G4A") == f"plans/{nested}/P-3-ANA-G4A.md"
    assert legacy_plan_file_rel("P-1-old") == "plans/P-1-old.md"
    assert legacy_calibration_report_rel("P-1-old") == "plans/P-1-old_calibration.md"


def test_find_plan_file_prefers_nested_and_falls_back_to_flat(tmp_path):
    nested = tmp_path / "plans" / "I-1-x" / "P-2-CLA-CO5.5.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("nested", encoding="utf-8")
    flat = tmp_path / "plans" / "P-2-CLA-CO5.5.md"
    flat.write_text("flat", encoding="utf-8")
    assert find_plan_file(str(tmp_path), "P-2-CLA-CO5.5", "I-1-x/_") == "plans/I-1-x/P-2-CLA-CO5.5.md"
    nested.unlink()
    assert find_plan_file(str(tmp_path), "P-2-CLA-CO5.5", "I-1-x/_") == "plans/P-2-CLA-CO5.5.md"
    flat.unlink()
    assert find_plan_file(str(tmp_path), "P-2-CLA-CO5.5", "I-1-x/_") is None


def test_calibration_report_is_written_beside_the_plan_file(tmp_path):
    plan_branch = "I-1-x/P-2-CLA-CO5.5/_"
    nested = tmp_path / "plans" / "I-1-x" / "P-2-CLA-CO5.5.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("plan", encoding="utf-8")
    assert resolve_calibration_report_rel(str(tmp_path), plan_branch, "P-2-CLA-CO5.5") == (
        "plans/I-1-x/P-2-CLA-CO5.5_calibration.md"
    )
    nested.unlink()
    # No plan file yet: the nested path mirroring the plan branch is used.
    assert resolve_calibration_report_rel(str(tmp_path), plan_branch, "P-2-CLA-CO5.5") == (
        "plans/I-1-x/P-2-CLA-CO5.5_calibration.md"
    )
    # Old-generation flat plan file: the report stays beside it.
    flat = tmp_path / "plans" / "P-9-old.md"
    flat.write_text("plan", encoding="utf-8")
    assert resolve_calibration_report_rel(str(tmp_path), "", "P-9-old") == "plans/P-9-old_calibration.md"
    assert plan_parent_dir(plan_branch) == "I-1-x"
    assert find_calibration_report(str(tmp_path), "P-9-old") is None
