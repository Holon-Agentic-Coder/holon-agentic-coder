"""Tests for the Stage 4 review-resolve loop and the engine-owned loop."""

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor import flow as flow_module
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    PipelineEngine,
    StageResult,
    StageStatus,
    format_review_git_trailers,
    load_checkpoint,
    probe_ci_checks,
    run_review_loop,
    run_review_loop_cli,
    run_review_stage,
    save_checkpoint,
)
from sandbox_executor.flow.github.pr_head import PR_HEAD_TIMEOUT_SECONDS
from sandbox_executor.ledger import read_merged_rows
from sandbox_executor.ledger.fragments import iter_fragment_files
from sandbox_executor.review_target import ReviewTarget

from tests.flow.github.fake_github import FakeGitHub, ci_checks_payload
from tests.flow.helpers import _git, _review_loop_gh_stubs
from tests.flow.lookup_patch import patch_lookup_sites
from tests.review_fakes import FakeEnsemble, clean_reviewer, objecting_reviewer, with_clean_peers
from tests.review_flow_repo import added_rows, build_flow_worktree, changed_paths, ledger_commits
from tests.review_loop_stubs import EXEC_TARGET, _ensemble_ctx, _LoopStubs

#: Where the loop's rows for the ``I-100-intent`` execution branch land (the fragment of its root intent).
EXEC_FRAGMENT = "holon-knowledge/ledger/pending/executions/I-100-intent.jsonl"


class TestReviewLoopStage:
    """Comprehensive tests for the native multi-pass review loop, CI check probing, git trailers and merge halt."""

    def test_probe_ci_checks_github_cli_success(self):
        checks_json = json.dumps(
            [
                {"bucket": "pass", "name": "unit-tests", "state": "COMPLETED", "event": "pull_request"},
                {"bucket": "pass", "name": "lint", "state": "COMPLETED", "event": "pull_request"},
            ]
        )
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run") as mock_subproc,
        ):
            mock_subproc.return_value = MagicMock(returncode=0, stdout=checks_json)
            res = probe_ci_checks("/tmp/repo", "my-exec-branch")
            assert res["source"] == "github_cli"
            assert res["total"] == 2
            assert res["passed"] == 2
            assert res["failed"] == 0
            assert res["pending"] == 0
            assert res["all_passed"] is True

    def test_probe_ci_checks_github_cli_failures(self):
        checks_json = json.dumps(
            [
                {"bucket": "pass", "name": "unit-tests", "state": "COMPLETED", "event": "pull_request"},
                {"bucket": "fail", "name": "lint", "state": "FAILURE", "event": "pull_request"},
            ]
        )
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run") as mock_subproc,
        ):
            mock_subproc.return_value = MagicMock(returncode=0, stdout=checks_json)
            res = probe_ci_checks("/tmp/repo", "my-exec-branch")
            assert res["source"] == "github_cli"
            assert res["total"] == 2
            assert res["passed"] == 1
            assert res["failed"] == 1
            assert res["all_passed"] is False

    def test_probe_ci_checks_fallback_when_gh_missing(self):
        with patch("shutil.which", return_value=None):
            res = probe_ci_checks("/tmp/repo", "my-exec-branch")
            assert res["source"] == "local_tests"
            assert res["all_passed"] is True
            assert res["passed"] == 1
            assert res["failed"] == 0

    def test_format_review_git_trailers(self):
        trailers = format_review_git_trailers(
            action_origin="pr_review",
            iteration=3,
            finding_ids=["FIND-001", "FIND-002"],
            severity="important",
        )
        assert "Action-Origin: pr_review" in trailers
        assert "Review-Iteration: 3" in trailers
        assert "Review-Finding-Ids: FIND-001, FIND-002" in trailers
        assert "Review-Severity: important" in trailers

        clean_trailers = format_review_git_trailers(iteration=1, finding_ids=None, severity="clean")
        assert "Review-Finding-Ids: none" in clean_trailers
        assert "Review-Severity: clean" in clean_trailers

    def test_review_loop_consensus_approval_halts_without_merge(self, tmp_path):
        """Verify review loop approves without halting for human and never invokes git merge main."""
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            dry_run=False,
        )
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"head_sha": "stage3-head", "test_pass_rate": 1.0, "exit_code": 0},
        )
        with (
            patch_lookup_sites(
                "sandbox_executor.flow.git_refs.run_git",
                "sandbox_executor.flow.github.pr.run_git",
                "sandbox_executor.flow.stage_conflicts.run_git",
                "sandbox_executor.flow.stages.calibrate.run_git",
                "sandbox_executor.flow.calibrate_sync.run_git",
                "sandbox_executor.flow.stages.review.run_git",
            ) as mock_git,
            patch("sandbox_executor.flow._execution_effective_diff", return_value=(True, "1 file changed")),
            _review_loop_gh_stubs(),
        ):
            mock_git.return_value = MagicMock(returncode=0, stdout=" 1 file changed, 10 insertions(+)\n")
            res = run_review_loop(ctx, target="I-100-intent/P-200-plan/E-300-exec/_", max_iterations=5)

            # Human-only merge check: git merge main must never have been called
            for call in mock_git.call_args_list:
                args = call[0][0]
                assert "merge" not in args, f"HUMAN-ONLY MERGE RULE VIOLATED: merge invoked with args {args}"

        assert res["consensus"]["approved"] is True
        assert res["halted_for_human"] is False
        assert res["approved"] is True
        assert res["human_merge_gate_enforced"] is True
        assert "holon review approve" in res["human_approval_command"]

    def test_review_loop_stops_after_the_first_non_approved_iteration(self, tmp_path):
        """A non-approved iteration stops the loop at once, well inside ``max_iterations``, since no resolver runs."""
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            dry_run=True,
        )
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.FAILED,
            payload={"head_sha": "stage3-head", "test_pass_rate": 0.0, "exit_code": 1},
        )
        res = run_review_loop(ctx, target="I-100-intent/P-200-plan/E-300-exec/_", max_iterations=3, dry_run=True)
        assert res["consensus"]["approved"] is False
        # No resolver runs, so the loop stops after the first iteration instead of re-reviewing the same diff.
        assert res["total_iterations"] == 1
        assert res["consensus"]["status"] == "rejected"

    def test_review_loop_appends_executions_ledger(self, tmp_path):
        """Assert executions.jsonl receives structured iteration and summary records with trailers when not dry_run."""
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            dry_run=False,
        )
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"head_sha": "stage3-head", "test_pass_rate": 1.0, "exit_code": 0},
        )

        # No PR view and no checks: mergeability falls back to local git and CI to local tests, as without gh.
        github = FakeGitHub()
        with (
            patch("sandbox_executor.flow.github.pr.run_git") as mock_git,
            patch("sandbox_executor.flow._execution_effective_diff", return_value=(True, "1 file changed")),
        ):
            mock_git.return_value = MagicMock(returncode=0, stdout=" 1 file changed, 10 insertions(+)\n")
            run_review_loop(
                ctx, target="I-100-intent/P-200-plan/E-300-exec/_", max_iterations=2, dry_run=False, github=github
            )
        assert github.methods().count("post_review") == 1

        exec_file = ledger_dir / "executions.jsonl"
        assert iter_fragment_files(str(exec_file))

        records = read_merged_rows(str(exec_file))
        action_records = [r for r in records if r.get("entry_type") == "action"]
        summary_records = [r for r in records if r.get("entry_type") == "summary"]

        assert len(action_records) >= 1
        assert action_records[0]["action_origin"] == "pr_review"
        assert action_records[0]["review_iteration"] == 1

        assert len(summary_records) >= 1
        assert summary_records[0]["status"] == "approved"
        assert summary_records[0]["total_iterations"] == 1
        assert summary_records[0]["trailers"]["action_origin"] == "pr_review"

    def test_run_review_loop_cli_direct(self, tmp_path, capsys):
        """Directly exercise run_review_loop_cli return codes and summary output formatting."""
        args = argparse.Namespace(
            target="I-100-intent/P-200-plan/E-300-exec/_",
            repo_dir=str(tmp_path),
            max_iterations=2,
            ensemble_size=3,
            no_auto_pr=False,
            dry_run=True,
            json=False,
            token_reduce=False,
        )
        with patch("sandbox_executor.flow.review.cli.run_review_loop") as mock_loop:
            mock_loop.return_value = {
                "consensus": {"approved": True, "status": "approved", "iterations": 1},
                "ensemble_verdict": "APPROVED",
                "max_iterations": 2,
                "ci_checks": {"total": 1, "passed": 1, "failed": 0, "pending": 0, "source": "local_tests"},
                "human_approval_command": "holon review approve I-100-intent/P-200-plan/E-300-exec/_",
            }
            code = run_review_loop_cli(args)
            assert code == 0
            captured = capsys.readouterr().out
            assert "Holon Review Loop Summary" in captured
            assert "[REVIEW APPROVED]" in captured
            assert "To merge manually" not in captured

        # Test rejected exit code
        with patch("sandbox_executor.flow.review.cli.run_review_loop") as mock_loop:
            mock_loop.return_value = {
                "consensus": {"approved": False, "status": "rejected", "iterations": 2},
                "max_iterations": 2,
                "ci_checks": {"total": 1, "passed": 0, "failed": 1, "pending": 0, "source": "local_tests"},
            }
            code = run_review_loop_cli(args)
            assert code == 1

    def test_review_loop_requires_pushed_true_to_log_and_advance_head(self, tmp_path, monkeypatch):
        """Review loop requires resolver_outcome.pushed to be True before logging new head and advancing target."""
        from sandbox_executor.flow import ResolverOutcome, run_review_loop
        from sandbox_executor.review_report import FindingDisposition

        from tests.review_fakes import FakeEnsemble, install_fake_reviewers, objecting_reviewer
        from tests.test_review_dispatch import EXEC_TARGET
        from tests.test_review_loop import _ensemble_ctx, _LoopStubs

        disp = FindingDisposition(
            id="R1-F1",
            severity="important",
            path="src/app.py",
            line=12,
            summary="important problem in the handler",
            disposition="applied",
        )

        # 1. When pushed is False: context.log must NOT log that a new head was pushed
        tmp1 = tmp_path / "case_unpushed"
        ensemble1 = FakeEnsemble([objecting_reviewer(1)])
        install_fake_reviewers(monkeypatch, ensemble1)
        ctx1 = _ensemble_ctx(tmp1)
        outcome_unpushed = ResolverOutcome(dispositions=(disp,), head_sha="unpushed_sha_123", pushed=False)
        with _LoopStubs():
            run_review_loop(
                ctx1,
                target=EXEC_TARGET,
                max_iterations=2,
                ensemble_size=1,
                run_ensemble_fn=ensemble1,
                run_resolver_fn=lambda *args, **kwargs: outcome_unpushed,
            )
        assert not any("Resolver pushed new head" in log for log in ctx1.logs)

        # 2. When pushed is True: context.log MUST log that resolver pushed new head
        tmp2 = tmp_path / "case_pushed"
        ensemble2 = FakeEnsemble([objecting_reviewer(1)])
        install_fake_reviewers(monkeypatch, ensemble2)
        ctx2 = _ensemble_ctx(tmp2)
        outcome_pushed = ResolverOutcome(dispositions=(disp,), head_sha="pushed_sha_456", pushed=True)
        with _LoopStubs():
            run_review_loop(
                ctx2,
                target=EXEC_TARGET,
                max_iterations=2,
                ensemble_size=1,
                run_ensemble_fn=ensemble2,
                run_resolver_fn=lambda *args, **kwargs: outcome_pushed,
            )
        assert any("Resolver pushed new head 'pushed_sha_456'" in log for log in ctx2.logs)


class TestEngineOwnedReviewLoop:
    """Stage 4: the engine owns the loop, dispatches one pass at a time and labels it with its own count."""

    @pytest.mark.parametrize("iteration", [1, 4, 25])
    def test_dispatch_requests_one_pass_and_labels_it_with_the_engine_iteration(self, tmp_path, monkeypatch, iteration):
        from sandbox_executor.review_target import ReviewTarget

        monkeypatch.setenv("HOME", str(tmp_path))
        captured: list[list[str]] = []

        def fake_dispatch(context, role, args, result_file=None, **_):
            captured.append(list(args))
            Path(result_file).write_text(json.dumps({"reviewers": [], "verdict": "FAILED"}), encoding="utf-8")
            return 0

        ctx = FlowContext(repo_dir=str(tmp_path), token_reduce=False)
        target = ReviewTarget(raw="feature", head_branch="feature", head_sha="a" * 40)
        with patch("sandbox_executor.flow.review.ensemble._dispatch_role", fake_dispatch):
            flow_module._dispatch_review_ensemble(ctx, target, iteration, 3)

        (args,) = captured
        limits = [a for a in args if a.startswith("--max-iterations")]
        assert limits == ["--max-iterations=1"]
        assert [a for a in args if a.startswith("--iteration")] == [f"--iteration={iteration}"]

    @staticmethod
    def _resumable_context(tmp_path: Path, done: int, budget: int) -> FlowContext:
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "resume-review"},
            execution_branch="I-1-resume-review/P-2-a-m/E-3-a-m/_",
            dry_run=True,
            token_reduce=False,
            max_review_iterations=budget,
            review_iterations_done=done,
        )
        path = save_checkpoint(ctx, str(tmp_path / "checkpoint.json"))
        return load_checkpoint(path).context

    def test_resume_continues_the_checkpointed_count(self, tmp_path):
        ctx = self._resumable_context(tmp_path, done=3, budget=5)
        assert ctx.review_iterations_done == 3
        loop_res = {"consensus": {"iterations": 4, "passes": 1, "max_review_iterations": 2}, "total_iterations": 4}

        with patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=loop_res) as loop:
            result = run_review_stage(ctx)

        assert loop.call_args.kwargs["first_iteration"] == 4
        assert loop.call_args.kwargs["max_iterations"] == 2
        assert ctx.review_iterations_done == 4
        assert result.payload["consensus"]["max_review_iterations"] == 5
        saved = save_checkpoint(ctx, str(tmp_path / "after.json"))
        assert load_checkpoint(saved).context.review_iterations_done == 4

    def test_an_exhausted_engine_budget_fails_without_dispatching(self, tmp_path):
        ctx = self._resumable_context(tmp_path, done=5, budget=5)

        with patch("sandbox_executor.flow.review.loop.run_review_loop") as loop:
            result = run_review_stage(ctx)

        loop.assert_not_called()
        assert result.status == StageStatus.FAILED
        assert "exhausted after 5 iteration(s)" in str(result.error)
        assert ctx.review_iterations_done == 5
        # The synthetic consensus speaks label units: iterations = label of the latest pass run (the count), 0 passes.
        assert result.payload["consensus"]["iterations"] == 5
        assert result.payload["consensus"]["passes"] == 0

    def test_an_exhausted_budget_clamps_a_malformed_negative_count(self, tmp_path):
        ctx = self._resumable_context(tmp_path, done=-2, budget=0)

        with patch("sandbox_executor.flow.review.loop.run_review_loop") as loop:
            result = run_review_stage(ctx)

        loop.assert_not_called()
        assert result.status == StageStatus.FAILED
        # The synthetic consensus never reports a negative label: the raw count is clamped like _review_window does.
        assert result.payload["consensus"]["iterations"] == 0

    def test_old_checkpoint_without_the_count_loads_as_zero(self):
        data = FlowContext(token_reduce=False).to_dict()
        data.pop("review_iterations_done")

        assert FlowContext.from_dict(data).review_iterations_done == 0

    def test_review_ceiling_counts_passes_not_the_label(self):
        engine = PipelineEngine(FlowContext(token_reduce=False))
        per_pass = engine.budgets.stage_seconds("review_iteration")
        labelled = StageResult(stage=FlowStage.REVIEW, status=StageStatus.FAILED, payload={})
        labelled.payload["consensus"] = {"iterations": 4, "passes": 1}

        assert engine.stage_budget_seconds(FlowStage.REVIEW, labelled) == per_pass

    def test_review_ceiling_honours_a_present_zero_passes_over_the_label(self):
        """A resumed run that dispatched nothing records passes == 0; the label must not resize the ceiling."""
        engine = PipelineEngine(FlowContext(token_reduce=False))
        per_pass = engine.budgets.stage_seconds("review_iteration")
        spent = StageResult(stage=FlowStage.REVIEW, status=StageStatus.FAILED, payload={})
        spent.payload["consensus"] = {"iterations": 4, "passes": 0}

        assert engine.stage_budget_seconds(FlowStage.REVIEW, spent) == per_pass


def _applied(findings) -> list[dict]:
    return [
        {
            "id": f.id,
            "severity": f.severity,
            "path": f.path,
            "line": f.line,
            "summary": f.summary,
            "disposition": "applied",
        }
        for f in findings
    ]


class TestReviewLoopCommitsItsLedgerRows:
    """Real git against a local bare origin: every iteration's rows are committed, ledger files only, and pushed."""

    @staticmethod
    def _run(work: Path, reviewers, resolver, max_iterations: int = 2) -> dict:
        passes: list[int] = []

        def ensemble(size: int):
            passes.append(size)
            return with_clean_peers(reviewers(len(passes)))

        def real_head(repo_dir: str, review_target: str):
            return ReviewTarget(review_target, review_target, _git(work, "rev-parse", "HEAD")), None

        with _LoopStubs(), patch("sandbox_executor.flow.review.loop._resolve_reviewed_target", real_head):
            return run_review_loop(
                _ensemble_ctx(work),
                target=EXEC_TARGET,
                max_iterations=max_iterations,
                ensemble_size=3,
                run_ensemble_fn=FakeEnsemble(ensemble),
                run_resolver_fn=resolver,
            )

    def test_each_iteration_leaves_a_clean_tree_with_its_rows_in_their_own_commit(self, tmp_path):
        origin, work = build_flow_worktree(tmp_path)
        fixes: list[str] = []

        def pushing_resolver(target, iteration, findings, action_ids, rejected_findings):
            (work / "src" / "app.py").write_text(f"x = {iteration + 1}\n", encoding="utf-8")
            _git(work, "add", "src/app.py")
            _git(work, "commit", "-q", "-m", f"fix iteration {iteration}")
            fixes.append(_git(work, "rev-parse", "HEAD"))
            _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{EXEC_TARGET}")
            return {"dispositions": _applied(findings), "head_sha": fixes[-1], "pushed": True}

        res = self._run(work, lambda n: [objecting_reviewer(1) if n == 1 else clean_reviewer(1)], pushing_resolver)

        assert res["consensus"]["approved"] is True
        assert _git(work, "status", "--porcelain") == ""
        assert _git(origin, "rev-parse", f"refs/heads/{EXEC_TARGET}") == _git(work, "rev-parse", "HEAD")
        final, first = ledger_commits(work)
        assert final == _git(work, "rev-parse", "HEAD")
        assert changed_paths(work, final) == changed_paths(work, first) == [EXEC_FRAGMENT]
        assert changed_paths(work, fixes[0]) == ["src/app.py"]  # The fix commit carries no ledger row.
        assert _git(work, "rev-parse", f"{first}^") == fixes[0]
        assert {row.get("review_iteration") for row in added_rows(work, first)} == {1}
        final_rows = added_rows(work, final)
        assert {row.get("review_iteration") for row in final_rows} >= {2}
        assert any(row.get("entry_type") == "summary" for row in final_rows)
        assert "co-authored-by" not in _git(work, "log", "-1", "--format=%B", final).lower()

    def test_a_failed_iteration_still_commits_its_rows(self, tmp_path):
        _, work = build_flow_worktree(tmp_path)

        def failing_resolver(target, iteration, findings, action_ids, rejected_findings):
            return {"error": "pre-push tests failed", "exit_code": 1, "failed_tests": ["tests/test_app.py::test_x"]}

        res = self._run(work, lambda n: [objecting_reviewer(1)], failing_resolver)

        assert res["resolver_error"]
        assert _git(work, "status", "--porcelain") == ""
        assert changed_paths(work) == [EXEC_FRAGMENT]
        rows = added_rows(work)
        assert any("resolver change rejected" in str(row.get("action")) for row in rows)
        assert any(row.get("entry_type") == "summary" for row in rows)
        assert any(row.get("review_iteration") == 1 for row in rows)

    def test_ledger_only_heads_still_halt_on_no_convergence(self, tmp_path):
        _, work = build_flow_worktree(tmp_path)

        def idle_resolver(target, iteration, findings, action_ids, rejected_findings):
            # Like the resolver container: it reports the worktree HEAD, and the host infers a push from a moved head.
            return {"dispositions": _applied(findings), "head_sha": _git(work, "rev-parse", "HEAD")}

        res = self._run(work, lambda n: [objecting_reviewer(1)], idle_resolver, max_iterations=5)

        assert res["no_convergence"] is True
        assert res["consensus"]["iterations"] == 3
        assert _git(work, "status", "--porcelain") == ""
        assert len(ledger_commits(work)) == 3


class TestScopedDeltaReviewInTheLoop:
    """A resolver fix after a completed full pass is re-reviewed as a scoped delta; ``0`` keeps every pass full."""

    @staticmethod
    def _run(work: Path, severity: str = "important") -> tuple[dict, FlowContext, FakeEnsemble, list[str]]:
        addressed: list[str] = []

        def pushing_resolver(target, iteration, findings, action_ids, rejected_findings):
            addressed.extend(f.id for f in findings)
            (work / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
            _git(work, "commit", "-q", "-am", "fix")
            _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{EXEC_TARGET}")
            return {"dispositions": _applied(findings), "head_sha": _git(work, "rev-parse", "HEAD"), "pushed": True}

        def real_head(repo_dir: str, review_target: str):
            return ReviewTarget(review_target, review_target, _git(work, "rev-parse", "HEAD")), None

        ctx = _ensemble_ctx(work)
        first_pass = with_clean_peers([objecting_reviewer(1, severity)])
        ensemble = FakeEnsemble(lambda size: first_pass if not ensemble.calls[1:] else with_clean_peers([]))
        with _LoopStubs(), patch("sandbox_executor.flow.review.loop._resolve_reviewed_target", real_head):
            res = run_review_loop(
                ctx,
                target=EXEC_TARGET,
                max_iterations=2,
                ensemble_size=3,
                run_ensemble_fn=ensemble,
                run_resolver_fn=pushing_resolver,
            )
        return res, ctx, ensemble, addressed

    @staticmethod
    def _modes(ctx: FlowContext) -> list[str]:
        return [line.split("Review mode at iteration ")[1] for line in ctx.logs if "Review mode at iteration" in line]

    def test_the_pass_after_a_resolver_fix_is_scoped(self, tmp_path, monkeypatch):
        from sandbox_executor.flow.review.approval_gates import diff_gate_of

        monkeypatch.delenv("HOLON_SCOPED_REVIEW_MAX_LINES", raising=False)
        _, work = build_flow_worktree(tmp_path)

        res, ctx, ensemble, addressed = self._run(work)

        assert res["consensus"]["approved"] is True
        assert self._modes(ctx) == ["1: full (last reviewed head unknown).", "2: scoped (delta 2 lines)."]
        assert diff_gate_of(ctx).mode == "scoped"
        first, second = (call["request"] for call in ensemble.calls)
        assert "Scoped Delta Review" not in first.pr_body
        assert "-x = 1" in second.diff_text and "+x = 2" in second.diff_text
        assert "holon-knowledge" not in second.diff_text  # The ledger commit between passes is bookkeeping.
        assert f"`{addressed[0]}`" in second.pr_body and "`src/app.py`:" in second.pr_body

    def test_the_pass_after_a_critical_finding_is_full(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HOLON_SCOPED_REVIEW_MAX_LINES", raising=False)
        _, work = build_flow_worktree(tmp_path)

        res, ctx, ensemble, _addressed = self._run(work, "critical")

        assert res["consensus"]["approved"] is True
        assert self._modes(ctx)[1] == "2: full (critical finding on last pass)."
        assert all("Scoped Delta Review" not in call["request"].pr_body for call in ensemble.calls)

    def test_a_zero_limit_keeps_every_pass_full(self, tmp_path, monkeypatch):
        from sandbox_executor.flow.review.approval_gates import diff_gate_of

        monkeypatch.setenv("HOLON_SCOPED_REVIEW_MAX_LINES", "0")
        _, work = build_flow_worktree(tmp_path)

        res, ctx, ensemble, _addressed = self._run(work)

        assert res["consensus"]["approved"] is True
        assert self._modes(ctx) == ["1: full (disabled).", "2: full (disabled)."]
        assert diff_gate_of(ctx).mode == "full"
        assert all("Scoped Delta Review" not in call["request"].pr_body for call in ensemble.calls)

    def test_a_conflict_scope_re_review_records_a_scoped_gate_even_when_the_delta_is_full(self, tmp_path, monkeypatch):
        from sandbox_executor.flow.review.approval_gates import diff_gate_of

        monkeypatch.setenv("HOLON_SCOPED_REVIEW_MAX_LINES", "0")
        _, work = build_flow_worktree(tmp_path)

        def real_head(repo_dir: str, review_target: str):
            return ReviewTarget(review_target, review_target, _git(work, "rev-parse", "HEAD")), None

        ctx = _ensemble_ctx(work)
        ctx.review_scope_paths = ["src/app.py"]
        ensemble = FakeEnsemble()
        with _LoopStubs(), patch("sandbox_executor.flow.review.loop._resolve_reviewed_target", real_head):
            res = run_review_loop(ctx, target=EXEC_TARGET, max_iterations=1, ensemble_size=3, run_ensemble_fn=ensemble)

        assert res["consensus"]["approved"] is True
        assert self._modes(ctx) == ["1: full (disabled)."]
        assert diff_gate_of(ctx).mode == "scoped"


class TestEveryFindingNeedsAResolverOutcome:
    @staticmethod
    def _run(tmp_path: Path, outcome: dict) -> dict:
        def ensemble(size: int):
            first = not ensemble_fake.calls[1:]
            return with_clean_peers([objecting_reviewer(1, "nit")] if first else [], size)

        def resolver(target, iteration, findings, action_ids, rejected_findings):
            return {"dispositions": [{**row, **outcome} for row in _applied(findings)], **_pushes(outcome)}

        ensemble_fake = FakeEnsemble(ensemble)
        with _LoopStubs():
            return run_review_loop(
                _ensemble_ctx(tmp_path),
                target=EXEC_TARGET,
                max_iterations=2,
                ensemble_size=3,
                run_ensemble_fn=ensemble_fake,
                run_resolver_fn=resolver,
            )

    @pytest.mark.parametrize(
        "outcome",
        [
            {"disposition": "open", "evidence": "could not reproduce"},
            {"disposition": "applied", "pushed": False},
        ],
    )
    def test_a_finding_left_without_an_outcome_blocks_a_clean_re_review(self, tmp_path, outcome):
        res = self._run(tmp_path, outcome)

        assert res["consensus"]["approved"] is False
        assert res["consensus"]["status"] != "approved"
        assert any(b.endswith("with a reason): R1-F1") for b in res["consensus_blockers"])

    @pytest.mark.parametrize(
        "outcome",
        [
            {"disposition": "applied", "pushed": True},
            {"disposition": "rejected", "rejection_reason": "inaccurate"},
        ],
    )
    def test_a_finding_applied_with_a_commit_or_rejected_with_a_reason_lets_a_clean_re_review_approve(
        self, tmp_path, outcome
    ):
        res = self._run(tmp_path, outcome)

        assert res["consensus"]["approved"] is True
        assert res["consensus_blockers"] == []


def _pushes(outcome: dict) -> dict:
    return {"head_sha": "c" * 40, "pushed": True} if outcome.get("pushed") else {"pushed": False}


def test_an_injected_adapter_receives_every_github_call_of_the_loop(tmp_path):
    """``run_review_loop(github=...)`` routes PR, mergeability, CI and posting through that one adapter."""
    from sandbox_executor.review_target import DiffVerdict

    github = FakeGitHub(
        views=[{"number": 7, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}] * 2,
        checks=ci_checks_payload(passed=1),
        head_sha="stage3-head",
    )
    with (
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc1234"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
    ):
        res = run_review_loop(_ensemble_ctx(tmp_path), max_iterations=1, run_ensemble_fn=FakeEnsemble(), github=github)

    assert res["consensus"]["approved"] is True
    assert {"open_or_update", "mergeability", "ci_checks", "post_review"} <= set(github.methods())
    assert github.methods()[0] == "open_or_update"
    assert all(args[0] == EXEC_TARGET for name, args in github.calls if name in {"open_or_update", "ci_checks"})


def test_an_injected_adapter_supplies_the_proxy_environment_of_the_loop(tmp_path):
    """With ``github=...`` the loop takes its proxy environment from the adapter and never resolves its own."""
    from sandbox_executor.flow.review import loop as loop_module
    from sandbox_executor.review_target import DiffVerdict

    github = FakeGitHub(
        views=[{"number": 7, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}] * 2,
        checks=ci_checks_payload(passed=1),
        head_sha="stage3-head",
        proxy_env={"HTTPS_PROXY": "http://p"},
    )
    with (
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc1234"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        patch.object(loop_module, "review_proxy_env") as resolve,
        patch.object(loop_module, "_ReviewTestGate", wraps=loop_module._ReviewTestGate) as gate,
    ):
        res = run_review_loop(_ensemble_ctx(tmp_path), max_iterations=1, run_ensemble_fn=FakeEnsemble(), github=github)

    assert res["consensus"]["approved"] is True
    resolve.assert_not_called()
    assert gate.call_args.kwargs["proxy_env"] == {"HTTPS_PROXY": "http://p"}
    assert gate.call_args.kwargs["pr_head"]("feat") == "stage3-head"
    assert github.calls[-1] == ("pr_head_sha", ("feat", PR_HEAD_TIMEOUT_SECONDS))


def test_without_an_injected_adapter_the_loop_builds_one_with_the_resolved_proxy_environment(tmp_path):
    """Without ``github=...`` the loop builds its adapter over the worktree with the proxy environment it resolved."""
    from sandbox_executor.flow.review import loop as loop_module
    from sandbox_executor.review_target import DiffVerdict

    github = FakeGitHub(
        views=[{"number": 7, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}] * 2,
        checks=ci_checks_payload(passed=1),
        head_sha="stage3-head",
    )
    ctx = _ensemble_ctx(tmp_path)
    with (
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc1234"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        patch.object(loop_module, "review_proxy_env", return_value={"HTTPS_PROXY": "http://p"}) as resolve,
        patch.object(loop_module, "GitHubAdapter", return_value=github) as adapter,
    ):
        res = run_review_loop(ctx, max_iterations=1, run_ensemble_fn=FakeEnsemble())

    assert res["consensus"]["approved"] is True
    resolve.assert_called_once_with(ctx)
    adapter.assert_called_once_with(ctx.target_worktree, {"HTTPS_PROXY": "http://p"})


def test_an_unavailable_adapter_attempts_no_pr(tmp_path):
    """Without ``gh`` the loop skips PR creation outright: no PR record and no adapter call for it."""
    from sandbox_executor.review_target import DiffVerdict

    github = FakeGitHub(available=False)
    with (
        patch("shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc1234"),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
    ):
        res = run_review_loop(
            _ensemble_ctx(tmp_path), max_iterations=1, auto_pr=True, run_ensemble_fn=FakeEnsemble(), github=github
        )

    assert res["pr"] == {}
    assert "open_or_update" not in github.methods()
