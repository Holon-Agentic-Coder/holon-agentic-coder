"""Branch-name limit, boundaries, deterministic shortening and the wiring around the single builder."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor import ref_names
from sandbox_executor.budgets import MAX_SUB_INTENT_DEPTH
from sandbox_executor.flow.context import FlowContext, StageStatus
from sandbox_executor.flow.stages.intent import run_intent_stage
from sandbox_executor.ledger import build_execution_id, build_plan_id
from sandbox_executor.ref_names import (
    CALIBRATION_SUFFIX,
    MAX_BRANCH_LENGTH,
    MAX_REF_LENGTH,
    REF_PREFIX,
    RefTooLongError,
    calibration_branch,
    ensure_chain_fits,
    fit_child_intent_branch,
    fit_execution_id,
    fit_parts,
    fit_plan_id,
    fit_root_intent_branch,
    measured_length,
    named_push_rejection,
    original_labels,
    resolve_root_intent_branch,
    shorten_text,
)

AGENT = "claude-agent"
MODEL = "claude-sonnet-5-5"
SLUG_56 = "a-fifty-six-character-slug-for-the-decomposed-refcap-run"
DIS = "abdc9b6d"
SEQ = 1794391544


def _padded(length_with_calibrated_suffix: int) -> list[ref_names.Part]:
    """Parts whose name measures ``length_with_calibrated_suffix`` characters without the ref prefix."""
    fixed = length_with_calibrated_suffix - len(CALIBRATION_SUFFIX) - len("I-1-")
    return [ref_names.Part("prefix", "I-1-"), ref_names.Part("slug", "s" * fixed, shortenable=True)]


def test_limit_constants_record_the_measured_service_limit():
    assert MAX_REF_LENGTH == 255
    assert MAX_BRANCH_LENGTH == MAX_REF_LENGTH - len(REF_PREFIX) == 244
    assert len(CALIBRATION_SUFFIX) - len("/_") == 9


@pytest.mark.parametrize("without_prefix", [243, 244, 245])
def test_boundary_without_prefix_and_with_it(without_prefix):
    parts = _padded(without_prefix)
    unfitted = "".join(p.text for p in parts)
    assert measured_length(unfitted) == len(REF_PREFIX) + without_prefix
    result = fit_parts(parts)
    if len(REF_PREFIX) + without_prefix <= MAX_REF_LENGTH:
        assert result.name == unfitted and result.originals == {}
    else:
        assert result.name != unfitted and "slug" in result.originals
    assert measured_length(result.name) <= MAX_REF_LENGTH


@pytest.mark.parametrize("with_prefix", [254, 255, 256])
def test_boundary_with_prefix(with_prefix):
    parts = _padded(with_prefix - len(REF_PREFIX))
    result = fit_parts(parts)
    assert (result.name == "".join(p.text for p in parts)) is (with_prefix <= MAX_REF_LENGTH)
    assert measured_length(result.name) <= MAX_REF_LENGTH


def test_a_name_that_fits_is_unchanged():
    assert fit_root_intent_branch(SEQ, "short").name == f"I-{SEQ}-short"
    plan = fit_plan_id(f"I-{SEQ}-short", SEQ, DIS, AGENT, MODEL)
    assert plan.name == f"P-{SEQ}-{DIS}-{AGENT}-{MODEL}" and plan.originals == {}
    assert fit_child_intent_branch("I-1-root/P-2-x/_", 1, "kid").name == "I-1-root/P-2-x--1-kid"


def test_shortening_is_deterministic_and_keeps_the_digest_of_the_original():
    long_slug = "x" * 400
    first = fit_child_intent_branch("I-1-root/_", 1, long_slug)
    assert first == fit_child_intent_branch("I-1-root/_", 1, long_slug)
    assert ref_names.digest(long_slug) in first.name
    assert first.originals == {"child slug": long_slug}
    assert "--" not in first.name.split("--1-", 1)[1]


def test_long_slugs_sharing_a_prefix_do_not_collide():
    shared = "y" * 300
    names = {fit_child_intent_branch("I-1-root/_", 1, f"{shared}{tail}").name for tail in ("one", "two", "three")}
    assert len(names) == 3


def test_shorten_text_never_goes_below_digest_only_length():
    assert len(shorten_text("z" * 50, 2)) == ref_names.DIGEST_LENGTH
    assert shorten_text("short", 10) == "short"


def _names_below(parent: str, depth: int, agent: str, model: str) -> list[str]:
    """Every plan, execution and child intent name from ``parent`` down to ``depth`` child levels."""
    plan_branch = f"{parent}/{fit_plan_id(parent, SEQ, DIS, agent, model).name}"
    execution = f"{plan_branch}/{fit_execution_id(plan_branch, SEQ, DIS, agent, model).name}"
    names = [plan_branch, execution]
    if depth:
        child = fit_child_intent_branch(plan_branch, depth, SLUG_56).name
        names += [child, *_names_below(child, depth - 1, agent, model)]
    return names


def test_the_failing_decomposed_case_fits_at_every_depth():
    root = fit_root_intent_branch(SEQ, SLUG_56).name
    for index in range(1, 5):
        plan_branch = f"{root}/{fit_plan_id(root, SEQ, DIS, AGENT, MODEL).name}"
        child = fit_child_intent_branch(plan_branch, index, SLUG_56).name
        for name in (root, plan_branch, child, *_names_below(child, 0, AGENT, MODEL)):
            assert measured_length(name) <= MAX_REF_LENGTH
            assert len(REF_PREFIX + name + "/_") <= MAX_REF_LENGTH


def test_depth_three_children_fit_at_every_level():
    names = _names_below("I-1-root", MAX_SUB_INTENT_DEPTH, "agent", "model")
    assert len(names) == 2 * (MAX_SUB_INTENT_DEPTH + 1) + MAX_SUB_INTENT_DEPTH
    assert all(measured_length(name) <= MAX_REF_LENGTH for name in names)


def test_labels_are_shortened_before_the_root_slug_and_originals_are_reported():
    parent = "I-1-" + "r" * 150
    plan = fit_plan_id(parent, SEQ, DIS, "agent-" + "a" * 60, "model-" + "m" * 60)
    assert plan.originals.keys() <= {"agent", "model"} and plan.originals
    assert original_labels(plan.name, "a", "b") == {"original_agent": "a", "original_model": "b"}
    assert original_labels(f"P-1-{DIS}-{AGENT}-{MODEL}", AGENT, MODEL) == {}


def test_ledger_builders_route_through_the_fitting_builders():
    parent = "I-1-" + "r" * 140
    plan_id = build_plan_id(SEQ, parent, "a" * 60, "m" * 60, disambiguator=DIS)
    assert measured_length(f"{parent}/{plan_id}") <= MAX_REF_LENGTH
    execution_id = build_execution_id(SEQ, f"{parent}/{plan_id}", "a" * 60, "m" * 60, disambiguator=DIS)
    assert measured_length(f"{parent}/{plan_id}/{execution_id}") <= MAX_REF_LENGTH
    assert build_plan_id(SEQ, "I-1-x/_", AGENT, MODEL, disambiguator=DIS) == f"P-{SEQ}-{DIS}-{AGENT}-{MODEL}"


def test_an_impossible_name_raises_with_limit_length_and_segment():
    with pytest.raises(RefTooLongError) as caught:
        fit_plan_id("I-1-" + "p" * 300, SEQ, DIS, AGENT, MODEL)
    assert caught.value.limit == MAX_REF_LENGTH
    assert caught.value.length > MAX_REF_LENGTH
    assert caught.value.segment == "parent"
    assert "255" in str(caught.value) and "parent" in str(caught.value)


def test_ensure_chain_fits_accepts_a_normal_intent_and_rejects_an_oversized_one():
    ensure_chain_fits(f"I-{SEQ}-{SLUG_56}", AGENT, MODEL)
    with pytest.raises(RefTooLongError):
        ensure_chain_fits("I-1-" + "p" * 240, AGENT, MODEL)


def test_calibration_branch_replaces_the_suffix_without_stripping_characters():
    assert calibration_branch("I-1-x/P-2-y/E-3-z_/_") == "I-1-x/P-2-y/E-3-z_/calibrated"


def test_a_recorded_branch_is_kept_when_the_rule_changes():
    data = {"branch": f"I-1-{SLUG_56}/_"}
    with patch("sandbox_executor.ref_names.fit_root_intent_branch", side_effect=AssertionError("recomputed")):
        assert resolve_root_intent_branch(data, SLUG_56, 99, AGENT, MODEL) == f"I-1-{SLUG_56}"
    assert "original_slug" not in data


def test_resolving_a_long_root_slug_records_the_original_slug():
    data: dict[str, str] = {}
    slug = "s" * 400
    branch = resolve_root_intent_branch(data, slug, SEQ, AGENT, MODEL)
    assert data["original_slug"] == slug and branch == f"I-{SEQ}-{data['slug']}"
    short: dict[str, str] = {}
    resolve_root_intent_branch(short, "ok", SEQ, AGENT, MODEL)
    assert short == {"slug": "ok"}


def test_stage_one_fails_before_any_branch_exists(tmp_path):
    ctx = FlowContext(
        target_worktree=str(tmp_path),
        dry_run=True,
        intent_data={"goal": "g", "slug": "x", "branch": "I-1-" + "p" * 300},
    )
    result = run_intent_stage(ctx)
    assert result.status == StageStatus.FAILED
    assert "255" in result.error and "parent" in result.error and "characters" in result.error
    assert not ctx.intent_branch
    assert not (tmp_path / ".git").exists()


def test_push_rejection_is_named_and_other_output_passes_through():
    branch = "I-1-x/P-2-y/E-3-z/_"
    named = named_push_rejection(branch, "remote: ref too long\n! [remote rejected] pre-receive hook declined")
    assert named.startswith(f"ref too long: branch {branch} ({len(REF_PREFIX + branch)} characters)")
    assert named_push_rejection(branch, "network unreachable") == "network unreachable"


def test_unpushed_work_report_names_a_ref_too_long_rejection(tmp_path, capsys):
    from sandbox_executor.entrypoint.executor import _report_unpushed_work

    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q", str(tmp_path)], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], cwd=tmp_path, check=True)
    tip = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.strip()
    _report_unpushed_work(str(tmp_path), "I-1-x/E-2-y/_", tip, "remote: ref too long")
    err = capsys.readouterr().err
    assert "ref too long: branch I-1-x/E-2-y/_" in err
    assert "BEGIN UNPUSHED EXECUTION PATCH" in err


_ALLOWED_SUFFIX_BUILDERS = {
    "calibration.py",
    "entrypoint/executor.py",
    "entrypoint/intent_creator.py",
    "entrypoint/planner.py",
    "flow/child_progress.py",
    "flow/stages/execute.py",
    "flow/stages/intent.py",
    "flow/stages/plan.py",
}


def test_no_other_module_builds_a_branch_with_a_literal_suffix():
    # Heuristic: only catches the f"...}/_" pattern, not concatenation or format() constructions.
    root = Path(ref_names.__file__).parent
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if re.search(r'f"[^"\n]*\}/_"', path.read_text(encoding="utf-8"))
        and str(path.relative_to(root)) not in _ALLOWED_SUFFIX_BUILDERS | {"ref_names.py"}
    ]
    assert offenders == []
