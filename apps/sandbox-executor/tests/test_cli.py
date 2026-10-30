import argparse
import copy
import importlib.metadata
import io
import json
import os
import secrets
import stat
import subprocess
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor import cli, local_llm
from sandbox_executor.cli import (
    _run_docker,
    build_network_isolation_args,
    find_github_token,
    get_agent_session_mounts,
    get_ssh_auth_mounts,
    main,
    normalize_repo_remote,
    resolve_target_repo_url,
    run_docker_container,
    strip_url_credentials,
)
from sandbox_executor.flow import SKIP_PUSH_DISPATCH_ERROR, FlowContext

from tests.leak_assertions import (
    CREDENTIAL_FREE_URLS,
    assert_names_repo,
    assert_no_fragment,
    assert_no_leak,
    make_gh_probe,
    make_probe,
    make_url_probe,
    url_userinfo_cases,
)
from tests.test_local_llm import HEALTH_COLD, HEALTH_OFF, HEALTH_SMALL_PROMPT, HEALTH_WARM, _config, _fetcher

# Captured at import, before the suite-wide `isolated_env_file_dir` fixture (conftest.py) replaces it.
_REAL_ENV_FILE_DIR = cli.env_file_dir


def _read_env_file(docker_argv: list[str]) -> dict[str, str]:
    """Parse the --env-file a recorded docker argv points at (only valid while the run is in progress)."""
    path = docker_argv[docker_argv.index("--env-file") + 1]
    with open(path, encoding="utf-8") as fh:
        return dict(line.rstrip("\n").split("=", 1) for line in fh)


class TestHolonCLI(unittest.TestCase):
    def test_find_github_token(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "gh_test_token_123"}):
            self.assertEqual(find_github_token(), "gh_test_token_123")

    def test_get_ssh_auth_mounts(self):
        mounts, envs = get_ssh_auth_mounts()
        self.assertIsInstance(mounts, list)
        self.assertIsInstance(envs, dict)

    @patch("sys.platform", "darwin")
    @patch("os.path.exists")
    def test_antigravity_macos_missing_session_exits_with_instructions(self, mock_exists):
        mock_exists.return_value = False
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as cm:
                get_agent_session_mounts("antigravity")
            self.assertEqual(cm.exception.code, 1)

    @patch("sys.platform", "darwin")
    @patch("os.path.exists", return_value=False)
    def test_antigravity_macos_missing_session_with_holon_agent_key_allowed(self, mock_exists):
        with patch.dict(os.environ, {"HOLON_AGENT_KEY": "dummy-token"}, clear=True):
            mounts = get_agent_session_mounts("antigravity")
            self.assertIsInstance(mounts, list)
            self.assertEqual(mounts, [])

    @patch("sys.platform", "darwin")
    @patch("os.path.exists")
    def test_antigravity_macos_existing_session_mounts(self, mock_exists):
        mock_exists.side_effect = lambda p: ".holon/sessions/antigravity" in p or "antigravity" in p
        mounts = get_agent_session_mounts("antigravity")
        self.assertIsInstance(mounts, list)
        self.assertIn("-v", mounts)
        self.assertTrue(any("/home/holon/.gemini/antigravity-cli:ro" in m for m in mounts))
        self.assertIn("--tmpfs", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/log:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/cache:uid=1000,gid=1000", mounts)

    @patch("sys.platform", "linux")
    @patch("os.path.exists")
    def test_antigravity_linux_mounts_gemini_cli(self, mock_exists):
        mock_exists.side_effect = lambda p: ".gemini/antigravity-cli" in p
        mounts = get_agent_session_mounts("antigravity")
        self.assertIsInstance(mounts, list)
        self.assertIn("-v", mounts)
        self.assertTrue(any("/home/holon/.gemini/antigravity-cli:ro" in m for m in mounts))
        self.assertIn("--tmpfs", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/log:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/cache:uid=1000,gid=1000", mounts)

    @patch("sys.platform", "linux")
    @patch("os.path.exists")
    @patch("os.getuid", return_value=1001, create=True)
    def test_antigravity_linux_dbus_mount(self, mock_uid, mock_exists):
        mock_exists.side_effect = lambda p: p in ("/run/user/1001/bus", os.path.expanduser("~/.gemini/antigravity-cli"))
        mounts = get_agent_session_mounts("antigravity")
        self.assertFalse(any("bus" in m for m in mounts))
        self.assertTrue(any("/home/holon/.gemini/antigravity-cli:ro" in m for m in mounts))
        self.assertIn("--tmpfs", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/log:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.gemini/antigravity-cli/cache:uid=1000,gid=1000", mounts)

    @patch("os.path.exists", return_value=True)
    def test_codex_session_mounts_tmpfs(self, mock_exists):
        mounts = get_agent_session_mounts("codex")
        self.assertIn("-v", mounts)
        self.assertTrue(any("/home/holon/.codex:ro" in m for m in mounts))
        self.assertIn("--tmpfs", mounts)
        self.assertIn("/home/holon/.codex/log:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.codex/cache:uid=1000,gid=1000", mounts)

    def test_all_agent_session_mounts_are_read_only(self):
        """Iterate across all supported agents and verify any -v directory mounts contain :ro."""
        agents = ["antigravity", "antigravity-agent", "claude", "codex", "pi", "gemini"]
        with patch("os.path.exists", return_value=True):
            for agent in agents:
                mounts = get_agent_session_mounts(agent)
                for idx, m in enumerate(mounts):
                    if m == "-v" and idx + 1 < len(mounts):
                        target = mounts[idx + 1]
                        self.assertTrue(
                            target.endswith(":ro"),
                            f"Mount for agent {agent} is not read-only: {target}",
                        )

    def test_build_network_isolation_args_contains_cap_drop(self):
        args = build_network_isolation_args()
        self.assertIn("--cap-drop=ALL", args)
        self.assertIn("--security-opt=no-new-privileges", args)

    def test_build_network_isolation_args_sinkholes_metadata(self):
        args = build_network_isolation_args()
        self.assertIn("--add-host", args)
        self.assertIn("169.254.169.254:127.0.0.1", args)
        self.assertIn("metadata.google.internal:127.0.0.1", args)
        self.assertIn("instance-data:127.0.0.1", args)

    def test_build_network_isolation_args_token_reduce(self):
        args = build_network_isolation_args(token_reduce=True)
        self.assertIn("--cap-drop=ALL", args)
        self.assertIn("--security-opt=no-new-privileges", args)
        self.assertIn("--add-host", args)
        self.assertIn("169.254.169.254:127.0.0.1", args)
        self.assertNotIn("--network", args)

    @patch("sys.platform", "darwin")
    @patch("os.path.exists", return_value=False)
    @patch("sys.stderr", new_callable=io.StringIO)
    def test_antigravity_darwin_missing_session_guidance_uses_rw(self, mock_stderr, mock_exists):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit):
            get_agent_session_mounts("antigravity")
        err_msg = mock_stderr.getvalue()
        self.assertIn(":rw", err_msg)
        self.assertNotIn(":ro", err_msg)

    @patch("sandbox_executor.cli.resolve_target_repo", return_value=("git@github.com:test/repo.git", None))
    @patch("sandbox_executor.cli.get_agent_session_mounts", return_value=["-v", "/mock:/mock:ro"])
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_run_docker_container_includes_network_isolation(
        self, mock_which, mock_run, mock_mounts, mock_resolve_repo
    ):
        mock_run.return_value = MagicMock(returncode=0)
        with patch.dict(os.environ, {"GITHUB_TOKEN": "secret_token"}, clear=True):
            code = run_docker_container(
                "planner",
                "holon/agent-antigravity",
                ["branch", "agent", "model"],
                agent_id="antigravity",
            )
            self.assertEqual(code, 0)
            executed_cmd = mock_run.call_args[0][0]
            self.assertIn("--cap-drop=ALL", executed_cmd)
            self.assertIn("--security-opt=no-new-privileges", executed_cmd)
            self.assertIn("169.254.169.254:127.0.0.1", executed_cmd)

    @patch("sandbox_executor.cli.resolve_target_repo", return_value=("git@github.com:test/repo.git", None))
    @patch("sandbox_executor.cli.get_agent_session_mounts", return_value=["-v", "/mock:/mock"])
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_run_docker_container(self, mock_which, mock_run, mock_mounts, mock_resolve_repo):
        env_files: list[dict[str, str]] = []
        mock_run.side_effect = lambda cmd, *a, **k: env_files.append(_read_env_file(cmd)) or MagicMock(returncode=0)
        with patch.dict(os.environ, {"GITHUB_TOKEN": "secret_token"}, clear=True):
            code = run_docker_container(
                "planner",
                "holon/agent-antigravity",
                ["branch", "agent", "model"],
                agent_id="antigravity",
            )
            self.assertEqual(code, 0)
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            self.assertIn("docker", args)
            # The token travels in the 0600 env-file, never on the docker command line.
            self.assertFalse(any("secret_token" in arg for arg in args))
            self.assertEqual(env_files, [{"GITHUB_TOKEN": "secret_token"}])
            self.assertIn("HOLON_REPO_URL=git@github.com:test/repo.git", args)

    @patch("sandbox_executor.cli.resolve_target_repo", return_value=("git@github.com:test/repo.git", None))
    @patch("sandbox_executor.cli.get_agent_session_mounts", return_value=["-v", "/mock:/mock"])
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_run_docker_container_forward_env_vars(self, mock_which, mock_run, mock_mounts, mock_resolve_repo):
        env_files: list[dict[str, str]] = []
        mock_run.side_effect = lambda cmd, *a, **k: env_files.append(_read_env_file(cmd)) or MagicMock(returncode=0)
        env = {
            "GITHUB_TOKEN": "my-github-token",
            "HOLON_ROLE": "host-role-should-be-overridden",
            "HOLON_AGENT_KEY": "my-agent-key",
            "HOLON_AGENT_EFFORT": "high",
            "HOLON_AGENT_PROVIDER": "anthropic",
            "SOME_OTHER_VAR": "should-not-be-forwarded",
        }
        with patch.dict(os.environ, env, clear=True):
            code = run_docker_container(
                "planner",
                "holon/agent-antigravity",
                ["branch", "agent", "model"],
                agent_id="antigravity",
            )
            self.assertEqual(code, 0)
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            self.assertIn("docker", args)
            # Credentials are forwarded through the env-file, not the docker command line.
            self.assertEqual(env_files, [{"GITHUB_TOKEN": "my-github-token", "HOLON_AGENT_KEY": "my-agent-key"}])
            self.assertFalse(any("my-github-token" in arg or "my-agent-key" in arg for arg in args))
            self.assertIn("HOLON_AGENT_EFFORT=high", args)
            self.assertIn("HOLON_AGENT_PROVIDER=anthropic", args)
            # Ensure programmatic role takes strict precedence over host environment
            self.assertIn("HOLON_ROLE=planner", args)
            self.assertNotIn("HOLON_ROLE=host-role-should-be-overridden", args)
            self.assertEqual([a for a in args if a.startswith("HOLON_ROLE=")], ["HOLON_ROLE=planner"])
            # Ensure non-prefixed variable is NOT forwarded
            self.assertFalse(any("SOME_OTHER_VAR" in arg for arg in args))
            self.assertIn("HOLON_REPO_URL=git@github.com:test/repo.git", args)

    @patch("sandbox_executor.cli.resolve_target_repo", return_value=("git@github.com:test/repo.git", None))
    @patch("sandbox_executor.cli.get_agent_session_mounts", return_value=["-v", "/mock:/mock"])
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_run_docker_container_hands_down_the_active_stage_remainder(
        self, mock_which, mock_run, mock_mounts, mock_resolve_repo
    ):
        # C6-I1: inside a timed host stage the container gets the remaining time; outside one nothing is added.
        from sandbox_executor.budgets import StageTimer, active_stage_timer

        mock_run.return_value = MagicMock(returncode=0)
        now = [1000.0]
        timer = StageTimer("execute", 2700, clock=lambda: now[0])
        now[0] += 700
        with patch.dict(os.environ, {"HOLON_STAGE_BUDGET_SECONDS": "9999"}, clear=True):
            with active_stage_timer(timer):
                run_docker_container("executor", "holon/agent-pi", ["branch"], agent_id="pi")
            budget_args = [a for a in mock_run.call_args[0][0] if a.startswith("HOLON_STAGE_BUDGET_SECONDS=")]
            self.assertEqual(budget_args, ["HOLON_STAGE_BUDGET_SECONDS=1940"])
        mock_run.reset_mock()
        with patch.dict(os.environ, {}, clear=True):
            run_docker_container("executor", "holon/agent-pi", ["branch"], agent_id="pi")
            self.assertFalse(
                any(a.startswith("HOLON_STAGE_BUDGET_SECONDS=") for a in mock_run.call_args[0][0]),
            )

    def test_run_docker_container_requires_agent_id(self):
        with self.assertRaises(TypeError):
            run_docker_container("planner", "holon/agent-antigravity", ["branch"])  # type: ignore[call-arg]

    @patch("sandbox_executor.cli.resolve_target_repo", return_value=("git@github.com:test/repo.git", None))
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_run_docker_container_intent_creator_requires_intent_file(self, mock_which, mock_resolve_repo):
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            rc = run_docker_container("intent-creator", "holon/orchestrator", [], agent_id="antigravity")
            self.assertEqual(rc, 1)
            self.assertIn("Error: Intent file is mandatory for intent creation.", mock_stderr.getvalue())

    @patch("sandbox_executor.cli.run_docker_container", return_value=0)
    def test_main_subcommands(self, mock_run_container):
        test_args = [
            "holon",
            "plan",
            "intent_branch_name",
            "--agent",
            "antigravity-agent",
            "--model",
            "gemini-3.5-flash",
        ]
        with patch("sys.argv", test_args):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)
            mock_run_container.assert_called_once_with(
                "planner",
                "holon/agent-antigravity",
                ["intent_branch_name", "antigravity-agent", "gemini-3.5-flash"],
                agent_id="antigravity",
                token_reduce=False,
            )

        mock_run_container.reset_mock()
        test_intent_args = ["holon", "intent", "intents/test.json"]
        with patch("sys.argv", test_intent_args):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)
            mock_run_container.assert_called_once_with(
                "intent-creator",
                "holon/orchestrator",
                [],
                agent_id="antigravity",
                intent_file="intents/test.json",
            )

        mock_run_container.reset_mock()
        test_exec_args = [
            "holon",
            "execute",
            "plan_branch_name",
            "--agent",
            "antigravity-agent",
            "--model",
            "gemini-3.5-flash",
        ]
        with patch("sys.argv", test_exec_args):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)
            mock_run_container.assert_called_once_with(
                "executor",
                "holon/agent-antigravity",
                ["plan_branch_name", "antigravity-agent", "gemini-3.5-flash"],
                agent_id="antigravity",
                token_reduce=False,
            )

    @patch("sandbox_executor.cli.run_docker_container", return_value=0)
    def test_main_subcommands_token_reduce_flag(self, mock_run_container):
        test_args = [
            "holon",
            "plan",
            "intent_branch_name",
            "--token-reduce",
            "--agent",
            "antigravity-agent",
            "--model",
            "gemini-3.5-flash",
        ]
        with patch("sys.argv", test_args):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)
            mock_run_container.assert_called_once_with(
                "planner",
                "holon/agent-antigravity",
                ["intent_branch_name", "antigravity-agent", "gemini-3.5-flash"],
                agent_id="antigravity",
                token_reduce=True,
            )

    def test_console_script_entrypoint_registered(self):
        eps = [ep for ep in importlib.metadata.entry_points(group="console_scripts") if ep.name == "holon"]
        self.assertEqual(len(eps), 1)
        holon_ep = eps[0]
        self.assertEqual(holon_ep.value, "sandbox_executor.cli:main")
        loaded_main = holon_ep.load()
        self.assertIs(loaded_main, main)

    @patch("sandbox_executor.cli.run_docker_container", return_value=0)
    def test_console_script_entrypoint_dispatch(self, mock_run_container):
        holon_ep = next(
            (ep for ep in importlib.metadata.entry_points(group="console_scripts") if ep.name == "holon"),
            None,
        )
        self.assertIsNotNone(holon_ep, "Console script entrypoint 'holon' not found in distribution metadata")
        loaded_entrypoint = holon_ep.load()
        test_intent_args = ["holon", "intent", "intents/test.json"]
        with patch("sys.argv", test_intent_args):
            with self.assertRaises(SystemExit) as cm:
                loaded_entrypoint()
            self.assertEqual(cm.exception.code, 0)
        mock_run_container.assert_called_once_with(
            "intent-creator",
            "holon/orchestrator",
            [],
            agent_id="antigravity",
            intent_file="intents/test.json",
        )

    def test_main_help(self):
        with (
            patch("sys.argv", ["holon", "--help"]),
            patch("sys.stdout", new_callable=io.StringIO),
            self.assertRaises(SystemExit) as cm,
        ):
            main()
        self.assertEqual(cm.exception.code, 0)

    def test_main_version(self):
        for flag in ["-v", "--version"]:
            with self.subTest(flag=flag):
                with (
                    patch("sys.argv", ["holon", flag]),
                    patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
                    self.assertRaises(SystemExit) as cm,
                ):
                    main()
                self.assertEqual(cm.exception.code, 0)
                self.assertIn("holon 0.1.0", mock_stdout.getvalue())

    @patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError)
    def test_main_version_fallback(self, mock_version):
        with (
            patch("sys.argv", ["holon", "--version"]),
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
            self.assertRaises(SystemExit) as cm,
        ):
            main()
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("holon 0.1.0", mock_stdout.getvalue())

    @patch("subprocess.run", side_effect=FileNotFoundError("docker not found"))
    def test_run_docker_catches_oserror(self, mock_run):
        res = _run_docker("version")
        self.assertEqual(res.returncode, 127)
        self.assertIn("docker not found", res.stderr)


class TestRepoUrlResolution(unittest.TestCase):
    def test_normalize_repo_remote_parse_error_withholds_value(self):
        head = str(secrets.randbelow(900000) + 100000)
        value = f"{head}?{make_probe()}@nohost"
        with self.assertRaises(ValueError) as ctx:
            normalize_repo_remote(value)
        self.assertIn("Unable to parse the remote URL", str(ctx.exception))
        self.assertNotIn(head, str(ctx.exception))
        assert_no_fragment(str(ctx.exception), value)

    def test_normalize_repo_remote_ssh_scp(self):
        """Test SSH SCP format normalization."""
        host, path = normalize_repo_remote("git@github.com:org/repo.git")
        self.assertEqual(host, "github.com")
        self.assertEqual(path, "org/repo.git")

    def test_normalize_repo_remote_ssh_url(self):
        """Test SSH URL format normalization."""
        host, path = normalize_repo_remote("ssh://git@github.com/org/repo.git")
        self.assertEqual(host, "github.com")
        self.assertEqual(path, "org/repo.git")

    def test_normalize_repo_remote_https(self):
        """Test HTTPS format normalization."""
        host, path = normalize_repo_remote("https://github.com/org/repo.git")
        self.assertEqual(host, "github.com")
        self.assertEqual(path, "org/repo.git")

    def test_normalize_repo_remote_custom_host_nested_path(self):
        """Test custom host and nested path normalization."""
        host, path = normalize_repo_remote("git@gitlab.internal.corp:group/subgroup/project.git")
        self.assertEqual(host, "gitlab.internal.corp")
        self.assertEqual(path, "group/subgroup/project.git")

    def test_normalize_repo_remote_without_git_suffix(self):
        """Test that missing .git suffix is automatically appended."""
        host, path = normalize_repo_remote("https://github.com/org/repo")
        self.assertEqual(host, "github.com")
        self.assertEqual(path, "org/repo.git")

    def test_normalize_repo_remote_invalid(self):
        """Test invalid remote URL raises ValueError."""
        with self.assertRaises(ValueError):
            normalize_repo_remote("invalid-remote-url-without-colons")

    def test_resolve_target_repo_url_explicit_override(self):
        """Test that HOLON_REPO_URL takes absolute precedence."""
        with patch.dict(os.environ, {"HOLON_REPO_URL": "https://custom.override/repo.git"}, clear=True):
            self.assertEqual(resolve_target_repo_url(), "https://custom.override/repo.git")

    def test_resolve_target_repo_url_ssh_with_token(self):
        """Test SSH remote paired with detected GitHub token."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            subprocess.run(["git", "init", "-b", "main", tmp_dir], capture_output=True, check=True)
            subprocess.run(
                ["git", "-C", tmp_dir, "remote", "add", "origin", "git@github.com:myorg/myrepo.git"],
                capture_output=True,
                check=True,
            )
            with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_mock_token_123"}, clear=True):
                resolved = resolve_target_repo_url(repo_dir=tmp_dir)
                # Credential-free: the token reaches git through a runtime credential helper instead.
                self.assertEqual(resolved, "https://github.com/myorg/myrepo.git")

    def test_resolve_target_repo_url_ssh_without_token(self):
        """Test SSH remote without token returns raw remote URL for SSH agent forwarding."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            subprocess.run(["git", "init", "-b", "main", tmp_dir], capture_output=True, check=True)
            subprocess.run(
                ["git", "-C", tmp_dir, "remote", "add", "origin", "git@github.com:myorg/myrepo.git"],
                capture_output=True,
                check=True,
            )
            with patch.dict(os.environ, {}, clear=True), patch("shutil.which", return_value=None):
                resolved = resolve_target_repo_url(repo_dir=tmp_dir)
                self.assertEqual(resolved, "git@github.com:myorg/myrepo.git")

    def test_resolve_target_repo_url_https_with_token(self):
        """Test HTTPS remote paired with token."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            subprocess.run(["git", "init", "-b", "main", tmp_dir], capture_output=True, check=True)
            subprocess.run(
                ["git", "-C", tmp_dir, "remote", "add", "origin", "https://github.com/myorg/myrepo.git"],
                capture_output=True,
                check=True,
            )
            with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_mock_token_456"}, clear=True):
                resolved = resolve_target_repo_url(repo_dir=tmp_dir)
                self.assertEqual(resolved, "https://github.com/myorg/myrepo.git")

    def test_resolve_target_repo_url_worktree(self):
        """Test worktree inherits origin from main repository."""
        with tempfile.TemporaryDirectory() as base_dir:
            main_repo = os.path.join(base_dir, "main_repo")
            worktree_dir = os.path.join(base_dir, "worktree_repo")
            os.makedirs(main_repo, exist_ok=True)
            subprocess.run(["git", "init", "-b", "main", main_repo], capture_output=True, check=True)
            subprocess.run(["git", "-C", main_repo, "config", "user.name", "Tester"], capture_output=True, check=True)
            subprocess.run(
                ["git", "-C", main_repo, "config", "user.email", "test@test.org"],
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "-C", main_repo, "remote", "add", "origin", "git@github.com:worktree/repo.git"],
                capture_output=True,
                check=True,
            )
            dummy_file = os.path.join(main_repo, "file.txt")
            with open(dummy_file, "w") as f:
                f.write("test")
            subprocess.run(["git", "-C", main_repo, "add", "file.txt"], capture_output=True, check=True)
            subprocess.run(["git", "-C", main_repo, "commit", "-m", "init"], capture_output=True, check=True)
            subprocess.run(
                ["git", "-C", main_repo, "worktree", "add", "-b", "branch1", worktree_dir],
                capture_output=True,
                check=True,
            )

            with patch.dict(os.environ, {}, clear=True), patch("shutil.which", return_value=None):
                resolved = resolve_target_repo_url(repo_dir=worktree_dir)
                self.assertEqual(resolved, "git@github.com:worktree/repo.git")

    def test_resolve_target_repo_url_missing_origin(self):
        """Test that repository without origin remote raises RuntimeError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            subprocess.run(["git", "init", "-b", "main", tmp_dir], capture_output=True, check=True)
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(RuntimeError) as cm:
                    resolve_target_repo_url(repo_dir=tmp_dir)
                self.assertIn("Unable to resolve git remote 'origin'", str(cm.exception))

    def test_resolve_target_repo_url_non_git_dir(self):
        """Test that non-git directory raises RuntimeError."""
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as cm:
                resolve_target_repo_url(repo_dir=tmp_dir)
            self.assertIn("Unable to resolve git remote 'origin'", str(cm.exception))

    @patch("sandbox_executor.cli.get_agent_session_mounts", return_value=[])
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_sanitized_cmd_masks_tokenized_repo_url(self, mock_which, mock_run, mock_mounts):
        """Test that a tokenised HOLON_REPO_URL is stripped of its credential before it is forwarded or echoed."""
        env_files: list[dict[str, str]] = []
        mock_run.side_effect = lambda cmd, *a, **k: env_files.append(_read_env_file(cmd)) or MagicMock(returncode=0)
        sample = make_gh_probe()
        sample_url = f"https://x-access-token:{sample}@github.com/myorg/myrepo.git"
        with (
            patch.dict(os.environ, {"HOLON_REPO_URL": sample_url}, clear=True),
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
        ):
            code = run_docker_container(
                "planner",
                "holon/agent-antigravity",
                ["branch", "agent", "model"],
                agent_id="antigravity",
            )
            self.assertEqual(code, 0)
            output = mock_stdout.getvalue()
            self.assertIn("HOLON_REPO_URL=https://github.com/myorg/myrepo.git", output)
            self.assertNotIn(sample, output)
            self.assertNotIn(sample, " ".join(mock_run.call_args[0][0]))
            # With no other token source, the credential stripped from the URL becomes the forwarded token.
            # The override carried the credential, so the host-scoped helper travels with it.
            self.assertEqual(len(env_files), 1)
            self.assertEqual(set(env_files[0]), {"GITHUB_TOKEN", "GIT_CONFIG_VALUE_1"})
            self.assertEqual(env_files[0]["GITHUB_TOKEN"], sample)

    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_run_docker_container_fails_fast_on_missing_origin(self, mock_which):
        """Test that run_docker_container exits non-zero without calling Docker if origin is unresolvable."""
        with (
            tempfile.TemporaryDirectory() as empty_dir,
            patch.dict(os.environ, {}, clear=True),
            patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
        ):
            code = run_docker_container(
                "planner",
                "holon/agent-antigravity",
                ["branch", "agent", "model"],
                agent_id="antigravity",
                repo_dir=empty_dir,
            )
            self.assertEqual(code, 1)
            self.assertIn("Error: Unable to resolve git remote 'origin'", mock_stderr.getvalue())


class TestHolonFlowCLI(unittest.TestCase):
    """Hermetic unit tests for 'holon flow' CLI argument parsing, flags, and dispatching."""

    def test_flow_skip_review_flag(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-skip-rev"}, f)

            with patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline, patch("sys.exit") as mock_exit:
                mock_pipeline.return_value = {"status": "ok"}
                with patch("sys.argv", ["holon", "flow", intent_file, "--skip-review"]):
                    main()
                mock_pipeline.assert_called_once()
                ctx = mock_pipeline.call_args[1]["context"]
                self.assertTrue(ctx.skip_review)
                self.assertFalse(ctx.skip_calibrate)
                mock_exit.assert_called_with(0)

    def test_flow_skip_calibrate_flag(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-skip-cal"}, f)

            with patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline, patch("sys.exit") as mock_exit:
                mock_pipeline.return_value = {"status": "ok"}
                with patch("sys.argv", ["holon", "flow", intent_file, "--skip-calibrate"]):
                    main()
                mock_pipeline.assert_called_once()
                ctx = mock_pipeline.call_args[1]["context"]
                self.assertFalse(ctx.skip_review)
                self.assertTrue(ctx.skip_calibrate)
                self.assertFalse(ctx.auto_calibrate)
                mock_exit.assert_called_with(0)

    def test_flow_max_review_iterations_flag(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-max-iter"}, f)

            with patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline, patch("sys.exit") as mock_exit:
                mock_pipeline.return_value = {"status": "ok"}
                with patch("sys.argv", ["holon", "flow", intent_file, "--max-review-iterations", "5"]):
                    main()
                mock_pipeline.assert_called_once()
                ctx = mock_pipeline.call_args[1]["context"]
                self.assertEqual(ctx.max_review_iterations, 5)
                mock_exit.assert_called_with(0)

    def test_flow_skip_push_rejected_outside_dry_run_for_any_resume(self):
        """C10-N2: --skip-push without --dry-run is rejected before the pre-flight probe or the pipeline runs."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-skip-push", "goal": "g"}, f)
            checkpoint_file = os.path.join(tmp_dir, "checkpoint.json")
            ckpt_ctx = FlowContext(repo_dir=tmp_dir, intent_data={"slug": "cp"}, skip_push=True)
            with open(checkpoint_file, "w", encoding="utf-8") as f:
                json.dump({"context": ckpt_ctx.to_dict(), "current_stage": "calibrate"}, f)

            argvs = [
                ["holon", "flow", intent_file, "--skip-push"],
                ["holon", "flow", intent_file, "--skip-push", "--from-stage", "review"],
                ["holon", "flow", intent_file, "--skip-push", "--from-stage", "calibrate"],
                # A checkpoint that recorded --skip-push is rejected on a non-dry resume too.
                ["holon", "flow", "--checkpoint", checkpoint_file],
            ]
            for argv in argvs:
                with (
                    self.subTest(argv=argv[2:]),
                    patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline,
                    patch("sandbox_executor.cli.preflight_gate") as mock_preflight,
                    patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
                    self.assertRaises(SystemExit) as cm,
                    patch("sys.argv", argv),
                ):
                    main()
                self.assertEqual(cm.exception.code, 1)
                self.assertIn(SKIP_PUSH_DISPATCH_ERROR, mock_stderr.getvalue())
                mock_pipeline.assert_not_called()
                mock_preflight.assert_not_called()

    def test_flow_skip_push_accepted_with_dry_run_resume(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-skip-push-dry", "goal": "g"}, f)
            with patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline, patch("sys.exit") as mock_exit:
                mock_pipeline.return_value = {"status": "ok"}
                argv = ["holon", "flow", intent_file, "--dry-run", "--skip-push", "--from-stage", "calibrate"]
                with patch("sys.argv", argv):
                    main()
                mock_pipeline.assert_called_once()
                self.assertTrue(mock_pipeline.call_args[1]["context"].skip_push)
                mock_exit.assert_called_with(0)

    def test_flow_invalid_max_review_iterations(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-invalid-iter"}, f)

            for invalid_val in ["0", "-1"]:
                with (
                    patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
                    self.assertRaises(SystemExit) as cm,
                    patch("sys.argv", ["holon", "flow", intent_file, "--max-review-iterations", invalid_val]),
                ):
                    main()
                self.assertEqual(cm.exception.code, 1)
                self.assertIn("--max-review-iterations must be at least 1", mock_stderr.getvalue())

    def test_flow_token_reduce_and_mitm_web_flags(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-mitm"}, f)

            with patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline, patch("sys.exit") as mock_exit:
                mock_pipeline.return_value = {"status": "ok"}
                with patch("sys.argv", ["holon", "flow", intent_file, "--token-reduce", "--mitm-web"]):
                    main()
                mock_pipeline.assert_called_once()
                ctx = mock_pipeline.call_args[1]["context"]
                self.assertTrue(ctx.token_reduce)
                self.assertTrue(ctx.mitm_web)
                mock_exit.assert_called_with(0)

            # Test coupling: --mitm-web alone automatically enables token_reduce
            with patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline, patch("sys.exit") as mock_exit:
                mock_pipeline.return_value = {"status": "ok"}
                with patch("sys.argv", ["holon", "flow", intent_file, "--mitm-web"]):
                    main()
                mock_pipeline.assert_called_once()
                ctx = mock_pipeline.call_args[1]["context"]
                self.assertTrue(ctx.token_reduce)
                self.assertTrue(ctx.mitm_web)
                mock_exit.assert_called_with(0)

    def test_flow_combined_flags(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-combined"}, f)

            with patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline, patch("sys.exit") as mock_exit:
                mock_pipeline.return_value = {"status": "ok"}
                args = [
                    "holon",
                    "flow",
                    intent_file,
                    "--dry-run",
                    "--skip-review",
                    "--skip-calibrate",
                    "--max-review-iterations",
                    "4",
                    "--token-reduce",
                    "--mitm-web",
                ]
                with patch("sys.argv", args):
                    main()
                mock_pipeline.assert_called_once()
                ctx = mock_pipeline.call_args[1]["context"]
                self.assertTrue(ctx.dry_run)
                self.assertTrue(ctx.skip_review)
                self.assertTrue(ctx.skip_calibrate)
                self.assertFalse(ctx.auto_calibrate)
                self.assertEqual(ctx.max_review_iterations, 4)
                self.assertTrue(ctx.token_reduce)
                self.assertTrue(ctx.mitm_web)
                mock_exit.assert_called_with(0)

    def test_flow_json_output_with_skipped_stages(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-json"}, f)

            def fake_run(context, **kwargs):
                from sandbox_executor.flow import FlowStage, StageResult, StageStatus

                context.stage_results["review"] = StageResult(
                    stage=FlowStage.REVIEW,
                    status=StageStatus.SKIPPED,
                    payload={"skipped_by_flag": True},
                )
                context.stage_results["calibrate"] = StageResult(
                    stage=FlowStage.CALIBRATE,
                    status=StageStatus.SKIPPED,
                    payload={"skipped_by_flag": True},
                )
                return context.to_dict()

            with (
                patch("sandbox_executor.cli.run_flow_pipeline", side_effect=fake_run),
                patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
                patch("sys.exit") as mock_exit,
            ):
                with patch("sys.argv", ["holon", "flow", intent_file, "--skip-review", "--skip-calibrate", "--json"]):
                    main()
                mock_exit.assert_called_with(0)
                output = mock_stdout.getvalue()
                parsed = json.loads(output)
                self.assertEqual(parsed["stage_results"]["review"]["status"], "skipped")
                self.assertEqual(parsed["stage_results"]["calibrate"]["status"], "skipped")
                self.assertTrue(parsed["skip_review"])
                self.assertTrue(parsed["skip_calibrate"])

    def test_flow_summary_lists_child_prs_and_pending_parent_step(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-decomposed"}, f)

            def fake_run(context, **kwargs):
                from sandbox_executor.flow import FlowStage, StageResult, StageStatus

                context.stage_results["execute"] = StageResult(
                    stage=FlowStage.EXECUTE,
                    status=StageStatus.SUCCESS,
                    payload={
                        "decomposed": True,
                        "parent_branch": "I-1-parent/_",
                        "parent_target_branch": "main",
                        "children": [
                            {"slug": "add-the-parser", "pr_url": "https://example.test/pull/7", "status": "success"},
                            {
                                "slug": "add-the-cli-flag",
                                "pr_url": None,
                                "execution_branch": "I-2-add-the-cli-flag/P-2/E-2/_",
                                "status": "skipped",
                            },
                        ],
                    },
                )
                return context.to_dict()

            with (
                patch("sandbox_executor.cli.run_flow_pipeline", side_effect=fake_run),
                patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
                patch("sys.exit") as mock_exit,
            ):
                with patch("sys.argv", ["holon", "flow", intent_file]):
                    main()
                mock_exit.assert_called_with(0)
                output = mock_stdout.getvalue()
                self.assertIn(
                    "\nDecomposed into 2 sub-intent(s); merge each child PR into the parent intent branch:\n"
                    "  1. add-the-parser: https://example.test/pull/7 (success)\n",
                    output,
                )
                self.assertIn("2. add-the-cli-flag: I-2-add-the-cli-flag/P-2/E-2/_ (skipped)", output)
                self.assertIn(
                    "Pending (not yet automated): after the child PRs merge, raise a PR from 'I-1-parent/_' to "
                    "'main'.\n\n",
                    output,
                )

    def test_flow_exit_code_on_failure_vs_success(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            intent_file = os.path.join(tmp_dir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"slug": "test-exit-code"}, f)

            def failing_run(context, **kwargs):
                from sandbox_executor.flow import FlowStage, StageResult, StageStatus

                context.stage_results["intent"] = StageResult(
                    stage=FlowStage.INTENT,
                    status=StageStatus.FAILED,
                    error="Stage failed intentionally",
                )
                return context.to_dict()

            with (
                patch("sandbox_executor.cli.run_flow_pipeline", side_effect=failing_run),
                patch("sys.exit") as mock_exit,
            ):
                with patch("sys.argv", ["holon", "flow", intent_file]):
                    main()
                mock_exit.assert_called_with(1)

    def test_flow_checkpoint_resumption_flag_overrides(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            from sandbox_executor.flow import FlowContext

            # Create existing context with default flags
            orig_ctx = FlowContext(
                repo_dir=tmp_dir,
                intent_data={"slug": "test-resume"},
                skip_review=False,
                skip_calibrate=False,
                max_review_iterations=5,
            )
            checkpoint_file = os.path.join(tmp_dir, "checkpoint.json")
            with open(checkpoint_file, "w", encoding="utf-8") as f:
                json.dump({"context": orig_ctx.to_dict(), "current_stage": "execute"}, f)

            with patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline, patch("sys.exit") as mock_exit:
                mock_pipeline.return_value = {"status": "ok"}
                args = [
                    "holon",
                    "flow",
                    "--checkpoint",
                    checkpoint_file,
                    "--skip-review",
                    "--skip-calibrate",
                    "--max-review-iterations",
                    "10",
                    "--mitm-web",
                ]
                with patch("sys.argv", args):
                    main()
                mock_pipeline.assert_called_once()
                ctx = mock_pipeline.call_args[1]["context"]
                self.assertTrue(ctx.skip_review)
                self.assertTrue(ctx.skip_calibrate)
                self.assertFalse(ctx.auto_calibrate)
                self.assertEqual(ctx.max_review_iterations, 10)
                self.assertTrue(ctx.token_reduce)
                self.assertTrue(ctx.mitm_web)
                mock_exit.assert_called_with(0)

    def test_review_loop_cli_subcommand_registration(self):
        with patch("sys.argv", ["holon", "--help"]), patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)
            help_output = mock_stdout.getvalue()
            self.assertIn("review-loop", help_output)

    def test_review_loop_cli_argument_parsing(self):
        with (
            patch("sandbox_executor.flow.run_review_loop_cli", return_value=0) as mock_runner,
            patch("sys.exit") as mock_exit,
        ):
            args = [
                "holon",
                "review-loop",
                "my-exec-branch",
                "--max-iterations",
                "15",
                "--ensemble-size",
                "2",
                "--dry-run",
            ]
            with patch("sys.argv", args):
                main()
            mock_runner.assert_called_once()
            call_args = mock_runner.call_args[0][0]
            self.assertEqual(call_args.command, "review-loop")
            self.assertEqual(call_args.target, "my-exec-branch")
            self.assertEqual(call_args.max_iterations, 15)
            self.assertEqual(call_args.ensemble_size, 2)
            self.assertTrue(call_args.dry_run)
            mock_exit.assert_called_with(0)

    def test_review_loop_cli_alias_invocations(self):
        for alias in ["review", "pr-review-loop"]:
            with (
                patch("sandbox_executor.flow.run_review_loop_cli", return_value=0) as mock_runner,
                patch("sys.exit") as mock_exit,
            ):
                with patch("sys.argv", ["holon", alias, "branch-123", "--local"]):
                    main()
                mock_runner.assert_called_once()
                call_args = mock_runner.call_args[0][0]
                self.assertEqual(call_args.target, "branch-123")
                mock_exit.assert_called_with(0)

    def test_review_loop_cli_validation(self):
        for invalid_arg in [["--max-iterations", "0"], ["--ensemble-size", "0"]]:
            with (
                patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
                patch("sys.exit") as mock_exit,
                patch("sandbox_executor.flow.run_review_loop_cli") as mock_runner,
            ):
                with patch("sys.argv", ["holon", "review-loop", "target-branch", *invalid_arg]):
                    main()
                mock_exit.assert_called_with(1)
                mock_runner.assert_not_called()
                self.assertIn("Error:", mock_stderr.getvalue())

    def test_review_loop_local_exits_one_when_not_approved(self):
        fake_result = {
            "target": "my-branch",
            "consensus": {"approved": False, "status": "post_failed", "iterations": 1},
            "error": "Posting the review consensus for 'my-branch' failed: HTTP 502",
        }
        with (
            patch("sandbox_executor.flow.run_review_loop", return_value=fake_result),
            patch("sandbox_executor.cli.detect_holon_coherence", return_value=False),
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
            patch("sys.exit") as mock_exit,
        ):
            with patch("sys.argv", ["holon", "review-loop", "my-branch", "--local"]):
                main()
            mock_exit.assert_called_with(1)
            self.assertIn("POST_FAILED", mock_stdout.getvalue())
            self.assertIn("HTTP 502", mock_stdout.getvalue())

    def test_review_loop_cli_json_output(self):
        fake_result = {
            "target": "my-branch",
            "consensus": {"approved": True, "status": "approved", "iterations": 1},
            "ensemble_verdict": "APPROVED",
            "halted_for_human": True,
            "bean_0034_enforced": True,
            "human_approval_command": "holon review approve my-branch",
        }
        with (
            patch("sandbox_executor.flow.run_review_loop", return_value=fake_result),
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
            patch("sys.exit") as mock_exit,
        ):
            with patch("sys.argv", ["holon", "review-loop", "my-branch", "--json"]):
                main()
            mock_exit.assert_called_with(0)
            output = mock_stdout.getvalue()
            parsed = json.loads(output)
            self.assertEqual(parsed["target"], "my-branch")
            self.assertTrue(parsed["consensus"]["approved"])


_SHARED_SERVER_CONFIG = {
    "providers": {
        "a": {
            "baseUrl": "http://127.0.0.1:8000/v1",
            "models": [{"id": "big", "contextWindow": 65536, "maxTokens": 16384}],
        },
        "b": {
            "baseUrl": "http://127.0.0.1:8000/anthropic",
            "models": [{"id": "small", "contextWindow": 8192, "maxTokens": 2048}],
        },
        "c": {
            "baseUrl": "http://127.0.0.1:9000/v1",
            "models": [{"id": "other", "contextWindow": 65536, "maxTokens": 8192}],
        },
    }
}


def _strict_utf8_stream() -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict", write_through=True)


class TestDoctor(unittest.TestCase):
    def run_doctor(self, env, fetch, host_config=None):
        out = io.StringIO()
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(local_llm, "host_models_json", return_value=host_config),
        ):
            code = cli.report_budgets_and_envelope(fetch=fetch, out=out)
        return code, out.getvalue()

    def test_doctor_prints_envelope_and_cache_state(self):
        host_config = _config({"id": "qwen3", "contextWindow": 65536, "maxTokens": 8000})
        code, output = self.run_doctor({"HOLON_LOCAL_LLM": "1"}, _fetcher(HEALTH_WARM), host_config)
        self.assertEqual(code, 0)
        self.assertIn("model=qwen3 context=65536 max_tokens=8000 source=host", output)
        self.assertIn("reasoning=off source=default", output)
        self.assertIn("prefill_cache=warm", output)
        self.assertIn("WARNING: model=qwen3 max_tokens=8000 exceeds", output)
        self.assertIn("budgets turn=300s stall=90s", output)

    def test_doctor_without_mismatch_has_no_warning(self):
        env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "http://localhost:11434/v1",
            "HOLON_LOCAL_MODELS": "llama",
            "HOLON_LOCAL_CONTEXT_WINDOW": "16384",
            "HOLON_REASONING_MODE": "high",
        }
        code, output = self.run_doctor(env, _fetcher(HEALTH_COLD))
        self.assertEqual(code, 0)
        self.assertIn("model=llama context=16384 max_tokens=4096 source=synthesised", output)
        self.assertIn("reasoning=high source=env", output)
        self.assertIn("prefill_cache=cold", output)
        self.assertNotIn("WARNING", output)

    def test_doctor_warns_when_context_window_exceeds_max_prompt_tokens(self):
        host_config = _config({"id": "qwen3", "contextWindow": 65536, "maxTokens": 8000})
        code, output = self.run_doctor({"HOLON_LOCAL_LLM": "1"}, _fetcher(HEALTH_SMALL_PROMPT), host_config)
        self.assertEqual(code, 0)
        self.assertIn("WARNING: model=qwen3 context=65536 exceeds the server's max_prompt_tokens=32768", output)
        self.assertNotIn("max_output_tokens=16384", output)

    # C12-N1: two providers on one server share one probe and one line, and each model is only compared with the
    # limits of the server that serves it.
    @staticmethod
    def two_server_fetch(urls):
        def fetch(url, timeout):
            urls.append(url)
            limits = (4096, 32768) if ":9000" in url else (16384, 65536)
            return json.dumps(
                {
                    "prefill_cache": {"enabled": True, "entries": 0},
                    "effective_defaults": {"max_output_tokens": limits[0], "max_prompt_tokens": limits[1]},
                }
            ).encode()

        return fetch

    def test_doctor_probes_each_server_once_and_scopes_warnings(self):
        urls = []
        code, output = self.run_doctor(
            {"HOLON_LOCAL_LLM": "1"}, self.two_server_fetch(urls), copy.deepcopy(_SHARED_SERVER_CONFIG)
        )
        self.assertEqual(code, 0)
        self.assertEqual(urls, ["http://127.0.0.1:8000/health", "http://127.0.0.1:9000/health"])
        gateway_lines = [line for line in output.splitlines() if line.startswith("gateway=")]
        self.assertEqual(
            gateway_lines,
            [
                "gateway=http://127.0.0.1:8000/health prefill_cache=cold",
                "gateway=http://127.0.0.1:9000/health prefill_cache=cold",
            ],
        )
        warnings = [line for line in output.splitlines() if line.startswith("WARNING")]
        self.assertEqual(
            warnings,
            [
                "WARNING: model=other max_tokens=8192 exceeds the server's effective_defaults.max_output_tokens=4096",
                "WARNING: model=other context=65536 exceeds the server's max_prompt_tokens=32768",
            ],
        )

    def test_unified_doctor_json_dedupes_servers_and_scopes_warnings(self):
        code, output = self.run_unified_doctor(
            {"HOLON_LOCAL_LLM": "1"},
            self.two_server_fetch([]),
            copy.deepcopy(_SHARED_SERVER_CONFIG),
            as_json=True,
        )
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(set(payload), {"agent", "model", "preflight", "environment"})
        self.assertEqual(set(payload["environment"]), {"ok", "lines", "warnings"})
        lines = payload["environment"]["lines"]
        self.assertEqual(sum(line.startswith("gateway=http://127.0.0.1:8000/") for line in lines), 1)
        self.assertEqual(sum(line.startswith("gateway=http://127.0.0.1:9000/") for line in lines), 1)
        warnings = [line for line in lines if line.startswith("WARNING")]
        self.assertEqual(len(warnings), 2)
        self.assertTrue(all("model=other " in line for line in warnings))
        self.assertEqual(payload["environment"]["warnings"], warnings)

    def test_doctor_scopes_synthesised_provider_to_its_base_url(self):
        env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "http://localhost:9000/v1",
            "HOLON_LOCAL_MODELS": "llama",
            "HOLON_LOCAL_CONTEXT_WINDOW": "65536",
        }
        urls = []
        code, output = self.run_doctor(env, self.two_server_fetch(urls))
        self.assertEqual(code, 0)
        self.assertEqual(urls, ["http://localhost:9000/health"])
        self.assertIn("WARNING: model=llama context=65536 exceeds the server's max_prompt_tokens=32768", output)

    def test_doctor_reports_config_errors(self):
        code, output = self.run_doctor({"HOLON_LOCAL_LLM": "1"}, _fetcher(HEALTH_OFF), _config({"id": "m"}))
        self.assertEqual(code, 1)
        self.assertIn("HOLON_LOCAL_CONTEXT_WINDOW", output)

    def test_doctor_with_local_mode_disabled(self):
        code, output = self.run_doctor({}, _fetcher(HEALTH_OFF))
        self.assertEqual(code, 0)
        self.assertIn("local_llm=disabled", output)

    def run_unified_doctor(self, env, fetch, host_config=None, as_json=False, status=None):
        """Run the single ``holon doctor`` command: runner/model resolution and pre-flight, then the environment."""
        from sandbox_executor.preflight import PreflightResult, PreflightStatus

        status = status or PreflightStatus.OK
        probe = PreflightResult(
            status=status,
            agent="antigravity-agent",
            model="m",
            image="holon/agent-antigravity",
            exit_code=0 if status is PreflightStatus.OK else 1,
            output_excerpt="",
            hint="",
        )
        args = argparse.Namespace(agent=None, model=None, json=as_json, timeout=None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(local_llm, "host_models_json", return_value=host_config),
            patch("sandbox_executor.cli.run_preflight_probe", return_value=probe),
            # A strict UTF-8 byte stream, like a terminal: text a StringIO accepts but a terminal cannot fails here.
            patch("sys.stdout", new_callable=_strict_utf8_stream) as mock_stdout,
        ):
            code = cli.run_doctor(args, fetch=fetch)
        return code, mock_stdout.buffer.getvalue().decode("utf-8")

    def test_unified_doctor_reports_resolution_probe_and_envelope(self):
        host_config = _config({"id": "qwen3", "contextWindow": 65536, "maxTokens": 8000})
        code, output = self.run_unified_doctor({"HOLON_LOCAL_LLM": "1"}, _fetcher(HEALTH_WARM), host_config)
        self.assertEqual(code, 0)
        self.assertIn("Agent runner", output)
        self.assertIn("Model", output)
        self.assertIn("budgets turn=300s stall=90s", output)
        self.assertIn("model=qwen3 context=65536 max_tokens=8000 source=host", output)
        self.assertIn("prefill_cache=warm", output)

    def test_unified_doctor_json_carries_environment_section(self):
        code, output = self.run_unified_doctor({}, _fetcher(HEALTH_OFF), as_json=True)
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["preflight"]["status"], "ok")
        self.assertTrue(payload["environment"]["ok"])
        self.assertTrue(any(line.startswith("budgets turn=") for line in payload["environment"]["lines"]))
        self.assertIn(
            "local_llm=disabled (set HOLON_LOCAL_LLM=1 to inspect the model envelope)", payload["environment"]["lines"]
        )

    def test_unified_doctor_fails_on_envelope_config_error_even_when_probe_passes(self):
        code, output = self.run_unified_doctor({"HOLON_LOCAL_LLM": "1"}, _fetcher(HEALTH_OFF), _config({"id": "m"}))
        self.assertEqual(code, 1)
        self.assertIn("HOLON_LOCAL_CONTEXT_WINDOW", output)

    def test_doctor_never_prints_base_url_userinfo(self):
        # C8-N1 / C9-N1: a credential embedded in HOLON_LOCAL_BASE_URL must reach neither the text nor the --json
        # report, including passwords with an unencoded "/", "?" or "#". Built at runtime, not literals.
        # C10-N1 adds hosts that do not parse (bad port, non-ASCII name), the case the "invalid port" warning is for.
        # #96 message rule: only a host validated after the userinfo is removed is shown; an unparseable or ambiguous
        # value names the variable only.
        sample = "pw-" + "z" * 8
        head = "hd-" + "q" * 5
        name = "some" + "one"
        hosts = ("gpu.example.com:8000/v1", "gpu.example.com:abc/v1", "h\u00f4st.example/v1")
        withheld = ("host withheld", "is not a valid URL")

        def assert_shown(text, host, sep):
            # C11-N1: a "/", "?" or "#" before the last "@" leaves no parseable authority, so the host is withheld.
            if host == "gpu.example.com:8000/v1" and sep in ("", "@"):
                self.assertIn("(host gpu.example.com:8000)", text)
            else:
                self.assertTrue(any(marker in text for marker in withheld))
            self.assertNotIn(f"***@{host}", text)

        for sep, host in ((sep, host) for sep in ("", "/", "?", "#", "@") for host in hosts):
            secret = f"{head}{sep}{sample}" if sep else sample
            env = {
                "HOLON_LOCAL_LLM": "1",
                "HOLON_LOCAL_BASE_URL": f"http://{name}:{secret}@{host}?key={sample}",
                "HOLON_LOCAL_MODELS": "m",
            }
            with self.subTest(sep=sep, host=host):
                code, output = self.run_doctor(env, _fetcher(HEALTH_OFF))
                self.assertEqual(code, 1)
                assert_shown(output, host, sep)
                for fragment in (sample, head, name):
                    self.assertNotIn(fragment, output)
            for as_json in (False, True):
                with self.subTest(sep=sep, host=host, as_json=as_json):
                    code, output = self.run_unified_doctor(env, _fetcher(HEALTH_OFF), as_json=as_json)
                    self.assertEqual(code, 1)
                    for fragment in (sample, head, name):
                        self.assertNotIn(fragment, output)
                    if as_json:
                        assert_shown("\n".join(json.loads(output)["environment"]["lines"]), host, sep)

    def test_unified_doctor_rejects_non_utf8_model_ids(self):
        # C23-I1 repro: a non-UTF-8 byte in HOLON_LOCAL_MODELS (surrogateescape) used to crash the text report.
        env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "http://127.0.0.1:1/v1",
            "HOLON_LOCAL_CONTEXT_WINDOW": "32768",
            "HOLON_LOCAL_MODELS": "m" + os.fsdecode(b"\xff"),
        }
        for as_json in (False, True):
            with self.subTest(as_json=as_json):
                code, output = self.run_unified_doctor(env, _fetcher(HEALTH_OFF), as_json=as_json)
                self.assertEqual(code, 1)
                self.assertIn("Error: HOLON_LOCAL_MODELS is not valid UTF-8 text", output)

    def test_unified_doctor_text_report_cannot_fail_to_encode(self):
        # C23-I1 output side: whatever text reaches the report is escaped, never a UnicodeEncodeError.
        def report(fetch=None, out=None):
            print("model=bad" + chr(0xD800) + "id", file=out)
            return 0

        with patch.object(cli, "report_budgets_and_envelope", side_effect=report):
            code, output = self.run_unified_doctor({}, _fetcher(HEALTH_OFF))
        self.assertEqual(code, 0)
        self.assertIn("model=bad\\ud800id", output)

    def test_print_encodable_leaves_encodable_text_unchanged(self):
        stream = _strict_utf8_stream()
        cli.print_encodable("h\u00f4st ok", file=stream)
        self.assertEqual(stream.buffer.getvalue().decode("utf-8"), "h\u00f4st ok\n")
        ascii_stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="strict", write_through=True)
        cli.print_encodable("h\u00f4st", file=ascii_stream)
        self.assertEqual(ascii_stream.buffer.getvalue().decode("ascii"), "h\\xf4st\n")

    def test_unified_doctor_fails_when_probe_fails(self):
        from sandbox_executor.preflight import PreflightStatus

        code, output = self.run_unified_doctor({}, _fetcher(HEALTH_OFF), status=PreflightStatus.EXPIRED_KEY)
        self.assertEqual(code, 1)
        self.assertIn("budgets turn=", output)


class TestSharedRoleDispatch(unittest.TestCase):
    """'holon intent|plan|execute' and 'holon flow' share one dispatch path (dispatch_role_agent)."""

    def test_normalise_agent_id_and_image_resolution(self):
        from sandbox_executor.agent_defaults import UnmappedAgentRunnerError
        from sandbox_executor.cli import normalise_agent_id, resolve_role_image

        self.assertEqual(normalise_agent_id("claude-agent"), "claude")
        self.assertEqual(normalise_agent_id("agent-codex"), "codex")
        self.assertEqual(resolve_role_image("planner", "claude"), "holon/agent-claude")
        self.assertEqual(resolve_role_image("executor", "claude-agent"), "holon/agent-claude")
        self.assertEqual(resolve_role_image("intent-creator", "claude"), "holon/orchestrator")
        # Image selection follows agent_defaults (#93): an unmapped runner fails loudly instead of guessing an image.
        with self.assertRaises(UnmappedAgentRunnerError):
            resolve_role_image("executor", "newrunner")
        self.assertEqual(resolve_role_image("intent-creator", "newrunner"), "holon/orchestrator")

    @patch("sandbox_executor.cli.dispatch_role_agent", return_value=0)
    def test_per_stage_commands_route_through_dispatch_role_agent(self, mock_dispatch):
        cases = [
            (["holon", "intent", "intents/test.json"], ("intent-creator", None, None, [])),
            (
                ["holon", "plan", "I-1-x/_", "--agent", "claude-agent", "--model", "claude-opus-5-5"],
                ("planner", "claude-agent", "claude-opus-5-5", ["I-1-x/_", "claude-agent", "claude-opus-5-5"]),
            ),
            (
                ["holon", "execute", "I-1-x/P-1/_", "--agent", "claude-agent", "--model", "claude-opus-5-5"],
                ("executor", "claude-agent", "claude-opus-5-5", ["I-1-x/P-1/_", "claude-agent", "claude-opus-5-5"]),
            ),
        ]
        for argv, expected in cases:
            mock_dispatch.reset_mock()
            with patch("sys.argv", argv), self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)
            mock_dispatch.assert_called_once()
            self.assertEqual(mock_dispatch.call_args.args, expected)

    @patch("sandbox_executor.cli.dispatch_role_agent")
    def test_execute_of_a_refused_over_budget_plan_exits_non_zero_with_a_message(self, mock_dispatch):
        from sandbox_executor.entrypoint.executor import OVER_BUDGET_EXIT_CODE

        argv = ["holon", "execute", "I-1-x/P-1/_", "--agent", "claude-agent", "--model", "claude-opus-5-5"]
        message = (
            f"Error: plan I-1-x/P-1/_ is over its entropy budget and was not executed (exit code "
            f"{OVER_BUDGET_EXIT_CODE}); its execution branch records the over_budget row. Re-plan it within the "
            "budget, or add an explicit sub-intent list."
        )
        for code in (OVER_BUDGET_EXIT_CODE, 1, 0):
            mock_dispatch.return_value = code
            stderr = io.StringIO()
            with patch("sys.argv", argv), patch("sys.stderr", stderr), self.assertRaises(SystemExit) as cm:
                main()
            # The container's exit status passes through, so 'holon execute ... && holon review ...' stops here.
            self.assertEqual(cm.exception.code, code)
            if code == OVER_BUDGET_EXIT_CODE:
                self.assertIn(message, stderr.getvalue().splitlines())
            else:
                self.assertNotIn("over its entropy budget", stderr.getvalue(), code)

    @patch("sandbox_executor.cli.run_docker_container", return_value=0)
    def test_dispatch_role_agent_uses_runner_image(self, mock_run_container):
        from sandbox_executor.cli import dispatch_role_agent

        rc = dispatch_role_agent(
            "executor",
            "claude-agent",
            "claude-opus-5-5",
            ["I-1-x/P-1/_", "claude-agent", "claude-opus-5-5"],
            repo_dir="/repo",
            token_reduce=True,
            mitm_web=True,
        )
        self.assertEqual(rc, 0)
        mock_run_container.assert_called_once_with(
            "executor",
            "holon/agent-claude",
            ["I-1-x/P-1/_", "claude-agent", "claude-opus-5-5"],
            agent_id="claude",
            token_reduce=True,
            mitm_web=True,
            repo_dir="/repo",
        )


# --------------------------------------------------------------------------------------
# Credential leaks: the echoed docker command, the docker argv and the env-file lifecycle
# --------------------------------------------------------------------------------------

_BRANCH = "I-1792762862-x/_"


@pytest.fixture
def leak_env(tmp_path, monkeypatch):
    """A clean environment with a runtime-built fake token, a dashboard password and an agent key."""
    probes = {
        "GITHUB_TOKEN": make_gh_probe(),
        "HOLON_MITM_WEB_PASSWORD": make_probe("mitmweb"),
        "HOLON_AGENT_KEY": make_probe("agentkey"),
    }
    for key in list(os.environ):
        if key.startswith(("HOLON_", "GIT_")) or key in ("GITHUB_TOKEN", "GH_TOKEN"):
            monkeypatch.delenv(key, raising=False)
    for key, value in probes.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:myorg/myrepo.git"], check=True)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(cli, "get_agent_session_mounts", lambda agent_id: [])
    monkeypatch.setattr(cli, "get_ssh_auth_mounts", lambda: ([], {}))
    # A proxy URL carrying its own credential, as an external HOLON_PROXY_URL can.
    proxy_sample = make_probe("proxypw")
    monkeypatch.setattr(
        cli,
        "get_token_reduction_mounts_and_envs",
        lambda **kwargs: ([], {"HTTPS_PROXY": f"http://proxyuser:{proxy_sample}@proxy.internal:3128"}),
    )
    probes["HTTPS_PROXY"] = proxy_sample
    return {"probes": probes, "repo": str(repo)}


class _RecordingDocker:
    """Delegates git to the real subprocess.run and records each docker run with its env-file state."""

    def __init__(self, outcome=None):
        self.outcome = outcome if outcome is not None else MagicMock(returncode=0)
        self.argv: list[str] = []
        self.env_file: str | None = None
        self.env_file_mode: int | None = None
        self.env_dir_mode: int | None = None
        self.env_file_contents: dict[str, str] = {}
        self._real_run = subprocess.run

    @property
    def argv_env(self) -> dict[str, str]:
        """The variables passed with `-e KEY=VALUE` on the docker command line."""
        pairs = zip(self.argv, self.argv[1:], strict=False)
        return dict(item.split("=", 1) for flag, item in pairs if flag == "-e" and "=" in item)

    @property
    def container_env(self) -> dict[str, str]:
        """Everything the container receives: the `-e` variables plus the env-file contents."""
        return {**self.argv_env, **self.env_file_contents}

    def __call__(self, cmd, *args, **kwargs):
        if cmd[0] != "docker":
            return self._real_run(cmd, *args, **kwargs)
        self.argv = list(cmd)
        if "--env-file" in cmd:
            self.env_file = cmd[cmd.index("--env-file") + 1]
            self.env_file_mode = stat.S_IMODE(os.stat(self.env_file).st_mode)
            self.env_dir_mode = stat.S_IMODE(os.stat(os.path.dirname(self.env_file)).st_mode)
            self.env_file_contents = _read_env_file(cmd)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _run_planner(repo_dir: str) -> int:
    return run_docker_container(
        "planner", "holon/agent-claude", [_BRANCH, "claude-agent", "opus"], agent_id="claude", repo_dir=repo_dir
    )


def test_docker_echo_and_argv_carry_no_secret_fragment(leak_env, monkeypatch, capsys):
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    echo = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("Executing: docker run"))
    argv_text = " ".join(docker.argv)
    for value in leak_env["probes"].values():
        assert_no_fragment(echo, value)  # goal 4a: the printed docker run command
        assert_no_fragment(argv_text, value)  # goal 4c: the docker argv list
    # The repository, role and branch stay readable.
    for readable in ("HOLON_REPO_URL=https://github.com/myorg/myrepo.git", "HOLON_ROLE=planner", _BRANCH):
        assert readable in echo
        assert readable in docker.argv
    assert "--env-file" in echo


def test_secrets_travel_in_an_owner_only_env_file_that_is_removed(leak_env, monkeypatch):
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    probes = leak_env["probes"]
    assert docker.env_file_mode == 0o600
    assert docker.env_dir_mode == 0o700
    assert docker.env_file_contents["GITHUB_TOKEN"] == probes["GITHUB_TOKEN"]
    assert docker.env_file_contents["HOLON_MITM_WEB_PASSWORD"] == probes["HOLON_MITM_WEB_PASSWORD"]
    assert docker.env_file_contents["HOLON_AGENT_KEY"] == probes["HOLON_AGENT_KEY"]
    assert probes["HTTPS_PROXY"] in docker.env_file_contents["HTTPS_PROXY"]
    assert not os.path.exists(docker.env_file)


@pytest.mark.parametrize(
    "form",
    ["alice:{v}@proxy.corp:8080", "socks5://alice:{v}@proxy:1080", "https://alice:{v}@proxy:3128"],
    ids=["schemeless", "socks5", "https"],
)
def test_proxy_url_password_travels_only_in_the_env_file(leak_env, monkeypatch, capsys, form):
    """A forwarded HOLON_PROXY_URL and the derived proxy variables keep their password off docker argv."""
    sample = make_probe()
    proxy_value = form.format(v=sample)
    monkeypatch.setenv("HOLON_PROXY_URL", proxy_value)
    monkeypatch.setattr(
        cli,
        "get_token_reduction_mounts_and_envs",
        lambda **kwargs: ([], {"HTTP_PROXY": proxy_value, "https_proxy": proxy_value, "NO_PROXY": "localhost"}),
    )
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    echo = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("Executing: docker run"))
    assert_no_leak(sample, echo, docker.argv)
    assert docker.env_file_mode == 0o600
    for name in ("HOLON_PROXY_URL", "HTTP_PROXY", "https_proxy"):
        assert docker.env_file_contents[name] == proxy_value
        assert name not in docker.argv_env
    # A value without a password stays on the command line, readable.
    assert docker.argv_env["NO_PROXY"] == "localhost"


_URL_ENV_NAMES = ["HOLON_PROXY_URL", "HOLON_LOCAL_BASE_URL", "HOLON_MIRROR_ENDPOINT"]


@pytest.mark.parametrize(
    ("form", "separators"), [pytest.param(form, seps, id=case_id) for case_id, form, seps in url_userinfo_cases()]
)
@pytest.mark.parametrize("name", _URL_ENV_NAMES)
def test_url_userinfo_travels_only_in_the_env_file_in_every_shape(  # noqa: PLR0913
    leak_env, monkeypatch, capsys, name, form, separators
):
    """Any scheme / `//` / path / password shape of a forwarded HOLON_* URL keeps its userinfo off the docker argv."""
    value, parts = make_url_probe(form, separators)
    monkeypatch.setenv(name, value)
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    echo = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("Executing: docker run"))
    for part in parts:
        assert_no_leak(part, echo, docker.argv)
    assert docker.env_file_contents[name] == value
    assert name not in docker.argv_env


@pytest.mark.parametrize("value", CREDENTIAL_FREE_URLS)
@pytest.mark.parametrize("name", _URL_ENV_NAMES)
def test_credential_free_url_stays_readable_on_the_argv(leak_env, monkeypatch, capsys, name, value):
    monkeypatch.setenv(name, value)
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    echo = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("Executing: docker run"))
    assert f"{name}={value}" in echo
    assert docker.argv_env[name] == value
    assert name not in docker.env_file_contents


@pytest.mark.parametrize("failure", ["write", "directory"])
def test_unwritable_env_file_fails_closed_with_a_clear_error(leak_env, monkeypatch, capsys, failure):
    """An OSError writing the env-file exits non-zero with a message, no traceback, and docker never runs."""
    monkeypatch.setattr(cli.subprocess, "run", _RecordingDocker(outcome=AssertionError("docker must not run")))

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(cli, "write_env_file" if failure == "write" else "env_file_dir", refuse)

    assert _run_planner(leak_env["repo"]) == 1

    err = capsys.readouterr().err
    assert "could not write the credential env-file" in err
    assert "Permission denied" in err
    for value in leak_env["probes"].values():
        assert_no_fragment(err, value)


@pytest.mark.parametrize(
    "outcome",
    [MagicMock(returncode=3), KeyboardInterrupt(), RuntimeError("docker exploded")],
    ids=["non-zero-exit", "keyboard-interrupt", "exception"],
)
def test_env_file_is_removed_however_the_run_ends(leak_env, monkeypatch, outcome):
    docker = _RecordingDocker(outcome=outcome)
    monkeypatch.setattr(cli.subprocess, "run", docker)

    if isinstance(outcome, BaseException):
        with pytest.raises(type(outcome)):
            _run_planner(leak_env["repo"])
    else:
        assert _run_planner(leak_env["repo"]) == 3

    assert docker.env_file_mode == 0o600
    assert not os.path.exists(docker.env_file)


def test_git_credential_helper_is_injected_by_reference_only(leak_env, monkeypatch):
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    env = docker.container_env
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "credential.https://github.com.helper"
    assert env["GIT_CONFIG_VALUE_0"] == ""
    assert env["GIT_CONFIG_KEY_1"] == "credential.https://github.com.helper"
    # The helper's literal `password=` text sends it through the env-file like any credential-shaped
    # value, although it holds only the `$GITHUB_TOKEN` reference.
    assert "GIT_CONFIG_VALUE_1" not in docker.argv_env
    helper = docker.env_file_contents["GIT_CONFIG_VALUE_1"]
    assert helper == cli.GIT_CREDENTIAL_HELPER
    assert '"$GITHUB_TOKEN"' in helper
    assert_no_fragment(helper, leak_env["probes"]["GITHUB_TOKEN"])


def test_credential_helper_answers_from_the_environment(leak_env):
    """The helper, run by real git, yields the token from the environment and nothing is read from a file."""
    sample = leak_env["probes"]["GITHUB_TOKEN"]
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GITHUB_TOKEN": sample,
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.https://github.com.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.https://github.com.helper",
        "GIT_CONFIG_VALUE_1": cli.GIT_CREDENTIAL_HELPER,
    }
    origin = subprocess.run(
        ["git", "config", "--show-scope", "--get-all", "credential.https://github.com.helper"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert [line.split("\t", 1)[0] for line in origin.stdout.splitlines()] == ["command", "command"]
    filled = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\npath=myorg/myrepo.git\n\n",
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "username=x-access-token" in filled.stdout.splitlines()
    assert f"password={sample}" in filled.stdout.splitlines()


def test_override_url_credential_is_stripped_and_forwarded_through_the_env_file(leak_env, monkeypatch, capsys):
    sample = leak_env["probes"]["GITHUB_TOKEN"]
    monkeypatch.delenv("GITHUB_TOKEN")
    monkeypatch.setenv("HOLON_REPO_URL", f"https://x-access-token:{sample}@github.com/myorg/myrepo.git")
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    captured = capsys.readouterr()
    assert "HOLON_REPO_URL contains embedded credentials" in captured.err
    for text in (captured.out, captured.err, " ".join(docker.argv)):
        assert_no_fragment(text, sample)
    assert "HOLON_REPO_URL=https://github.com/myorg/myrepo.git" in docker.argv
    assert docker.env_file_contents["GITHUB_TOKEN"] == sample
    assert docker.container_env.get("GIT_CONFIG_KEY_1") == "credential.https://github.com.helper"


def test_origin_url_credential_is_stripped_and_forwarded_through_the_env_file(leak_env, monkeypatch, capsys):
    """An origin remote that embeds a credential never reaches the container URL; the credential is a helper token."""
    url_sample = make_probe("origincred")
    monkeypatch.delenv("GITHUB_TOKEN")
    subprocess.run(
        [
            "git",
            "-C",
            leak_env["repo"],
            "remote",
            "set-url",
            "origin",
            f"https://x-access-token:{url_sample}@git.example.org/myorg/myrepo.git",
        ],
        check=True,
    )
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    captured = capsys.readouterr()
    assert "remote.origin.url contains embedded credentials" in captured.err
    for text in (captured.out, captured.err, " ".join(docker.argv)):
        assert_no_fragment(text, url_sample)
    assert "HOLON_REPO_URL=https://git.example.org/myorg/myrepo.git" in docker.argv
    assert docker.env_file_contents["GITHUB_TOKEN"] == url_sample
    assert docker.container_env.get("GIT_CONFIG_KEY_1") == "credential.https://git.example.org.helper"


@pytest.mark.parametrize("source", ["override", "origin"])
def test_embedded_url_credential_wins_over_the_ambient_host_token(leak_env, monkeypatch, source):
    """A credential embedded in the repository URL names its remote, so it beats a host token for another host."""
    url_sample = make_probe("enterprisecred")
    url = f"https://x-access-token:{url_sample}@git.example.org/myorg/myrepo.git"
    if source == "override":
        monkeypatch.setenv("HOLON_REPO_URL", url)
    else:
        subprocess.run(["git", "-C", leak_env["repo"], "remote", "set-url", "origin", url], check=True)
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    assert docker.env_file_contents["GITHUB_TOKEN"] == url_sample
    assert "HOLON_REPO_URL=https://git.example.org/myorg/myrepo.git" in docker.argv
    assert docker.container_env.get("GIT_CONFIG_KEY_1") == "credential.https://git.example.org.helper"


def _set_repo_url(leak_env, monkeypatch, source: str, url: str) -> None:
    if source == "override":
        monkeypatch.setenv("HOLON_REPO_URL", url)
    else:
        subprocess.run(["git", "-C", leak_env["repo"], "remote", "set-url", "origin", url], check=True)


@pytest.mark.parametrize("source", ["override", "origin"])
def test_plain_user_name_in_the_url_is_not_a_credential(leak_env, monkeypatch, capsys, source):
    """`https://<user>@host/...` names a login, not a token: it is stripped silently and the host token applies."""
    _set_repo_url(leak_env, monkeypatch, source, "https://someuser@git.example.org/myorg/myrepo.git")
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    assert "embedded credentials" not in capsys.readouterr().err
    assert "HOLON_REPO_URL=https://git.example.org/myorg/myrepo.git" in docker.argv
    assert docker.env_file_contents["GITHUB_TOKEN"] == leak_env["probes"]["GITHUB_TOKEN"]
    # An origin remote gets the host-scoped helper for the host token; a credential-free override gets none.
    has_helper = docker.container_env.get("GIT_CONFIG_KEY_1") == "credential.https://git.example.org.helper"
    assert has_helper is (source == "origin")


def _token_user_url() -> tuple[str, str]:
    sample = make_gh_probe("gh" + "o_")
    return f"https://{sample}@git.example.org/myorg/myrepo.git", sample


def _password_url() -> tuple[str, str]:
    pw_sample = make_probe("userpass")
    return f"https://someuser:{pw_sample}@git.example.org/myorg/myrepo.git", pw_sample


def _access_token_url() -> tuple[str, str]:
    sample = make_probe("accesstoken")
    return f"https://x-access-token:{sample}@git.example.org/myorg/myrepo.git", sample


@pytest.mark.parametrize("source", ["override", "origin"])
@pytest.mark.parametrize(
    "make_url",
    [_token_user_url, _password_url, _access_token_url],
    ids=["token-as-user", "user-password", "x-access-token"],
)
def test_url_credential_forms_supply_the_env_file_token(leak_env, monkeypatch, capsys, source, make_url):
    """Each userinfo form that carries a credential is stripped, warned about, and wins over the host token."""
    url, url_part = make_url()
    _set_repo_url(leak_env, monkeypatch, source, url)
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    captured = capsys.readouterr()
    expected_source = "HOLON_REPO_URL" if source == "override" else "remote.origin.url"
    assert f"{expected_source} contains embedded credentials" in captured.err
    for text in (captured.out, captured.err, " ".join(docker.argv)):
        assert_no_fragment(text, url_part)
    assert "HOLON_REPO_URL=https://git.example.org/myorg/myrepo.git" in docker.argv
    assert docker.env_file_contents["GITHUB_TOKEN"] == url_part
    assert docker.container_env.get("GIT_CONFIG_KEY_1") == "credential.https://git.example.org.helper"


@pytest.mark.parametrize("override", ["ssh://git@github.com/myorg/myrepo.git", "git@github.com:myorg/myrepo.git"])
def test_ssh_override_url_keeps_its_user_and_synthesises_no_token(leak_env, monkeypatch, capsys, override):
    """The SSH login user (`git`) is not a credential: it stays in the URL and never becomes GITHUB_TOKEN."""
    monkeypatch.delenv("GITHUB_TOKEN")
    monkeypatch.setenv("HOLON_REPO_URL", override)
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    assert "embedded credentials" not in capsys.readouterr().err
    assert f"HOLON_REPO_URL={override}" in docker.argv
    assert "GITHUB_TOKEN" not in docker.env_file_contents
    assert not any(arg.startswith("GITHUB_TOKEN=") for arg in docker.argv)
    assert not any(key.startswith("GIT_CONFIG_") for key in docker.container_env)


@pytest.mark.parametrize("source", ["override", "origin"])
def test_plain_http_url_credential_is_discarded_not_forwarded(leak_env, monkeypatch, capsys, source):
    """Plain HTTP is never sent a credential: it is dropped with an accurate warning, not forwarded unused."""
    url_sample = make_probe("plainhttpcred")
    url = f"http://x-access-token:{url_sample}@git.example.org/myorg/myrepo.git"
    monkeypatch.delenv("GITHUB_TOKEN")
    if source == "override":
        monkeypatch.setenv("HOLON_REPO_URL", url)
    else:
        subprocess.run(["git", "-C", leak_env["repo"], "remote", "set-url", "origin", url], check=True)
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    captured = capsys.readouterr()
    assert "contains embedded credentials" in captured.err
    assert "discarded" in captured.err
    assert "credential helper" not in captured.err
    for text in (captured.out, captured.err, " ".join(docker.argv), *docker.env_file_contents.values()):
        assert_no_fragment(text, url_sample)
    assert "HOLON_REPO_URL=http://git.example.org/myorg/myrepo.git" in docker.argv
    assert "GITHUB_TOKEN" not in docker.env_file_contents
    assert not any(key.startswith("GIT_CONFIG_") for key in docker.container_env)


def test_plain_http_override_falls_back_to_the_host_token(leak_env, monkeypatch):
    """Discarding a plain-HTTP credential leaves the ambient host token as the forwarded GITHUB_TOKEN."""
    url_sample = make_probe("plainhttpcred")
    monkeypatch.setenv("HOLON_REPO_URL", f"http://x-access-token:{url_sample}@git.example.org/myorg/myrepo.git")
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    assert docker.env_file_contents["GITHUB_TOKEN"] == leak_env["probes"]["GITHUB_TOKEN"]
    for value in docker.env_file_contents.values():
        assert_no_fragment(value, url_sample)
    assert not any(key.startswith("GIT_CONFIG_") for key in docker.container_env)


def test_no_credential_helper_for_an_override_url_that_never_carried_the_token(leak_env, monkeypatch):
    monkeypatch.setenv("HOLON_REPO_URL", "https://git.example.org/myorg/myrepo.git")
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    assert not any(key.startswith("GIT_CONFIG_") for key in docker.container_env)


def test_agent_settings_carrying_an_api_key_travel_in_the_env_file(leak_env, monkeypatch, capsys):
    provider_sample = "sk-" + "ant-" + make_probe("anthropic")
    settings = '{"apiKey":"' + provider_sample + '"}'
    monkeypatch.setenv("HOLON_AGENT_SETTINGS", settings)
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    captured = capsys.readouterr()
    for text in (captured.out, captured.err, " ".join(docker.argv)):
        assert_no_fragment(text, provider_sample)
    assert docker.env_file_contents["HOLON_AGENT_SETTINGS"] == settings


def test_line_break_in_a_secret_aborts_before_docker_runs(leak_env, monkeypatch, capsys):
    monkeypatch.setenv("HOLON_AGENT_KEY", "first-line\nINJECTED=1")
    monkeypatch.setattr(cli.subprocess, "run", _RecordingDocker(outcome=AssertionError("docker must not run")))

    assert _run_planner(leak_env["repo"]) == 1

    err = capsys.readouterr().err
    assert "HOLON_AGENT_KEY" in err
    assert "first-line" not in err
    assert os.listdir(cli.env_file_dir()) == []


def test_stale_env_files_are_swept(isolated_env_file_dir):
    stale = isolated_env_file_dir / "holon-env-stale"
    fresh = isolated_env_file_dir / "holon-env-fresh"
    unrelated = isolated_env_file_dir / "keep-me"
    for path in (stale, fresh, unrelated):
        path.write_text("X=1\n")
    old = time.time() - 2 * 24 * 60 * 60
    os.utime(stale, (old, old))
    os.utime(unrelated, (old, old))

    cli.sweep_stale_env_files(str(isolated_env_file_dir))

    assert not stale.exists()
    assert fresh.exists()
    assert unrelated.exists()


def test_env_file_dir_is_tightened_to_owner_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    loose = tmp_path / ".holon" / "tmp" / "env-files"
    loose.mkdir(parents=True, mode=0o755)
    os.chmod(loose, 0o755)

    assert _REAL_ENV_FILE_DIR() == str(loose)
    assert stat.S_IMODE(os.stat(loose).st_mode) == 0o700


# Built once at collection, so the URL and the expected credential are the same runtime value.
_GH_USER_PROBE = make_gh_probe()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://x-access-token:SECRET@github.com/o/r.git", ("https://github.com/o/r.git", "SECRET")),
        # A plain bare user name is a login name, not a credential.
        ("https://someuser@github.com/o/r.git", ("https://github.com/o/r.git", None)),
        ("https://someuser:@github.com/o/r.git", ("https://github.com/o/r.git", None)),
        # A token-shaped bare user name is the credential (GitHub accepts a token as the user name).
        # A fixed id: the probe is random per process, and xdist workers must collect identical test ids.
        pytest.param(
            f"https://{_GH_USER_PROBE}@github.com/o/r.git",
            ("https://github.com/o/r.git", _GH_USER_PROBE),
            id="token-as-user-name",
        ),
        ("https://someuser:SECRET@github.com/o/r.git", ("https://github.com/o/r.git", "SECRET")),
        # The `<token>:x-oauth-basic` form carries the token as the user name.
        ("https://SECRET:x-oauth-basic@github.com/o/r.git", ("https://github.com/o/r.git", "SECRET")),
        # Userinfo is percent-decoded, as git decodes it.
        ("https://someuser:SE%40CRET@github.com/o/r.git", ("https://github.com/o/r.git", "SE@CRET")),
        ("https://github.com/o/r.git", ("https://github.com/o/r.git", None)),
        ("https://u:SECRET@ghe.example:8443/o/r.git", ("https://ghe.example:8443/o/r.git", "SECRET")),
        ("git@github.com:o/r.git", ("git@github.com:o/r.git", None)),
        ("ssh://git@github.com/o/r.git", ("ssh://git@github.com/o/r.git", None)),
        ("ssh://git@ghe.example:2222/o/r.git", ("ssh://git@ghe.example:2222/o/r.git", None)),
        ("git+ssh://git@github.com/o/r.git", ("git+ssh://git@github.com/o/r.git", None)),
        # The placeholder password is compared case-insensitively and is never itself the credential.
        ("https://SECRET:X-OAuth-Basic@github.com/o/r.git", ("https://github.com/o/r.git", "SECRET")),
        ("https://:x-oauth-basic@github.com/o/r.git", ("https://github.com/o/r.git", None)),
        # Bracketed IPv6 hosts and ports survive; an `@` in the path is not userinfo.
        ("https://u:SECRET@[::1]:8443/o/r.git", ("https://[::1]:8443/o/r.git", "SECRET")),
        ("https://github.com/o/r@v1.git", ("https://github.com/o/r@v1.git", None)),
        # A credential-free URL is returned byte for byte: case, port, query and fragment kept.
        ("https://GitHub.com:443/O/R.git?x=1#frag", ("https://GitHub.com:443/O/R.git?x=1#frag", None)),
        ("http://git.example.org/o/r.git", ("http://git.example.org/o/r.git", None)),
        ("file:///srv/git/r.git", ("file:///srv/git/r.git", None)),
        # A raw `/`, `?` or `#` in the password is still userinfo to git: the whole credential is stripped.
        ("https://someuser:SE/CRET@github.com/o/r.git", ("https://github.com/o/r.git", "SE/CRET")),
        ("https://someuser:SE?CRET@github.com/o/r.git", ("https://github.com/o/r.git", "SE?CRET")),
        ("https://someuser:SE#CRET@github.com/o/r.git", ("https://github.com/o/r.git", "SE#CRET")),
        ("https://u:S/E?C#RET@ghe.example:8443/o/r.git", ("https://ghe.example:8443/o/r.git", "S/E?C#RET")),
        ("https://u:SE/CRET@github.com/o/r@v1.git", ("https://github.com/o/r@v1.git", "SE/CRET")),
        # A port, an IPv6 literal or a bare host before the delimiter is not a password.
        ("https://ghe.example:8443/o/r@v1.git", ("https://ghe.example:8443/o/r@v1.git", None)),
        ("https://[::1]:8443/o/r@v1.git", ("https://[::1]:8443/o/r@v1.git", None)),
        ("https://github.com/o/r.git?ref=a@b#c@d", ("https://github.com/o/r.git?ref=a@b#c@d", None)),
    ],
)
def test_strip_url_credentials(url, expected):
    assert strip_url_credentials(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/o/r.git", "github.com"),
        ("https://GHE.example:8443/o/r.git", "ghe.example:8443"),
        ("http://github.com/o/r.git", None),
        ("git@github.com:o/r.git", None),
        ("ssh://git@github.com/o/r.git", None),
    ],
)
def test_credential_host(url, expected):
    assert cli.credential_host(url) == expected


def test_credential_helper_is_scoped_to_host_and_port(leak_env, monkeypatch):
    url_sample = make_probe("e")
    subprocess.run(
        [
            "git",
            "-C",
            leak_env["repo"],
            "remote",
            "set-url",
            "origin",
            f"https://x-access-token:{url_sample}@ghe.example:8443/myorg/myrepo.git",
        ],
        check=True,
    )
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    assert docker.container_env["GIT_CONFIG_KEY_0"] == "credential.https://ghe.example:8443.helper"
    assert docker.container_env["GIT_CONFIG_KEY_1"] == "credential.https://ghe.example:8443.helper"
    assert docker.container_env["HOLON_REPO_URL"] == "https://ghe.example:8443/myorg/myrepo.git"
    assert docker.env_file_contents["GITHUB_TOKEN"] == url_sample


def _helper_env(sample: str, helper_host: str) -> dict[str, str]:
    key = f"credential.https://{helper_host}.helper"
    return {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        "GITHUB_TOKEN": sample,
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": key,
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": key,
        "GIT_CONFIG_VALUE_1": cli.GIT_CREDENTIAL_HELPER,
    }


@pytest.mark.parametrize(
    ("asked_host", "answered"),
    [("ghe.example:8443", True), ("ghe.example", False), ("ghe.example:9443", False), ("github.com", False)],
)
def test_real_git_answers_only_for_the_scoped_host(leak_env, asked_host, answered):
    """Real `git credential fill`: the helper answers for its own host:port and stays silent elsewhere."""
    sample = leak_env["probes"]["GITHUB_TOKEN"]
    filled = subprocess.run(
        ["git", "credential", "fill"],
        input=f"protocol=https\nhost={asked_host}\npath=myorg/myrepo.git\n\n",
        env=_helper_env(sample, "ghe.example:8443"),
        capture_output=True,
        text=True,
        check=False,
    )
    if answered:
        assert filled.returncode == 0
        assert f"password={sample}" in filled.stdout.splitlines()
    else:
        # With prompts disabled and no helper for this host, git cannot fill a credential.
        assert filled.returncode != 0
        assert_no_leak(sample, filled.stdout, filled.stderr)


def test_empty_user_oauth_basic_override_forwards_no_placeholder(leak_env, monkeypatch, capsys):
    """`https://:x-oauth-basic@host` carries no credential: the placeholder is never forwarded."""
    monkeypatch.delenv("GITHUB_TOKEN")
    monkeypatch.setenv("HOLON_REPO_URL", "https://:x-oauth-basic@git.example.org/myorg/myrepo.git")
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    assert "embedded credentials" not in capsys.readouterr().err
    assert docker.container_env["HOLON_REPO_URL"] == "https://git.example.org/myorg/myrepo.git"
    assert "GITHUB_TOKEN" not in docker.container_env
    assert not any(key.startswith("GIT_CONFIG_") for key in docker.container_env)


def test_oauth_settings_json_travels_in_the_env_file(leak_env, monkeypatch, capsys):
    settings_probe = "sk-" + "ant-" + "oat01-" + make_probe("o")
    settings = '{"env":{"CLAUDE_CODE_OAUTH_TOKEN":"' + settings_probe + '"}}'
    monkeypatch.setenv("HOLON_AGENT_SETTINGS", settings)
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    captured = capsys.readouterr()
    assert_no_leak(settings_probe, captured.out, captured.err, docker.argv)
    assert docker.env_file_contents["HOLON_AGENT_SETTINGS"] == settings


def test_multiline_non_secret_settings_stay_on_the_command_line(leak_env, monkeypatch):
    """Settings are secret by pattern, not by name: a credential-free multi-line JSON is not rejected."""
    settings = '{\n  "model": "opus",\n  "permissions": {"allow": ["Bash"]}\n}'
    monkeypatch.setenv("HOLON_AGENT_SETTINGS", settings)
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    assert docker.argv_env["HOLON_AGENT_SETTINGS"] == settings
    assert "HOLON_AGENT_SETTINGS" not in docker.env_file_contents


def test_mitm_web_password_is_neither_in_argv_nor_echoed(leak_env, monkeypatch, capsys):
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    probe = leak_env["probes"]["HOLON_MITM_WEB_PASSWORD"]
    assert_no_leak(probe, capsys.readouterr().out, docker.argv)
    assert "HOLON_MITM_WEB_PASSWORD" not in docker.argv_env


def test_intent_file_error_path_leaves_no_env_file(leak_env, monkeypatch, capsys):
    monkeypatch.setattr(cli.subprocess, "run", _RecordingDocker(outcome=AssertionError("docker must not run")))

    code = run_docker_container(
        "intent-creator",
        "holon/agent-claude",
        ["claude-agent", "opus"],
        agent_id="claude",
        repo_dir=leak_env["repo"],
        intent_file=os.path.join(leak_env["repo"], "missing-intent.json"),
    )

    assert code == 1
    assert "does not exist" in capsys.readouterr().err
    assert os.listdir(cli.env_file_dir()) == []


def test_redact_command_echo_masks_the_helper_reference_only(leak_env, monkeypatch, capsys):
    """The echo shows `--env-file <path>` but never the env-file contents or the token."""
    docker = _RecordingDocker()
    monkeypatch.setattr(cli.subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    echo = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("Executing: docker run"))
    assert f"--env-file {docker.env_file}" in echo
    assert "GIT_CONFIG_VALUE_1" not in echo
    assert_names_repo(echo, "github.com/myorg/myrepo.git", _BRANCH)


def _password_head_error(head: str) -> ValueError:
    """The `ValueError` `urlparse` raises when a raw delimiter cuts a password into the port."""
    return ValueError(f"Port could not be cast to integer value as '{head}'")


@pytest.mark.parametrize("delim", ["/", "?", "#"])
def test_review_loop_handler_masks_a_quoted_password_head(monkeypatch, capsys, delim):
    """The review-loop top-level handler never prints a password piece quoted by an exception."""
    head, tail = make_probe("a"), make_probe("b")
    monkeypatch.setenv("HOLON_PROXY_URL", f"http://alice:{head}{delim}{tail}@127.0.0.1:3128")
    with (
        patch("sys.argv", ["holon", "review-loop", "1"]),
        patch("sandbox_executor.cli.detect_holon_coherence", side_effect=_password_head_error(head)),
        pytest.raises(SystemExit) as exit_info,
    ):
        main()

    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert "Error during review-loop execution" in captured.err
    for text in (captured.out, captured.err):
        assert_no_fragment(text, head)
        assert_no_fragment(text, tail)


def test_flow_handler_masks_a_quoted_password_head(monkeypatch, capsys, tmp_path):
    """The flow top-level handler never prints a password piece quoted by an exception."""
    head, tail = make_probe("a"), make_probe("b")
    monkeypatch.setenv("HOLON_PROXY_URL", f"alice:{head}/{tail}@127.0.0.1:3128")
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text("{}", encoding="utf-8")
    with (
        patch("sys.argv", ["holon", "flow", "--checkpoint", str(checkpoint)]),
        patch("sandbox_executor.cli.load_checkpoint", side_effect=_password_head_error(head)),
        pytest.raises(SystemExit) as exit_info,
    ):
        main()

    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert "Error during flow execution" in captured.err
    for text in (captured.out, captured.err):
        assert_no_fragment(text, head)


def test_calibrate_handler_masks_a_quoted_password_head(monkeypatch, capsys):
    """The calibrate top-level handler never prints a password piece quoted by an exception."""
    head, tail = make_probe("a"), make_probe("b")
    monkeypatch.setenv("HOLON_LOCAL_BASE_URL", f"socks5://alice:{head}#{tail}@127.0.0.1:3128")
    with (
        patch("sys.argv", ["holon", "calibrate", "I-x/P-y/E-z/_"]),
        patch("sandbox_executor.cli.run_calibrate", side_effect=RuntimeError(f"bad value {head}#{tail}")),
        pytest.raises(SystemExit) as exit_info,
    ):
        main()

    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert "Error during calibration" in captured.err
    for text in (captured.out, captured.err):
        assert_no_fragment(text, head)
        assert_no_fragment(text, tail)


def test_redact_cli_error_keeps_messages_without_url_credentials(monkeypatch):
    """A message with nothing from a URL credential in it is printed unchanged."""
    monkeypatch.setenv("HOLON_PROXY_URL", "http://127.0.0.1:3128")
    message = "Checkpoint file is not valid JSON: Expecting value: line 1 column 1"

    assert cli.redact_cli_error(ValueError(message)) == message


def _confine_git_lookups(monkeypatch, root):
    """Make `_find_git_root` see only `.git` entries under `root`, whatever the real ancestors hold."""
    real_exists = os.path.exists
    root_str = str(root)

    def fake_exists(path):
        text = os.fspath(path)
        if os.path.basename(text) == ".git" and not text.startswith(root_str + os.sep):
            return False
        return real_exists(path)

    monkeypatch.setattr(cli.os.path, "exists", fake_exists)


def _find_git_root_within_deadline(start_dir, deadline_seconds=5.0):
    """Run `_find_git_root` on a daemon thread, returning `(finished, result)` after the deadline."""
    import threading

    result: dict[str, str] = {}
    worker = threading.Thread(target=lambda: result.update(root=cli._find_git_root(start_dir=start_dir)), daemon=True)
    worker.start()
    worker.join(timeout=deadline_seconds)
    return not worker.is_alive(), result.get("root")


def test_find_git_root_terminates_without_a_git_ancestor(tmp_path, monkeypatch):
    """With no `.git` anywhere above the start directory, the search ends and falls back to it."""
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    _confine_git_lookups(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.os, "getcwd", lambda: str(nested))
    monkeypatch.setattr(cli, "__file__", str(nested / "cli.py"))

    finished, root = _find_git_root_within_deadline(str(nested))

    assert finished, "_find_git_root did not return within its deadline"
    assert root == os.path.abspath(nested)


def test_find_git_root_returns_the_nearest_git_ancestor(tmp_path, monkeypatch):
    """A `.git` two levels above the start directory is found by walking upwards."""
    repo = tmp_path / "repo"
    nested = repo / "x" / "y"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()
    _confine_git_lookups(monkeypatch, tmp_path)

    finished, root = _find_git_root_within_deadline(str(nested))

    assert finished, "_find_git_root did not return within its deadline"
    assert root == str(repo)
