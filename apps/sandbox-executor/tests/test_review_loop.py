"""Unit tests for containerised Holon Review Loop Agent (Bean 0118)."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.entrypoint.review_loop import main as review_loop_main
from sandbox_executor.entrypoint.review_loop import run_cmd

from tests.leak_assertions import assert_no_fragment, make_gh_probe
from tests.review_fakes import install_fake_reviewers


@pytest.fixture(autouse=True)
def _sandboxed_clean_reviewers(monkeypatch):
    """Run every review-loop test inside a simulated sandbox with clean fake reviewers (no runner is ever called).

    Mergeability and CI then decide the outcome as they did before the real ensemble was wired in; the tests of the
    ensemble wiring and of the host refusal override this explicitly.
    """
    install_fake_reviewers(monkeypatch)


def _prepared(branch: str):
    """Skip the git preparation of the entrypoint, as if ``branch`` had been cloned and verified."""
    from sandbox_executor.review_target import ReviewTarget

    return patch(
        "sandbox_executor.entrypoint.review_loop._prepare_workspace",
        return_value=ReviewTarget(raw=branch, head_branch=branch, head_sha="a" * 40),
    )


class TestReviewLoopEntrypoint:
    """Test suite for entrypoint.review_loop."""

    def test_run_cmd_success(self):
        proc = run_cmd(["echo", "hello"], check=True)
        assert proc.returncode == 0
        assert "hello" in proc.stdout

    def test_run_cmd_failure_exits(self):
        with pytest.raises(SystemExit):
            run_cmd(["false"], check=True)

    def test_review_loop_main_consensus_approval_bean_0034(self, tmp_path: Path):
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        (tmp_path / ".git").mkdir()

        # Seed intent and plan ledger
        intents_file = ledger_dir / "intents.jsonl"
        intents_file.write_text(
            json.dumps({"branch": "I-100-test", "slug": "test", "description": "test intent"}) + "\n"
        )
        plans_file = ledger_dir / "plans.jsonl"
        plans_file.write_text(json.dumps({"plan_id": "P-200-test", "plan_branch": "I-100-test/P-200-test/_"}) + "\n")

        test_args = [
            "review_loop",
            "I-100-test/P-200-test/E-300-exec/_",
            "antigravity-agent",
            "gemini-3.8-flash-medium",
            "--max-iterations=5",
            f"--repo-dir={tmp_path}",
            "--dry-run",
        ]

        with (
            patch.object(sys, "argv", test_args),
            patch("sandbox_executor.entrypoint.review_loop.check_and_resolve_stage_conflicts") as mock_conflicts,
            patch("sandbox_executor.entrypoint.review_loop.run_review_loop") as mock_loop,
            _prepared("I-100-test/P-200-test/E-300-exec/_"),
        ):
            mock_loop.return_value = {
                "target": "I-100-test/P-200-test/E-300-exec/_",
                "consensus": {
                    "approved": True,
                    "score": 1.0,
                    "iterations": 1,
                    "status": "approved",
                },
                "ensemble_verdict": "APPROVED",
                "halted_for_human": True,
                "human_approval_command": "holon review approve I-100-test/P-200-test/E-300-exec/_",
                "ci_checks": {"total": 1, "passed": 1, "failed": 0, "pending": 0, "source": "local_tests"},
                "max_iterations": 5,
            }

            with pytest.raises(SystemExit) as exc_info:
                review_loop_main()

            assert exc_info.value.code == 0
            mock_conflicts.assert_called_once_with(repo_dir=str(tmp_path), target_branch="main", stage_name="review")
            mock_loop.assert_called_once()
            ctx = mock_loop.call_args[1]["context"]
            assert ctx.execution_branch == "I-100-test/P-200-test/E-300-exec/_"
            assert ctx.agent == "antigravity-agent"
            assert ctx.model == "gemini-3.8-flash-medium"
            assert ctx.dry_run is True

    def test_review_loop_main_consensus_failure_exits_one(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        test_args = [
            "review_loop",
            "I-100-test/P-200-test/E-300-exec/_",
            "--agent=claude-agent",
            "--model=claude-3-5-sonnet",
            f"--repo-dir={tmp_path}",
            "--dry-run",
        ]

        with (
            patch.object(sys, "argv", test_args),
            patch("sandbox_executor.entrypoint.review_loop.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.entrypoint.review_loop.run_review_loop") as mock_loop,
            _prepared("I-100-test/P-200-test/E-300-exec/_"),
        ):
            mock_loop.return_value = {
                "target": "I-100-test/P-200-test/E-300-exec/_",
                "consensus": {
                    "approved": False,
                    "score": 0.0,
                    "iterations": 3,
                    "status": "failed",
                },
                "halted_for_human": False,
                "ci_checks": {"total": 1, "passed": 0, "failed": 1, "pending": 0, "source": "local_tests"},
                "max_iterations": 25,
            }

            with pytest.raises(SystemExit) as exc_info:
                review_loop_main()

            assert exc_info.value.code == 1

    def test_review_loop_main_json_output(self, tmp_path: Path, capsys):
        (tmp_path / ".git").mkdir()
        test_args = [
            "review_loop",
            "I-100-test",
            f"--repo-dir={tmp_path}",
            "--json",
            "--dry-run",
        ]

        with (
            patch.object(sys, "argv", test_args),
            patch("sandbox_executor.entrypoint.review_loop.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.entrypoint.review_loop.run_review_loop") as mock_loop,
            _prepared("I-100-test"),
        ):
            mock_loop.return_value = {
                "target": "I-100-test",
                "consensus": {"approved": True, "iterations": 1},
                "ensemble_verdict": "APPROVED",
                "max_iterations": 25,
            }

            with pytest.raises(SystemExit) as exc_info:
                review_loop_main()

            assert exc_info.value.code == 0
            captured = capsys.readouterr()
            data = json.loads(captured.out)
            assert data["consensus"]["approved"] is True

    def test_review_loop_main_writes_result_file(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        result_file = tmp_path / "result.json"
        test_args = [
            "review_loop",
            "I-100-test",
            f"--repo-dir={tmp_path}",
            f"--result-file={result_file}",
            "--skip-post",
            "--dry-run",
        ]

        with (
            patch.object(sys, "argv", test_args),
            patch("sandbox_executor.entrypoint.review_loop.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.entrypoint.review_loop.run_review_loop") as mock_loop,
            _prepared("I-100-test"),
        ):
            mock_loop.return_value = {
                "target": "I-100-test",
                "consensus": {"approved": True, "iterations": 1},
                "ensemble_verdict": "APPROVED",
                "max_iterations": 25,
            }

            with pytest.raises(SystemExit) as exc_info:
                review_loop_main()

            assert exc_info.value.code == 0
            assert result_file.exists()
            data = json.loads(result_file.read_text())
            assert data["consensus"]["approved"] is True

    def test_review_loop_main_writes_result_file_on_prep_failure(self, tmp_path: Path):
        from sandbox_executor.entrypoint.review_loop import ReviewPreparationError

        (tmp_path / ".git").mkdir()
        result_file = tmp_path / "result.json"
        test_args = [
            "review_loop",
            "I-100-test",
            f"--repo-dir={tmp_path}",
            f"--result-file={result_file}",
        ]

        with (
            patch.object(sys, "argv", test_args),
            patch(
                "sandbox_executor.entrypoint.review_loop._prepare_workspace",
                side_effect=ReviewPreparationError("fetch", "Preparation failed: remote branch missing"),
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                review_loop_main()

            assert exc_info.value.code == 1
            assert result_file.exists()
            data = json.loads(result_file.read_text())
            assert "Preparation failed" in data["error"]
            assert data["ensemble_verdict"] == "FAILED"


class TestCLIDispatchReviewLoop:
    """Test CLI dispatch of review-loop (Bean 0118)."""

    def test_cli_dispatches_docker_container_by_default(self):
        from sandbox_executor.cli import main as cli_main

        test_args = [
            "holon",
            "review-loop",
            "I-100/P-200/E-300/_",
            "--agent=antigravity-agent",
            "--model=gemini-3.8-flash-medium",
        ]

        with (
            patch.object(sys, "argv", test_args),
            patch.dict(os.environ, {}, clear=True),
            patch("sandbox_executor.cli.run_docker_container") as mock_docker,
            patch("sandbox_executor.cli.detect_holon_coherence", return_value=False),
        ):
            mock_docker.return_value = 0
            with pytest.raises(SystemExit) as exc_info:
                cli_main()

            assert exc_info.value.code == 0
            mock_docker.assert_called_once()
            role, image, container_args = mock_docker.call_args[0]
            assert role == "review-loop"
            assert "holon/agent-antigravity" in image
            assert "I-100/P-200/E-300/_" in container_args

    def test_cli_local_flag_dispatches_local_runner(self):
        from sandbox_executor.cli import main as cli_main

        test_args = [
            "holon",
            "review-loop",
            "I-100/P-200/E-300/_",
            "--local",
        ]

        with (
            patch.object(sys, "argv", test_args),
            patch.dict(os.environ, {"HOLON_AGENT": "antigravity-agent", "HOLON_MODEL": "gemini-3.8-flash-medium"}),
            patch("sandbox_executor.flow.run_review_loop_cli", return_value=0) as mock_local,
            patch("sandbox_executor.cli.detect_holon_coherence", return_value=False),
        ):
            with pytest.raises(SystemExit) as exc_info:
                cli_main()

            assert exc_info.value.code == 0
            mock_local.assert_called_once()


class TestReviewLoopGitHubCLIDiagnostics:
    """Test suite verifying GitHub CLI (gh) detection and diagnostics in review loop (Bean 0131)."""

    def test_post_pr_review_consensus_detects_gh_and_executes_commands(self):
        from sandbox_executor.flow import post_pr_review_consensus

        from tests.review_fakes import clean_reviewer

        with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Success", stderr="")
            consensus_data = {
                "approved": True,
                "ensemble_verdict": "APPROVED",
                "status": "approved",
                "iterations": 1,
                "max_review_iterations": 25,
            }
            res = post_pr_review_consensus(
                repo_dir="/tmp", target="123", consensus_data=consensus_data, reviewers=[clean_reviewer(1)]
            )

            assert res["gh_available"] is True
            assert res["dual_posted"] is True
            assert res["discussion_comment"]["success"] is True
            assert res["official_review"]["success"] is True
            assert mock_run.call_count == 2
            comment_call = mock_run.call_args_list[0][0][0]
            review_call = mock_run.call_args_list[1][0][0]
            assert comment_call[:4] == ["gh", "pr", "comment", "123"]
            assert review_call[:5] == ["gh", "pr", "review", "123", "--approve"]

    @pytest.mark.parametrize(
        ("verdict", "reviewers"),
        [("APPROVED", []), (None, [1]), ("FAILED", [1])],
        ids=["zero-reviewers", "no-ensemble-verdict", "failed-ensemble"],
    )
    def test_post_pr_review_consensus_never_approves_without_a_completed_approving_ensemble(self, verdict, reviewers):
        from sandbox_executor.flow import post_pr_review_consensus

        from tests.review_fakes import clean_reviewer

        consensus_data = {"approved": True, "ensemble_verdict": verdict, "status": "approved", "iterations": 1}
        with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Success", stderr="")
            res = post_pr_review_consensus(
                repo_dir="/tmp",
                target="123",
                consensus_data=consensus_data,
                reviewers=[clean_reviewer(i) for i in reviewers],
            )

        assert res["official_review"]["flag"] == "--comment"
        review_call = mock_run.call_args_list[1][0][0]
        assert review_call[:5] == ["gh", "pr", "review", "123", "--comment"]
        assert "--approve" not in review_call

    def test_post_pr_review_consensus_diagnostics_when_gh_missing(self):
        from sandbox_executor.flow import post_pr_review_consensus

        with patch("shutil.which", return_value=None):
            consensus_data = {"approved": True, "status": "approved", "iterations": 1}
            res = post_pr_review_consensus(repo_dir="/tmp", target="123", consensus_data=consensus_data)

            assert res["gh_available"] is False
            assert res["dual_posted"] is False
            assert res["discussion_comment"]["attempted"] is False
            assert "not available in PATH" in res["diagnostics"]
            assert "not available in PATH" in res["discussion_comment"]["output"]
            assert res["official_review"]["attempted"] is False
            assert "not available in PATH" in res["official_review"]["output"]

    def test_post_pr_review_consensus_diagnostics_when_gh_fails_nonzero(self):
        from sandbox_executor.flow import post_pr_review_consensus

        with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run") as mock_run:
            mock_comment = MagicMock(returncode=1, stdout="", stderr="HTTP 502 Bad Gateway")
            mock_review = MagicMock(returncode=1, stdout="", stderr="HTTP 502 Bad Gateway")
            mock_run.side_effect = [mock_comment, mock_review]

            consensus_data = {"approved": True}
            res = post_pr_review_consensus(repo_dir="/tmp", target="123", consensus_data=consensus_data)

            assert res["gh_available"] is True
            assert res["dual_posted"] is False
            assert res["discussion_comment"]["success"] is False
            assert "exit code 1" in res["discussion_comment"]["output"]
            assert "HTTP 502 Bad Gateway" in res["discussion_comment"]["output"]
            assert res["official_review"]["success"] is False
            assert "exit code 1" in res["official_review"]["output"]
            assert "HTTP 502 Bad Gateway" in res["official_review"]["output"]
            assert "Review posting partial/failed" in res["diagnostics"]


class TestReviewLoopEmptyDiffGuard:
    """run_review_loop never creates or updates a PR for an execution branch with no effective diff."""

    PLAN = "I-1-guard/P-1-claude-agent-m/_"
    EXEC = "I-1-guard/P-1-claude-agent-m/E-2-claude-agent-m/_"

    @staticmethod
    def _repo(tmp_path: Path, change_source: bool) -> Path:
        import subprocess

        repo = tmp_path / "repo"

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

        repo.mkdir()
        git("init", "-q", "-b", TestReviewLoopEmptyDiffGuard.PLAN)
        git("config", "user.email", "test@holon.com")
        git("config", "user.name", "Holon Test")
        (repo / "holon-knowledge" / "ledger").mkdir(parents=True)
        (repo / "holon-knowledge" / "ledger" / "executions.jsonl").write_text("", encoding="utf-8")
        (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "plan")
        git("checkout", "-q", "-b", TestReviewLoopEmptyDiffGuard.EXEC)
        (repo / "holon-knowledge" / "ledger" / "executions.jsonl").write_text('{"status": "success"}\n')
        if change_source:
            (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "exec")
        return repo

    def _run(self, repo: Path):
        from sandbox_executor.flow import FlowContext, run_review_loop

        ctx = FlowContext(repo_dir=str(repo), execution_branch=self.EXEC, token_reduce=False)
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("sandbox_executor.flow.create_or_update_pr", return_value={"success": True}) as mock_pr,
            patch("sandbox_executor.flow.check_pr_mergeability", return_value={"is_dirty": False}),
            patch("sandbox_executor.flow.probe_ci_checks", return_value={"all_passed": True, "source": "github_cli"}),
            patch("sandbox_executor.flow.post_pr_review_consensus", return_value={"dual_posted": True}),
        ):
            res = run_review_loop(ctx, target=self.EXEC, max_iterations=1, dry_run=False)
        return res, mock_pr

    def test_empty_execution_branch_gets_no_pr(self, tmp_path: Path):
        res, mock_pr = self._run(self._repo(tmp_path, change_source=False))
        mock_pr.assert_not_called()
        assert res["consensus"]["approved"] is False
        assert res["pr"]["reason"] == "empty_diff"
        assert "no changes outside holon-knowledge/ledger/, plans/ and executions/" in res["error"]

    def test_non_empty_execution_branch_still_gets_pr(self, tmp_path: Path):
        res, mock_pr = self._run(self._repo(tmp_path, change_source=True))
        mock_pr.assert_called_once()
        assert mock_pr.call_args.kwargs["branch"] == self.EXEC
        assert res["consensus"]["approved"] is True

    def test_unverifiable_diff_skips_pr(self, tmp_path: Path):
        repo = tmp_path / "not-a-repo"
        repo.mkdir()
        res, mock_pr = self._run(repo)
        mock_pr.assert_not_called()
        assert res["pr"]["reason"] == "diff_unverified"
        # C15-N1: green CI cannot turn the refusal into approval; the loop fails closed with the refusal as its error.
        assert res["consensus"]["approved"] is False
        assert res["consensus"]["iterations"] == 0
        assert "could not verify the execution diff" in res["error"]


class TestReviewLoopEmptyDiffGuardOrigin:
    """The review-loop guard judges the branches on origin, which is what the PR is opened from (C8-I1)."""

    PLAN = TestReviewLoopEmptyDiffGuard.PLAN
    EXEC = TestReviewLoopEmptyDiffGuard.EXEC

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        import subprocess

        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def _commit(self, repo: Path, path: str, content: str, message: str) -> None:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-q", "-m", message)

    def _setup(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        """Bare origin, a 'container' clone that pushes the plan branch, and an unfetched host clone."""
        origin = tmp_path / "origin.git"
        self._git(tmp_path, "init", "-q", "--bare", str(origin))
        container = tmp_path / "container"
        container.mkdir()
        self._git(container, "init", "-q", "-b", self.PLAN)
        self._git(container, "config", "user.email", "test@holon.com")
        self._git(container, "config", "user.name", "Holon Test")
        self._git(container, "remote", "add", "origin", str(origin))
        self._commit(container, "holon-knowledge/ledger/executions.jsonl", "", "plan base")
        self._commit(container, "app.py", "x = 1\n", "plan app")
        self._git(container, "push", "-q", "origin", self.PLAN)
        self._git(container, "checkout", "-q", "-b", self.EXEC)
        host = tmp_path / "host"
        host.mkdir()
        self._git(host, "init", "-q", "-b", "main")
        self._git(host, "config", "user.email", "test@holon.com")
        self._git(host, "config", "user.name", "Holon Test")
        self._git(host, "remote", "add", "origin", str(origin))
        return origin, container, host

    def _run(self, host: Path):
        from sandbox_executor.flow import FlowContext, run_review_loop

        ctx = FlowContext(repo_dir=str(host), execution_branch=self.EXEC, token_reduce=False)
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("sandbox_executor.flow.create_or_update_pr", return_value={"success": True}) as mock_pr,
            patch("sandbox_executor.flow.check_pr_mergeability", return_value={"is_dirty": False}),
            patch("sandbox_executor.flow.probe_ci_checks", return_value={"all_passed": True, "source": "github_cli"}),
            patch("sandbox_executor.flow.post_pr_review_consensus", return_value={"dual_posted": True}),
        ):
            res = run_review_loop(ctx, target=self.EXEC, max_iterations=1, dry_run=False)
        return res, mock_pr

    def test_unfetched_host_with_non_empty_origin_branch_gets_pr(self, tmp_path: Path):
        _origin, container, host = self._setup(tmp_path)
        self._commit(container, "app.py", "x = 2\n", "implement")
        self._git(container, "push", "-q", "origin", self.EXEC)
        res, mock_pr = self._run(host)
        mock_pr.assert_called_once()
        assert mock_pr.call_args.kwargs["branch"] == self.EXEC
        assert res["pr"] == {"success": True}

    def test_stale_empty_local_branch_follows_non_empty_origin(self, tmp_path: Path):
        _origin, container, host = self._setup(tmp_path)
        self._commit(container, "holon-knowledge/ledger/executions.jsonl", '{"status": "running"}\n', "ledger")
        self._git(container, "push", "-q", "origin", self.EXEC)
        self._git(host, "fetch", "-q", "origin", f"+refs/heads/{self.EXEC}:refs/heads/{self.EXEC}")
        self._commit(container, "app.py", "x = 2\n", "implement")
        self._git(container, "push", "-q", "origin", self.EXEC)
        res, mock_pr = self._run(host)
        mock_pr.assert_called_once()
        assert res["pr"] == {"success": True}

    def test_empty_origin_branch_is_refused_despite_unpushed_local_commit(self, tmp_path: Path):
        _origin, container, host = self._setup(tmp_path)
        self._commit(container, "holon-knowledge/ledger/executions.jsonl", '{"status": "success"}\n', "ledger")
        self._git(container, "push", "-q", "origin", self.EXEC)
        self._git(host, "fetch", "-q", "origin", f"+refs/heads/{self.EXEC}:refs/heads/{self.EXEC}")
        self._git(host, "fetch", "-q", "origin", self.PLAN)
        self._git(host, "checkout", "-q", self.EXEC)
        self._commit(host, "app.py", "x = 3\n", "unpushed local change")
        res, mock_pr = self._run(host)
        mock_pr.assert_not_called()
        assert res["pr"]["reason"] == "empty_diff"
        assert res["consensus"]["approved"] is False

    def test_missing_origin_branch_skips_pr_with_fetch_error(self, tmp_path: Path):
        _origin, _container, host = self._setup(tmp_path)
        self._git(host, "fetch", "-q", "origin", f"+refs/heads/{self.PLAN}:refs/heads/{self.EXEC}")
        self._git(host, "checkout", "-q", self.EXEC)
        self._commit(host, "app.py", "x = 4\n", "never pushed")
        res, mock_pr = self._run(host)
        mock_pr.assert_not_called()
        assert res["pr"]["reason"] == "diff_unverified"

    def test_fetch_error_is_redacted(self, tmp_path: Path):
        from sandbox_executor.flow import _execution_effective_diff

        token = make_gh_probe()
        host = tmp_path / "host"
        host.mkdir()
        self._git(host, "init", "-q")
        self._git(host, "remote", "add", "origin", f"https://x-access-token:{token}@127.0.0.1:9/r.git")
        has_diff, detail = _execution_effective_diff(str(host), self.EXEC)
        assert has_diff is None
        assert "from origin failed" in detail
        assert_no_fragment(detail, token)


# --------------------------------------------------------------------------------------
# Credential leaks in the echoed clone command and its failure diagnostics
# --------------------------------------------------------------------------------------


@pytest.fixture
def clone_leak_env(monkeypatch):
    sample = make_gh_probe()
    for name in ("GH_TOKEN", "HOLON_AGENT_KEY", "HOLON_MITM_WEB_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", sample)
    url = f"https://x-access-token:{sample}@github.com/myorg/myrepo.git"
    argv = ["git", "clone", "--branch", "I-1792762862-x/_", "--single-branch", "--depth", "1", url, "."]
    failed = MagicMock(
        returncode=128,
        stdout=f"Cloning into '.' from {url}",
        stderr=f"fatal: unable to access '{url}/': The requested URL returned error: 403",
    )
    return sample, argv, failed


def test_run_cmd_clone_echo_and_failure_carry_no_token_fragment(clone_leak_env, capsys):
    sample, argv, failed = clone_leak_env
    with patch("subprocess.run", return_value=failed), pytest.raises(SystemExit):
        run_cmd(argv, check=True)
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert_no_fragment(output, sample)
    assert "Running: git clone --branch I-1792762862-x/_ --single-branch" in output
    assert "github.com/myorg/myrepo.git" in output
    assert "returned error: 403" in output


def test_main_clone_banner_carries_no_token_fragment(clone_leak_env, monkeypatch, tmp_path, capsys):
    sample, argv, _ = clone_leak_env
    monkeypatch.setenv("HOLON_REPO_URL", argv[7])
    test_args = ["review_loop", "I-100-test", f"--repo-dir={tmp_path}", "--json", "--dry-run"]
    with (
        patch.object(sys, "argv", test_args),
        patch("sandbox_executor.entrypoint.review_loop.run_cmd"),
        patch("sandbox_executor.entrypoint.review_loop.check_and_resolve_stage_conflicts"),
        patch(
            "sandbox_executor.entrypoint.review_loop.run_review_loop",
            return_value={"target": "I-100-test", "consensus": {"approved": True, "iterations": 1}},
        ),
        pytest.raises(SystemExit),
    ):
        review_loop_main()
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "Cloning I-100-test into" in output
    assert "github.com/myorg/myrepo.git" in output
    assert_no_fragment(output, sample)


# --------------------------------------------------------------------------------------
# Fail-closed entrypoint preparation: resolve, clone, verify
# --------------------------------------------------------------------------------------

PR_HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
ENTRYPOINT = "sandbox_executor.entrypoint.review_loop"


class FakeGitCommands:
    """A fake ``run_cmd`` that records argv and answers git commands from a small rule table."""

    def __init__(self, head_sha: str = PR_HEAD_SHA, fail_on: tuple[str, ...] = (), heads: list[str] | None = None):
        self.calls: list[list[str]] = []
        self._head_sha = head_sha
        self._fail_on = fail_on
        # Successive answers for 'git rev-parse HEAD'; the last one repeats.
        self._heads = list(heads or [head_sha])

    def __call__(self, args, cwd=None, env=None, check=True):
        self.calls.append(list(args))
        verb = args[1] if len(args) > 1 else ""
        if verb in self._fail_on:
            return subprocess.CompletedProcess(args, 128, stdout="", stderr=f"fatal: {verb} failed")
        if verb == "rev-parse" and args[-1].startswith("HEAD"):
            head = self._heads.pop(0) if len(self._heads) > 1 else self._heads[0]
            return subprocess.CompletedProcess(args, 0, stdout=f"{head}\n", stderr="")
        if verb == "rev-parse":
            return subprocess.CompletedProcess(args, 0, stdout=f"{self._head_sha}\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


def _fake_gh(head_branch: str = "feature/x"):
    payload = {
        "number": 90,
        "headRefName": head_branch,
        "headRefOid": PR_HEAD_SHA,
        "baseRefName": "release",
        "title": "T",
        "body": "B",
    }
    calls: list[list[str]] = []

    def _run(argv, cwd):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    return _run, calls


def _ledger_rows(repo_dir: Path) -> list[dict]:
    ledger = repo_dir / "holon-knowledge" / "ledger" / "executions.jsonl"
    if not ledger.exists():
        return []
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_entrypoint(repo_dir: Path, target: str, fake_git: FakeGitCommands, *extra: str):
    """Run the entrypoint main() with fakes at the gh, run_cmd and loop seams; returns (exit, loop, post)."""
    run_gh, gh_calls = _fake_gh()
    loop_result = {
        "target": target,
        "consensus": {"approved": True, "status": "approved", "iterations": 1},
        "ensemble_verdict": "APPROVED",
    }
    with (
        patch.object(sys, "argv", ["review_loop", target, f"--repo-dir={repo_dir}", *extra]),
        patch.dict(os.environ, {"HOLON_REPO_URL": "https://github.com/acme/widgets.git"}),
        patch(f"{ENTRYPOINT}._gh_runner", return_value=run_gh),
        patch(f"{ENTRYPOINT}.run_cmd", side_effect=fake_git),
        patch(f"{ENTRYPOINT}.check_and_resolve_stage_conflicts"),
        patch(f"{ENTRYPOINT}.run_review_loop", return_value=loop_result) as mock_loop,
        patch("sandbox_executor.flow.post_pr_review_consensus") as mock_post,
        pytest.raises(SystemExit) as exit_info,
    ):
        review_loop_main()
    return exit_info.value.code, mock_loop, mock_post, gh_calls


def test_pr_number_target_clones_the_head_branch_never_the_number(tmp_path):
    from sandbox_executor.entrypoint import review_loop as entrypoint

    fake_git = FakeGitCommands()
    verified = []
    real_verify_head = entrypoint._verify_head

    def spy(repo_dir, target):
        verified.append(real_verify_head(repo_dir, target))
        return verified[-1]

    with patch(f"{ENTRYPOINT}._verify_head", side_effect=spy):
        code, mock_loop, _post, gh_calls = _run_entrypoint(tmp_path, "90", fake_git)

    assert code == 0
    assert gh_calls == [["gh", "pr", "view", "90", "--json", "number,headRefName,headRefOid,baseRefName,title,body"]]
    clone = next(c for c in fake_git.calls if c[1] == "clone")
    assert clone[:4] == ["git", "clone", "--branch", "feature/x"]
    for argv in fake_git.calls:
        for i, token in enumerate(argv[:-1]):
            assert not (token == "--branch" and argv[i + 1] == "90"), argv
    # The PR stays the loop target; the head branch is the execution branch.
    assert mock_loop.call_args.kwargs["target"] == "90"
    assert mock_loop.call_args.kwargs["context"].execution_branch == "feature/x"
    # Verifying HEAD keeps every resolved field, including the PR's own base branch.
    assert [(t.head_sha, t.pr_number, t.base_branch) for t in verified] == [(PR_HEAD_SHA, 90, "release")]


def test_branch_target_in_an_existing_checkout_fetches_and_checks_out_the_branch(tmp_path):
    (tmp_path / ".git").mkdir()
    fake_git = FakeGitCommands()

    code, mock_loop, _post, gh_calls = _run_entrypoint(tmp_path, "feature/y", fake_git)

    assert code == 0
    assert gh_calls == []
    assert ["git", "fetch", "origin", "+refs/heads/feature/y:refs/remotes/origin/feature/y"] in fake_git.calls
    assert ["git", "checkout", "-B", "feature/y", "origin/feature/y"] in fake_git.calls
    assert mock_loop.call_args.kwargs["target"] == "feature/y"


def test_failing_clone_exits_one_posts_nothing_and_writes_one_failure_row(tmp_path, capsys):
    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True)
    fake_git = FakeGitCommands(fail_on=("clone",))

    code, mock_loop, mock_post, _gh = _run_entrypoint(tmp_path, "90", fake_git)

    assert code == 1
    mock_loop.assert_not_called()
    mock_post.assert_not_called()
    rows = _ledger_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["action_id"].startswith("ACT-REV-")
    assert rows[0]["outcome"] == "failure"
    assert rows[0]["action_origin"] == "pr_review"
    assert rows[0]["target"] == "90"
    assert "clone failed" in rows[0]["error_context"]
    out = capsys.readouterr().out
    assert "Consensus      : FAILED" in out
    assert "APPROVED" not in out


def test_failing_clone_without_a_ledger_reports_on_stderr_only(tmp_path, capsys):
    fake_git = FakeGitCommands(fail_on=("clone",))

    code, mock_loop, _post, _gh = _run_entrypoint(tmp_path, "feature/x", fake_git)

    assert code == 1
    mock_loop.assert_not_called()
    assert not (tmp_path / "holon-knowledge").exists()
    err = capsys.readouterr().err
    assert "Review preparation failed (clone)" in err
    assert "No ledger row was written" in err


def test_failing_resolution_exits_one_before_any_git_command(tmp_path, capsys):
    fake_git = FakeGitCommands()

    def _failing_gh(argv, cwd):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no pull requests found")

    with (
        patch.object(sys, "argv", ["review_loop", "90", f"--repo-dir={tmp_path}", "--json"]),
        patch(f"{ENTRYPOINT}._gh_runner", return_value=_failing_gh),
        patch(f"{ENTRYPOINT}.run_cmd", side_effect=fake_git),
        patch(f"{ENTRYPOINT}.run_review_loop") as mock_loop,
        pytest.raises(SystemExit) as exit_info,
    ):
        review_loop_main()

    assert exit_info.value.code == 1
    assert fake_git.calls == []
    mock_loop.assert_not_called()
    data = json.loads(capsys.readouterr().out)
    assert data["consensus"] == {"approved": False, "status": "failed", "iterations": 0}
    assert "no pull requests found" in data["error"]


def test_head_mismatch_fetches_the_resolved_sha_explicitly(tmp_path):
    fake_git = FakeGitCommands(heads=["f" * 40, PR_HEAD_SHA])

    code, _loop, _post, _gh = _run_entrypoint(tmp_path, "90", fake_git)

    assert code == 0
    assert ["git", "fetch", "origin", PR_HEAD_SHA] in fake_git.calls
    assert ["git", "checkout", "--detach", PR_HEAD_SHA] in fake_git.calls


def test_head_mismatch_with_a_failing_sha_fetch_exits_one(tmp_path):
    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True)
    fake_git = FakeGitCommands(heads=["f" * 40], fail_on=("fetch",))

    code, mock_loop, _post, _gh = _run_entrypoint(tmp_path, "90", fake_git)

    assert code == 1
    mock_loop.assert_not_called()
    assert len(_ledger_rows(tmp_path)) == 1


def test_gh_runner_names_the_repository_without_credentials(tmp_path, monkeypatch):
    from sandbox_executor.entrypoint import review_loop as entry

    monkeypatch.setenv("HOLON_REPO_URL", "https://x-access-token:secret@github.com/acme/widgets.git")
    seen: dict = {}

    def _fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch(f"{ENTRYPOINT}.subprocess.run", side_effect=_fake_run):
        entry._gh_runner(str(tmp_path))(["gh", "pr", "view", "90"], str(tmp_path))

    assert seen["env"]["GH_REPO"] == "acme/widgets"
    assert seen["timeout"] == 60


# --------------------------------------------------------------------------------------
# run_review_loop: empty-diff guard, failed posts, dry runs, merges and pending CI
# --------------------------------------------------------------------------------------


def _loop_ctx(tmp_path: Path, target: str = "feature/x", dry_run: bool = False):
    from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus

    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True, exist_ok=True)
    ctx = FlowContext(repo_dir=str(tmp_path), execution_branch=target, token_reduce=False, dry_run=dry_run)
    ctx.stage_results["execute"] = StageResult(
        stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"test_pass_rate": 1.0, "exit_code": 0}
    )
    return ctx


def _green_loop_patches(verdict=None, post=None, which=None):
    from contextlib import ExitStack

    from sandbox_executor.review_target import DiffVerdict

    stack = ExitStack()
    verify = stack.enter_context(
        patch(
            "sandbox_executor.review_target.verify_review_diff",
            return_value=(verdict or DiffVerdict.VERIFIED, "guard detail"),
        )
    )
    stack.enter_context(patch("sandbox_executor.flow.shutil.which", return_value=which))
    stack.enter_context(patch("sandbox_executor.flow.check_pr_mergeability", return_value={"is_dirty": False}))
    stack.enter_context(
        patch("sandbox_executor.flow.probe_ci_checks", return_value={"all_passed": True, "source": "local_tests"})
    )
    stack.enter_context(patch("sandbox_executor.flow._resolve_review_commit", return_value="abc123"))
    mock_post = stack.enter_context(
        patch("sandbox_executor.flow.post_pr_review_consensus", **(post or {"return_value": {"dual_posted": True}}))
    )
    return stack, verify, mock_post


@pytest.mark.parametrize("which", [None, "/usr/bin/gh"], ids=["no-gh", "gh"])
def test_empty_diff_guard_runs_without_auto_pr_and_without_gh(tmp_path, which):
    from sandbox_executor.flow import run_review_loop
    from sandbox_executor.review_target import DiffVerdict

    stack, verify, mock_post = _green_loop_patches(verdict=DiffVerdict.EMPTY, which=which)
    with stack:
        res = run_review_loop(_loop_ctx(tmp_path), target="feature/x", auto_pr=False, max_iterations=1)

    verify.assert_called_once()
    assert res["consensus"]["approved"] is False
    assert res["consensus"]["status"] == "rejected"
    mock_post.assert_not_called()


def test_empty_diff_guard_runs_for_a_pr_number_target(tmp_path):
    from sandbox_executor.flow import run_review_loop
    from sandbox_executor.review_target import DiffVerdict, ReviewTarget

    resolved = ReviewTarget(raw="90", head_branch="feature/x", head_sha=PR_HEAD_SHA, pr_number=90)
    stack, verify, mock_post = _green_loop_patches(verdict=DiffVerdict.EMPTY)
    with stack, patch("sandbox_executor.review_target.resolve_review_target", return_value=resolved) as resolve:
        res = run_review_loop(_loop_ctx(tmp_path, "90"), target="90", auto_pr=False, max_iterations=1)

    resolve.assert_called_once_with(str(tmp_path), "90", run_gh=None)
    assert verify.call_args.args[1] == resolved
    assert res["consensus"]["approved"] is False
    mock_post.assert_not_called()


def test_unresolvable_pr_target_fails_closed_without_posting(tmp_path):
    from sandbox_executor.flow import run_review_loop
    from sandbox_executor.review_target import ReviewTargetError

    stack, verify, mock_post = _green_loop_patches()
    with (
        stack,
        patch("sandbox_executor.review_target.resolve_review_target", side_effect=ReviewTargetError("gh missing")),
    ):
        res = run_review_loop(_loop_ctx(tmp_path, "90"), target="90", auto_pr=False, max_iterations=1)

    verify.assert_not_called()
    assert res["consensus"]["approved"] is False
    assert res["consensus"]["status"] == "failed"
    assert "gh missing" in res["error"]
    mock_post.assert_not_called()


@pytest.mark.parametrize(
    "post",
    [
        {"return_value": {"dual_posted": False, "error": "HTTP 502"}},
        {"side_effect": RuntimeError("HTTP 502")},
    ],
    ids=["returned-failure", "raised"],
)
def test_failed_post_makes_the_loop_non_approved(tmp_path, post):
    from sandbox_executor.flow import run_review_loop

    stack, _verify, mock_post = _green_loop_patches(post=post)
    with stack:
        res = run_review_loop(_loop_ctx(tmp_path), target="feature/x", auto_pr=False, max_iterations=1)

    mock_post.assert_called_once()
    assert res["consensus"]["approved"] is False
    assert res["consensus"]["status"] == "post_failed"
    assert res["consensus"]["score"] == 0.0
    assert res["halted_for_human"] is False
    assert "HTTP 502" in res["error"]
    rows = _ledger_rows(tmp_path)
    posts = [r for r in rows if str(r.get("action_id", "")).startswith("ACT-POST")]
    assert len(posts) == 1
    assert posts[0]["outcome"] == "failure"
    summary = next(r for r in rows if r.get("entry_type") == "summary")
    assert summary["status"] == "failed"
    assert "consensus post failed" in summary["summary"]


def test_failed_post_makes_the_cli_exit_one(tmp_path, capsys):
    import argparse

    from sandbox_executor.flow import run_review_loop_cli

    args = argparse.Namespace(repo_dir=str(tmp_path), target="feature/x", auto_pr=False, dry_run=False, json=False)
    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True)
    stack, _verify, _post = _green_loop_patches(post={"return_value": {"dual_posted": False, "error": "HTTP 502"}})
    with stack:
        code = run_review_loop_cli(args)

    assert code == 1
    out = capsys.readouterr().out
    assert "POST_FAILED" in out
    assert "Reason:" in out and "HTTP 502" in out


def test_dry_run_is_never_approved(tmp_path):
    from sandbox_executor.flow import run_review_loop

    stack, verify, mock_post = _green_loop_patches()
    with stack:
        res = run_review_loop(_loop_ctx(tmp_path, dry_run=True), target="feature/x", dry_run=True, max_iterations=1)

    verify.assert_not_called()
    mock_post.assert_not_called()
    assert res["consensus"]["approved"] is False
    assert res["consensus"]["status"] == "not_measured"


def test_approved_loop_never_merges(tmp_path, gh_write_guard):
    """A full approved loop through the real posting code issues no merge or auto-merge gh command."""
    from sandbox_executor.flow import run_review_loop
    from sandbox_executor.review_target import DiffVerdict

    real_run = subprocess.run
    gh_calls: list[list[str]] = []

    def _recording_run(argv, *args, **kwargs):
        if isinstance(argv, list) and argv and argv[0] == "gh":
            gh_calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return real_run(argv, *args, **kwargs)

    with (
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "ok")),
        patch("sandbox_executor.flow.shutil.which", return_value="/usr/bin/gh"),
        patch("sandbox_executor.flow.check_pr_mergeability", return_value={"is_dirty": False}),
        patch("sandbox_executor.flow.probe_ci_checks", return_value={"all_passed": True, "source": "github_cli"}),
        patch("sandbox_executor.flow.create_or_update_pr", return_value={"success": True}),
        patch("sandbox_executor.flow._resolve_review_commit", return_value="abc123"),
        patch("sandbox_executor.flow.subprocess.run", side_effect=_recording_run),
    ):
        res = run_review_loop(_loop_ctx(tmp_path), target="feature/x", max_iterations=1)

    assert res["consensus"]["approved"] is True
    assert gh_calls, "the posting path issued no gh command"
    for argv in gh_calls:
        assert "merge" not in argv[:3], argv
        assert "--auto" not in argv, argv
    assert gh_write_guard == []


def test_pending_ci_wait_honours_its_deadline(tmp_path):
    from sandbox_executor.flow import FlowContext, _wait_for_pending_ci

    now = [0.0]
    sleeps: list[float] = []

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    pending = {"source": "github_cli", "pending": 2, "all_passed": False}
    probe = MagicMock(return_value=dict(pending))
    with patch("sandbox_executor.flow.probe_ci_checks", probe):
        res = _wait_for_pending_ci(
            FlowContext(repo_dir=str(tmp_path)),
            str(tmp_path),
            "feature/x",
            {},
            dict(pending),
            timeout=40.0,
            poll_interval=15.0,
            clock=lambda: now[0],
            sleep=_sleep,
        )

    assert sleeps == [15.0, 15.0, 10.0]
    assert now[0] == 40.0
    assert res["pending"] == 2
    assert res["all_passed"] is False


def test_pending_ci_wait_returns_once_checks_settle(tmp_path):
    from sandbox_executor.flow import FlowContext, _wait_for_pending_ci

    sleeps: list[float] = []
    settled = {"source": "github_cli", "pending": 0, "all_passed": True}
    probe = MagicMock(return_value=dict(settled))
    with patch("sandbox_executor.flow.probe_ci_checks", probe):
        res = _wait_for_pending_ci(
            FlowContext(repo_dir=str(tmp_path)),
            str(tmp_path),
            "feature/x",
            {},
            {"source": "github_cli", "pending": 1},
            timeout=600.0,
            poll_interval=15.0,
            clock=lambda: 0.0,
            sleep=sleeps.append,
        )

    assert sleeps == [15.0]
    assert probe.call_count == 1
    assert res["all_passed"] is True


def test_mergeability_probe_is_bounded_by_a_subprocess_timeout(tmp_path):
    from sandbox_executor.flow import check_pr_mergeability

    seen: list[dict] = []

    def _timing_out(argv, *args, **kwargs):
        if argv[0] == "gh":
            seen.append(kwargs)
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")

    with (
        patch("sandbox_executor.flow.shutil.which", return_value="/usr/bin/gh"),
        patch("sandbox_executor.flow.subprocess.run", side_effect=_timing_out),
        patch("sandbox_executor.flow.run_git", return_value=MagicMock(returncode=1, stdout="", stderr="")),
    ):
        res = check_pr_mergeability(str(tmp_path), "feature/x", "main")

    assert len(seen) == 1
    assert seen[0]["timeout"] == 60
    assert res["source"] != "github_cli"


# --- Real reviewer ensemble wiring (fakes only: no runner, gh, Docker or remote) ------------------------------------

EXEC_TARGET = "I-100-intent/P-200-plan/E-300-exec/_"
_CLEAN_MERGE = {"is_dirty": False, "merge_state_status": "CLEAN", "mergeable": "MERGEABLE"}
_GREEN_CI = {"all_passed": True, "passed": 1, "failed": 0, "pending": 0, "total": 1, "source": "github_cli"}


def _ensemble_ctx(tmp_path: Path, **kwargs):
    from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus

    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True, exist_ok=True)
    ctx = FlowContext(
        repo_dir=str(tmp_path),
        execution_branch=EXEC_TARGET,
        agent="fake-agent",
        model="fake-model",
        token_reduce=False,
        **kwargs,
    )
    ctx.stage_results["execute"] = StageResult(
        stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"test_pass_rate": 1.0, "exit_code": 0}
    )
    return ctx


def _ledger(tmp_path: Path) -> list[dict]:
    ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
    return [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()] if ledger.exists() else []


def _rows(tmp_path: Path, prefix: str) -> list[dict]:
    return [r for r in _ledger(tmp_path) if str(r.get("action_id", "")).startswith(prefix + "-")]


def _reviewer_rows(tmp_path: Path) -> list[dict]:
    return [r for r in _rows(tmp_path, "ACT-REV") if "review_reviewer_index" in r]


class _LoopStubs:
    """Patch mergeability, CI, the empty-diff guard, the reviewed commit and posting; ``post`` is the posting mock."""

    def __init__(self, merge=None, ci=None):
        self.merge = dict(merge or _CLEAN_MERGE)
        self.ci = dict(ci or _GREEN_CI)
        self._patches = []
        self.post = MagicMock(return_value={"dual_posted": True, "official_review": {"success": True}})

    def __enter__(self):
        from sandbox_executor.review_target import DiffVerdict

        self._patches = [
            patch("sandbox_executor.flow.shutil.which", return_value=None),
            patch("sandbox_executor.flow.check_pr_mergeability", side_effect=lambda *a, **k: dict(self.merge)),
            patch("sandbox_executor.flow.probe_ci_checks", side_effect=lambda *a, **k: dict(self.ci)),
            patch("sandbox_executor.flow.post_pr_review_consensus", self.post),
            patch("sandbox_executor.flow._resolve_review_commit", return_value="abc1234"),
            patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()


def _loop(tmp_path: Path, ensemble, *, size: int = 3, merge=None, ci=None, **ctx_kwargs):
    from sandbox_executor.flow import run_review_loop

    ctx = _ensemble_ctx(tmp_path, **ctx_kwargs)
    with _LoopStubs(merge, ci) as stubs:
        res = run_review_loop(ctx, target=EXEC_TARGET, max_iterations=5, ensemble_size=size, run_ensemble_fn=ensemble)
    return res, stubs.post, ctx


def _cli(tmp_path: Path, ensemble, **stub_kwargs) -> int:
    import argparse

    from sandbox_executor.flow import run_review_loop_cli

    _ensemble_ctx(tmp_path)
    args = argparse.Namespace(
        repo_dir=str(tmp_path),
        target=EXEC_TARGET,
        max_iterations=5,
        ensemble_size=3,
        auto_pr=False,
        dry_run=False,
        json=False,
        agent="fake-agent",
        model="fake-model",
    )
    with _LoopStubs(**stub_kwargs), patch("sandbox_executor.flow.run_ensemble", ensemble):
        return run_review_loop_cli(args)


def test_three_clean_reviewers_with_mergeable_pr_and_green_ci_approve(tmp_path):
    from tests.review_fakes import FakeEnsemble

    ensemble = FakeEnsemble()
    res, post, _ = _loop(tmp_path, ensemble)

    assert res["consensus"]["approved"] is True
    assert res["consensus"]["status"] == "approved"
    assert res["ensemble_verdict"] == "APPROVED"
    assert [call["size"] for call in ensemble.calls] == [3]
    assert (ensemble.calls[0]["agent"], ensemble.calls[0]["model"]) == ("fake-agent", "fake-model")
    rows = _reviewer_rows(tmp_path)
    assert [r["review_reviewer_index"] for r in rows] == [1, 2, 3]
    assert {(r["review_status"], r["review_verdict"], r["outcome"]) for r in rows} == {
        ("COMPLETED", "APPROVED", "success")
    }
    assert rows[0]["review_counts"] == {"critical": 0, "important": 0, "nit": 0}
    assert not _rows(tmp_path, "ACT-FIND")
    assert not _rows(tmp_path, "ACT-RES")
    post.assert_called_once()
    assert [r.index for r in post.call_args.kwargs["reviewers"]] == [1, 2, 3]


def test_stage4_action_event_rewrite_propagates_instead_of_warning(tmp_path):
    """A rewrite reported by the Stage 4 action event stops the loop; it is never downgraded to a warning."""
    from sandbox_executor.ledger import LedgerRewriteError

    from tests.review_fakes import FakeEnsemble

    rewrite = LedgerRewriteError("executions.jsonl was rewritten, not appended")
    with (
        patch("sandbox_executor.flow.append_action_event", side_effect=rewrite),
        patch("sandbox_executor.flow._warn_ledger_failure") as warn,
        pytest.raises(LedgerRewriteError),
    ):
        _loop(tmp_path, FakeEnsemble())
    warn.assert_not_called()


def test_report_names_the_commit_the_reviewers_saw_even_when_the_branch_moves(tmp_path):
    from tests.review_fakes import FAKE_HEAD_SHA, FakeEnsemble

    # _LoopStubs makes the late branch lookup return "abc1234", as if the head moved after the reviewers ran.
    res, post, _ = _loop(tmp_path, FakeEnsemble())

    assert res["consensus"]["approved"] is True
    assert post.call_args.kwargs["consensus_data"]["target_commit"] == FAKE_HEAD_SHA


def test_cli_exits_zero_only_for_the_clean_ensemble(tmp_path, capsys):
    from tests.review_fakes import FakeEnsemble

    assert _cli(tmp_path, FakeEnsemble()) == 0
    out = capsys.readouterr().out
    assert "Ensemble       : APPROVED" in out
    assert "Reviewer 1: fake-agent / fake-model: APPROVED (critical 0, important 0, nit 0)" in out


def _blocking_cases():
    from sandbox_executor.review_ensemble import ReviewerVerdict

    from tests.review_fakes import clean_reviewer, failed_reviewer, objecting_reviewer

    return {
        "zero-reviewers": [],
        "failed-reviewer": [clean_reviewer(1), failed_reviewer(2), clean_reviewer(3)],
        "unparseable-reviewer": [
            clean_reviewer(1),
            clean_reviewer(2),
            failed_reviewer(3, "invalid review trailer: the closing marker is not the last line"),
        ],
        "changes-requested": [clean_reviewer(1), objecting_reviewer(2, "important"), clean_reviewer(3)],
        "approved-with-a-nit": [
            clean_reviewer(1),
            clean_reviewer(2),
            objecting_reviewer(3, "nit", verdict=ReviewerVerdict.APPROVED),
        ],
    }


@pytest.mark.parametrize(
    "case", ["zero-reviewers", "failed-reviewer", "unparseable-reviewer", "changes-requested", "approved-with-a-nit"]
)
def test_any_blocking_reviewer_outcome_stops_after_one_iteration_and_records_rows(tmp_path, case):
    from tests.review_fakes import FakeEnsemble

    results = _blocking_cases()[case]
    ensemble = FakeEnsemble(results)
    res, post, _ = _loop(tmp_path, ensemble)

    assert res["consensus"]["approved"] is False
    assert res["ensemble_verdict"] in ("FAILED", "CHANGES_REQUESTED")
    assert res["total_iterations"] == 1
    assert len(ensemble.calls) == 1
    rows = _reviewer_rows(tmp_path)
    assert [r["review_reviewer_index"] for r in rows] == [r.index for r in results]
    findings = [r for r in _rows(tmp_path, "ACT-FIND") if "review_reviewer_index" in r]
    assert len(findings) == sum(len(r.findings) for r in results)
    for row in findings:
        assert row["review_file"] == "src/app.py"
        assert row["review_line"] == 12
        assert row["outcome"] == "failure"
    failed_rows = [r for r in rows if r["review_status"] == "FAILED"]
    assert all(r["review_verdict"] is None and r["error_context"] for r in failed_rows)
    assert not _rows(tmp_path, "ACT-RES")
    # A review is posted only when at least one reviewer completed.
    assert post.called is bool(results)
    assert bool(_rows(tmp_path, "ACT-POST")) is bool(results)


@pytest.mark.parametrize(
    "case", ["zero-reviewers", "failed-reviewer", "unparseable-reviewer", "changes-requested", "approved-with-a-nit"]
)
def test_cli_exits_one_for_every_blocking_reviewer_outcome(tmp_path, case):
    from tests.review_fakes import FakeEnsemble

    assert _cli(tmp_path, FakeEnsemble(_blocking_cases()[case])) == 1
    assert _rows(tmp_path, "ACT-REV")


def test_changes_requested_maps_to_its_own_status_and_carries_reviewer_summaries(tmp_path):
    from tests.review_fakes import FakeEnsemble, clean_reviewer, objecting_reviewer

    res, post, _ = _loop(tmp_path, FakeEnsemble([clean_reviewer(1), objecting_reviewer(2, "critical")]), size=2)

    assert res["consensus"]["status"] == "changes_requested"
    assert res["severity"] == "critical"
    assert "FIND-R2-001-01" in res["findings"]
    assert res["reviewers"][1]["verdict"] == "CHANGES_REQUESTED"
    assert "report_text" not in json.dumps(res)
    assert post.call_args.kwargs["consensus_data"]["ensemble_verdict"] == "CHANGES_REQUESTED"


@pytest.mark.parametrize(
    ("merge", "ci"),
    [
        ({"is_dirty": True, "merge_state_status": "DIRTY", "mergeable": "CONFLICTING"}, None),
        (None, {**_GREEN_CI, "all_passed": False, "passed": 0, "failed": 1}),
        (None, {"source": "github_cli", "total": 0}),
    ],
    ids=["dirty", "ci-failing", "ci-unknown"],
)
def test_clean_reviewers_never_approve_without_a_mergeable_pr_and_green_ci(tmp_path, merge, ci):
    from tests.review_fakes import FakeEnsemble

    res, _, _ = _loop(tmp_path, FakeEnsemble(), merge=merge, ci=ci)

    assert res["consensus"]["approved"] is False
    assert res["ensemble_verdict"] == "CHANGES_REQUESTED"
    assert res["total_iterations"] == 1


def test_host_run_dispatches_review_loop_container_when_unsandboxed(tmp_path, monkeypatch):
    from sandbox_executor.flow import run_review_loop
    from sandbox_executor.review_ensemble import (
        ConsensusVerdict,
        ParsedTrailer,
        ReviewCounts,
        ReviewerResult,
        ReviewerVerdict,
        serialize_review_outcome,
    )

    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)

    def fake_dispatch(role, runner, model, args, **kwargs):
        assert role == "review-loop"
        assert "--result-file=/tmp/review_result.json" in args
        result_file = kwargs.get("result_file")
        assert result_file is not None
        p1 = ParsedTrailer(ReviewerVerdict.APPROVED, ReviewCounts(), ())
        r1 = ReviewerResult.completed(1, "fake-agent", "fake-model", "r1.md", "", p1)
        r2 = ReviewerResult.completed(2, "fake-agent", "fake-model", "r2.md", "", p1)
        r3 = ReviewerResult.completed(3, "fake-agent", "fake-model", "r3.md", "", p1)
        payload = serialize_review_outcome([r1, r2, r3], ConsensusVerdict.APPROVED)
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        return 0

    ctx = _ensemble_ctx(tmp_path, dispatcher=fake_dispatch)
    ensemble = MagicMock(side_effect=AssertionError("a reviewer turn ran on the host"))
    with (
        _LoopStubs() as stubs,
        patch("sandbox_executor.flow.run_ensemble", ensemble),
    ):
        res = run_review_loop(ctx, target=EXEC_TARGET, max_iterations=1)

    assert res["consensus"]["status"] == "approved"
    assert res["consensus"]["approved"] is True
    assert res["total_iterations"] == 1
    ensemble.assert_not_called()
    stubs.post.assert_called_once()
    assert len(_rows(tmp_path, "ACT-REV")) >= 3
    assert len(_rows(tmp_path, "ACT-POST")) == 1


def test_stage4_on_the_host_fails_closed_when_container_dispatch_fails(tmp_path, monkeypatch):
    from sandbox_executor.flow import StageStatus, run_review_stage

    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)
    dispatcher = MagicMock(return_value=1)
    ensemble = MagicMock(side_effect=AssertionError("a reviewer turn ran on the host"))
    ctx = _ensemble_ctx(tmp_path, plan_branch="I-100-intent/P-200-plan/_", dispatcher=dispatcher)
    with (
        _LoopStubs() as stubs,
        patch("sandbox_executor.flow.run_ensemble", ensemble),
        patch("sandbox_executor.flow._checkout_execution_branch", return_value=None),
        patch("sandbox_executor.flow.check_and_resolve_stage_conflicts"),
        patch("sandbox_executor.flow.run_git", return_value=MagicMock(returncode=1, stdout="", stderr="")),
    ):
        result = run_review_stage(ctx)

    assert result.status is StageStatus.FAILED
    assert "Reviewer ensemble container dispatch exited with code 1" in (result.error or "")
    dispatcher.assert_called_once()
    assert dispatcher.call_args[0][0] == "review-loop"
    ensemble.assert_not_called()
    stubs.post.assert_not_called()


def test_stage4_threads_the_flow_ensemble_size_and_reports_reviewer_objections(tmp_path):
    from sandbox_executor.flow import StageStatus, run_review_stage

    from tests.review_fakes import FakeEnsemble, objecting_reviewer

    ensemble = FakeEnsemble(lambda size: [objecting_reviewer(i) for i in range(1, size + 1)])
    ctx = _ensemble_ctx(tmp_path, review_ensemble_size=2)
    with (
        _LoopStubs(),
        patch("sandbox_executor.flow.run_ensemble", ensemble),
        patch("sandbox_executor.flow._checkout_execution_branch", return_value=None),
        patch("sandbox_executor.flow.check_and_resolve_stage_conflicts"),
    ):
        result = run_review_stage(ctx)

    assert [call["size"] for call in ensemble.calls] == [2]
    assert result.status is StageStatus.FAILED
    assert "requested changes" in (result.error or "")
    assert "no automatic resolver" in (result.error or "")


def test_stage4_reports_an_incomplete_ensemble_rather_than_failed_tests(tmp_path):
    from sandbox_executor.flow import StageStatus, run_review_stage

    from tests.review_fakes import FakeEnsemble, clean_reviewer, failed_reviewer

    ensemble = FakeEnsemble([clean_reviewer(1), failed_reviewer(2, "runner exited token=ghp_" + "a" * 36)])
    ctx = _ensemble_ctx(tmp_path, review_ensemble_size=2)
    with (
        _LoopStubs(),
        patch("sandbox_executor.flow.run_ensemble", ensemble),
        patch("sandbox_executor.flow._checkout_execution_branch", return_value=None),
        patch("sandbox_executor.flow.check_and_resolve_stage_conflicts"),
    ):
        result = run_review_stage(ctx)

    error = result.error or ""
    assert result.status is StageStatus.FAILED
    assert "the reviewer ensemble did not complete" in error
    assert "Reviewer 1: fake-agent / fake-model: APPROVED" in error
    assert "Reviewer 2: fake-agent / fake-model: FAILED (runner exited" in error
    assert "ghp_" + "a" * 36 not in error
    assert "test suite reported failures" not in error


def test_unresolvable_target_fails_closed_without_reviewers_or_post(tmp_path, monkeypatch):
    from sandbox_executor import flow

    monkeypatch.setattr(flow, "_resolve_reviewed_target", lambda repo, target: (None, "Could not resolve x"))
    ensemble = MagicMock()
    res, post, _ = _loop(tmp_path, ensemble)

    assert res["consensus"]["status"] == "failed"
    assert res["error"] == "Could not resolve x"
    ensemble.assert_not_called()
    post.assert_not_called()


def test_a_failing_diff_fails_the_iteration_and_posts_nothing(tmp_path, monkeypatch):
    from sandbox_executor import flow

    def _broken(*_args, **_kwargs):
        raise RuntimeError("git diff exited 128")

    monkeypatch.setattr(flow, "build_review_request", _broken)
    ensemble = MagicMock()
    res, post, _ = _loop(tmp_path, ensemble)

    assert res["consensus"]["status"] == "failed"
    assert res["ensemble_verdict"] == "FAILED"
    assert "git diff exited 128" in res["error"]
    assert res["total_iterations"] == 1
    ensemble.assert_not_called()
    post.assert_not_called()
    assert not _rows(tmp_path, "ACT-POST")


def test_dry_run_never_dispatches_reviewers(tmp_path):
    ensemble = MagicMock()
    res, post, _ = _loop(tmp_path, ensemble, dry_run=True)

    assert res["consensus"]["approved"] is False
    assert res["consensus"]["status"] == "not_measured"
    ensemble.assert_not_called()
    post.assert_not_called()


def test_entrypoint_exits_one_when_the_consensus_lacks_an_approved_ensemble_verdict(tmp_path):
    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True)
    loop_result = {"target": "I-100-test", "consensus": {"approved": True, "iterations": 1}}
    with (
        patch.object(sys, "argv", ["review_loop", "I-100-test", f"--repo-dir={tmp_path}"]),
        patch("sandbox_executor.entrypoint.review_loop.check_and_resolve_stage_conflicts"),
        patch("sandbox_executor.entrypoint.review_loop.run_review_loop", return_value=loop_result),
        patch("sandbox_executor.entrypoint.review_loop.run_cmd"),
        _prepared("I-100-test"),
        pytest.raises(SystemExit) as exc_info,
    ):
        review_loop_main()
    assert exc_info.value.code == 1


def test_build_review_request_diffs_base_to_head_without_metadata_paths(tmp_path):
    from sandbox_executor.review_ensemble import build_review_request
    from sandbox_executor.review_target import ReviewTarget

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q", "-b", "main")
    (tmp_path / "README.md").write_text("base\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    git("checkout", "-q", "-b", "feature")
    (tmp_path / "src.py").write_text("print('changed')\n")
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "P-1.md").write_text("plan\n")
    git("add", "-A")
    git("commit", "-q", "-m", "change")
    head = git("rev-parse", "HEAD")

    target = ReviewTarget(raw="feature", head_branch="feature", head_sha=head, title="Real title", body="Real body")
    request = build_review_request(target, "main", 2, "fallback title", "fallback body", str(tmp_path))

    assert request.head_sha == head
    assert request.iteration == 2
    assert (request.pr_title, request.pr_body) == ("Real title", "Real body")
    assert "src.py" in request.diff_text
    assert "plans/P-1.md" not in request.diff_text
    with pytest.raises(RuntimeError):
        build_review_request(target, "no-such-base", 1, "", "", str(tmp_path))
    with pytest.raises(RuntimeError):
        build_review_request(ReviewTarget("x", "x", ""), "main", 1, "", "", str(tmp_path))


def test_ensemble_size_help_text_and_validation(capsys):
    from sandbox_executor.cli import main
    from sandbox_executor.review_ensemble import ENSEMBLE_SIZE_HELP

    assert ENSEMBLE_SIZE_HELP == (
        "Number of independent reviewer agent turns per iteration; approval needs every reviewer to approve with "
        "zero findings (default: 3)"
    )
    for command in ("review-loop", "flow"):
        with patch.object(sys, "argv", ["holon", command, "--help"]), pytest.raises(SystemExit):
            main()
        assert ENSEMBLE_SIZE_HELP in " ".join(capsys.readouterr().out.split())
    with patch.object(sys, "argv", ["holon", "flow", "--ensemble-size", "0"]), pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "--ensemble-size must be at least 1" in capsys.readouterr().err
