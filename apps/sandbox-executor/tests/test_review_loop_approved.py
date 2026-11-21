"""Focused tests for the review loop's consensus-approved path and its ledger summary row."""

from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.review_fakes import install_fake_reviewers


@pytest.fixture(autouse=True)
def _sandboxed_clean_reviewers(monkeypatch):
    install_fake_reviewers(monkeypatch)


def _ledger_rows(repo_dir: Path) -> list[dict]:
    ledger = repo_dir / "holon-knowledge" / "ledger" / "executions.jsonl"
    if not ledger.exists():
        return []
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]


def _loop_ctx(tmp_path: Path, target: str = "feature/x", dry_run: bool = False):
    from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus

    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True, exist_ok=True)
    ctx = FlowContext(repo_dir=str(tmp_path), execution_branch=target, token_reduce=False, dry_run=dry_run)
    ctx.stage_results["execute"] = StageResult(
        stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"test_pass_rate": 1.0, "exit_code": 0}
    )
    return ctx


def _green_loop_patches(verdict=None, post=None, which=None, ci=None):
    from sandbox_executor.review_target import DiffVerdict

    stack = ExitStack()
    verify = stack.enter_context(
        patch(
            "sandbox_executor.review_target.verify_review_diff",
            return_value=(verdict or DiffVerdict.VERIFIED, "guard detail"),
        )
    )
    stack.enter_context(patch("sandbox_executor.flow.review.loop.shutil.which", return_value=which))
    stack.enter_context(
        patch("sandbox_executor.flow.review.loop.check_pr_mergeability", return_value={"is_dirty": False})
    )
    stack.enter_context(
        patch(
            "sandbox_executor.flow.review.loop.probe_ci_checks",
            return_value=ci or {"all_passed": True, "source": "local_tests"},
        )
    )
    stack.enter_context(patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc123"))
    mock_post = stack.enter_context(
        patch(
            "sandbox_executor.flow.review.loop.post_pr_review_consensus",
            **(post or {"return_value": {"dual_posted": True}}),
        )
    )
    return stack, verify, mock_post


def test_approved_loop_records_summary_row_status_approved(tmp_path):
    from sandbox_executor.flow import run_review_loop

    stack, _verify, _mock_post = _green_loop_patches()
    with stack:
        res = run_review_loop(_loop_ctx(tmp_path), target="feature/x", auto_pr=False, max_iterations=1)

    assert res["consensus"]["approved"] is True
    assert res["halted_for_human"] is False
    assert res["ready_for_maintainer_merge"] is True
    rows = _ledger_rows(tmp_path)
    summary = next(r for r in rows if r.get("entry_type") == "summary")
    assert summary["status"] == "approved"
    expected_msg = "Review Loop reached consensus approval after 1 iteration(s); PR ready for maintainer merge"
    assert expected_msg in summary["summary"]
