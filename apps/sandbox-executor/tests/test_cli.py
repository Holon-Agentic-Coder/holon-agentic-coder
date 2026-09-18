import importlib.metadata
import io
import os
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor.cli import (
    find_github_token,
    get_agent_session_mounts,
    get_ssh_auth_mounts,
    main,
    run_docker_container,
)


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
    def test_antigravity_macos_existing_session_mounts_rw(self, mock_exists):
        mock_exists.side_effect = lambda p: ".holon/sessions/antigravity" in p or "antigravity" in p
        mounts = get_agent_session_mounts("antigravity")
        self.assertIsInstance(mounts, list)
        self.assertIn("-v", mounts)
        self.assertTrue(any("/home/holon/.gemini/antigravity-cli:rw" in m for m in mounts))
        self.assertIn("--tmpfs", mounts)

    @patch("sys.platform", "linux")
    @patch("os.path.exists")
    def test_antigravity_linux_mounts_gemini_cli(self, mock_exists):
        mock_exists.side_effect = lambda p: ".gemini/antigravity-cli" in p
        mounts = get_agent_session_mounts("antigravity")
        self.assertIsInstance(mounts, list)
        self.assertIn("-v", mounts)
        self.assertTrue(any("/home/holon/.gemini/antigravity-cli:rw" in m for m in mounts))

    @patch("sys.platform", "linux")
    @patch("os.path.exists")
    @patch("os.getuid", return_value=1001, create=True)
    def test_antigravity_linux_dbus_mount(self, mock_uid, mock_exists):
        mock_exists.side_effect = lambda p: p in ("/run/user/1001/bus", os.path.expanduser("~/.gemini/antigravity-cli"))
        mounts = get_agent_session_mounts("antigravity")
        self.assertIn("-v", mounts)
        self.assertTrue(any("/run/user/1001/bus:/run/user/1000/bus" in m for m in mounts))
        self.assertTrue(any("/home/holon/.gemini/antigravity-cli:rw" in m for m in mounts))

    @patch("sandbox_executor.cli.get_agent_session_mounts", return_value=["-v", "/mock:/mock"])
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_run_docker_container(self, mock_which, mock_run, mock_mounts):
        mock_run.return_value = MagicMock(returncode=0)
        with patch.dict(os.environ, {"GITHUB_TOKEN": "secret_token"}, clear=True):
            code = run_docker_container("planner", "holon/agent-antigravity", ["branch", "agent", "model"])
            self.assertEqual(code, 0)
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            self.assertIn("docker", args)
            self.assertIn("GITHUB_TOKEN=secret_token", args)

    @patch("sandbox_executor.cli.get_agent_session_mounts", return_value=["-v", "/mock:/mock"])
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_run_docker_container_forward_env_vars(self, mock_which, mock_run, mock_mounts):
        mock_run.return_value = MagicMock(returncode=0)
        env = {
            "GITHUB_TOKEN": "my-github-token",
            "HOLON_AGENT_KEY": "my-agent-key",
            "HOLON_AGENT_EFFORT": "high",
            "HOLON_AGENT_PROVIDER": "anthropic",
            "SOME_OTHER_VAR": "should-not-be-forwarded",
        }
        with patch.dict(os.environ, env, clear=True):
            code = run_docker_container("planner", "holon/agent-antigravity", ["branch", "agent", "model"])
            self.assertEqual(code, 0)
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            self.assertIn("docker", args)
            self.assertIn("GITHUB_TOKEN=my-github-token", args)
            self.assertIn("HOLON_AGENT_KEY=my-agent-key", args)
            self.assertIn("HOLON_AGENT_EFFORT=high", args)
            self.assertIn("HOLON_AGENT_PROVIDER=anthropic", args)
            # Ensure non-prefixed variable is NOT forwarded
            self.assertFalse(any("SOME_OTHER_VAR" in arg for arg in args))

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


if __name__ == "__main__":
    unittest.main()
