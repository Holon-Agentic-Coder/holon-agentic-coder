"""Tests for the reviewer data blocks that travel from the ensemble into the posted review."""

import json
from pathlib import Path
from unittest.mock import patch

from sandbox_executor import flow as flow_module
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    StageResult,
    StageStatus,
    run_review_loop,
)
from sandbox_executor.review_report import collect_review_data
from sandbox_executor.review_target import ReviewTarget

from tests.flow.github.fake_github import FakeGitHub, merge_view
from tests.flow.helpers import _reviewer_with_data_block, _verified_diff
from tests.review_fakes import FakeEnsemble, install_fake_reviewers


def _posts(github: FakeGitHub) -> list[tuple]:
    """The ``post_review`` calls made on the fake, as ``(target, flag, body)``."""
    return [args for name, args in github.calls if name == "post_review"]


class TestReviewDataSeam:
    """Reviewer data blocks travel from the reviewer results through the flow into the posted body."""

    def test_reviewer_data_block_reaches_the_body_passed_to_gh(self, tmp_path, monkeypatch):
        install_fake_reviewers(monkeypatch, FakeEnsemble([_reviewer_with_data_block()]))
        ctx = FlowContext(repo_dir=str(tmp_path), execution_branch="I-1-x/P-1-x/E-1-x/_", dry_run=False)
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"test_pass_rate": 1.0, "exit_code": 0}
        )
        github = FakeGitHub(standing_view=merge_view())
        with patch("shutil.which", return_value=None), _verified_diff():
            res = run_review_loop(ctx, auto_pr=False, ensemble_size=1, max_iterations=1, github=github)

        posted = [
            {"argv": ["gh", "pr", "review", target, flag, "--body", body]} for target, flag, body in _posts(github)
        ]
        bodies = [entry["argv"][entry["argv"].index("--body") + 1] for entry in posted]
        assert len(bodies) == 1
        body = bodies[0]
        assert body.startswith("## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration 1)")
        assert "| Security | CHANGES | The error path leaks the token. |" in body
        assert "| SEC-7 | important | src/app.py:12 | 1 | The handler drops the error. |" in body
        assert "Redact the token before logging." in body
        assert "VERDICT:" not in body
        assert posted[0]["argv"][:3] == ["gh", "pr", "review"]
        assert posted[0]["argv"][4] == "--comment"
        assert res["review_data"] == {
            "roles": [{"role": "Security", "verdict": "CHANGES"}],
            "findings": [{"id": "SEC-7", "severity": "important", "reviewers": [1]}],
        }

    def test_container_transport_carries_the_data_block_to_the_host(self, tmp_path):
        reviewer = _reviewer_with_data_block()
        ctx = FlowContext(repo_dir=str(tmp_path), execution_branch="I-1-x/P-1-x/E-1-x/_")
        target = ReviewTarget(raw="I-1-x/P-1-x/E-1-x/_", head_branch="I-1-x/P-1-x/E-1-x/_", head_sha="0" * 40)

        def dispatch(_context, _role, _args, result_file):
            outcome = {"ensemble_verdict": "CHANGES_REQUESTED", "reviewers": [flow_module._reviewer_summary(reviewer)]}
            Path(result_file).write_text(json.dumps(outcome), encoding="utf-8")
            return 1

        with patch("sandbox_executor.flow.review.ensemble._dispatch_role", side_effect=dispatch):
            reviewers, _verdict, error = flow_module._dispatch_review_ensemble(ctx, target, 1, 1)

        assert error is None
        assert collect_review_data(reviewers) == collect_review_data([reviewer])
        assert collect_review_data(reviewers).findings[0].recommendation == "Redact the token before logging."

    def test_review_summary_lines_list_activated_roles_and_open_findings(self):
        loop_res = {
            "ensemble_verdict": "CHANGES_REQUESTED",
            "reviewers": [],
            "review_data": {"roles": [{"role": "Security", "verdict": "CHANGES"}], "findings": [{"id": "SEC-7"}]},
        }
        assert (
            flow_module.review_summary_lines(loop_res)[-1] == "  Roles          : Security (CHANGES); open findings: 1"
        )
        assert flow_module.review_summary_lines({})[-1] == "  Roles          : none reported; open findings: 0"
