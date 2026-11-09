"""Tests for pull request mergeability checks and their ref resolution."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    StageResult,
    StageStatus,
    build_pr_review_consensus_report,
    check_pr_mergeability,
    run_execute_stage,
    run_review_loop,
    run_review_stage,
)
from sandbox_executor.ledger import parse_commit_trailers

from tests.flow.helpers import _git, _init_mergeability_repo
from tests.flow.lookup_patch import patch_lookup_sites


class TestReviewMergeabilityGuards:
    """Tests for PR mergeability inspection and DIRTY / CONFLICTING guardrails."""

    def test_review_stage_flags_dirty_merge_state(self, tmp_path):
        context = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100/P-100/E-100",
            plan_branch="I-100/P-100",
            intent_branch="I-100",
            dry_run=True,
        )
        context.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"test_pass_rate": 1.0, "exit_code": 0},
        )

        mock_mergeability = {
            "is_dirty": True,
            "merge_state_status": "DIRTY",
            "mergeable": "CONFLICTING",
            "source": "github_cli",
        }

        with patch("sandbox_executor.flow.review.loop.check_pr_mergeability", return_value=mock_mergeability):
            result = run_review_stage(context)

        assert result.status == StageStatus.FAILED
        assert result.payload["consensus"]["approved"] is False
        assert result.payload["consensus"]["status"] == "conflicted_dirty"
        assert result.error is not None
        assert "CI SIGNAL SUPPRESSED" in result.error
        assert "DIRTY" in result.error

    def test_check_pr_mergeability_via_gh_cli(self):
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run") as mock_subproc,
        ):
            mock_subproc.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(
                    {
                        "number": 42,
                        "mergeable": "CONFLICTING",
                        "mergeStateStatus": "DIRTY",
                        "statusCheckRollup": [],
                    }
                ),
            )
            res = check_pr_mergeability(".", "my-branch", "main")
            assert res["is_dirty"] is True
            assert res["mergeable"] == "CONFLICTING"
            assert res["merge_state_status"] == "DIRTY"
            assert res["source"] == "github_cli"
            assert res["pr_number"] == 42

    def test_check_pr_mergeability_short_circuit_on_gh_error(self):
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run") as mock_subproc,
            patch("sandbox_executor.flow.github.pr.run_git") as mock_git,
        ):
            # gh pr view returns error (e.g. no PR found)
            mock_subproc.return_value = MagicMock(returncode=1, stdout="", stderr="no pull requests found")
            mock_git.return_value = MagicMock(returncode=0, stdout="")

            res = check_pr_mergeability(".", "my-branch", "main")
            # Should short-circuit and call gh pr view only ONCE, not 3 times
            assert mock_subproc.call_count == 1
            assert res["source"] == "git_local"

    def test_check_pr_mergeability_local_git_fallback_with_remote_tracking_ref(self):
        with (
            patch("shutil.which", return_value=None),
            patch("sandbox_executor.flow.github.pr.run_git") as mock_git,
        ):

            def git_side_effect(args, **kwargs):  # noqa: PLR0911
                cmd = args[0]
                if cmd == "rev-parse":
                    ref = args[-1]
                    # Commit-peel lookups ('<ref>^{commit}') resolve the chosen refs to SHAs.
                    if ref == "origin/main^{commit}":
                        return MagicMock(returncode=0, stdout="a" * 40, stderr="")
                    if ref == "origin/feature-branch^{commit}":
                        return MagicMock(returncode=0, stdout="b" * 40, stderr="")
                    if ref == "origin/main":
                        return MagicMock(returncode=0, stdout="origin/main")
                    if ref == "feature-branch":
                        return MagicMock(returncode=1, stdout="")
                    if ref == "origin/feature-branch":
                        return MagicMock(returncode=0, stdout="origin/feature-branch")
                    return MagicMock(returncode=1, stdout="")
                if cmd == "merge-tree":
                    # args: ['merge-tree', '--write-tree', branch_sha, target_sha]
                    assert args[2] == "b" * 40
                    assert args[3] == "a" * 40
                    return MagicMock(returncode=0, stdout="tree-sha")
                return MagicMock(returncode=0, stdout="")

            mock_git.side_effect = git_side_effect
            res = check_pr_mergeability(".", "feature-branch", "main")
            assert res["source"] == "git_local"
            assert res["is_dirty"] is False
            assert res["mergeable"] == "MERGEABLE"

    def test_flow_context_token_reduce_auto_detection(self, monkeypatch):
        """Verify FlowContext defaults token_reduce=True when proxy is detected and preserves False when opted out."""
        with patch("sandbox_executor.cli.detect_holon_coherence", return_value=True):
            ctx = FlowContext()
            assert ctx.token_reduce is True

        monkeypatch.setenv("HOLON_TOKEN_REDUCE", "0")
        ctx_opt_out = FlowContext()
        assert ctx_opt_out.token_reduce is False

        # When explicitly passed False, preserves False
        ctx_explicit_false = FlowContext(token_reduce=False)
        assert ctx_explicit_false.token_reduce is False

    def test_flow_stage_environment_propagation(self, monkeypatch, tmp_path):
        """Verify proxy settings reach the execute-stage dispatcher and the review stage subprocess environment."""
        host_proxy_envs = {
            "HTTP_PROXY": "http://127.0.0.1:8080",
            "HTTPS_PROXY": "http://127.0.0.1:8080",
            "NODE_EXTRA_CA_CERTS": "/tmp/mitmproxy-ca-cert.pem",
            "SSL_CERT_FILE": "/tmp/mitmproxy-ca-cert.pem",
        }
        monkeypatch.setattr(
            "sandbox_executor.cli.get_host_proxy_envs",
            lambda: host_proxy_envs,
        )

        # 1. Execute stage: proxy settings reach the role-agent dispatcher, which mounts them into the container
        dispatcher = MagicMock(return_value=0)
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            token_reduce=True,
            mitm_web=True,
            plan_branch="I-test/P-1/_",
            dry_run=False,
            dispatcher=dispatcher,
        )
        with patch_lookup_sites(
            "sandbox_executor.flow.git_refs.run_git",
            "sandbox_executor.flow.github.pr.run_git",
            "sandbox_executor.flow.stage_conflicts.run_git",
        ) as mock_git:
            mock_git.return_value = MagicMock(returncode=0, stdout="")
            res = run_execute_stage(ctx)
        dispatcher.assert_called_once()
        assert dispatcher.call_args.kwargs["token_reduce"] is True
        assert dispatcher.call_args.kwargs["mitm_web"] is True
        assert res.status == StageStatus.FAILED  # No execution branch was pushed by the mocked dispatcher.
        assert "no execution branch" in (res.error or "")

        # 2. Review stage propagation
        ctx_rev = FlowContext(
            repo_dir=str(tmp_path),
            token_reduce=True,
            execution_branch="I-test/P-1/E-1/_",
            plan_branch="I-test/P-1/_",
            dry_run=True,
        )
        ctx_rev.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"test_pass_rate": 1.0, "exit_code": 0},
        )
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("sandbox_executor.flow.git_refs.run_git") as mock_git,
            patch("subprocess.run") as mock_subproc,
        ):
            mock_git.return_value = MagicMock(returncode=0, stdout="")
            mock_subproc.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "number": 123}),
            )
            res_rev = run_review_stage(ctx_rev)
            assert mock_subproc.call_count >= 1
            gh_call_kwargs = mock_subproc.call_args.kwargs
            gh_env = gh_call_kwargs.get("env")
            assert gh_env is not None
            assert gh_env.get("HTTP_PROXY") == "http://127.0.0.1:8080"
            assert gh_env.get("NODE_EXTRA_CA_CERTS") == "/tmp/mitmproxy-ca-cert.pem"
            assert res_rev.payload.get("review_package", {}).get("token_reduce") is True
            assert res_rev.payload.get("review_package", {}).get("proxy_active") is True

    def test_get_host_proxy_envs_contract(self, monkeypatch):
        """Verify get_host_proxy_envs returns localhost URLs and CA paths when probe succeeds."""
        from sandbox_executor.cli import get_host_proxy_envs

        # When opted out, returns empty dict
        monkeypatch.setenv("HOLON_TOKEN_REDUCE", "0")
        assert get_host_proxy_envs() == {}

        monkeypatch.delenv("HOLON_TOKEN_REDUCE", raising=False)

        # When socket connects and CA exists, returns host envs
        with (
            patch("socket.create_connection"),
            patch("os.path.exists", return_value=True),
        ):
            envs = get_host_proxy_envs()
            assert envs["HTTP_PROXY"] == "http://127.0.0.1:8080"
            assert envs["HTTPS_PROXY"] == "http://127.0.0.1:8080"
            assert "NODE_EXTRA_CA_CERTS" in envs
            assert "SSL_CERT_FILE" in envs


class TestMergeabilityRefResolution:
    """Real-repository tests: unresolvable refs are reported as MISSING_REF, never as merge conflicts."""

    @pytest.fixture
    def repo(self, tmp_path: Path) -> Path:
        return _init_mergeability_repo(tmp_path / "repo")

    @pytest.fixture(autouse=True)
    def _no_gh(self):
        # Force the local git path so no test can reach GitHub over the network.
        with patch("shutil.which", return_value=None) as which:
            yield which

    def test_missing_branch_ref_yields_missing_ref(self, repo: Path):
        res = check_pr_mergeability(str(repo), "does-not-exist", "main")
        assert res["source"] == "git_local"
        assert res["merge_state_status"] == "MISSING_REF"
        assert res["mergeable"] == "UNKNOWN"
        assert res["is_dirty"] is False
        assert "does-not-exist" in res["diagnostics"]["missing_ref"]
        assert res["diagnostics"]["ref_role"] == "branch"
        assert res["diagnostics"]["candidates_tried"] == ["does-not-exist", "origin/does-not-exist"]

    def test_missing_target_ref_yields_missing_ref(self, repo: Path):
        res = check_pr_mergeability(str(repo), "clean-feature", "no-such-target")
        assert res["merge_state_status"] == "MISSING_REF"
        assert res["mergeable"] == "UNKNOWN"
        assert res["is_dirty"] is False
        assert res["diagnostics"]["ref_role"] == "target"
        assert res["diagnostics"]["missing_ref"] == "no-such-target"
        assert "cannot evaluate mergeability for ref no-such-target" in res["diagnostics"]["message"]

    def test_genuine_conflict_yields_conflicting_dirty(self, repo: Path):
        res = check_pr_mergeability(str(repo), "conflict-feature", "main")
        assert res["mergeable"] == "CONFLICTING"
        assert res["merge_state_status"] == "DIRTY"
        assert res["is_dirty"] is True

    def test_clean_branch_yields_mergeable_clean(self, repo: Path):
        res = check_pr_mergeability(str(repo), "clean-feature", "main")
        assert res["mergeable"] == "MERGEABLE"
        assert res["merge_state_status"] == "CLEAN"
        assert res["is_dirty"] is False

    def test_review_loop_reports_missing_ref_without_find_dirty(self, repo: Path):
        ctx = FlowContext(repo_dir=str(repo), intent_data={"target_branch": "main"}, dry_run=True, token_reduce=False)
        ci_passed = {"source": "local_tests", "all_passed": True, "passed": 1, "failed": 0, "pending": 0}
        with patch("sandbox_executor.flow.review.loop.probe_ci_checks", return_value=ci_passed):
            res = run_review_loop(ctx, target="does-not-exist", max_iterations=3, dry_run=True)

        assert not any(fid.startswith("FIND-DIRTY") for fid in res["findings"])
        assert res["findings"] == ["FIND-REF-001"]
        assert res["consensus"]["approved"] is False
        assert res["consensus"]["status"] != "conflicted_dirty"
        assert res["mergeability"]["merge_state_status"] == "MISSING_REF"
        # A ref that does not resolve will not resolve on the next pass either, so the loop stops early.
        assert res["total_iterations"] == 1
        assert any("cannot evaluate mergeability for ref does-not-exist" in line for line in ctx.logs)
        assert not any("CI SIGNAL SUPPRESSED" in line for line in ctx.logs)

        # The loop's own trailers must carry the new finding id through the round trip used by ledger consumers.
        assert res["severity"] == "important"
        trailers = parse_commit_trailers(res["trailers"])
        assert trailers["review_finding_ids"] == ["FIND-REF-001"]
        assert trailers["review_severity"] == "important"
        assert trailers["review_iteration"] == 1

    def test_consensus_report_renders_mergeability_note(self):
        note = "cannot evaluate mergeability for ref does-not-exist against main: ref could not be resolved."
        consensus = {
            "approved": False,
            "status": "rejected",
            "findings": ["FIND-REF-001"],
            "mergeability_note": note,
        }
        body = build_pr_review_consensus_report(".", "does-not-exist", consensus)
        # The report renders only real data: the mergeability note, never a fabricated per-finding remediation.
        assert f"mergeability MISSING_REF ({note})." in body
        assert "**Checks:** Mergeability MISSING_REF · CI unmeasured" in body
        assert "Actionable Remediations" not in body

        without_note = build_pr_review_consensus_report(".", "does-not-exist", {**consensus, "mergeability_note": None})
        assert "cannot evaluate mergeability" not in without_note

    def test_review_stage_reports_missing_ref_instead_of_ci_signal_suppressed(self, tmp_path):
        context = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100/P-100/E-100",
            plan_branch="I-100/P-100",
            intent_branch="I-100",
            dry_run=True,
            token_reduce=False,
        )
        context.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"test_pass_rate": 1.0, "exit_code": 0},
        )
        missing_ref = {
            "mergeable": "UNKNOWN",
            "merge_state_status": "MISSING_REF",
            "is_dirty": False,
            "source": "git_local",
            "pr_number": None,
            "status_check_rollup": None,
            "diagnostics": {
                "missing_ref": "origin/I-100/P-100/E-100",
                "ref_role": "branch",
                "candidates_tried": ["I-100/P-100/E-100", "origin/I-100/P-100/E-100"],
                "stderr": "fatal: Needed a single revision",
                "message": "cannot evaluate mergeability for ref origin/I-100/P-100/E-100",
            },
        }
        with patch("sandbox_executor.flow.review.loop.check_pr_mergeability", return_value=missing_ref):
            result = run_review_stage(context)

        assert result.status == StageStatus.FAILED
        assert result.payload["consensus"]["approved"] is False
        assert result.error is not None
        assert "CI SIGNAL SUPPRESSED" not in result.error
        assert "cannot evaluate mergeability for ref origin/I-100/P-100/E-100" in result.error
        assert "MISSING_REF" in result.error

    def test_non_repository_yields_unknown_not_missing_ref(self, tmp_path: Path):
        # Outside a repository no ref can resolve, so blaming a specific ref would be a misdiagnosis.
        res = check_pr_mergeability(str(tmp_path), "feature", "main")
        assert res["mergeable"] == "UNKNOWN"
        assert res["merge_state_status"] == "UNKNOWN"
        assert res["is_dirty"] is False
        assert "not a git repository" in res["diagnostics"]["error"]

    # Exploratory probes (plan Step 4): does "exit 1 means conflict" hold once both refs resolve?

    def test_unrelated_histories_yield_unknown_with_stderr(self, repo: Path):
        _git(repo, "checkout", "-q", "--orphan", "orphan")
        _git(repo, "rm", "-rfq", ".")
        (repo / "orphan.txt").write_text("orphan\n", encoding="utf-8")
        _git(repo, "add", "orphan.txt")
        _git(repo, "commit", "-q", "-m", "orphan root")
        _git(repo, "checkout", "-q", "-f", "main")

        res = check_pr_mergeability(str(repo), "orphan", "main")
        assert res["mergeable"] == "UNKNOWN"
        assert res["merge_state_status"] == "UNKNOWN"
        assert res["is_dirty"] is False
        assert res["diagnostics"]["merge_tree_exit_code"] not in (0, 1)
        assert res["diagnostics"]["stderr"]

    def test_annotated_tag_is_peeled_to_commit(self, repo: Path):
        _git(repo, "tag", "-a", "v-clean", "-m", "annotated tag", "clean-feature")
        res = check_pr_mergeability(str(repo), "v-clean", "main")
        assert res["mergeable"] == "MERGEABLE"
        assert res["merge_state_status"] == "CLEAN"

    def test_tree_object_ref_yields_missing_ref(self, repo: Path):
        tree_sha = _git(repo, "rev-parse", "main^{tree}")
        res = check_pr_mergeability(str(repo), tree_sha, "main")
        assert res["merge_state_status"] == "MISSING_REF"
        assert res["is_dirty"] is False
        assert res["diagnostics"]["ref_role"] == "branch"

    def test_empty_branch_resolves_to_head(self, repo: Path):
        _git(repo, "checkout", "-q", "clean-feature")
        res = check_pr_mergeability(str(repo), "", "main")
        assert res["mergeable"] == "MERGEABLE"
        assert res["merge_state_status"] == "CLEAN"
