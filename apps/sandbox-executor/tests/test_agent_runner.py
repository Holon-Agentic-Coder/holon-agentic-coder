import subprocess
import unittest

import pytest
from sandbox_executor.agent_runner import get_repo_url, get_runner, runners


class TestAgentRunner(unittest.TestCase):
    def test_runner_mappings(self):
        """Test that get_runner correctly maps agent names and validates support."""
        supported_agents = ["pi", "pi-agent", "gemini-agent", "claude-agent", "antigravity-agent"]
        for agent in supported_agents:
            runner = get_runner(agent)
            self.assertIsNotNone(runner)

        with self.assertRaises(SystemExit):
            get_runner("unsupported-agent-name")

    def test_standard_runner_cmd(self):
        """Test command construction for all agents in the registry."""
        import os
        from unittest.mock import patch

        expected_commands = {
            "pi": ["pi", "-p", "--model", "gemini-3.5-flash", "compiled prompt text"],
            "claude": ["claude", "--model", "gemini-3.5-flash", "-p", "compiled prompt text"],
            "gemini": ["gemini", "--model", "gemini-3.5-flash", "-p", "compiled prompt text"],
            "opencode": ["opencode", "run", "--model", "gemini-3.5-flash", "compiled prompt text"],
            "codex": ["codex", "exec", "-m", "gemini-3.5-flash", "compiled prompt text"],
            "antigravity": [
                "agy",
                "--dangerously-skip-permissions",
                "--model",
                "gemini-3.5-flash",
                "--effort",
                "medium",
                "-p",
                "compiled prompt text",
            ],
        }
        dummy_env = {
            "HOLON_AGENT_KEY": "dummy",
        }
        with patch.dict(os.environ, dummy_env):
            for agent_name, expected_cmd in expected_commands.items():
                with self.subTest(agent=agent_name):
                    runner = get_runner(agent_name)
                    cmd = runner.build_cmd(
                        model_name="gemini-3.5-flash",
                        prompt_file="/tmp/prompt.md",
                        intent_file="/tmp/intent.json",
                        full_prompt="compiled prompt text",
                    )
                    self.assertEqual(cmd, expected_cmd)

    def test_runner_validation_failures(self):
        """Test that validation fails with SystemExit when credentials are missing."""
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {}, clear=True), patch("os.path.exists", return_value=False):
            for agent_name in runners:
                with self.subTest(agent=agent_name):
                    runner = get_runner(agent_name)
                    with self.assertRaises(SystemExit):
                        runner.build_cmd("model", "/tmp/p", "/tmp/i", "prompt")

    def test_runner_env_vars(self):
        """Test that runners correctly append configurations derived from environment variables."""
        import os
        from unittest.mock import patch

        # 1. Claude settings
        with patch.dict(os.environ, {"HOLON_AGENT_SETTINGS": "/path/to/settings.json", "HOLON_AGENT_KEY": "dummy"}):
            runner = get_runner("claude")
            cmd = runner.build_cmd("claude-3", "/tmp/p", "/tmp/i", "prompt")
            self.assertIn("--settings", cmd)
            self.assertIn("/path/to/settings.json", cmd)

        # 2. Codex OSS and local provider configurations
        env = {
            "HOLON_AGENT_OSS_MODE": "true",
            "HOLON_AGENT_LOCAL_PROVIDER": "ollama",
            "HOLON_AGENT_CONFIG": "temperature=0.2",
        }
        with patch.dict(os.environ, env):
            runner = get_runner("codex")
            cmd = runner.build_cmd("codex-m", "/tmp/p", "/tmp/i", "prompt")
            self.assertIn("--oss", cmd)
            self.assertIn("--local-provider", cmd)
            self.assertIn("ollama", cmd)
            self.assertIn("-c", cmd)
            self.assertIn("temperature=0.2", cmd)

        # 3. Pi provider (auth handled internally via HOLON_AGENT_KEY -> PI_API_KEY)
        # Note: --api-key is NOT a CLI flag anymore; the pi CLI reads PI_API_KEY from
        # os.environ directly. _apply_generic_token() maps HOLON_AGENT_KEY -> PI_API_KEY.
        env = {
            "HOLON_AGENT_PROVIDER": "anthropic",
            "HOLON_AGENT_KEY": "sk-ant-123",
        }
        with patch.dict(os.environ, env, clear=True):
            runner = get_runner("pi-agent")
            cmd = runner.build_cmd("claude-3", "/tmp/p", "/tmp/i", "prompt")
            self.assertIn("--provider", cmd)
            self.assertIn("anthropic", cmd)

        # 5. Antigravity skip permissions
        with patch.dict(os.environ, {"HOLON_AGENT_SKIP_PERMISSIONS": "true", "HOLON_AGENT_KEY": "dummy"}):
            runner = get_runner("antigravity")
            cmd = runner.build_cmd("gemini-3.5-flash", "/tmp/p", "/tmp/i", "prompt")
            self.assertIn("--dangerously-skip-permissions", cmd)
            self.assertIn("--effort", cmd)

    def test_three_tier_fallback_contract(self):
        """Test that Tier 1 (HOLON_AGENT_KEY) passes validation for all runners."""
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"HOLON_AGENT_KEY": "universal-token-123"}, clear=True):
            for agent_name in runners:
                with self.subTest(agent=agent_name):
                    runner = get_runner(agent_name)
                    # Should pass validation without raising SystemExit
                    runner.validate()

    def test_secret_bundle_parsing_and_filtering(self):
        """Test secret bundle parsing, agent filtering, and config_files unpacking."""
        import json
        import os
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_path = os.path.join(tmpdir, "holon_auth.json")
            bundle_data = {
                "agent_id": "claude",
                "api_key": "bundle-claude-key",
                "config_files": {
                    "~/.config/claude/config.json": '{"setting": true}',
                },
            }
            with open(bundle_path, "w") as f:
                json.dump(bundle_data, f)

            # Test targeting matching agent (claude)
            fake_home = os.path.join(tmpdir, "home")
            os.makedirs(fake_home, exist_ok=True)
            env = {"HOLON_SECRET_BUNDLE_PATH": bundle_path, "HOME": fake_home}
            with patch.dict(os.environ, env, clear=True):
                runner = get_runner("claude")
                runner.validate()
                self.assertEqual(os.getenv("HOLON_AGENT_KEY"), "bundle-claude-key")
                self.assertTrue(os.path.exists(os.path.join(fake_home, ".config/claude/config.json")))

            # Test targeting non-matching agent (gemini) -> should not set HOLON_AGENT_KEY
            with (
                patch.dict(os.environ, env, clear=True),
                patch("os.path.exists", side_effect=lambda p: p == bundle_path),
            ):
                runner = get_runner("gemini")
                with self.assertRaises(SystemExit):
                    runner.validate()
                self.assertIsNone(os.getenv("HOLON_AGENT_KEY"))

    def test_secret_bundle_path_traversal(self):
        """Test that path traversal attempts in secret bundle config_files are detected and blocked."""
        import json
        import os
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = os.path.join(tmpdir, "home")
            os.makedirs(fake_home, exist_ok=True)
            bundle_path = os.path.join(tmpdir, "holon_auth.json")

            traversal_paths = [
                "../../etc/malicious",
                "~/../home_sibling/malicious",
            ]

            for rel_path in traversal_paths:
                bundle_data = {
                    "agent_id": "claude",
                    "api_key": "key",
                    "config_files": {rel_path: "data"},
                }
                with open(bundle_path, "w") as f:
                    json.dump(bundle_data, f)

                env = {"HOLON_SECRET_BUNDLE_PATH": bundle_path, "HOME": fake_home}
                with patch.dict(os.environ, env, clear=True), patch("logging.Logger.warning") as mock_warn:
                    runner = get_runner("claude")
                    runner.resolve_credentials()
                    mock_warn.assert_called()
                    self.assertIn("Path traversal detected", str(mock_warn.call_args))

    def test_secret_bundle_api_key_only(self):
        """Assert that a secret bundle containing only api_key (with no config files)
        successfully validates and propagates the token.
        """
        import json
        import os
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_path = os.path.join(tmpdir, "holon_auth.json")
            bundle_data = {
                "agent_id": "claude",
                "api_key": "bundle-claude-key",
            }
            with open(bundle_path, "w") as f:
                json.dump(bundle_data, f)

            fake_home = os.path.join(tmpdir, "home")
            os.makedirs(fake_home, exist_ok=True)
            env = {"HOLON_SECRET_BUNDLE_PATH": bundle_path, "HOME": fake_home}
            with patch.dict(os.environ, env, clear=True):
                runner = get_runner("claude")
                runner.validate()
                self.assertEqual(os.getenv("HOLON_AGENT_KEY"), "bundle-claude-key")

    @pytest.mark.integration_test
    def test_real_images_have_binaries(self):
        """Integration test to verify that the configured agent runner binaries

        actually exist and are runnable inside the corresponding real Docker images.
        """
        import shutil

        if not shutil.which("docker"):
            self.skipTest("Docker is not available in test environment.")

        agent_image_mapping = {
            "pi": "holon/agent-pi",
            "claude": "holon/agent-claude",
            "gemini": "holon/agent-gemini",
            "opencode": "holon/agent-opencode",
            "codex": "holon/agent-codex",
            "antigravity": "holon/agent-antigravity",
        }

        for agent_id, runner in runners.items():
            with self.subTest(agent=agent_id):
                image_name = agent_image_mapping.get(agent_id)
                self.assertIsNotNone(image_name, f"Missing image mapping for agent: {agent_id}")

                # Run 'docker run --rm <image> <binary> --help'
                cmd = ["docker", "run", "--rm", image_name, runner.binary_name, "--help"]
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                    # Verify command was found (127 means command not found)
                    self.assertNotEqual(
                        result.returncode,
                        127,
                        f"Binary '{runner.binary_name}' not found (exited 127) inside image '{image_name}'.\n"
                        f"Stderr:\n{result.stderr}",
                    )
                except subprocess.TimeoutExpired:
                    # Timeout is fine since it means the binary started up and block-waited
                    pass
                except Exception as e:
                    self.fail(f"Failed to run container for agent {agent_id}: {e}")

    @pytest.mark.integration_test
    def test_real_images_get_version(self):
        """Integration test to verify that get_version() resolves correctly

        inside each real built Docker image for all supported agents.
        """
        import re
        import shutil

        if not shutil.which("docker"):
            self.skipTest("Docker is not available in test environment.")

        agent_image_mapping = {
            "pi": "holon/agent-pi",
            "claude": "holon/agent-claude",
            "gemini": "holon/agent-gemini",
            "opencode": "holon/agent-opencode",
            "codex": "holon/agent-codex",
            "antigravity": "holon/agent-antigravity",
        }

        for agent_id in runners:
            with self.subTest(agent=agent_id):
                image_name = agent_image_mapping.get(agent_id)
                self.assertIsNotNone(image_name, f"Missing image mapping for agent: {agent_id}")

                code = (
                    f"from sandbox_executor.agent_runner import get_runner; "
                    f"version = get_runner('{agent_id}').get_version(); "
                    f"print(version)"
                )
                cmd = ["docker", "run", "--rm", image_name, "python3", "-c", code]
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                    self.assertEqual(
                        result.returncode,
                        0,
                        f"Failed to run get_version() inside image '{image_name}'.\n"
                        f"Stdout: {result.stdout}\nStderr: {result.stderr}",
                    )
                    version_str = result.stdout.strip()
                    self.assertTrue(
                        bool(re.search(r"\d+\.\d+\.\d+", version_str)),
                        f"Resolved version '{version_str}' inside image '{image_name}' does not match semver format.",
                    )
                except Exception as e:
                    self.fail(f"Failed get_version integration test for container agent {agent_id}: {e}")

    def test_get_version_success(self):
        """Test get_version when the binary returns a version successfully."""
        from unittest.mock import MagicMock, patch

        runner = get_runner("pi")
        runner._resolved_version = None

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "pi version 0.84.3\n"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            version = runner.get_version()
            self.assertEqual(version, "0.84.3")
            mock_run.assert_called_with(["pi", "--version"], capture_output=True, text=True, timeout=2.0)
            mock_run.reset_mock()
            self.assertEqual(runner.get_version(), "0.84.3")
            mock_run.assert_not_called()

    def test_get_version_fallback(self):
        """Test get_version when the subprocess call fails or returns no version."""
        from unittest.mock import patch

        runner = get_runner("claude")
        original_agent_id = runner.agent_id
        try:
            runner._resolved_version = None

            with patch("subprocess.run", side_effect=Exception("binary not found")):
                version = runner.get_version()
                self.assertEqual(version, "unknown")

            runner._resolved_version = None
            runner.agent_id = "unknown-agent"
            with patch("subprocess.run", side_effect=Exception("binary not found")):
                version = runner.get_version()
                self.assertEqual(version, "unknown")
        finally:
            runner._resolved_version = None
            runner.agent_id = original_agent_id

    def test_get_version_nonzero_returncode(self):
        """Test get_version when subprocess returns a non-zero exit code."""
        from unittest.mock import MagicMock, patch

        runner = get_runner("pi")
        runner._resolved_version = None

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "pi 0.84.3"
        mock_result.stderr = "Error: execution failed"

        with patch("subprocess.run", return_value=mock_result):
            version = runner.get_version()
            self.assertEqual(version, "unknown")

    def test_get_version_timeout(self):
        """Test get_version handling when subprocess times out."""
        import subprocess
        from unittest.mock import patch

        runner = get_runner("pi")
        runner._resolved_version = None

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="pi", timeout=2.0)):
            version = runner.get_version()
            self.assertEqual(version, "unknown")

    def test_open_codex_removed(self):
        """Test that open-codex is removed and accessing it raises KeyError in runners dict
        and SystemExit in get_runner."""
        with self.assertRaises(KeyError):
            _ = runners["open-codex"]
        with self.assertRaises(SystemExit):
            get_runner("open-codex")


class TestGetRepoUrl(unittest.TestCase):
    def test_ssh_agent_forwarding_default(self):
        """Test that get_repo_url returns the standard SSH URL."""
        import unittest.mock

        with unittest.mock.patch.dict("os.environ", {}, clear=True):
            url = get_repo_url()
            self.assertEqual(url, "git@github.com:Holon-Agentic-Coder/holon-agentic-coder-ref.git")

    def test_ssh_agent_forwarding_override(self):
        """Test that get_repo_url respects the HOLON_REPO_URL environment variable."""
        import unittest.mock

        with unittest.mock.patch.dict("os.environ", {"HOLON_REPO_URL": "git@github.com:custom/repo.git"}):
            self.assertEqual(get_repo_url(), "git@github.com:custom/repo.git")

    def test_classic_pat_token(self):
        """Test that get_repo_url supports classic GitHub PAT tokens starting with gh."""
        import unittest.mock

        with unittest.mock.patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_secret123"}, clear=True):
            url = get_repo_url()
            self.assertEqual(
                url,
                "https://x-access-token:ghp_secret123@github.com/Holon-Agentic-Coder/holon-agentic-coder-ref.git",
            )

    def test_fine_grained_pat_token(self):
        """Test that get_repo_url supports fine-grained GitHub PAT tokens starting with github_pat_."""
        import unittest.mock

        with unittest.mock.patch.dict("os.environ", {"GITHUB_TOKEN": "github_pat_secret456"}, clear=True):
            url = get_repo_url()
            self.assertEqual(
                url,
                (
                    "https://x-access-token:github_pat_secret456@"
                    "github.com/Holon-Agentic-Coder/holon-agentic-coder-ref.git"
                ),
            )


class TestWorkspaceDirAndCleanup(unittest.TestCase):
    def test_get_workspace_dir_override(self):
        """Test get_workspace_dir respects HOLON_REPO_DIR when set."""
        import os
        from unittest.mock import patch

        from sandbox_executor.agent_runner import get_workspace_dir

        with patch.dict(os.environ, {"HOLON_REPO_DIR": "/custom/workspace/path"}):
            self.assertEqual(get_workspace_dir(), "/custom/workspace/path")

    def test_get_workspace_dir_sandbox(self):
        """Test get_workspace_dir returns sandbox workspace path when HOLON_IN_SANDBOX is set."""
        import os
        from unittest.mock import patch

        from sandbox_executor.agent_runner import get_workspace_dir

        with (
            patch.dict(os.environ, {"HOLON_IN_SANDBOX": "1"}, clear=True),
            patch("os.path.expanduser", side_effect=lambda p: p.replace("~", "/fake/home")),
        ):
            self.assertEqual(get_workspace_dir(), "/fake/home/.holon-sandbox/workspace")

    def test_get_workspace_dir_default(self):
        """Test get_workspace_dir returns default repo path outside sandbox."""
        import os
        from unittest.mock import patch

        from sandbox_executor.agent_runner import get_workspace_dir

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("os.path.exists", return_value=False),
            patch("os.path.expanduser", side_effect=lambda p: p.replace("~", "/fake/home")),
        ):
            self.assertEqual(get_workspace_dir(), "/fake/home/.holon/repo")

    def test_cleanup_repo_dir_nonexistent(self):
        """Test cleanup_repo_dir does nothing if directory does not exist."""
        from unittest.mock import patch

        from sandbox_executor.agent_runner import cleanup_repo_dir

        with patch("os.path.lexists", return_value=False):
            cleanup_repo_dir("/path/does/not/exist")

    def test_cleanup_repo_dir_forbidden_root(self):
        """Test cleanup_repo_dir raises RuntimeError for forbidden root directories."""
        from sandbox_executor.agent_runner import cleanup_repo_dir

        with self.assertRaises(RuntimeError):
            cleanup_repo_dir("/etc", raise_on_error=True)

    def test_cleanup_invariant_descendant_of_fixture(self):
        """Test invariant: cleanup operations only operate within designated fixture path."""
        import os
        import tempfile
        from unittest.mock import patch

        from sandbox_executor.agent_runner import _rmtree, cleanup_repo_dir

        with tempfile.TemporaryDirectory() as tmp_dir:
            test_subdir = os.path.join(tmp_dir, "test_repo")
            os.makedirs(test_subdir, exist_ok=True)
            with open(os.path.join(test_subdir, "file.txt"), "w") as f:
                f.write("test")

            with patch("sandbox_executor.agent_runner._rmtree", wraps=_rmtree) as spy_rmtree:
                cleanup_repo_dir(test_subdir, raise_on_error=True)
                for call in spy_rmtree.call_args_list:
                    cleaned = os.path.abspath(call.args[0])
                    self.assertTrue(
                        cleaned.startswith(os.path.abspath(tmp_dir)),
                        f"Invariant violated: cleanup called on {cleaned} outside {tmp_dir}",
                    )
