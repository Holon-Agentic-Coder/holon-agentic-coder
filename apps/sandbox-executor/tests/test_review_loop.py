"""Unit tests for containerised Holon Review Loop Agent (Bean 0118)."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.entrypoint.review_loop import main as review_loop_main
from sandbox_executor.entrypoint.review_loop import run_cmd

from tests.leak_assertions import assert_no_fragment, make_gh_probe


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
        ):
            mock_loop.return_value = {
                "target": "I-100-test/P-200-test/E-300-exec/_",
                "consensus": {
                    "approved": True,
                    "score": 1.0,
                    "iterations": 1,
                    "status": "approved",
                },
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
        ):
            mock_loop.return_value = {
                "target": "I-100-test",
                "consensus": {"approved": True, "iterations": 1},
                "max_iterations": 25,
            }

            with pytest.raises(SystemExit) as exc_info:
                review_loop_main()

            assert exc_info.value.code == 0
            captured = capsys.readouterr()
            data = json.loads(captured.out)
            assert data["consensus"]["approved"] is True


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

        with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Success", stderr="")
            consensus_data = {
                "approved": True,
                "status": "approved",
                "iterations": 1,
                "max_review_iterations": 25,
            }
            res = post_pr_review_consensus(repo_dir="/tmp", target="123", consensus_data=consensus_data)

            assert res["gh_available"] is True
            assert res["dual_posted"] is True
            assert res["discussion_comment"]["success"] is True
            assert res["official_review"]["success"] is True
            assert mock_run.call_count == 2
            comment_call = mock_run.call_args_list[0][0][0]
            review_call = mock_run.call_args_list[1][0][0]
            assert comment_call[:4] == ["gh", "pr", "comment", "123"]
            assert review_call[:5] == ["gh", "pr", "review", "123", "--approve"]

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
            patch("sandbox_executor.flow.post_pr_review_consensus", return_value={}),
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
            patch("sandbox_executor.flow.post_pr_review_consensus", return_value={}),
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

        host = tmp_path / "host"
        host.mkdir()
        self._git(host, "init", "-q")
        self._git(
            host,
            "remote",
            "add",
            "origin",
            "https://x-access-token:ghp_abcdefghijklmnopqrstuvwxyz0123456789@127.0.0.1:9/r.git",
        )
        has_diff, detail = _execution_effective_diff(str(host), self.EXEC)
        assert has_diff is None
        assert "from origin failed" in detail
        assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in detail


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
