import importlib.metadata
import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

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
)
from sandbox_executor.flow import SKIP_PUSH_DISPATCH_ERROR, FlowContext


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

    @patch("sandbox_executor.cli.resolve_target_repo_url", return_value="git@github.com:test/repo.git")
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

    @patch("sandbox_executor.cli.resolve_target_repo_url", return_value="git@github.com:test/repo.git")
    @patch("sandbox_executor.cli.get_agent_session_mounts", return_value=["-v", "/mock:/mock"])
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_run_docker_container(self, mock_which, mock_run, mock_mounts, mock_resolve_repo):
        mock_run.return_value = MagicMock(returncode=0)
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
            self.assertIn("GITHUB_TOKEN=secret_token", args)
            self.assertIn("HOLON_REPO_URL=git@github.com:test/repo.git", args)

    @patch("sandbox_executor.cli.resolve_target_repo_url", return_value="git@github.com:test/repo.git")
    @patch("sandbox_executor.cli.get_agent_session_mounts", return_value=["-v", "/mock:/mock"])
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_run_docker_container_forward_env_vars(self, mock_which, mock_run, mock_mounts, mock_resolve_repo):
        mock_run.return_value = MagicMock(returncode=0)
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
            self.assertIn("GITHUB_TOKEN=my-github-token", args)
            self.assertIn("HOLON_AGENT_KEY=my-agent-key", args)
            self.assertIn("HOLON_AGENT_EFFORT=high", args)
            self.assertIn("HOLON_AGENT_PROVIDER=anthropic", args)
            # Ensure programmatic role takes strict precedence over host environment
            self.assertIn("HOLON_ROLE=planner", args)
            self.assertNotIn("HOLON_ROLE=host-role-should-be-overridden", args)
            self.assertEqual([a for a in args if a.startswith("HOLON_ROLE=")], ["HOLON_ROLE=planner"])
            # Ensure non-prefixed variable is NOT forwarded
            self.assertFalse(any("SOME_OTHER_VAR" in arg for arg in args))
            self.assertIn("HOLON_REPO_URL=git@github.com:test/repo.git", args)

    def test_run_docker_container_requires_agent_id(self):
        with self.assertRaises(TypeError):
            run_docker_container("planner", "holon/agent-antigravity", ["branch"])  # type: ignore[call-arg]

    @patch("sandbox_executor.cli.resolve_target_repo_url", return_value="git@github.com:test/repo.git")
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
                self.assertEqual(
                    resolved,
                    "https://x-access-token:ghp_mock_token_123@github.com/myorg/myrepo.git",
                )

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
                self.assertEqual(
                    resolved,
                    "https://x-access-token:ghp_mock_token_456@github.com/myorg/myrepo.git",
                )

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
        """Test that run_docker_container masks tokenized HOLON_REPO_URL in sanitized_cmd log."""
        mock_run.return_value = MagicMock(returncode=0)
        token = "ghp_super_secret_pat_999"
        token_url = f"https://x-access-token:{token}@github.com/myorg/myrepo.git"
        with (
            patch.dict(os.environ, {"HOLON_REPO_URL": token_url}, clear=True),
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
            self.assertIn("HOLON_REPO_URL=https://x-access-token:***REDACTED***@github.com/myorg/myrepo.git", output)
            self.assertNotIn(token, output)

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

    def test_review_loop_cli_json_output(self):
        fake_result = {
            "target": "my-branch",
            "consensus": {"approved": True, "status": "approved", "iterations": 1},
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
