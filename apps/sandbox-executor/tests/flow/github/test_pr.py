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
from sandbox_executor.launcher.coherence import FlowProxyChoice
from sandbox_executor.ledger import parse_commit_trailers

from tests.flow.github.fake_github import FakeGitHub, spawn_failure, view_for
from tests.flow.helpers import _git, _init_mergeability_repo
from tests.flow.lookup_patch import patch_lookup_sites
from tests.flow.sleep_patch import scoped_sleep_patch

# why: without gh, mergeability is decided locally by git merge-tree
NO_GH = FakeGitHub(available=False)


class TestReviewMergeabilityGuards:
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

        github = FakeGitHub(standing_view=view_for(mock_mergeability))
        with patch("sandbox_executor.flow.stages.review.GitHubAdapter", return_value=github):
            result = run_review_stage(context)

        assert result.status == StageStatus.FAILED
        assert result.payload["consensus"]["approved"] is False
        assert result.payload["consensus"]["status"] == "conflicted_dirty"
        assert result.error is not None
        assert "CI SIGNAL SUPPRESSED" in result.error
        assert "DIRTY" in result.error

    def test_check_pr_mergeability_via_gh_cli(self):
        github = FakeGitHub(
            views=[{"number": 42, "mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY", "statusCheckRollup": []}]
        )
        res = check_pr_mergeability(".", "my-branch", "main", github=github)
        assert res["is_dirty"] is True
        assert res["mergeable"] == "CONFLICTING"
        assert res["merge_state_status"] == "DIRTY"
        assert res["source"] == "github_cli"
        assert res["pr_number"] == 42
        assert github.calls == [("mergeability", ("my-branch",))]

    def test_check_pr_mergeability_short_circuit_on_gh_error(self):
        github = FakeGitHub(views=[(1, "", "no pull requests found")])
        with patch("sandbox_executor.flow.github.pr.run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0, stdout="")

            res = check_pr_mergeability(".", "my-branch", "main", github=github)
            assert github.methods() == ["mergeability"]
            assert res["source"] == "git_local"

    def test_check_pr_mergeability_retries_while_github_computes(self):
        unknown = {"number": 7, "mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN", "statusCheckRollup": []}
        clean = {"number": 7, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "statusCheckRollup": []}
        github = FakeGitHub(views=[unknown, (0, "", ""), clean])
        with scoped_sleep_patch("sandbox_executor.flow.github.pr") as sleep:
            res = check_pr_mergeability(".", "my-branch", "main", github=github)
        assert github.methods() == ["mergeability"] * 3
        assert sleep.call_count == 1
        assert (res["source"], res["mergeable"], res["pr_number"]) == ("github_cli", "MERGEABLE", 7)

    def test_check_pr_mergeability_falls_back_when_the_view_cannot_run(self):
        github = FakeGitHub(views=[spawn_failure()])
        with patch("sandbox_executor.flow.github.pr.run_git", return_value=MagicMock(returncode=1, stdout="")):
            res = check_pr_mergeability(".", "my-branch", "main", github=github)
        assert github.methods() == ["mergeability"]
        assert res["source"] == "git_local"

    def test_check_pr_mergeability_local_git_fallback_with_remote_tracking_ref(self):
        github = FakeGitHub(available=False)
        with patch("sandbox_executor.flow.github.pr.run_git") as mock_git:

            def git_side_effect(args, **kwargs):  # noqa: PLR0911
                cmd = args[0]
                if cmd == "rev-parse":
                    ref = args[-1]
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
                    assert args[2] == "b" * 40
                    assert args[3] == "a" * 40
                    return MagicMock(returncode=0, stdout="tree-sha")
                return MagicMock(returncode=0, stdout="")

            mock_git.side_effect = git_side_effect
            res = check_pr_mergeability(".", "feature-branch", "main", github=github)
            assert github.calls == []
            assert res["source"] == "git_local"
            assert res["is_dirty"] is False
            assert res["mergeable"] == "MERGEABLE"

    def test_flow_context_attaches_detected_proxy_without_spawning_and_keeps_token_reduce_false(self, monkeypatch):
        detected = FlowProxyChoice(spawn_sidecar=False, attach_running_proxy=True)
        with patch("sandbox_executor.launcher.coherence.resolve_flow_proxy_choice", return_value=detected):
            ctx = FlowContext()
            assert ctx.token_reduce is False
            assert ctx.proxy_attach is True

        monkeypatch.setenv("HOLON_TOKEN_REDUCE", "0")
        ctx_opt_out = FlowContext()
        assert ctx_opt_out.token_reduce is False

        ctx_explicit_false = FlowContext(token_reduce=False)
        assert ctx_explicit_false.token_reduce is False

    def test_proxy_settings_reach_execute_dispatcher_and_review_subprocess_env(self, monkeypatch, tmp_path):
        host_proxy_envs = {
            "HTTP_PROXY": "http://127.0.0.1:8080",
            "HTTPS_PROXY": "http://127.0.0.1:8080",
            "NODE_EXTRA_CA_CERTS": "/tmp/mitmproxy-ca-cert.pem",
            "SSL_CERT_FILE": "/tmp/mitmproxy-ca-cert.pem",
        }
        monkeypatch.setattr(
            "sandbox_executor.launcher.sidecar.get_host_proxy_envs",
            lambda: host_proxy_envs,
        )

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
            "sandbox_executor.flow.host_remote.run_git",
            "sandbox_executor.flow.stage_conflicts.run_git",
        ) as mock_git:
            mock_git.return_value = MagicMock(returncode=0, stdout="")
            res = run_execute_stage(ctx)
        dispatcher.assert_called_once()
        assert dispatcher.call_args.kwargs["token_reduce"] is True
        assert dispatcher.call_args.kwargs["mitm_web"] is True
        assert res.status == StageStatus.FAILED  # why: the mocked dispatcher pushes no execution branch
        assert "no execution branch" in (res.error or "")

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
            # why: the review stage builds its own adapter, so gh is patched where the adapter spawns it
            patch("sandbox_executor.flow.github.adapter.shutil.which", return_value="/usr/bin/gh"),
            patch("sandbox_executor.flow.git_refs.run_git") as mock_git,
            patch("sandbox_executor.flow.github.adapter.subprocess.run") as mock_subproc,
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

    def test_get_host_proxy_envs_is_empty_when_opted_out_and_localhost_when_probe_succeeds(self, monkeypatch):
        from sandbox_executor.cli import get_host_proxy_envs

        monkeypatch.setenv("HOLON_TOKEN_REDUCE", "0")
        assert get_host_proxy_envs() == {}

        monkeypatch.delenv("HOLON_TOKEN_REDUCE", raising=False)

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
    @pytest.fixture
    def repo(self, tmp_path: Path) -> Path:
        return _init_mergeability_repo(tmp_path / "repo")

    @pytest.fixture(autouse=True)
    def _no_gh(self):
        # why: the loop-level tests build their own adapter, so gh is hidden to keep them off GitHub
        with patch("sandbox_executor.flow.github.adapter.shutil.which", return_value=None) as which:
            yield which

    def test_missing_branch_ref_yields_missing_ref(self, repo: Path):
        res = check_pr_mergeability(str(repo), "does-not-exist", "main", github=NO_GH)
        assert res["source"] == "git_local"
        assert res["merge_state_status"] == "MISSING_REF"
        assert res["mergeable"] == "UNKNOWN"
        assert res["is_dirty"] is False
        assert "does-not-exist" in res["diagnostics"]["missing_ref"]
        assert res["diagnostics"]["ref_role"] == "branch"
        assert res["diagnostics"]["candidates_tried"] == ["does-not-exist", "origin/does-not-exist"]

    def test_missing_target_ref_yields_missing_ref(self, repo: Path):
        res = check_pr_mergeability(str(repo), "clean-feature", "no-such-target", github=NO_GH)
        assert res["merge_state_status"] == "MISSING_REF"
        assert res["mergeable"] == "UNKNOWN"
        assert res["is_dirty"] is False
        assert res["diagnostics"]["ref_role"] == "target"
        assert res["diagnostics"]["missing_ref"] == "no-such-target"
        assert "cannot evaluate mergeability for ref no-such-target" in res["diagnostics"]["message"]

    def test_genuine_conflict_yields_conflicting_dirty(self, repo: Path):
        res = check_pr_mergeability(str(repo), "conflict-feature", "main", github=NO_GH)
        assert res["mergeable"] == "CONFLICTING"
        assert res["merge_state_status"] == "DIRTY"
        assert res["is_dirty"] is True

    def test_clean_branch_yields_mergeable_clean(self, repo: Path):
        res = check_pr_mergeability(str(repo), "clean-feature", "main", github=NO_GH)
        assert res["mergeable"] == "MERGEABLE"
        assert res["merge_state_status"] == "CLEAN"
        assert res["is_dirty"] is False

    def test_review_loop_reports_missing_ref_without_find_dirty(self, repo: Path):
        ctx = FlowContext(repo_dir=str(repo), intent_data={"target_branch": "main"}, dry_run=True, token_reduce=False)
        # why: without gh, CI and mergeability fall back to the fixture repository's local state
        github = FakeGitHub(available=False)
        res = run_review_loop(ctx, target="does-not-exist", max_iterations=3, dry_run=True, github=github)

        assert not any(fid.startswith("FIND-DIRTY") for fid in res["findings"])
        assert res["findings"] == ["FIND-REF-001"]
        assert res["consensus"]["approved"] is False
        assert res["consensus"]["status"] != "conflicted_dirty"
        assert res["mergeability"]["merge_state_status"] == "MISSING_REF"
        # why: a ref that does not resolve will not resolve on the next pass either, so the loop stops early
        assert res["total_iterations"] == 1
        assert any("cannot evaluate mergeability for ref does-not-exist" in line for line in ctx.logs)
        assert not any("CI SIGNAL SUPPRESSED" in line for line in ctx.logs)

        # why: ledger consumers read the finding id back from these trailers
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
        # why: scripted seam, a missing branch ref in a real repo never resolves to the origin/ candidate named here
        with (
            patch("sandbox_executor.flow.stages.review.GitHubAdapter", return_value=FakeGitHub(available=False)),
            patch("sandbox_executor.flow.github.pr._local_mergeability", return_value=missing_ref),
        ):
            result = run_review_stage(context)

        assert result.status == StageStatus.FAILED
        assert result.payload["consensus"]["approved"] is False
        assert result.error is not None
        assert "CI SIGNAL SUPPRESSED" not in result.error
        assert "cannot evaluate mergeability for ref origin/I-100/P-100/E-100" in result.error
        assert "MISSING_REF" in result.error

    def test_non_repository_yields_unknown_not_missing_ref(self, tmp_path: Path):
        # why: outside a repository no ref can resolve, so blaming a specific ref would be a misdiagnosis
        res = check_pr_mergeability(str(tmp_path), "feature", "main", github=NO_GH)
        assert res["mergeable"] == "UNKNOWN"
        assert res["merge_state_status"] == "UNKNOWN"
        assert res["is_dirty"] is False
        assert "not a git repository" in res["diagnostics"]["error"]

    def test_unrelated_histories_yield_unknown_with_stderr(self, repo: Path):
        _git(repo, "checkout", "-q", "--orphan", "orphan")
        _git(repo, "rm", "-rfq", ".")
        (repo / "orphan.txt").write_text("orphan\n", encoding="utf-8")
        _git(repo, "add", "orphan.txt")
        _git(repo, "commit", "-q", "-m", "orphan root")
        _git(repo, "checkout", "-q", "-f", "main")

        res = check_pr_mergeability(str(repo), "orphan", "main", github=NO_GH)
        assert res["mergeable"] == "UNKNOWN"
        assert res["merge_state_status"] == "UNKNOWN"
        assert res["is_dirty"] is False
        assert res["diagnostics"]["merge_tree_exit_code"] not in (0, 1)
        assert res["diagnostics"]["stderr"]

    def test_annotated_tag_is_peeled_to_commit(self, repo: Path):
        _git(repo, "tag", "-a", "v-clean", "-m", "annotated tag", "clean-feature")
        res = check_pr_mergeability(str(repo), "v-clean", "main", github=NO_GH)
        assert res["mergeable"] == "MERGEABLE"
        assert res["merge_state_status"] == "CLEAN"

    def test_tree_object_ref_yields_missing_ref(self, repo: Path):
        tree_sha = _git(repo, "rev-parse", "main^{tree}")
        res = check_pr_mergeability(str(repo), tree_sha, "main", github=NO_GH)
        assert res["merge_state_status"] == "MISSING_REF"
        assert res["is_dirty"] is False
        assert res["diagnostics"]["ref_role"] == "branch"

    def test_empty_branch_resolves_to_head(self, repo: Path):
        _git(repo, "checkout", "-q", "clean-feature")
        res = check_pr_mergeability(str(repo), "", "main", github=NO_GH)
        assert res["mergeable"] == "MERGEABLE"
        assert res["merge_state_status"] == "CLEAN"
