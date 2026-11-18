"""Branch-name limit, boundaries, deterministic shortening, slug caps and the wiring around the single builder."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor import ref_names
from sandbox_executor.budgets import MAX_SUB_INTENT_DEPTH
from sandbox_executor.flow.context import FlowContext, StageStatus
from sandbox_executor.flow.stages.intent import run_intent_stage
from sandbox_executor.ledger import build_execution_id, build_plan_id
from sandbox_executor.plan_paths import new_stage_id
from sandbox_executor.ref_names import (
    CALIBRATION_SUFFIX,
    MAX_BRANCH_LENGTH,
    MAX_REF_LENGTH,
    MAX_SLUG_LENGTH,
    MAX_STAGE_SEGMENT_LENGTH,
    REF_PREFIX,
    RefTooLongError,
    SlugTooLongError,
    calibration_branch,
    ensure_chain_fits,
    fit_child_intent_branch,
    fit_parts,
    fit_root_intent_branch,
    measured_length,
    named_push_rejection,
    resolve_root_intent_branch,
    shorten_text,
    truncate_slug,
    validate_slug_length,
)

AGENT = "claude-agent"
MODEL = "claude-sonnet-5-5"
SLUG_32 = "a-thirty-two-character-root-slug"
SEQ = 1794391544


def _padded(length_with_calibrated_suffix: int) -> list[ref_names.Part]:
    """Parts whose name measures ``length_with_calibrated_suffix`` characters without the ref prefix."""
    fixed = length_with_calibrated_suffix - len(CALIBRATION_SUFFIX) - len("I-1-")
    return [ref_names.Part("prefix", "I-1-"), ref_names.Part("slug", "s" * fixed, shortenable=True)]


def test_limit_constants_record_the_measured_service_limit():
    assert MAX_REF_LENGTH == 255
    assert MAX_BRANCH_LENGTH == MAX_REF_LENGTH - len(REF_PREFIX) == 244
    assert len(CALIBRATION_SUFFIX) - len("/_") == 9
    assert len(SLUG_32) == MAX_SLUG_LENGTH == 32


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
    assert build_plan_id(SEQ, f"I-{SEQ}-short", AGENT, MODEL) == f"P-{SEQ}-CLA-CS5.5"
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


def _names_below(parent: str, depth: int) -> list[str]:
    """Every plan, execution and child intent name from ``parent`` down to ``depth`` child levels."""
    plan_branch = f"{parent}/{build_plan_id(SEQ, parent, AGENT, MODEL)}"
    execution = f"{plan_branch}/{build_execution_id(SEQ, plan_branch, AGENT, MODEL)}"
    names = [plan_branch, execution]
    if depth:
        child = fit_child_intent_branch(plan_branch, depth, SLUG_32).name
        names += [child, *_names_below(child, depth - 1)]
    return names


def test_root_and_two_nested_children_with_32_char_slugs_fit_unshortened():
    """Acceptance: root + two child levels, all 32-character slugs, fit 255 with gen-3 segments, unshortened."""
    root = fit_root_intent_branch(SEQ, SLUG_32)
    assert root.originals == {} and root.name == f"I-{SEQ}-{SLUG_32}"
    level_one_plan = f"{root.name}/{new_stage_id('P', SEQ, AGENT, MODEL)}"
    child_one = fit_child_intent_branch(level_one_plan, 1, SLUG_32)
    assert child_one.originals == {}
    level_two_plan = f"{child_one.name}/{new_stage_id('P', SEQ, AGENT, MODEL)}"
    child_two = fit_child_intent_branch(level_two_plan, 1, SLUG_32)
    assert child_two.originals == {}
    leaf_plan = f"{child_two.name}/{new_stage_id('P', SEQ, AGENT, MODEL)}"
    leaf_exec = f"{leaf_plan}/{new_stage_id('E', SEQ, AGENT, MODEL)}"
    calibrated = f"{leaf_exec}{CALIBRATION_SUFFIX}"
    assert len(f"refs/heads/{calibrated}") <= MAX_REF_LENGTH


def test_deeper_nesting_falls_back_to_slug_shortening_and_the_length_check():
    parent = "I-1-" + "r" * 150
    child = fit_child_intent_branch(f"{parent}/_", 1, SLUG_32)
    assert "child slug" in child.originals
    assert measured_length(child.name, extra=2 * MAX_STAGE_SEGMENT_LENGTH) <= MAX_REF_LENGTH
    with pytest.raises(RefTooLongError):
        build_plan_id(SEQ, "I-1-" + "p" * 300, AGENT, MODEL)


def test_depth_three_children_fit_at_every_level():
    names = _names_below("I-1-root", MAX_SUB_INTENT_DEPTH)
    assert len(names) == 2 * (MAX_SUB_INTENT_DEPTH + 1) + MAX_SUB_INTENT_DEPTH
    assert all(measured_length(name) <= MAX_REF_LENGTH for name in names)


def test_ledger_builders_measure_room_for_the_chain():
    parent = f"I-1-{SLUG_32}"
    plan_id = build_plan_id(SEQ, parent, AGENT, MODEL)
    assert measured_length(f"{parent}/{plan_id}", extra=MAX_STAGE_SEGMENT_LENGTH) <= MAX_REF_LENGTH
    execution_id = build_execution_id(SEQ, f"{parent}/{plan_id}", AGENT, MODEL)
    assert measured_length(f"{parent}/{plan_id}/{execution_id}") <= MAX_REF_LENGTH


def test_an_impossible_name_raises_with_limit_length_and_segment():
    with pytest.raises(RefTooLongError) as caught:
        build_plan_id(SEQ, "I-1-" + "p" * 300, AGENT, MODEL)
    assert caught.value.limit == MAX_REF_LENGTH
    assert caught.value.length > MAX_REF_LENGTH
    assert "255" in str(caught.value)


def test_ensure_chain_fits_accepts_a_normal_intent_and_rejects_an_oversized_one():
    ensure_chain_fits(f"I-{SEQ}-{SLUG_32}")
    with pytest.raises(RefTooLongError):
        ensure_chain_fits("I-1-" + "p" * 240)


def test_calibration_branch_replaces_the_suffix_without_stripping_characters():
    assert calibration_branch("I-1-x/P-2-y/E-3-z_/_") == "I-1-x/P-2-y/E-3-z_/calibrated"


def test_a_recorded_branch_is_kept_when_the_rule_changes():
    data = {"branch": f"I-1-{SLUG_32}/_"}
    with patch("sandbox_executor.ref_names.fit_root_intent_branch", side_effect=AssertionError("recomputed")):
        assert resolve_root_intent_branch(data, SLUG_32, 99) == f"I-1-{SLUG_32}"
    assert "original_slug" not in data


def test_a_supplied_root_slug_over_32_characters_is_rejected_with_limit_and_length():
    slug = "s" * 40
    with pytest.raises(SlugTooLongError) as caught:
        resolve_root_intent_branch({}, slug, SEQ)
    assert caught.value.limit == 32 and caught.value.length == 40
    assert "32" in str(caught.value) and "40" in str(caught.value)
    with pytest.raises(SlugTooLongError):
        validate_slug_length(slug)
    assert validate_slug_length(SLUG_32) == SLUG_32


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("short", "short"),
        (SLUG_32, SLUG_32),
        ("a-thirty-two-character-root-slug-and-more", "a-thirty-two-character-root-slug"),
        ("a-thirty-two-character-root-slugs-and-more", "a-thirty-two-character-root"),
        ("x" * 40, "x" * 32),
        ("-" * 40, "-" * 32),
    ],
)
def test_engine_derived_slugs_are_cut_at_a_word_boundary(slug, expected):
    truncated = truncate_slug(slug)
    assert truncated == expected
    assert len(truncated) <= MAX_SLUG_LENGTH
    assert truncated


def test_truncated_sibling_slugs_still_get_unique_names():
    """Truncation that makes two siblings equal is disambiguated by the engine's duplicate suffixing."""
    from sandbox_executor.decomposition_rule import _sub_intent_dicts

    shared = "a shared very long sibling slug prefix one"
    items = [(shared + " alpha", []), (shared + " beta", [])]
    slugs = [d["slug"] for d in _sub_intent_dicts(items)]
    assert len(set(slugs)) == 2
    assert slugs[1].endswith("-2")


def test_stage_one_fails_before_any_branch_exists(tmp_path):
    ctx = FlowContext(
        target_worktree=str(tmp_path),
        dry_run=True,
        intent_data={"goal": "g", "slug": "x", "branch": "I-1-" + "p" * 300},
    )
    result = run_intent_stage(ctx)
    assert result.status == StageStatus.FAILED
    assert "255" in result.error and "characters" in result.error
    assert not ctx.intent_branch
    assert not (tmp_path / ".git").exists()


def test_stage_one_rejects_a_supplied_root_slug_over_32_characters(tmp_path):
    ctx = FlowContext(
        target_worktree=str(tmp_path),
        dry_run=True,
        intent_data={"goal": "g", "slug": "s" * 40},
    )
    result = run_intent_stage(ctx)
    assert result.status == StageStatus.FAILED
    assert "32" in result.error and "40" in result.error
    assert not ctx.intent_branch


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


@pytest.mark.parametrize("slug_length", [8, 20, 32])
def test_root_slug_that_fits_alone_is_unchanged_through_the_real_builder(slug_length):
    slug = "s" * slug_length
    data: dict[str, str] = {}
    branch = resolve_root_intent_branch(data, slug, SEQ)
    assert branch == f"I-{SEQ}-{slug}" and data == {"slug": slug}


def test_root_name_is_shortened_only_when_it_exceeds_the_limit_or_leaves_no_room_below():
    room = MAX_REF_LENGTH - len(REF_PREFIX) - len(CALIBRATION_SUFFIX) - len(f"I-{SEQ}-")
    assert "slug" in fit_root_intent_branch(SEQ, "s" * (room + 1)).originals
    at_limit = fit_root_intent_branch(SEQ, "s" * room)
    assert measured_length(at_limit.name) + 2 * MAX_STAGE_SEGMENT_LENGTH <= MAX_REF_LENGTH
