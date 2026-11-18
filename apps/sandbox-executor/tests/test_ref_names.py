"""Branch-name limit, boundaries, deterministic slug shortening and the gen-3 segment wiring."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor import ref_names
from sandbox_executor.budgets import MAX_SUB_INTENT_DEPTH
from sandbox_executor.flow.context import FlowContext, StageStatus
from sandbox_executor.flow.stages.intent import run_intent_stage
from sandbox_executor.ledger import build_execution_id, build_plan_id
from sandbox_executor.plan_paths import parse_flow_id
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
    ensure_segment_fits,
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
SLUG_32 = "a-thirty-two-character-long-slug"
SEQ = 1794391544

REPO_ROOT = Path(__file__).resolve().parents[3]


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


# ---------------------------------------------------------------------------
# Gen-3 identifiers: shape, round-trips for all three generations, ref-limit fit
# ---------------------------------------------------------------------------


def test_new_ids_carry_ticker_and_model_code_and_no_hex():
    plan_id = build_plan_id(SEQ, f"I-{SEQ}-short/_", AGENT, MODEL)
    assert plan_id == f"P-{SEQ}-CLA-CS5.5"
    execution_id = build_execution_id(SEQ, f"I-{SEQ}-short/{plan_id}/_", AGENT, MODEL)
    assert execution_id == f"E-{SEQ}-CLA-CS5.5"
    for identifier in (plan_id, execution_id):
        assert not re.search(r"\b[0-9a-f]{8}\b", identifier)


def _ledger_ids(name: str, key: str) -> list[str]:
    path = REPO_ROOT / "holon-knowledge" / "ledger" / name
    if not path.exists():
        pytest.skip(f"repository ledger {name} is absent")
    ids = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line).get(key)
        except json.JSONDecodeError:
            continue
        if isinstance(value, str) and value[:2] in ("P-", "E-"):
            ids.append(value)
    return ids


def test_every_historical_ledger_id_parses_as_generation_one_or_two():
    for name, key in (("plans.jsonl", "plan_id"), ("executions.jsonl", "execution_id")):
        for identifier in _ledger_ids(name, key):
            parsed = parse_flow_id(identifier)
            assert parsed.generation in (1, 2), identifier
            assert parsed.kind == identifier[0]
            assert str(parsed.epoch) in identifier


@pytest.mark.parametrize(
    ("identifier", "generation", "label"),
    [
        ("P-1787048480-antigravity-agent-gemini-3.5-flash", 1, "antigravity-agent-gemini-3.5-flash"),
        ("E-1792765484-claude-agent-claude-opus-5-5", 1, "claude-agent-claude-opus-5-5"),
        ("P-1794976507-654a2c7f-claude-agent-claude-fable-5", 2, "claude-agent-claude-fable-5"),
        ("E-1794391637-5549d1a0-claude-agent-claude-sonnet-5-5", 2, "claude-agent-claude-sonnet-5-5"),
        ("P-1794567531-CLA-CS5.5", 3, "CLA-CS5.5"),
        ("E-1794567598-ANA-G3.8FM", 3, "ANA-G3.8FM"),
    ],
)
def test_three_generation_parser_disambiguates_and_round_trips(identifier, generation, label):
    parsed = parse_flow_id(identifier)
    assert parsed.generation == generation
    assert parsed.label == label
    rebuilt = f"{parsed.kind}-{parsed.epoch}-" + (f"{parsed.disambiguator}-" if parsed.disambiguator else "") + label
    assert rebuilt == identifier


def test_ensure_segment_fits_rejects_an_oversized_parent():
    ensure_segment_fits(f"I-{SEQ}-{SLUG_32}/_", f"P-{SEQ}-CLA-CS5.5")
    with pytest.raises(RefTooLongError):
        ensure_segment_fits("I-1-" + "p" * 240, f"P-{SEQ}-CLA-CS5.5")
    with pytest.raises(RefTooLongError):
        build_plan_id(SEQ, "I-1-" + "p" * 240, AGENT, MODEL)


def test_root_and_two_nested_children_with_max_slugs_fit_unshortened():
    """Acceptance criterion 7: 32-character slugs at three levels fit the ref limit with gen-3 segments."""
    assert len(SLUG_32) == MAX_SLUG_LENGTH
    root = fit_root_intent_branch(SEQ, SLUG_32)
    assert root.originals == {}
    branch = root.name
    for index in (1, 2):
        plan_id = build_plan_id(SEQ, branch, AGENT, MODEL)
        plan_branch = f"{branch}/{plan_id}"
        child = fit_child_intent_branch(plan_branch, index, SLUG_32)
        assert child.originals == {}, "a 32-character child slug must fit unshortened"
        branch = child.name
    plan_id = build_plan_id(SEQ, branch, AGENT, MODEL)
    execution_id = build_execution_id(SEQ, f"{branch}/{plan_id}", AGENT, MODEL)
    calibrated = f"{branch}/{plan_id}/{execution_id}{CALIBRATION_SUFFIX}"
    assert len(REF_PREFIX + calibrated) <= MAX_REF_LENGTH


def test_deeper_nesting_falls_back_to_slug_shortening():
    branch = fit_root_intent_branch(SEQ, "root").name
    shortened = False
    for index in range(1, 8):
        plan_id = build_plan_id(SEQ, branch, AGENT, MODEL)
        try:
            child = fit_child_intent_branch(f"{branch}/{plan_id}", index, SLUG_32)
        except RefTooLongError:
            break  # the existing length check still fires when even shortened slugs cannot fit
        shortened = shortened or bool(child.originals)
        branch = child.name
        assert measured_length(branch) <= MAX_REF_LENGTH
    assert shortened, "deep nesting must engage the existing slug shortening"


def _names_below(parent: str, depth: int) -> list[str]:
    """Every plan, execution and child intent name from ``parent`` down to ``depth`` child levels."""
    plan_branch = f"{parent}/{build_plan_id(SEQ, parent, AGENT, MODEL)}"
    execution = f"{plan_branch}/{build_execution_id(SEQ, plan_branch, AGENT, MODEL)}"
    names = [plan_branch, execution]
    if depth:
        child = fit_child_intent_branch(plan_branch, depth, SLUG_32).name
        names += [child, *_names_below(child, depth - 1)]
    return names


def test_depth_three_children_fit_at_every_level():
    names = _names_below("I-1-root", MAX_SUB_INTENT_DEPTH)
    assert len(names) == 2 * (MAX_SUB_INTENT_DEPTH + 1) + MAX_SUB_INTENT_DEPTH
    assert all(measured_length(name) <= MAX_REF_LENGTH for name in names)


# ---------------------------------------------------------------------------
# Slug limit: supplied slugs are rejected, engine-derived slugs are cut at a word boundary
# ---------------------------------------------------------------------------


def test_validate_slug_length_names_the_limit_and_the_actual_length():
    validate_slug_length(SLUG_32)
    with pytest.raises(SlugTooLongError) as caught:
        validate_slug_length("s" * 40)
    assert caught.value.limit == 32
    assert caught.value.length == 40
    assert "32" in str(caught.value) and "40" in str(caught.value)


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("short", "short"),
        (SLUG_32, SLUG_32),
        ("a-thirty-two-character-long-slug-and-more", "a-thirty-two-character-long-slug"),
        ("a-slug-that-cuts-inside-the-last-word-here", "a-slug-that-cuts-inside-the-last"),
        ("x" * 50, "x" * 32),
    ],
)
def test_truncate_slug_cuts_at_a_word_boundary_and_never_returns_empty(slug, expected):
    result = truncate_slug(slug)
    assert result == expected
    assert 0 < len(result) <= MAX_SLUG_LENGTH


def test_an_impossible_name_raises_with_limit_length_and_segment():
    with pytest.raises(RefTooLongError) as caught:
        ensure_chain_fits("I-1-" + "p" * 240, AGENT, MODEL)
    assert caught.value.limit == MAX_REF_LENGTH
    assert caught.value.length > MAX_REF_LENGTH
    assert "255" in str(caught.value)


def test_ensure_chain_fits_accepts_a_normal_intent_and_rejects_an_oversized_one():
    ensure_chain_fits(f"I-{SEQ}-{SLUG_32}", AGENT, MODEL)
    with pytest.raises(RefTooLongError):
        ensure_chain_fits("I-1-" + "p" * 240, AGENT, MODEL)


def test_calibration_branch_replaces_the_suffix_without_stripping_characters():
    assert calibration_branch("I-1-x/P-2-y/E-3-z_/_") == "I-1-x/P-2-y/E-3-z_/calibrated"


def test_a_recorded_branch_is_kept_when_the_rule_changes():
    data = {"branch": f"I-1-{SLUG_32}/_"}
    with patch("sandbox_executor.ref_names.fit_root_intent_branch", side_effect=AssertionError("recomputed")):
        assert resolve_root_intent_branch(data, SLUG_32, 99, AGENT, MODEL) == f"I-1-{SLUG_32}"
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
    assert "255" in result.error and "characters" in result.error
    assert not ctx.intent_branch
    assert not (tmp_path / ".git").exists()


def test_stage_one_rejects_a_supplied_root_slug_over_the_limit_before_any_branch(tmp_path):
    ctx = FlowContext(
        target_worktree=str(tmp_path),
        dry_run=True,
        intent_data={"goal": "g", "slug": "s" * 40},
    )
    result = run_intent_stage(ctx)
    assert result.status == StageStatus.FAILED
    assert "32" in result.error and "40" in result.error
    assert not ctx.intent_branch
    assert not (tmp_path / ".git").exists()


def test_stage_one_rejects_a_tickerless_runner_before_any_branch(tmp_path):
    ctx = FlowContext(
        target_worktree=str(tmp_path),
        dry_run=True,
        agent="codex-agent",
        model="gpt-5-codex",
        intent_data={"goal": "g", "slug": "ok"},
    )
    result = run_intent_stage(ctx)
    assert result.status == StageStatus.FAILED
    assert "codex-agent" in result.error and "ticker" in result.error
    assert not ctx.intent_branch
    assert not (tmp_path / ".git").exists()


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


@pytest.mark.parametrize("slug_length", [10, 20, 32])
def test_root_slug_that_fits_alone_is_unchanged_through_the_real_builder(slug_length):
    slug = "s" * slug_length
    data: dict[str, str] = {}
    branch = resolve_root_intent_branch(data, slug, SEQ, AGENT, MODEL)
    assert branch == f"I-{SEQ}-{slug}" and data == {"slug": slug}


def test_root_name_is_shortened_only_when_it_exceeds_the_limit_or_leaves_no_room_below():
    room = MAX_REF_LENGTH - len(REF_PREFIX) - len(CALIBRATION_SUFFIX) - len(f"I-{SEQ}-")
    assert "slug" in fit_root_intent_branch(SEQ, "s" * (room + 1)).originals
    at_limit = fit_root_intent_branch(SEQ, "s" * room)
    assert measured_length(at_limit.name) + 2 * ref_names._MAX_SEGMENT_RESERVE <= MAX_REF_LENGTH
