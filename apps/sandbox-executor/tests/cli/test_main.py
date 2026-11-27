from __future__ import annotations

import importlib.metadata
import io
import os
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor import cli
from sandbox_executor.agent_runner import SANDBOX_UV_PROJECT_ENVIRONMENT, SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.cli import (
    _run_docker,
    build_network_isolation_args,
    find_github_token,
    get_agent_session_mounts,
    main,
    run_docker_container,
)
from sandbox_executor.launcher import proxy_paths

from tests.cli._helpers import _WORKTREE_MOUNTS, _RecordingDocker, _stub_host_publish
from tests.env_file_capture import DockerEnvFileRecorder
from tests.leak_assertions import make_gh_probe


class TestHolonCLI(unittest.TestCase):
    def setUp(self):
        self.host = _stub_host_publish(self)

    def test_find_github_token(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "gh_test_token_123"}):
            self.assertEqual(find_github_token(), "gh_test_token_123")

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

    @patch("os.path.exists", return_value=True)
    def test_codex_session_mounts_tmpfs(self, mock_exists):
        mounts = get_agent_session_mounts("codex")
        self.assertIn("-v", mounts)
        self.assertTrue(any("/home/holon/.codex:ro" in m for m in mounts))
        self.assertIn("--tmpfs", mounts)
        self.assertIn("/home/holon/.codex/log:uid=1000,gid=1000", mounts)
        self.assertIn("/home/holon/.codex/cache:uid=1000,gid=1000", mounts)

    def test_all_agent_session_mounts_are_read_only(self):
        agents = ["antigravity", "antigravity-agent", "claude", "codex", "pi"]
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

    @patch("sandbox_executor.cli.git_flow.sandbox_worktree_mounts", return_value=list(_WORKTREE_MOUNTS))
    @patch("sandbox_executor.launcher.mounts.get_agent_session_mounts", return_value=["-v", "/mock:/mock:ro"])
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_run_docker_container_includes_network_isolation(
        self, mock_which, mock_run, mock_mounts, mock_worktree_mounts
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

    @patch("sandbox_executor.cli.git_flow.sandbox_worktree_mounts", return_value=list(_WORKTREE_MOUNTS))
    @patch("sandbox_executor.launcher.mounts.get_agent_session_mounts", return_value=["-v", "/mock:/mock"])
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_run_docker_container(self, mock_which, mock_run, mock_mounts, mock_worktree_mounts):
        docker = mock_run.side_effect = DockerEnvFileRecorder()
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
            # why: the token travels in the 0600 env-file, never on the docker command line
            self.assertFalse(any("secret_token" in arg for arg in args))
            (forwarded,) = docker.env_files
            self.assertEqual(forwarded["GITHUB_TOKEN"], "secret_token")
            # why: no repository URL, SSH agent or HOLON_* item reaches the container through its command line
            self.assertIn(f"/work/flow-x:{SANDBOX_WORKSPACE_MOUNT}", args)
            self.assertEqual(forwarded["HOLON_TARGET_WORKTREE"], SANDBOX_WORKSPACE_MOUNT)
            self.assertFalse(any(a.startswith("HOLON_") for a in args))
            self.assertIn(f"UV_PROJECT_ENVIRONMENT={SANDBOX_UV_PROJECT_ENVIRONMENT}", args)
            self.assertFalse(any("HOLON_REPO_URL" in a or "SSH_AUTH_SOCK" in a or "GIT_CONFIG_" in a for a in args))

    @patch("sandbox_executor.cli.git_flow.sandbox_worktree_mounts", return_value=list(_WORKTREE_MOUNTS))
    @patch("sandbox_executor.launcher.mounts.get_agent_session_mounts", return_value=["-v", "/mock:/mock"])
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_run_docker_container_forward_env_vars(self, mock_which, mock_run, mock_mounts, mock_worktree_mounts):
        docker = mock_run.side_effect = DockerEnvFileRecorder()
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
            (forwarded,) = docker.env_files
            self.assertEqual(forwarded["GITHUB_TOKEN"], "my-github-token")
            self.assertEqual(forwarded["HOLON_AGENT_KEY"], "my-agent-key")
            self.assertFalse(any("my-github-token" in arg or "my-agent-key" in arg for arg in args))
            self.assertEqual(forwarded["HOLON_AGENT_EFFORT"], "high")
            self.assertEqual(forwarded["HOLON_AGENT_PROVIDER"], "anthropic")
            self.assertFalse(any(a.startswith("HOLON_") for a in args))
            programmatic_role_overrides_host = "planner"
            self.assertEqual(forwarded["HOLON_ROLE"], programmatic_role_overrides_host)
            self.assertFalse(any("SOME_OTHER_VAR" in arg for arg in args))
            self.assertNotIn("SOME_OTHER_VAR", forwarded)

    @patch("sandbox_executor.cli.git_flow.sandbox_worktree_mounts", return_value=list(_WORKTREE_MOUNTS))
    @patch("sandbox_executor.launcher.mounts.get_agent_session_mounts", return_value=["-v", "/mock:/mock"])
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_run_docker_container_hands_down_the_active_stage_remainder(
        self, mock_which, mock_run, mock_mounts, mock_worktree_mounts
    ):
        from sandbox_executor.budgets import StageTimer, active_stage_timer

        docker = mock_run.side_effect = DockerEnvFileRecorder()
        now = [1000.0]
        timer = StageTimer("execute", 2700, clock=lambda: now[0])
        now[0] += 700
        with patch.dict(os.environ, {"HOLON_STAGE_BUDGET_SECONDS": "9999"}, clear=True):
            with active_stage_timer(timer):
                run_docker_container("planner", "holon/agent-pi", ["branch"], agent_id="pi")
            self.assertEqual(docker.env_files[-1]["HOLON_STAGE_BUDGET_SECONDS"], "1940")
            self.assertFalse(any("HOLON_STAGE_BUDGET_SECONDS" in a for a in mock_run.call_args[0][0]))
        with patch.dict(os.environ, {}, clear=True):
            run_docker_container("planner", "holon/agent-pi", ["branch"], agent_id="pi")
            self.assertNotIn("HOLON_STAGE_BUDGET_SECONDS", docker.env_files[-1])
            self.assertFalse(any("HOLON_STAGE_BUDGET_SECONDS" in a for a in mock_run.call_args[0][0]))

    def test_run_docker_container_requires_agent_id(self):
        with self.assertRaises(TypeError):
            run_docker_container("planner", "holon/agent-antigravity", ["branch"])  # type: ignore[call-arg]

    @patch("sandbox_executor.cli.git_flow.sandbox_worktree_mounts", return_value=list(_WORKTREE_MOUNTS))
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_run_docker_container_intent_creator_requires_intent_file(self, mock_which, mock_worktree_mounts):
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            rc = run_docker_container("intent-creator", "holon/orchestrator", [], agent_id="antigravity")
            self.assertEqual(rc, 1)
            self.assertIn("Error: Intent file is mandatory for intent creation.", mock_stderr.getvalue())

    @patch("sandbox_executor.launcher.containers.run_docker_container", return_value=0)
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
                target_worktree="/work/flow-x",
            )
            self.host["plan_prepare"].assert_called_once_with("intent_branch_name", None, "intent_branch_name")
            self.host["plan_push"].assert_called_once_with("/work/flow-x")

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
                target_worktree="/work/flow-x",
            )
            self.host["intent_push"].assert_called_once_with("/work/flow-x")

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
                target_worktree="/work/flow-x",
            )
            self.host["execute_push"].assert_called_once_with("/work/flow-x")

    @patch("sandbox_executor.launcher.containers.run_docker_container", return_value=0)
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
                target_worktree="/work/flow-x",
            )

    def _run_execute_with_failing_push(self, dispatch_exit: int) -> tuple[int, str]:
        self.host["execute_push"].side_effect = RuntimeError("no such branch")
        argv = ["holon", "execute", "plan_branch_name", "--agent", "antigravity-agent", "--model", "m"]
        stderr = io.StringIO()
        with (
            patch("sandbox_executor.launcher.containers.run_docker_container", return_value=dispatch_exit),
            patch("sys.argv", argv),
            patch("sys.stderr", stderr),
            self.assertRaises(SystemExit) as cm,
        ):
            main()
        return cm.exception.code, stderr.getvalue()

    def test_execute_push_failure_after_a_successful_dispatch_exits_1_with_the_error(self):
        code, err = self._run_execute_with_failing_push(0)
        self.assertEqual(code, 1)
        self.assertIn("Error: no such branch", err)

    def test_execute_push_failure_after_a_failed_dispatch_keeps_the_dispatch_exit_code(self):
        code, err = self._run_execute_with_failing_push(2)
        self.assertEqual(code, 2)
        self.assertNotIn("no such branch", err)

    def test_console_script_entrypoint_registered(self):
        eps = [ep for ep in importlib.metadata.entry_points(group="console_scripts") if ep.name == "holon"]
        self.assertEqual(len(eps), 1)
        holon_ep = eps[0]
        self.assertEqual(holon_ep.value, "sandbox_executor.cli:main")
        loaded_main = holon_ep.load()
        self.assertIs(loaded_main, main)

    @patch("sandbox_executor.launcher.containers.run_docker_container", return_value=0)
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
            target_worktree="/work/flow-x",
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


class TestRunDockerContainerWorktree(unittest.TestCase):
    @patch("sandbox_executor.cli.git_flow.sandbox_worktree_mounts", return_value=list(_WORKTREE_MOUNTS))
    @patch("sandbox_executor.launcher.mounts.get_agent_session_mounts", return_value=[])
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_a_stale_repo_url_is_dropped_and_changes_nothing(self, mock_which, mock_run, mock_mounts, mock_worktree):
        docker = mock_run.side_effect = DockerEnvFileRecorder()
        stale = f"https://x-access-token:{make_gh_probe()}@github.com/myorg/myrepo.git"
        base_env = {"GITHUB_TOKEN": "secret_token", "HOLON_AGENT_EFFORT": "high"}

        def run_with(env: dict[str, str]) -> tuple[list[str], dict[str, str]]:
            with patch.dict(os.environ, env, clear=True), patch("sys.stdout", new_callable=io.StringIO):
                code = run_docker_container("planner", "img", ["branch"], agent_id="antigravity")
            self.assertEqual(code, 0)
            # why: the recorder replaces the env-file path, a fresh temporary name each run
            return docker.argvs[-1], docker.env_files[-1]

        baseline = run_with(base_env)
        with_stale = run_with({**base_env, "HOLON_REPO_URL": stale})

        self.assertEqual(with_stale, baseline)
        self.assertFalse(any("HOLON_REPO_URL" in a for a in with_stale[0]))
        self.assertNotIn("HOLON_REPO_URL", with_stale[1])

    @patch("sandbox_executor.cli.git_flow.sandbox_worktree_mounts", return_value=list(_WORKTREE_MOUNTS))
    @patch("sandbox_executor.launcher.mounts.get_agent_session_mounts", return_value=[])
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_github_token_stays_in_the_env_file_and_out_of_the_echo(self, mock_which, mock_run, mock_mounts, mock_wt):
        docker = mock_run.side_effect = DockerEnvFileRecorder()
        sample = make_gh_probe()
        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": sample}, clear=True),
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
        ):
            code = run_docker_container("planner", "img", ["branch"], agent_id="antigravity")
        self.assertEqual(code, 0)
        self.assertNotIn(sample, mock_stdout.getvalue())
        self.assertNotIn(sample, " ".join(mock_run.call_args[0][0]))
        self.assertEqual([forwarded["GITHUB_TOKEN"] for forwarded in docker.env_files], [sample])

    @patch("shutil.which", return_value="/usr/bin/docker")
    def test_a_role_without_a_valid_worktree_fails_before_docker_runs(self, mock_which):
        docker = _RecordingDocker()
        with (
            tempfile.TemporaryDirectory() as not_a_repo,
            patch.dict(os.environ, {}, clear=True),
            patch.object(subprocess, "run", docker),
        ):
            for worktree in (None, not_a_repo):
                with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                    code = run_docker_container(
                        "planner", "holon/agent-pi", ["branch"], agent_id="pi", target_worktree=worktree
                    )
                self.assertEqual(code, 1)
                self.assertIn("Error:", mock_stderr.getvalue())
        self.assertEqual(docker.argv, [])


class TestSharedRoleDispatch(unittest.TestCase):
    def setUp(self):
        self.host = _stub_host_publish(self)

    def test_normalise_agent_id_and_image_resolution(self):
        from sandbox_executor.agent_defaults import UnmappedAgentRunnerError
        from sandbox_executor.cli import normalise_agent_id, resolve_role_image

        self.assertEqual(normalise_agent_id("claude-agent"), "claude")
        self.assertEqual(normalise_agent_id("agent-codex"), "codex")
        self.assertEqual(resolve_role_image("planner", "claude"), "holon/agent-claude")
        self.assertEqual(resolve_role_image("executor", "claude-agent"), "holon/agent-claude")
        self.assertEqual(resolve_role_image("intent-creator", "claude"), "holon/orchestrator")
        # why: image selection follows agent_defaults (#93), so an unmapped runner fails instead of guessing an image
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
            self.assertEqual(mock_dispatch.call_args.kwargs["target_worktree"], "/work/flow-x")

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
            # why: the exit status passes through, so 'holon execute ... && holon review ...' stops here
            self.assertEqual(cm.exception.code, code)
            if code == OVER_BUDGET_EXIT_CODE:
                self.assertIn(message, stderr.getvalue().splitlines())
            else:
                self.assertNotIn("over its entropy budget", stderr.getvalue(), code)

    @patch("sandbox_executor.launcher.containers.run_docker_container", return_value=0)
    def test_dispatch_role_agent_uses_runner_image(self, mock_run_container):
        from sandbox_executor.cli import dispatch_role_agent

        rc = dispatch_role_agent(
            "executor",
            "claude-agent",
            "claude-opus-5-5",
            ["I-1-x/P-1/_", "claude-agent", "claude-opus-5-5"],
            target_worktree="/repo",
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
            target_worktree="/repo",
        )


def test_redact_cli_error_keeps_messages_without_url_credentials(monkeypatch):
    monkeypatch.setenv("HOLON_PROXY_URL", "http://127.0.0.1:3128")
    message = "Checkpoint file is not valid JSON: Expecting value: line 1 column 1"

    assert cli.redact_cli_error(ValueError(message)) == message


def _confine_git_lookups(monkeypatch, root):
    real_exists = os.path.exists
    root_str = str(root)

    def fake_exists(path):
        text = os.fspath(path)
        if os.path.basename(text) == ".git" and not text.startswith(root_str + os.sep):
            return False
        return real_exists(path)

    monkeypatch.setattr(os.path, "exists", fake_exists)


def _find_git_root_within_deadline(start_dir, deadline_seconds=5.0):
    import threading

    result: dict[str, str] = {}
    worker = threading.Thread(target=lambda: result.update(root=cli._find_git_root(start_dir=start_dir)), daemon=True)
    worker.start()
    worker.join(timeout=deadline_seconds)
    return not worker.is_alive(), result.get("root")


def test_find_git_root_terminates_without_a_git_ancestor(tmp_path, monkeypatch):
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    _confine_git_lookups(monkeypatch, tmp_path)
    monkeypatch.setattr(os, "getcwd", lambda: str(nested))
    monkeypatch.setattr(proxy_paths, "__file__", str(nested / "cli.py"))

    finished, root = _find_git_root_within_deadline(str(nested))

    assert finished, "_find_git_root did not return within its deadline"
    assert root == os.path.abspath(nested)


def test_find_git_root_returns_the_nearest_git_ancestor(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    nested = repo / "x" / "y"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()
    _confine_git_lookups(monkeypatch, tmp_path)

    finished, root = _find_git_root_within_deadline(str(nested))

    assert finished, "_find_git_root did not return within its deadline"
    assert root == str(repo)
