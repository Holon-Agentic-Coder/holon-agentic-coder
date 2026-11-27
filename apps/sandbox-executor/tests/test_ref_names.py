"""Branch-name limit, boundaries, deterministic slug shortening and the new short stage segments."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor import ref_names
from sandbox_executor.budgets import MAX_SUB_INTENT_DEPTH
from sandbox_executor.flow.context import FlowContext, StageStatus
from sandbox_executor.flow.stages.intent import run_intent_stage
from sandbox_executor.intent_assumption_rules import default_assumptions_fields
from sandbox_executor.ledger import build_execution_id, build_plan_id
from sandbox_executor.naming import model_code, ticker_for
from sandbox_executor.ref_names import (
    CALIBRATION_SUFFIX,
    MAX_BRANCH_LENGTH,
    MAX_REF_LENGTH,
    MAX_SLUG_LENGTH,
    REF_PREFIX,
    RefTooLongError,
    SlugTooLongError,
    calibration_branch,
    ensure_chain_fits,
    ensure_stage_segment_fits,
    fit_child_intent_branch,
    fit_parts,
    fit_root_intent_branch,
    measured_length,
    named_push_rejection,
    resolve_root_intent_branch,
    root_intent_slug,
    shorten_text,
    stage_segment_length,
    truncate_derived_slug,
    validate_supplied_slug,
)

AGENT = "claude-agent"
MODEL = "claude-sonnet-5-5"
SLUG_32 = "a-thirtytwo-character-slug-here1"
SEQ = 1794391544


def _padded(length_with_calibrated_suffix: int) -> list[ref_names.Part]:
    """Parts whose name measures ``length_with_calibrated_suffix`` characters without the ref prefix."""
    fixed = length_with_calibrated_suffix - len(CALIBRATION_SUFFIX) - len("I-1-")
    return [ref_names.Part("prefix", "I-1-"), ref_names.Part("slug", "s" * fixed, shortenable=True)]


def test_limit_constants_record_the_measured_service_limit():
    assert MAX_REF_LENGTH == 255
    assert MAX_BRANCH_LENGTH == MAX_REF_LENGTH - len(REF_PREFIX) == 244
    assert len(CALIBRATION_SUFFIX) - len("/_") == 9
    assert MAX_SLUG_LENGTH == 32


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


def test_stage_segment_length_matches_a_real_new_format_segment():
    plan_id = build_plan_id(SEQ, f"I-{SEQ}-short", AGENT, MODEL)
    assert len(f"/{plan_id}") == stage_segment_length(len(model_code(MODEL)))


def test_ledger_builders_compose_short_hexless_names():
    plan_id = build_plan_id(SEQ, f"I-{SEQ}-short/_", AGENT, MODEL)
    assert plan_id == f"P-{SEQ}-CLA-CS5.5"
    execution_id = build_execution_id(SEQ, f"I-{SEQ}-short/{plan_id}/_", AGENT, MODEL)
    assert execution_id == f"E-{SEQ}-CLA-CS5.5"
    assert not re.search(r"-[0-9a-f]{8}-", plan_id + execution_id)


def test_two_levels_of_32_character_nesting_fit_without_shortening():
    """The acceptance arithmetic: a root and two nested child intents, each with a 32-character slug, fit 255."""
    root = fit_root_intent_branch(SEQ, SLUG_32)
    assert root.originals == {} and root.name == f"I-{SEQ}-{SLUG_32}"
    branch = root.name
    for index in (1, 2):
        plan_id = build_plan_id(SEQ, branch, AGENT, MODEL)
        plan_branch = f"{branch}/{plan_id}"
        child = fit_child_intent_branch(plan_branch, index, SLUG_32)
        assert child.originals == {}, f"level {index} was shortened"
        branch = child.name
    plan_id = build_plan_id(SEQ, branch, AGENT, MODEL)
    execution_id = build_execution_id(SEQ, f"{branch}/{plan_id}", AGENT, MODEL)
    calibrated = f"{branch}/{plan_id}/{execution_id}{CALIBRATION_SUFFIX}"
    assert len(REF_PREFIX) + len(calibrated) <= MAX_REF_LENGTH


def test_deeper_nesting_falls_back_to_slug_shortening_and_the_length_check():
    # A long derived slug still goes through the kept shortening path rather than overflowing the ref.
    deep_parent = f"I-{SEQ}-{SLUG_32}/P-{SEQ}-CLA-CS5.5"
    child = fit_child_intent_branch(deep_parent, 1, "slug-" * 40)
    assert "child slug" in child.originals and measured_length(child.name) <= MAX_REF_LENGTH
    # Beyond what shortening can absorb, the kept length check engages instead of producing an over-limit name.
    guarded = False
    branch = fit_root_intent_branch(SEQ, SLUG_32).name
    for index in range(1, 12):
        try:
            plan_id = build_plan_id(SEQ, branch, AGENT, MODEL)
            branch = fit_child_intent_branch(f"{branch}/{plan_id}", index, SLUG_32).name
        except RefTooLongError:
            guarded = True
            break
        assert measured_length(branch) <= MAX_REF_LENGTH
    assert guarded, "the length check never engaged at depth"


def test_depth_three_children_fit_at_every_level():
    branch = "I-1-root"
    for index in range(1, MAX_SUB_INTENT_DEPTH + 1):
        plan_id = build_plan_id(SEQ, branch, AGENT, MODEL)
        execution_id = build_execution_id(SEQ, f"{branch}/{plan_id}", AGENT, MODEL)
        assert measured_length(f"{branch}/{plan_id}/{execution_id}") <= MAX_REF_LENGTH
        branch = fit_child_intent_branch(f"{branch}/{plan_id}", index, SLUG_32).name
        assert measured_length(branch) <= MAX_REF_LENGTH


def test_an_impossible_name_raises_with_limit_length_and_segment():
    with pytest.raises(RefTooLongError) as caught:
        ensure_stage_segment_fits("I-1-" + "p" * 300, f"P-{SEQ}-CLA-CS5.5")
    assert caught.value.limit == MAX_REF_LENGTH
    assert caught.value.length > MAX_REF_LENGTH
    assert caught.value.segment == "parent"
    assert "255" in str(caught.value) and "parent" in str(caught.value)


def test_ensure_chain_fits_accepts_a_normal_intent_and_rejects_an_oversized_one():
    ensure_chain_fits(f"I-{SEQ}-{SLUG_32}", AGENT, MODEL)
    ensure_chain_fits(f"I-{SEQ}-{SLUG_32}", "", "")
    with pytest.raises(RefTooLongError):
        ensure_chain_fits("I-1-" + "p" * 240, AGENT, MODEL)


def test_calibration_branch_replaces_the_suffix_without_stripping_characters():
    assert calibration_branch("I-1-x/P-2-y/E-3-z_/_") == "I-1-x/P-2-y/E-3-z_/calibrated"


def test_a_recorded_branch_is_kept_when_the_rule_changes():
    data = {"branch": f"I-1-{SLUG_32}/_"}
    with patch("sandbox_executor.ref_names.fit_root_intent_branch", side_effect=AssertionError("recomputed")):
        assert resolve_root_intent_branch(data, SLUG_32, 99, AGENT, MODEL) == f"I-1-{SLUG_32}"
    assert "original_slug" not in data


def test_supplied_slug_over_the_limit_is_rejected_with_limit_and_length():
    long_slug = "s" * 40
    with pytest.raises(SlugTooLongError) as caught:
        validate_supplied_slug(long_slug)
    assert "32" in str(caught.value) and "40" in str(caught.value)
    assert validate_supplied_slug(SLUG_32) == SLUG_32


def test_derived_slug_is_cut_at_a_word_boundary():
    assert truncate_derived_slug("short-slug") == "short-slug"
    cut = truncate_derived_slug("alpha-beta-gamma-delta-epsilon-zeta-eta-theta")
    assert len(cut) <= MAX_SLUG_LENGTH
    assert cut == "alpha-beta-gamma-delta-epsilon"
    assert len(truncate_derived_slug("x" * 80)) == MAX_SLUG_LENGTH


def test_stage_one_fails_before_any_branch_exists(tmp_path):
    ctx = FlowContext(
        target_worktree=str(tmp_path),
        dry_run=True,
        intent_data={**default_assumptions_fields(), "goal": "g", "slug": "x", "branch": "I-1-" + "p" * 300},
    )
    result = run_intent_stage(ctx)
    assert result.status == StageStatus.FAILED
    assert "255" in result.error and "characters" in result.error
    assert not ctx.intent_branch
    assert not (tmp_path / ".git").exists()


def test_stage_one_rejects_a_supplied_root_slug_over_the_limit_before_any_branch_exists(tmp_path):
    long_slug = "a-supplied-slug-that-is-far-too-long-for-a-branch"
    intent = {**default_assumptions_fields(), "goal": "g", "slug": long_slug}
    ctx = FlowContext(target_worktree=str(tmp_path), dry_run=True, agent=AGENT, model=MODEL, intent_data=intent)
    result = run_intent_stage(ctx)
    assert result.status == StageStatus.FAILED
    assert f"at most {MAX_SLUG_LENGTH} characters" in result.error and f"{len(long_slug)} characters" in result.error
    assert not ctx.intent_branch
    assert not (tmp_path / ".git").exists()


def test_stage_one_cuts_a_slug_derived_from_the_goal_at_a_word_boundary(tmp_path):
    goal = "Rework the calibration report so it names every plan and execution it covers"
    ctx = FlowContext(
        target_worktree=str(tmp_path),
        dry_run=True,
        agent=AGENT,
        model=MODEL,
        intent_data={**default_assumptions_fields(), "goal": goal},
    )
    result = run_intent_stage(ctx)
    assert result.status == StageStatus.SUCCESS, result.error
    slug = ctx.intent_data["slug"]
    assert slug == "rework-the-calibration-report-so" and len(slug) <= MAX_SLUG_LENGTH


def test_root_intent_slug_keeps_an_accepted_recorded_branch_and_sanitises_a_supplied_slug():
    assert root_intent_slug({"slug": "Fix The Thing!"}, "ignored") == "fix-the-thing"
    # On resume the recorded branch was accepted when the intent was opened; an older long slug is not re-judged.
    recorded = {"slug": "s" * 40, "branch": f"I-1-{'s' * 40}"}
    assert root_intent_slug(recorded, "ignored") == "s" * 40
    with pytest.raises(SlugTooLongError):
        root_intent_slug({"slug": "s" * 33}, "ignored")


def test_push_rejection_is_named_and_other_output_passes_through():
    branch = "I-1-x/P-2-y/E-3-z/_"
    named = named_push_rejection(branch, "remote: ref too long\n! [remote rejected] pre-receive hook declined")
    assert named.startswith(f"ref too long: branch {branch} ({len(REF_PREFIX + branch)} characters)")
    assert named_push_rejection(branch, "network unreachable") == "network unreachable"


def test_host_push_names_a_ref_too_long_rejection(monkeypatch):
    from sandbox_executor.flow import host_remote

    class _Rejected:
        returncode = 1
        stdout = ""
        stderr = 'remote: ref too long: "refs/heads/I-1-x/E-2-y/_"\nremote: pre-receive hook declined'

    monkeypatch.setattr(host_remote, "run_git", lambda *args, **kwargs: _Rejected())
    with pytest.raises(host_remote.HostRemoteError) as info:
        host_remote.push_commit("/unused", "a" * 40, "I-1-x/E-2-y/_")
    assert "ref too long: branch I-1-x/E-2-y/_" in str(info.value)
    assert "pre-receive hook declined" in str(info.value)


_ALLOWED_SUFFIX_BUILDERS = {
    "calibration/branches.py",
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


def test_root_slug_at_the_limit_passes_unchanged_through_the_real_builder():
    data: dict[str, str] = {}
    branch = resolve_root_intent_branch(data, SLUG_32, SEQ, AGENT, MODEL)
    assert branch == f"I-{SEQ}-{SLUG_32}" and data == {"slug": SLUG_32}
    assert ticker_for(AGENT) == "CLA"


def test_root_name_is_shortened_only_when_it_exceeds_the_limit_or_leaves_no_room_below():
    room = MAX_REF_LENGTH - len(REF_PREFIX) - len(CALIBRATION_SUFFIX) - len(f"I-{SEQ}-")
    assert "slug" in fit_root_intent_branch(SEQ, "s" * (room + 1)).originals
    at_limit = fit_root_intent_branch(SEQ, "s" * room)
    assert measured_length(at_limit.name) + 2 * ref_names._MIN_SEGMENT_LENGTH <= MAX_REF_LENGTH
