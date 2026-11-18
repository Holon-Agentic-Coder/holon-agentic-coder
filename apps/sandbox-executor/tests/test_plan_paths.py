"""Model codes, three-generation ID parsing and nested intent-keyed plan file paths."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.plan_paths import (
    execution_segment,
    is_calibration_report_path,
    legacy_calibration_report,
    legacy_plan_file,
    model_code,
    parse_flow_id,
    plan_file_paths,
    plan_segment,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("model", "code"),
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
def test_required_model_codes(model, code):
    assert model_code(model) == code


def _ref_component_valid(component: str) -> bool:
    probe = subprocess.run(
        ["git", "check-ref-format", "--allow-onelevel", component], capture_output=True, text=True
    )
    return probe.returncode == 0


@pytest.mark.parametrize(
    "model",
    [
        "qwen2.5-coder:32b",
        "llama3.1:70b-instruct-q4_K_M",
        "mistral-7b",
        "provider/odd~name^with:invalid*chars",
        "dots..everywhere..",
        "----",
        "4-4-4",
        "UPPER-case-MIX-9",
    ],
)
def test_arbitrary_local_model_names_yield_valid_nonempty_ref_components(model):
    code = model_code(model)
    assert code
    assert _ref_component_valid(code), f"'{code}' is not a valid git ref component"


def test_historical_ledger_model_names_all_map_to_valid_codes():
    models: set[str] = set()
    for name in ("plans.jsonl", "executions.jsonl"):
        path = REPO_ROOT / "holon-knowledge" / "ledger" / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line).get("model")
            except json.JSONDecodeError:
                continue
            if isinstance(value, str) and value.strip():
                models.add(value)
    assert models, "no historical model names found"
    for model in models:
        code = model_code(model)
        assert code and _ref_component_valid(code), f"{model} -> '{code}'"


def test_segments_compose_the_documented_example():
    assert plan_segment(1794567531, "CLA", "CS5.5") == "P-1794567531-CLA-CS5.5"
    assert execution_segment(1794567598, "CLA", "CS5.5") == "E-1794567598-CLA-CS5.5"


def test_parse_flow_id_exposes_the_generation_so_callers_never_reguess():
    gen1 = parse_flow_id("P-1-antigravity-agent-gemini-3.5-flash")
    gen2 = parse_flow_id("E-2-0123abcd-claude-agent-claude-opus-5-5")
    gen3 = parse_flow_id("P-3-PIA-CO5.5")
    assert (gen1.generation, gen2.generation, gen3.generation) == (1, 2, 3)
    assert gen2.disambiguator == "0123abcd"
    assert gen3.label == "PIA-CO5.5"


def test_parse_flow_id_rejects_non_ids():
    for bad in ("X-1-foo", "P-abc-def", "plain-text", ""):
        with pytest.raises(ValueError):
            parse_flow_id(bad)


def test_root_intent_plan_path_mirrors_the_branch():
    plan_rel, report_rel = plan_file_paths("I-1794567515-sleepfix/P-1794567531-CLA-CS5.5/_")
    assert plan_rel == "plans/I-1794567515-sleepfix/P-1794567531-CLA-CS5.5.md"
    assert report_rel == "plans/I-1794567515-sleepfix/P-1794567531-CLA-CS5.5_calibration.md"


def test_child_intent_plan_path_mirrors_the_double_dash_nesting():
    branch = "I-1-root/P-2-CLA-CF5/I-1-root/P-2-CLA-CF5--1-kid/P-3-CLA-CF5/_"
    plan_rel, report_rel = plan_file_paths(branch)
    assert plan_rel == "plans/I-1-root/P-2-CLA-CF5/I-1-root/P-2-CLA-CF5--1-kid/P-3-CLA-CF5.md"
    assert report_rel.endswith("P-3-CLA-CF5_calibration.md")


def test_plan_nested_under_a_plan_keeps_every_ancestor_segment():
    plan_rel, _report_rel = plan_file_paths("I-1-root/P-2-CLA-CF5/P-3-ANA-G3.8FM/_")
    assert plan_rel == "plans/I-1-root/P-2-CLA-CF5/P-3-ANA-G3.8FM.md"


@pytest.mark.parametrize(
    "branch",
    [
        "I-1-root/P-2-CLA-CF5/_",
        "refs/heads/I-1-root/P-2-CLA-CF5/E-3-CLA-CF5/calibrated",
        "origin/I-1-root--1-kid/P-2-CLA-CF5/_",
    ],
)
def test_paths_equal_the_branch_with_prefixes_and_suffixes_stripped(branch):
    plan_rel, report_rel = plan_file_paths(branch)
    bare = branch
    for prefix in ("refs/heads/", "origin/"):
        bare = bare.removeprefix(prefix)
    bare = bare.removesuffix("/_").removesuffix("/calibrated")
    assert plan_rel == f"plans/{bare}.md"
    assert report_rel == f"plans/{bare}_calibration.md"


def test_a_bare_identifier_is_not_a_nested_plan_branch():
    with pytest.raises(ValueError):
        plan_file_paths("P-2-CLA-CF5")


def test_legacy_paths_and_report_detection_cover_both_generations():
    assert legacy_plan_file("P-1-a") == "plans/P-1-a.md"
    assert legacy_calibration_report("P-1-a") == "plans/P-1-a_calibration.md"
    assert is_calibration_report_path("plans/P-1-a_calibration.md")
    assert is_calibration_report_path("plans/I-1-x/P-1-a_calibration.md")
    assert not is_calibration_report_path("plans/I-1-x/P-1-a.md")
    assert not is_calibration_report_path("docs/P-1-a_calibration.md")
