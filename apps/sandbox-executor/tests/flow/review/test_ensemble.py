"""Tests for the reviewer data blocks that travel from the ensemble into the posted review."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from sandbox_executor import flow as flow_module
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    StageResult,
    StageStatus,
    post_pr_review_consensus,
    run_review_loop,
)
from sandbox_executor.review_report import collect_review_data
from sandbox_executor.review_target import ReviewTarget

from tests.flow.helpers import _reviewer_with_data_block, _verified_diff
from tests.review_fakes import FakeEnsemble, install_fake_reviewers


class TestReviewDataSeam:
    """Reviewer data blocks travel from the reviewer results through the flow into the posted body."""

    def test_reviewer_data_block_reaches_the_body_passed_to_gh(self, tmp_path, monkeypatch):
        install_fake_reviewers(monkeypatch, FakeEnsemble([_reviewer_with_data_block()]))
        ctx = FlowContext(repo_dir=str(tmp_path), execution_branch="I-1-x/P-1-x/E-1-x/_", dry_run=False)
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"test_pass_rate": 1.0, "exit_code": 0}
        )
        posted: list[dict[str, Any]] = []

        def capture_post(**kwargs: Any) -> dict[str, Any]:
            gh = MagicMock(return_value=MagicMock(returncode=0, stdout="ok", stderr=""))
            with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run", gh):
                result = post_pr_review_consensus(**kwargs)
            posted.extend({"argv": call.args[0]} for call in gh.call_args_list)
            return result

        with (
            patch(
                "sandbox_executor.flow.review.loop.check_pr_mergeability", return_value={"merge_state_status": "CLEAN"}
            ),
            patch(
                "sandbox_executor.flow.review.loop.probe_ci_checks",
                return_value={"all_passed": True, "source": "local_tests"},
            ),
            patch("sandbox_executor.flow.review.loop.post_pr_review_consensus", side_effect=capture_post),
            _verified_diff(),
        ):
            res = run_review_loop(ctx, auto_pr=False, ensemble_size=1, max_iterations=1)

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
