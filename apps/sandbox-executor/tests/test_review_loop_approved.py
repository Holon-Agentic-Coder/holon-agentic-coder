"""Focused tests for the review loop's consensus-approved path and its ledger summary row."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.ledger import read_merged_rows

from tests.flow.github.fake_github import FakeGitHub, merge_view
from tests.review_fakes import install_fake_reviewers
from tests.review_loop_stubs import _green_loop_patches, _loop_ctx


@pytest.fixture(autouse=True)
def _sandboxed_clean_reviewers(monkeypatch):
    install_fake_reviewers(monkeypatch)


def _ledger_rows(repo_dir: Path) -> list[dict]:
    return read_merged_rows(str(repo_dir / "holon-knowledge" / "ledger" / "executions.jsonl"))


def test_approved_loop_records_summary_row_status_approved(tmp_path):
    from sandbox_executor.flow import run_review_loop

    stack, _verify, github = _green_loop_patches()
    github.available = True  # ``gh`` for the post; ``shutil.which`` still fences the gh callers outside the adapter
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


@pytest.mark.parametrize(
    ("status", "payload", "expected_snippets", "absent_snippets"),
    [
        (
            "approved",
            {"halted_for_human": False, "consensus": {"approved": True}},
            ["[REVIEW APPROVED]", "ready for the maintainer to merge"],
            ["[SAFETY HALT]"],
        ),
        (
            "halted_for_human",
            {"halted_for_human": True, "consensus": {"approved": True}},
            ["[REVIEW APPROVED]"],
            ["[SAFETY HALT]"],
        ),
        (
            "halted_for_human",
            {
                "halted_for_human": True,
                "consensus": {"approved": False},
                "human_approval_command": "holon review approve X",
            },
            ["[SAFETY HALT]", "To approve manually, run: holon review approve X"],
            ["[REVIEW APPROVED]"],
        ),
        (
            "approved",
            {"halted_for_human": False, "consensus": True},
            ["[REVIEW APPROVED]"],
            ["[SAFETY HALT]"],
        ),
        (
            "halted_for_human",
            {"halted_for_human": True, "consensus": True},
            ["[SAFETY HALT]"],
            ["[REVIEW APPROVED]"],
        ),
        (
            "halted_for_human",
            {"consensus": {"approved": True}},
            ["[REVIEW APPROVED]"],
            ["[SAFETY HALT]", "To approve manually"],
        ),
        (
            "halted_for_human",
            {"consensus": {"approved": False}, "human_approval_command": "holon review approve X"},
            ["[SAFETY HALT]", "To approve manually, run: holon review approve X"],
            ["[REVIEW APPROVED]"],
        ),
        (
            "halted_for_human",
            {},
            ["[SAFETY HALT]"],
            ["[REVIEW APPROVED]"],
        ),
    ],
)
def test_report_review_outcome_renders_approved_legacy_and_safety_halt(
    capsys: pytest.CaptureFixture[str],
    status: str,
    payload: dict,
    expected_snippets: list[str],
    absent_snippets: list[str],
) -> None:
    from sandbox_executor.cli.commands.flow import _report_review_outcome
    from sandbox_executor.flow import FlowStage, StageResult, StageStatus

    res = StageResult(stage=FlowStage.REVIEW, status=StageStatus.from_str(status), payload=payload)
    _report_review_outcome(res)
    out = capsys.readouterr().out
    for snippet in expected_snippets:
        assert snippet in out
    for snippet in absent_snippets:
        assert snippet not in out


def test_report_review_outcome_none_prints_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    from sandbox_executor.cli.commands.flow import _report_review_outcome

    _report_review_outcome(None)
    assert capsys.readouterr().out == ""


def test_resolver_rejected_find_ci_survives_ledger_commit_on_next_iteration(tmp_path: Path, monkeypatch) -> None:
    """Loop-authored executions.jsonl commits between iterations must not invalidate accept_resolver_evidence."""
    from sandbox_executor.entrypoint.test_measurement import TestMeasurement
    from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus, run_review_loop
    from sandbox_executor.flow.review import local_tests
    from sandbox_executor.git import run_git as real_run_git
    from sandbox_executor.review_target import DiffVerdict, ReviewTarget

    from tests.flow.helpers import _git
    from tests.review_flow_repo import build_flow_worktree, ledger_commits
    from tests.review_loop_stubs import EXEC_TARGET

    _origin, work = build_flow_worktree(tmp_path)
    initial_sha = _git(work, "rev-parse", "HEAD")

    monkeypatch.setattr(local_tests, "run_git", real_run_git)
    measure = MagicMock(return_value=TestMeasurement(1.0, 10, 0, 0, 0, None, ""))
    monkeypatch.setattr(local_tests, "run_measurement", measure)

    ctx = FlowContext(repo_dir=str(work), execution_branch=EXEC_TARGET, token_reduce=False)
    ctx.stage_results["execute"] = StageResult(
        stage=FlowStage.EXECUTE,
        status=StageStatus.SUCCESS,
        payload={"head_sha": initial_sha, "test_pass_rate": 0.5, "exit_code": 1},
    )

    def fake_resolver(**kwargs):
        findings = kwargs["findings"]
        return {
            "dispositions": [
                {
                    "id": findings[0].id,
                    "disposition": "rejected",
                    "rejection_reason": "inaccurate",
                    "evidence": "tests/test_app.py::test_ok passed",
                }
            ],
            "pushed": False,
        }

    target_obj = ReviewTarget(raw=EXEC_TARGET, head_branch=EXEC_TARGET, head_sha=initial_sha)
    with (
        patch("sandbox_executor.flow.review.loop._resolve_reviewed_target", return_value=(target_obj, None)),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 file")),
        patch("shutil.which", return_value=None),
    ):
        # A clean PR with no GitHub checks reported, so CI falls back to local tests.
        res = run_review_loop(
            ctx,
            target=EXEC_TARGET,
            max_iterations=3,
            ensemble_size=3,
            auto_pr=False,
            run_resolver_fn=fake_resolver,
            github=FakeGitHub(standing_view=merge_view()),
        )

    commits = ledger_commits(work)
    assert len(commits) == 2
    assert commits[1] != initial_sha
    measure.assert_called_once()
    assert res["consensus"]["approved"] is True
    assert res["total_iterations"] == 2
    assert res["ci_checks"]["test_source"] == "resolver_evidence"
    assert res["ci_checks"]["test_head_sha"] == commits[1]
    assert res["ci_checks"]["test_counts"] == {"passed": 10, "failed": 0, "errored": 0}
