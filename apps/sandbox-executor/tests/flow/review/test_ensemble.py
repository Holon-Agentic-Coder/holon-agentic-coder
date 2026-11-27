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
from sandbox_executor.flow.review.ensemble import _semantic_pr_text
from sandbox_executor.review_report import collect_review_data
from sandbox_executor.review_target import ReviewTarget

from tests.flow.github.fake_github import FakeGitHub, merge_view
from tests.flow.helpers import _git, _reviewer_with_data_block, _verified_diff
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


class TestScopedDeltaRequest:
    """A scoped pass reviews the delta request; a full pass, or a failed scoped build, the unchanged full request."""

    @staticmethod
    def _scoped(tmp_path: Path) -> tuple[FlowContext, ReviewTarget]:
        from sandbox_executor.flow.review.delta_review import begin_review_pass, delta_state_of

        repo = tmp_path / "work"
        (repo / "src").mkdir(parents=True)
        _git(repo, "init", "-q", "-b", "main")
        (repo / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")
        reviewed = _git(repo, "rev-parse", "HEAD")
        (repo / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
        _git(repo, "commit", "-q", "-am", "fix")
        head = _git(repo, "rev-parse", "HEAD")
        ctx = FlowContext(repo_dir=str(repo), execution_branch="main")
        delta_state_of(ctx).update(
            last_reviewed_head=reviewed, anchor_head=reviewed, scoped_chain=0, last_critical=False
        )
        assert begin_review_pass(ctx, str(repo), head, 2).scoped
        return ctx, ReviewTarget(raw="main", head_branch="main", head_sha=head)

    def test_scoped_pass_reviews_the_delta_request(self, tmp_path):
        from sandbox_executor.flow.review.ensemble import _ensemble_iteration

        ctx, target = self._scoped(tmp_path)
        ensemble = FakeEnsemble()
        with patch("sandbox_executor.flow.review.ensemble.build_review_request") as full_request:
            _reviewers, error = _ensemble_iteration(ctx, target, "main", 2, ("T", "B"), ensemble, 1)

        assert error is None
        full_request.assert_not_called()
        request = ensemble.calls[0]["request"]
        assert "## Scoped Delta Review" in request.pr_body
        assert "+x = 2" in request.diff_text

    def test_failed_scoped_build_falls_back_to_the_full_request(self, tmp_path):
        from sandbox_executor.flow.review.delta_review import current_decision
        from sandbox_executor.flow.review.ensemble import _ensemble_iteration

        from tests.review_fakes import fake_review_request

        ctx, target = self._scoped(tmp_path)
        ensemble = FakeEnsemble()
        with (
            patch(
                "sandbox_executor.flow.review.delta_review_request.build_delta_request",
                side_effect=RuntimeError("boom"),
            ),
            patch("sandbox_executor.flow.review.ensemble.build_review_request", side_effect=fake_review_request),
        ):
            _reviewers, error = _ensemble_iteration(ctx, target, "main", 2, ("T", "B"), ensemble, 1)

        assert error is None
        assert ensemble.calls[0]["request"].diff_text == "diff --git a/x b/x\n"
        assert (current_decision(ctx).mode, current_decision(ctx).reason) == ("full", "scoped request failed")
        assert any("scoped request failed; falling back to full" in line for line in ctx.logs)

    def test_full_pass_builds_the_unchanged_full_request(self, tmp_path):
        from sandbox_executor.flow.review.ensemble import _ensemble_iteration

        ctx = FlowContext(repo_dir=str(tmp_path), execution_branch="b")
        target = ReviewTarget(raw="b", head_branch="b", head_sha="0" * 40)
        with (
            patch("sandbox_executor.flow.review.ensemble._review_base_ref", return_value="origin/main"),
            patch("sandbox_executor.flow.review.ensemble.build_review_request") as full_request,
        ):
            _ensemble_iteration(ctx, target, "main", 1, ("T", "B"), FakeEnsemble(), 1)

        full_request.assert_called_once_with(target, "origin/main", 1, "T", "B", str(tmp_path))

    def test_container_dispatch_forwards_and_removes_the_scoped_decision(self, tmp_path):
        from sandbox_executor.flow.review.delta_review_forwarding import FORWARDED_REL

        ctx, target = self._scoped(tmp_path)
        seen: dict = {}

        def dispatch(_context, _role, args, result_file):
            seen["args"] = args
            seen["forwarded"] = json.loads((Path(ctx.target_worktree) / FORWARDED_REL).read_text(encoding="utf-8"))
            Path(result_file).write_text(json.dumps({"ensemble_verdict": "APPROVED", "reviewers": []}), "utf-8")
            return 0

        with patch("sandbox_executor.flow.review.ensemble._dispatch_role", side_effect=dispatch):
            flow_module._dispatch_review_ensemble(ctx, target, 2, 1)

        assert f"--delta-request={FORWARDED_REL}" in seen["args"]
        assert seen["forwarded"]["mode"] == "scoped" and seen["forwarded"]["head"] == target.head_sha
        assert not (Path(ctx.target_worktree) / FORWARDED_REL).exists()
        assert _git(Path(ctx.target_worktree), "status", "--porcelain") == ""


def _context_with_plan(tmp_path, plan_payload: dict) -> FlowContext:
    ctx = FlowContext(repo_dir=str(tmp_path), execution_branch="b", agent="claude-agent", model="claude-opus-5-5")
    ctx.stage_results["plan"] = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS, payload=plan_payload)
    return ctx


def test_semantic_pr_text_names_the_flow_runner(tmp_path):
    ctx = _context_with_plan(tmp_path, {"plan_id": "P-1795696276-CLA-CO5.5", "ev": 76.51})
    _title, body = _semantic_pr_text(ctx)
    assert "- **Agent:** `claude-agent`" in body
    assert "- **Model:** `claude-opus-5-5`" in body
    assert "- **Plan ID:** `P-1795696276-CLA-CO5.5`" in body


def test_semantic_pr_text_prefers_the_plan_payload_runner(tmp_path):
    ctx = _context_with_plan(tmp_path, {"plan_id": "P-1", "agent": "plan-agent", "model": "plan-model"})
    _title, body = _semantic_pr_text(ctx)
    assert "- **Agent:** `plan-agent`" in body
    assert "- **Model:** `plan-model`" in body
