from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sandbox_executor import flow
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.lazy_exports_review import REVIEW_LAZY_EXPORTS
from sandbox_executor.flow.review import main_baseline_guard
from sandbox_executor.flow.review.ci_failure_routing import (
    BLOCK,
    RERUN,
    RESOLVE,
    FailedRun,
    record_routing,
    route_failure,
)
from sandbox_executor.flow.review.consensus_withdrawal import withdraw_consensus
from sandbox_executor.flow.review.main_baseline_guard import (
    MAIN_BASELINE_FAILING,
    BaselineGuardSeams,
    baseline_block_for_ci_finding,
    baseline_blocked_on_record,
    classify_baseline_block,
    guard_target_sync,
    inspect_main_baseline,
    should_preserve_consensus,
)

TARGET_SHA = "b" * 40
HEAD = "a" * 40
MAIN_ONLY_PATH = "apps/sandbox-executor/src/untouched.py"
PR_PATH = "apps/sandbox-executor/src/touched.py"


def _check(name: str, conclusion: str | None = "failure", status: str = "completed") -> dict[str, Any]:
    return {"name": name, "status": status, "conclusion": conclusion, "check_run_id": 7, "run_id": "9"}


def _seams(
    checks: list[dict[str, Any]] | None,
    failing_paths: list[str],
    pr_paths: set[str],
    target_sha: str | None = TARGET_SHA,
) -> BaselineGuardSeams:
    return BaselineGuardSeams(
        probe_head_checks=lambda _sha: checks,
        target_sha_reader=lambda _repo, _branch: target_sha,
        pr_diff_paths=lambda _repo, _branch, _head: pr_paths,
        failing_paths_lookup=lambda _check: failing_paths,
    )


def _context(tmp_path: Path) -> FlowContext:
    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True, exist_ok=True)
    return FlowContext(
        target_worktree=str(tmp_path),
        repo_dir=str(tmp_path),
        execution_branch="I-1-x/P-1-A/E-1-A/_",
        intent_data={"target_branch": "main"},
        agent="a",
        model="m",
    )


def _inspect(seams: BaselineGuardSeams, check_names: list[str] | None = None) -> Any:
    return inspect_main_baseline(".", "main", HEAD, check_names=check_names, seams=seams)


@pytest.mark.parametrize("check_name", ["hygiene", "Unit tests (3.13)"])
def test_red_guarded_check_on_untouched_path_is_a_baseline_failure(check_name: str) -> None:
    failure = _inspect(_seams([_check(check_name)], [MAIN_ONLY_PATH], {PR_PATH}))

    assert failure is not None
    assert failure.target_sha == TARGET_SHA
    assert failure.untouched_paths == (MAIN_ONLY_PATH,)
    assert failure.failing_checks == (check_name,)


def test_failing_path_touched_by_the_pr_is_not_a_baseline_failure() -> None:
    assert _inspect(_seams([_check("hygiene")], [f"./{PR_PATH}"], {PR_PATH})) is None


@pytest.mark.parametrize(
    "seams",
    [
        _seams([_check("hygiene", "success")], [MAIN_ONLY_PATH], set()),
        _seams([_check("hygiene", None, "in_progress")], [MAIN_ONLY_PATH], set()),
        _seams(None, [MAIN_ONLY_PATH], set()),
        _seams([_check("hygiene")], [], set()),
        _seams([_check("lint")], [MAIN_ONLY_PATH], set()),
        _seams([_check("hygiene")], [MAIN_ONLY_PATH], set(), target_sha=None),
    ],
    ids=["green", "pending", "unreachable", "no-paths", "unguarded-check", "no-origin-ref"],
)
def test_baseline_guard_fails_safe(seams: BaselineGuardSeams) -> None:
    assert _inspect(seams) is None


def test_check_names_restrict_the_guard_to_the_prs_failing_checks() -> None:
    seams = _seams([_check("hygiene")], [MAIN_ONLY_PATH], set())

    assert _inspect(seams, ["unit"]) is None
    assert _inspect(seams, ["Hygiene"]) is not None


def test_blocked_on_record_carries_both_dict_and_string_contracts() -> None:
    failure = _inspect(_seams([_check("hygiene")], [MAIN_ONLY_PATH], set()))

    record = baseline_blocked_on_record(failure, HEAD, ["FIND-CI"])

    assert record["blocked_on"] == record["code"] == MAIN_BASELINE_FAILING
    assert record["reason"].startswith(f"{MAIN_BASELINE_FAILING}: ")
    assert record["finding_ids"] == ["FIND-CI"]
    assert record["head_sha"] == HEAD


def test_guard_target_sync_refuses_only_an_approved_pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    seams = _seams([_check("hygiene")], [MAIN_ONLY_PATH], set())
    monkeypatch.setattr(main_baseline_guard, "approved_head_of", lambda _context: None)
    monkeypatch.setattr(main_baseline_guard, "diff_gate_of", lambda _context: None)

    assert guard_target_sync(context, "main", HEAD, seams) is None

    monkeypatch.setattr(main_baseline_guard, "approved_head_of", lambda _context: HEAD)

    assert guard_target_sync(context, "main", HEAD, seams) is not None
    assert context.review_state["blocked_on"]["blocked_on"] == MAIN_BASELINE_FAILING

    context.dry_run = True
    assert guard_target_sync(context, "main", HEAD, seams) is None


def test_guard_target_sync_allows_sync_when_main_is_green(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_baseline_guard, "approved_head_of", lambda _context: HEAD)
    seams = _seams([_check("hygiene", "success")], [MAIN_ONLY_PATH], set())

    assert guard_target_sync(_context(tmp_path), "main", HEAD, seams) is None


def test_classify_baseline_block_persists_the_record(tmp_path: Path) -> None:
    context = _context(tmp_path)
    seams = _seams([_check("hygiene")], [MAIN_ONLY_PATH], set())

    record = classify_baseline_block(context, "main", HEAD, ["hygiene"], seams)

    assert record is not None
    assert context.review_state["blocked_on"] == record
    assert baseline_block_for_ci_finding(context, "main", None, {}, None) == record


def test_baseline_block_for_ci_finding_is_none_without_a_baseline_record(tmp_path: Path) -> None:
    context = _context(tmp_path)
    seams = _seams(None, [], set())

    assert classify_baseline_block(context, "main", HEAD, [], seams) is None
    assert "blocked_on" not in context.review_state


def test_route_failure_blocks_baseline_failures_and_records_blocked_on(tmp_path: Path) -> None:
    context = _context(tmp_path)
    runs = [FailedRun("hygiene", "11", "failure")]

    decision = route_failure(context, HEAD, MAIN_BASELINE_FAILING, runs)
    record_routing(context, HEAD, MAIN_BASELINE_FAILING, decision, runs)

    assert decision == BLOCK
    assert decision not in {RESOLVE, RERUN}
    assert context.review_state["ci_routing"]["last"]["blocked_on"] == MAIN_BASELINE_FAILING


def test_withdraw_consensus_preserves_approval_for_baseline_blocked_records(tmp_path: Path) -> None:
    context = _context(tmp_path)
    record = {"head_sha": HEAD, "blocked_on": MAIN_BASELINE_FAILING, "runs": []}

    result = withdraw_consensus(context, record)

    assert should_preserve_consensus(record)
    assert result["withdrawn"] is False
    assert result["preserved"] is True
    assert result["blocked_on"] == MAIN_BASELINE_FAILING
    assert result["comment_posted"] is False
    assert not should_preserve_consensus({"blocked_on": None})


@pytest.mark.parametrize(
    "name",
    ["BaselineFailure", *sorted(REVIEW_LAZY_EXPORTS["sandbox_executor.flow.review.main_baseline_guard"].split())],
)
def test_new_review_exports_are_importable_from_flow(name: str) -> None:
    assert getattr(flow, name) is not None
