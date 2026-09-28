import io
import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor.agent_runner import (
    _check_forbidden_root,
    _clear_dir_contents,
    _handle_remove_readonly,
    cleanup_repo_dir,
)
from sandbox_executor.entrypoint import executor


class TestExecutor(unittest.TestCase):
    def test_redact_args(self):
        from sandbox_executor.entrypoint.executor import redact_args

        args = [
            "git",
            "clone",
            "https://token:secret@github.com/repo.git",
            "https://single_token@github.com/repo.git",
            "https://github.com/repo.git?token=secret123&password=mypass",
            "not-a-url",
            "--token",
            "secret_token_123",
            "--access-token",
            "access_123",
            "-p",
            "my_pass",
            "--secret",
            "top_secret",
            "--api-key",
            "key_abc",
            "--pattern",
            "*.py",
            "--author",
            "alice",
            "--path",
            "/tmp/test",
            "--auth_token",
            "at_123",
            "--client-secret",
            "cs_123",
            "--client_secret",
            "cs_456",
            "--github-token",
            "gh_token_123",
            "--private-key",
            "priv_key_456",
        ]
        redacted = redact_args(args)
        self.assertEqual(redacted[2], "https://*******@github.com/repo.git")
        self.assertEqual(redacted[3], "https://*******@github.com/repo.git")
        self.assertEqual(redacted[4], "https://github.com/repo.git?token=*******&password=*******")
        self.assertEqual(redacted[5], "not-a-url")
        self.assertEqual(redacted[6], "--token")
        self.assertEqual(redacted[7], "*******")
        self.assertEqual(redacted[8], "--access-token")
        self.assertEqual(redacted[9], "*******")
        self.assertEqual(redacted[10], "-p")
        self.assertEqual(redacted[11], "my_pass")
        self.assertEqual(redacted[12], "--secret")
        self.assertEqual(redacted[13], "*******")
        self.assertEqual(redacted[14], "--api-key")
        self.assertEqual(redacted[15], "*******")
        self.assertEqual(redacted[16], "--pattern")
        self.assertEqual(redacted[17], "*.py")
        self.assertEqual(redacted[18], "--author")
        self.assertEqual(redacted[19], "alice")
        self.assertEqual(redacted[20], "--path")
        self.assertEqual(redacted[21], "/tmp/test")
        self.assertEqual(redacted[22], "--auth_token")
        self.assertEqual(redacted[23], "*******")
        self.assertEqual(redacted[24], "--client-secret")
        self.assertEqual(redacted[25], "*******")
        self.assertEqual(redacted[26], "--client_secret")
        self.assertEqual(redacted[27], "*******")
        self.assertEqual(redacted[28], "--github-token")
        self.assertEqual(redacted[29], "*******")
        self.assertEqual(redacted[30], "--private-key")
        self.assertEqual(redacted[31], "*******")

        # When --token is followed by another flag, the positional value
        # after that flag is NOT masked (known limitation — by design).
        chained = redact_args(["--token", "--verbose", "secret"])
        self.assertEqual(chained, ["--token", "--verbose", "secret"])

        # Equal-sign formatted secret flags
        equal_fmt = redact_args(["--token=secret123", "--password=mypass", "--author=alice", "--github-token=gh123"])
        self.assertEqual(
            equal_fmt, ["--token=*******", "--password=*******", "--author=alice", "--github-token=*******"]
        )

        # Trailing secret flags at the end of args
        trailing = redact_args(["git", "clone", "--token"])
        self.assertEqual(trailing, ["git", "clone", "--token"])

        # Secret values starting with - or --
        secret_dash = redact_args(["--token", "-secret-value"])
        self.assertEqual(secret_dash, ["--token", "-secret-value"])

        # Single-character non-flag value after a secret flag should be masked
        single_char = redact_args(["cmd", "--password", "s"])
        self.assertEqual(single_char, ["cmd", "--password", "*******"])

    def test_redact_text(self):
        from sandbox_executor.entrypoint.executor import redact_text

        text = "This is a log with a secret URL: https://token:secret@github.com/repo.git and token=secret123 parameter"
        redacted = redact_text(text)
        self.assertEqual(
            redacted, "This is a log with a secret URL: https://*******@github.com/repo.git and token=******* parameter"
        )

        extended_text = (
            "api_key=secret_123 auth=abc bearer=def pat=ghi key=jkl Bearer my_jwt_token Authorization: Bearer token_xyz"
        )
        redacted_ext = redact_text(extended_text)
        expected_ext = (
            "api_key=******* auth=******* bearer=******* pat=******* "
            # Note: bare `key=` is intentionally NOT redacted to avoid over-masking non-secret
            # patterns like cache_key, sort_key, foreign_key etc. Only compound forms are matched.
            "key=jkl Bearer ******* Authorization: Bearer *******"
        )
        self.assertEqual(redacted_ext, expected_ext)

        # Quoted secrets and JSON key redaction tests
        quoted_text = 'token="secret" github_token=\'secret_abc\' {"api_key": "secret"} "auth_key": \'secret\''
        redacted_quoted = redact_text(quoted_text)
        expected_quoted = 'token="*******" github_token=\'*******\' {"api_key": "*******"} "auth_key": \'*******\''
        self.assertEqual(redacted_quoted, expected_quoted)

        # Quoted secret values with spaces
        quoted_spaces = (
            'token="secret with spaces" api_key=\'my secret key\' {"auth_token": "bearer token with spaces"}'
        )
        redacted_spaces = redact_text(quoted_spaces)
        expected_spaces = 'token="*******" api_key=\'*******\' {"auth_token": "*******"}'
        self.assertEqual(redacted_spaces, expected_spaces)

        benign_text = (
            "--pattern=*.py --author=alice --path=/tmp/test git log -p "
            "monkey=banana donkey=kong compat=1.0.0 compact=true impact=high"
        )
        self.assertEqual(redact_text(benign_text), benign_text)

        self.assertEqual(redact_text(""), "")

    def test_redact_text_oversized_input(self):
        from sandbox_executor.entrypoint.executor import _MAX_REDACT_INPUT_LEN, redact_text

        # Input exceeding the limit should be truncated and redacted.
        big_text = "token=secret " + "x" * (_MAX_REDACT_INPUT_LEN) + " tail end"
        result = redact_text(big_text)

        half_len = _MAX_REDACT_INPUT_LEN // 2

        # The head is truncated before redaction, so it takes the first half_len chars of big_text.
        # "token=secret " is 13 chars, so the head has half_len - 13 "x"s.
        # Then redaction changes "secret" (6) to "*******" (7), making it 14 + (half_len - 13) chars.
        expected_head = "token=******* " + "x" * (half_len - len("token=secret "))

        # The tail takes the last half_len chars of big_text.
        # " tail end" is 9 chars, so the tail has half_len - 9 "x"s.
        expected_tail = "x" * (half_len - len(" tail end")) + " tail end"

        expected = expected_head + "\n... (truncated) ...\n" + expected_tail
        self.assertEqual(result, expected)

    def test_should_decompose_high_entropy(self):
        plan_data = {"entropy": 6.0, "entropy_budget": 5.0, "plan_id": "P-test"}
        plan_content = "# Plan\nSome plan"
        decompose, sub_intents = executor.should_decompose(plan_data, plan_content)
        self.assertTrue(decompose)
        self.assertGreater(len(sub_intents), 0)

    def test_should_decompose_sub_intents_section(self):
        plan_data = {"entropy": 2.0, "entropy_budget": 5.0}
        plan_content = """# Plan
## Sub-Intents
- Create DB schema
- Build API routes
"""
        decompose, sub_intents = executor.should_decompose(plan_data, plan_content)
        self.assertTrue(decompose)
        self.assertEqual(len(sub_intents), 2)
        self.assertEqual(sub_intents[0]["slug"], "create-db-schema")
        self.assertEqual(sub_intents[1]["slug"], "build-api-routes")

    def test_should_not_decompose_low_entropy(self):
        plan_data = {"entropy": 2.0, "entropy_budget": 5.0}
        plan_content = "# Plan\nStandard plan without sub-intents"
        decompose, sub_intents = executor.should_decompose(plan_data, plan_content)
        self.assertFalse(decompose)
        self.assertEqual(len(sub_intents), 0)

    def test_safe_float(self):
        self.assertEqual(executor._safe_float(None, 5.0), 5.0)
        self.assertEqual(executor._safe_float(None), 0.0)
        self.assertEqual(executor._safe_float(3.14), 3.14)
        self.assertEqual(executor._safe_float("2.5"), 2.5)
        self.assertEqual(executor._safe_float("invalid", 1.0), 1.0)

    def test_should_decompose_numbered_list(self):
        plan_data = {"entropy": 2.0, "entropy_budget": 5.0}
        plan_content = """# Plan
## Sub-Intents
1. Create DB schema
2. Build API routes
3. Write unit tests
"""
        decompose, sub_intents = executor.should_decompose(plan_data, plan_content)
        self.assertTrue(decompose)
        self.assertEqual(len(sub_intents), 3)
        self.assertEqual(sub_intents[0]["slug"], "create-db-schema")
        self.assertEqual(sub_intents[1]["slug"], "build-api-routes")
        self.assertEqual(sub_intents[2]["slug"], "write-unit-tests")

    def test_should_decompose_none_entropy(self):
        plan_data = {"entropy": None, "entropy_budget": None}
        plan_content = "# Plan\nStandard plan without sub-intents"
        decompose, sub_intents = executor.should_decompose(plan_data, plan_content)
        self.assertFalse(decompose)
        self.assertEqual(len(sub_intents), 0)

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_main_execution_flow(self, mock_get_repo_url, mock_get_runner, mock_run_cmd):
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_runner.build_cmd.return_value = ["agy", "--model", "gemini-3.5-flash", "prompt"]
        mock_get_runner.return_value = mock_runner

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "OK"

        def side_effect(args, cwd=None, **kwargs):
            if "clone" in args:
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(json.dumps({"plan_id": None}) + "\n")
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            return mock_result

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory(prefix="sandbox_executor_test_") as tmp_dir,
            patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir, "HOLON_SKIP_PUSH": "1"}),
        ):
            ledger_dir = os.path.join(tmp_dir, "holon-knowledge/ledger")

            with patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]):
                executor.main()

            mock_runner.validate.assert_called_once()
            mock_runner.build_cmd.assert_called_once()
            self.assertTrue(os.path.exists(os.path.join(ledger_dir, "executions.jsonl")))
            with open(os.path.join(ledger_dir, "executions.jsonl")) as ef:
                content = ef.read()
                self.assertIn("P-123", content)
                self.assertIn("success", content)

    @patch("sandbox_executor.entrypoint.executor.shutil.rmtree")
    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_main_git_recovery_on_corrupted_repo(self, mock_get_repo_url, mock_get_runner, mock_run_cmd, mock_rmtree):
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_runner.build_cmd.return_value = ["agy", "--model", "gemini-3.5-flash", "prompt"]
        mock_get_runner.return_value = mock_runner

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            if "clone" in args:
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
                mock_res.returncode = 0
                mock_res.stdout = "OK"
            elif args == ["git", "remote", "get-url", "origin"]:
                mock_res.returncode = 0
                mock_res.stdout = "/mock/repo"
            elif args == ["git", "rev-parse", "--is-inside-work-tree"]:
                # Simulate corrupted git repository after agent execution
                mock_res.returncode = 1
                mock_res.stdout = ""
                mock_res.stderr = "fatal: not a git repository"
            elif args == ["git", "rev-parse", "FETCH_HEAD"]:
                mock_res.returncode = 0
                mock_res.stdout = "mock_tip_hash_12345"
            else:
                mock_res.returncode = 0
                mock_res.stdout = "OK"
                mock_res.stderr = ""
            return mock_res

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory(prefix="sandbox_executor_test_") as tmp_dir,
            patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir, "HOLON_SKIP_PUSH": "1"}),
            patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
        ):
            # Create dummy .git directory to test removal/re-init
            git_dot = os.path.join(tmp_dir, ".git")
            os.makedirs(git_dot, exist_ok=True)

            with patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]):
                executor.main()

            self.assertIn("Warning: git repository invalid or missing after agent execution", mock_stderr.getvalue())

            # Verify that shutil.rmtree was NOT called on .git
            mock_rmtree.assert_not_called()

            # Verify backup directory .git-unusable-* was created
            backup_dirs = [d for d in os.listdir(tmp_dir) if d.startswith(".git-unusable-")]
            self.assertEqual(len(backup_dirs), 1)

            # Verify .git/info/exclude contains the backup directory name
            exclude_path = os.path.join(tmp_dir, ".git", "info", "exclude")
            self.assertTrue(os.path.exists(exclude_path))
            with open(exclude_path) as ef:
                content = ef.read()
                self.assertIn(backup_dirs[0], content)
                self.assertIn(".git-unusable-*", content)

            # Verify that recovery commands were executed in sequence
            called_cmds = [call.args[0] for call in mock_run_cmd.call_args_list if call.args]
            self.assertIn(["git", "init"], called_cmds)
            self.assertIn(["git", "remote", "add", "origin", "/mock/repo"], called_cmds)
            self.assertTrue(any(cmd[:4] == ["git", "fetch", "--no-tags", "origin"] for cmd in called_cmds))
            self.assertTrue(any(cmd[:2] == ["git", "reset"] for cmd in called_cmds))

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_main_decomposition_flow(self, mock_get_repo_url, mock_get_runner, mock_run_cmd):
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_get_runner.return_value = mock_runner

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        def side_effect(args, cwd=None, **kwargs):
            if "clone" in args:
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 8.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
                with open(os.path.join(ledger_dir, "intents.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "branch": "I-456/_",
                                "slug": "parent-intent",
                            }
                        )
                        + "\n"
                    )
            return mock_result

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory(prefix="sandbox_executor_test_") as tmp_dir,
            patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir, "HOLON_SKIP_PUSH": "1"}),
        ):
            ledger_dir = os.path.join(tmp_dir, "holon-knowledge/ledger")

            with patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]):
                executor.main()

            mock_runner.validate.assert_called_once()
            self.assertTrue(os.path.exists(os.path.join(ledger_dir, "executions.jsonl")))
            with open(os.path.join(ledger_dir, "executions.jsonl")) as ef:
                content = ef.read()
                self.assertIn("P-123", content)
                self.assertIn("decomposed", content)
            self.assertTrue(os.path.exists(os.path.join(ledger_dir, "intents.jsonl")))
            with open(os.path.join(ledger_dir, "intents.jsonl")) as inf:
                content = inf.read()
                self.assertIn("sub-intent-part-1", content)

    def test_run_cmd_raises_called_process_error(self):
        with self.assertRaises(subprocess.CalledProcessError) as ctx:
            executor.run_cmd(["false"], check=True)
        self.assertEqual(ctx.exception.returncode, 1)

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_main_custom_holon_repo_dir_not_deleted(self, mock_get_repo_url, mock_get_runner, mock_run_cmd):
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_get_runner.return_value = mock_runner
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_run_cmd.return_value = mock_result

        with tempfile.TemporaryDirectory() as tmp_dir:
            custom_dir = os.path.join(tmp_dir, "custom_repo")
            os.makedirs(custom_dir, exist_ok=True)
            with (
                patch.dict(os.environ, {"HOLON_REPO_DIR": custom_dir, "HOLON_SKIP_PUSH": "1"}),
                patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]),
            ):
                executor.main()

            self.assertTrue(os.path.exists(custom_dir))

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    @patch("sandbox_executor.agent_runner.os.path.expanduser")
    @patch("sandbox_executor.agent_runner._rmtree")
    def test_main_default_workspace_deleted(
        self, mock_rmtree, mock_expanduser, mock_get_repo_url, mock_get_runner, mock_run_cmd
    ):
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_get_runner.return_value = mock_runner
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_run_cmd.return_value = mock_result

        with tempfile.TemporaryDirectory() as tmp_dir:
            default_dir = os.path.join(tmp_dir, "repo")
            mock_expanduser.return_value = default_dir
            os.makedirs(default_dir, exist_ok=True)

            env = os.environ.copy()
            if "HOLON_REPO_DIR" in env:
                del env["HOLON_REPO_DIR"]
            env["HOLON_SKIP_PUSH"] = "1"

            with (
                patch.dict(os.environ, env, clear=True),
                patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]),
            ):
                executor.main()

            mock_rmtree.assert_any_call(default_dir)
            for call_args in mock_rmtree.call_args_list:
                cleaned_path = os.path.abspath(call_args.args[0])
                self.assertTrue(
                    cleaned_path.startswith(os.path.abspath(tmp_dir)),
                    f"Invariant violated: _rmtree called on {cleaned_path} outside fixture {tmp_dir}",
                )

            mock_run_cmd_args = [call.args[0] for call in mock_run_cmd.call_args_list if call.args]
            self.assertTrue(any("add" in cmd and "-A" in cmd for cmd in mock_run_cmd_args))
            self.assertTrue(any("status" in cmd for cmd in mock_run_cmd_args))

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_main_raises_exception_on_failure(self, mock_get_repo_url, mock_get_runner, mock_run_cmd):
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_get_runner.return_value = mock_runner
        mock_run_cmd.side_effect = subprocess.CalledProcessError(1, ["git", "clone"])

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir}),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]),
            patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                executor.main()
            self.assertIn("Execution failed:", mock_stderr.getvalue())

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    @patch("sandbox_executor.agent_runner.os.path.expanduser")
    @patch("sandbox_executor.agent_runner._rmtree")
    @patch("sandbox_executor.agent_runner.os.path.lexists", return_value=True)
    @patch("sandbox_executor.agent_runner.os.path.ismount", return_value=False)
    @patch("sandbox_executor.agent_runner.os.path.islink", return_value=False)
    def test_main_raises_runtime_error_on_cleanup_failure(
        self,
        mock_islink,
        mock_ismount,
        mock_lexists,
        mock_rmtree,
        mock_expanduser,
        mock_get_repo_url,
        mock_get_runner,
        mock_run_cmd,
    ):
        mock_rmtree.side_effect = PermissionError("Permission denied")
        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_expanduser.return_value = tmp_dir
            env = os.environ.copy()
            env.pop("HOLON_REPO_DIR", None)
            with (
                patch.dict(os.environ, env, clear=True),
                patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    executor.main()
                self.assertIn("Failed to clean up existing repo dir", str(ctx.exception))
                for call_args in mock_rmtree.call_args_list:
                    cleaned_path = os.path.abspath(call_args.args[0])
                    self.assertTrue(
                        cleaned_path.startswith(os.path.abspath(tmp_dir)),
                        f"Invariant violated: _rmtree called on {cleaned_path} outside fixture {tmp_dir}",
                    )

    def test_clear_dir_contents(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sub_dir = os.path.join(tmp_dir, "subdir")
            file_path = os.path.join(tmp_dir, "test.txt")
            os.makedirs(sub_dir, exist_ok=True)
            with open(file_path, "w") as f:
                f.write("hello")

            _clear_dir_contents(tmp_dir)
            self.assertTrue(os.path.exists(tmp_dir))
            self.assertEqual(os.listdir(tmp_dir), [])

            # Test guard clause when path is a file, not a directory
            with open(file_path, "w") as f:
                f.write("hello")
            _clear_dir_contents(file_path)
            self.assertTrue(os.path.exists(file_path))

    @patch("sandbox_executor.agent_runner.os.path.lexists", return_value=True)
    def test_cleanup_repo_dir_forbidden_root(self, mock_lexists):
        with self.assertRaises(RuntimeError) as ctx:
            cleanup_repo_dir("/etc", raise_on_error=True)
        self.assertIn("Refusing to perform operation on system root-level directory", str(ctx.exception))

        with self.assertRaises(RuntimeError) as ctx2:
            cleanup_repo_dir("/etc/apt", raise_on_error=True)
        self.assertIn("Refusing to perform operation on system root-level directory", str(ctx2.exception))

        with self.assertRaises(RuntimeError) as ctx3:
            cleanup_repo_dir("/usr/bin", raise_on_error=True)
        self.assertIn("Refusing to perform operation on system root-level directory", str(ctx3.exception))

        with self.assertRaises(RuntimeError) as ctx_sys:
            cleanup_repo_dir("/private/var/log", raise_on_error=True)
        self.assertIn("Refusing to perform operation on system root-level directory", str(ctx_sys.exception))

    @patch("sandbox_executor.agent_runner.os.remove")
    @patch("sandbox_executor.agent_runner.os.path.isdir", return_value=False)
    @patch("sandbox_executor.agent_runner.os.path.islink", return_value=False)
    @patch("sandbox_executor.agent_runner.os.path.ismount", return_value=False)
    @patch("sandbox_executor.agent_runner.os.path.lexists", return_value=True)
    def test_cleanup_repo_dir_regular_file(self, mock_lexists, mock_ismount, mock_islink, mock_isdir, mock_remove):
        cleanup_repo_dir("/tmp/repo_file.txt", raise_on_error=True)
        mock_remove.assert_called_once_with("/tmp/repo_file.txt")

    @patch("sandbox_executor.agent_runner.os.path.isdir", return_value=True)
    def test_clear_dir_contents_forbidden_roots(self, mock_isdir):
        with self.assertRaises(RuntimeError) as ctx:
            _clear_dir_contents("/etc", raise_on_error=True)
        self.assertIn("Refusing to perform operation on system root-level directory", str(ctx.exception))

        with self.assertRaises(RuntimeError) as ctx2:
            _clear_dir_contents("/etc/apt", raise_on_error=True)
        self.assertIn("Refusing to perform operation on system root-level directory", str(ctx2.exception))

        with self.assertRaises(RuntimeError) as ctx3:
            _clear_dir_contents("/usr/bin", raise_on_error=True)
        self.assertIn("Refusing to perform operation on system root-level directory", str(ctx3.exception))

        with self.assertRaises(RuntimeError) as ctx_sys:
            _clear_dir_contents("/private/var/log", raise_on_error=True)
        self.assertIn("Refusing to perform operation on system root-level directory", str(ctx_sys.exception))

    def test_check_forbidden_root_blocked_paths(self):
        # Paths outside the safelist should raise RuntimeError
        with self.assertRaises(RuntimeError) as ctx:
            _check_forbidden_root("/var")
        self.assertIn("Refusing to perform operation on system root-level directory", str(ctx.exception))

        with self.assertRaises(RuntimeError) as ctx2:
            _check_forbidden_root("/etc/apt")
        self.assertIn("Refusing to perform operation on system root-level directory", str(ctx2.exception))

        with self.assertRaises(RuntimeError) as ctx3:
            _check_forbidden_root("/opt/workspace")
        self.assertIn("Refusing to perform operation on system root-level directory", str(ctx3.exception))

    def test_check_forbidden_root_mac_var_folders_allowed(self):
        # Should not raise any error
        _check_forbidden_root("/private/var/folders/xx/yyyy/T/workspace")

    def test_check_forbidden_root_linux_var_allowed(self):
        # Should not raise any error
        _check_forbidden_root("/var/tmp/workspace")

        with self.assertRaises(RuntimeError) as ctx:
            _check_forbidden_root("/var/log")
        self.assertIn("Refusing to perform operation on system root-level directory", str(ctx.exception))

    @patch("sandbox_executor.agent_runner.os.path.realpath")
    @patch("sandbox_executor.agent_runner.os.path.abspath")
    def test_check_forbidden_root_symlinks(self, mock_abspath, mock_realpath):
        # Even if abs_path is allowed, realpath being forbidden should trigger rejection
        mock_abspath.return_value = "/var/folders/etc_symlink"
        mock_realpath.return_value = "/private/etc"
        with self.assertRaises(RuntimeError) as ctx:
            _check_forbidden_root("/var/folders/etc_symlink")
        self.assertIn("Refusing to perform operation on system root-level directory", str(ctx.exception))

    @patch("sandbox_executor.agent_runner.os.listdir", return_value=[])
    @patch("sandbox_executor.agent_runner.os.path.isdir", return_value=True)
    def test_clear_dir_contents_allowed_roots(self, mock_isdir, mock_listdir):
        # Should not raise any error
        _clear_dir_contents("/home/user/workspace/repo", raise_on_error=True)
        mock_listdir.assert_called_once_with("/home/user/workspace/repo")

    def test_clear_dir_contents_raise_on_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sub_file = os.path.join(tmp_dir, "test.txt")
            with open(sub_file, "w") as f:
                f.write("test")
            unlink_patch = patch(
                "sandbox_executor.agent_runner.os.unlink", side_effect=PermissionError("Permission denied")
            )
            with unlink_patch:
                # Default raise_on_error=False swallows error
                _clear_dir_contents(tmp_dir, raise_on_error=False)
                # raise_on_error=True propagates error
                with self.assertRaises(PermissionError):
                    _clear_dir_contents(tmp_dir, raise_on_error=True)

    @patch("sandbox_executor.agent_runner.os.chmod")
    @patch("sandbox_executor.agent_runner.os.unlink", side_effect=PermissionError("Permission denied"))
    @patch("sandbox_executor.agent_runner.os.path.islink", return_value=True)
    @patch("sandbox_executor.agent_runner.os.listdir", return_value=["symlink_item"])
    @patch("sandbox_executor.agent_runner.os.path.isdir", return_value=True)
    def test_clear_dir_contents_symlink_permission_error(
        self, mock_isdir, mock_listdir, mock_islink, mock_unlink, mock_chmod
    ):
        _clear_dir_contents("/tmp/workspace_dir")
        mock_chmod.assert_not_called()

    @patch("sandbox_executor.agent_runner.os.chmod")
    @patch("sandbox_executor.agent_runner.os.path.isdir")
    def test_handle_remove_readonly(self, mock_isdir, mock_chmod):
        import stat

        mock_func = MagicMock()

        # Test for directory
        mock_isdir.return_value = True
        _handle_remove_readonly(mock_func, "/fake/dir", PermissionError("error"))
        mock_chmod.assert_called_with("/fake/dir", stat.S_IWUSR | stat.S_IRUSR | stat.S_IXUSR, follow_symlinks=False)
        mock_func.assert_called_with("/fake/dir")

        mock_chmod.reset_mock()
        mock_func.reset_mock()

        # Test for file
        mock_isdir.return_value = False
        _handle_remove_readonly(mock_func, "/fake/file.txt", PermissionError("error"))
        mock_chmod.assert_called_with("/fake/file.txt", stat.S_IWUSR | stat.S_IRUSR, follow_symlinks=False)
        mock_func.assert_called_with("/fake/file.txt")

    @patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir")
    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    @patch("sandbox_executor.agent_runner.os.path.expanduser")
    def test_main_keep_workspace(self, mock_expanduser, mock_get_repo_url, mock_get_runner, mock_run_cmd, mock_cleanup):
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_get_runner.return_value = mock_runner
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_run_cmd.return_value = mock_result

        with tempfile.TemporaryDirectory() as tmp_dir:
            default_dir = os.path.join(tmp_dir, "repo")
            mock_expanduser.return_value = default_dir
            os.makedirs(default_dir, exist_ok=True)

            env = os.environ.copy()
            if "HOLON_REPO_DIR" in env:
                del env["HOLON_REPO_DIR"]
            env["HOLON_SKIP_PUSH"] = "1"
            env["HOLON_KEEP_WORKSPACE"] = "true"

            with (
                patch.dict(os.environ, env, clear=True),
                patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]),
            ):
                executor.main()

            mock_cleanup.assert_not_called()

    @patch("sandbox_executor.entrypoint.executor.os.path.exists")
    @patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir")
    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    @patch("sandbox_executor.agent_runner.os.path.expanduser")
    def test_main_keep_workspace_existing_git(
        self, mock_expanduser, mock_get_repo_url, mock_get_runner, mock_run_cmd, mock_cleanup, mock_exists
    ):
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_get_runner.return_value = mock_runner

        import genericpath

        mock_exists.side_effect = lambda p: False if p == "/.dockerenv" else genericpath.exists(p)

        def _run_cmd_side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            mock_res.returncode = 0
            # Return the correct remote URL so the workspace reuse path is followed.
            mock_res.stdout = "/mock/repo" if args == ["git", "remote", "get-url", "origin"] else ""
            return mock_res

        mock_run_cmd.side_effect = _run_cmd_side_effect

        with tempfile.TemporaryDirectory() as tmp_dir:
            default_dir = os.path.join(tmp_dir, "repo")
            git_dir = os.path.join(default_dir, ".git")
            mock_expanduser.return_value = default_dir
            os.makedirs(git_dir, exist_ok=True)

            env = os.environ.copy()
            if "HOLON_REPO_DIR" in env:
                del env["HOLON_REPO_DIR"]
            if "HOLON_ROLE" in env:
                del env["HOLON_ROLE"]
            if "HOLON_IN_SANDBOX" in env:
                del env["HOLON_IN_SANDBOX"]
            env["HOLON_SKIP_PUSH"] = "1"
            env["HOLON_KEEP_WORKSPACE"] = "true"
            if "USER" in env:
                del env["USER"]
            if "USERNAME" in env:
                del env["USERNAME"]

            with (
                patch.dict(os.environ, env, clear=True),
                patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]),
                patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
            ):
                executor.main()
                self.assertIn("Warning: Reusing workspace at", mock_stderr.getvalue())

            mock_cleanup.assert_not_called()
            called_cmds = [call.args[0] for call in mock_run_cmd.call_args_list if call.args]
            fetch_idx = next(
                i for i, cmd in enumerate(called_cmds) if cmd == ["git", "fetch", "/mock/repo", "I-456/P-123/_"]
            )
            clean_idx = next(i for i, cmd in enumerate(called_cmds) if cmd == ["git", "clean", "-fd"])
            checkout_idx = next(
                i
                for i, cmd in enumerate(called_cmds)
                if cmd == ["git", "checkout", "-f", "-B", "I-456/P-123/_", "FETCH_HEAD"]
            )
            self.assertTrue(fetch_idx < clean_idx < checkout_idx)
            self.assertFalse(any("clone" in cmd for cmd in called_cmds))

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    @patch("sandbox_executor.agent_runner.os.path.expanduser")
    @patch("sandbox_executor.agent_runner._clear_dir_contents")
    @patch("sandbox_executor.agent_runner.os.path.ismount", return_value=True)
    def test_main_mount_point_clears_contents(
        self, mock_ismount, mock_clear_dir_contents, mock_expanduser, mock_get_repo_url, mock_get_runner, mock_run_cmd
    ):
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_get_runner.return_value = mock_runner
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_run_cmd.return_value = mock_result

        with tempfile.TemporaryDirectory() as tmp_dir:
            default_dir = os.path.join(tmp_dir, "repo")
            mock_expanduser.return_value = default_dir
            os.makedirs(default_dir, exist_ok=True)

            env = os.environ.copy()
            if "HOLON_REPO_DIR" in env:
                del env["HOLON_REPO_DIR"]
            env["HOLON_SKIP_PUSH"] = "1"

            with (
                patch.dict(os.environ, env, clear=True),
                patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]),
            ):
                executor.main()

            self.assertTrue(mock_clear_dir_contents.called)
            mock_clear_dir_contents.assert_any_call(default_dir, raise_on_error=True)
            for call_args in mock_clear_dir_contents.call_args_list:
                cleared_path = os.path.abspath(call_args.args[0])
                self.assertTrue(
                    cleared_path.startswith(os.path.abspath(tmp_dir)),
                    f"Invariant violated: _clear_dir_contents called on {cleared_path} outside fixture {tmp_dir}",
                )

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_main_git_add_not_called_on_failure(self, mock_get_repo_url, mock_get_runner, mock_run_cmd):
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            if "clone" in args:
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            if "agy" in args:
                mock_res.returncode = 1
                mock_res.stdout = "Failure stdout"
                mock_res.stderr = "Failure stderr"
            else:
                mock_res.returncode = 0
                mock_res.stdout = ""
                mock_res.stderr = ""
            return mock_res

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir, "HOLON_SKIP_PUSH": "1"}),
        ):
            with patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]):
                executor.main()

            mock_run_cmd_args = [call.args[0] for call in mock_run_cmd.call_args_list if call.args]
            self.assertFalse(any("add" in cmd and "-A" in cmd for cmd in mock_run_cmd_args))

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_agent_output_captured_on_failure(self, mock_get_repo_url, mock_get_runner, mock_run_cmd):
        """Test that failing agent runs capture bounded diagnostic output into execution record."""
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        diagnostic_line = "printmode.go:521] Print mode: timed out after 1488 polls"

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            if "clone" in args:
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            if "agy" in args:
                mock_res.returncode = 1
                mock_res.stdout = "Starting agent execution...\n"
                mock_res.stderr = f"CRITICAL: {diagnostic_line}\n"
            else:
                mock_res.returncode = 0
                mock_res.stdout = ""
                mock_res.stderr = ""
            return mock_res

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir, "HOLON_SKIP_PUSH": "1"}),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir"),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            exec_dir = os.path.join(tmp_dir, "executions")
            self.assertTrue(os.path.isdir(exec_dir))
            exec_files = os.listdir(exec_dir)
            self.assertEqual(len(exec_files), 1)
            with open(os.path.join(exec_dir, exec_files[0])) as ef:
                record = ef.read()

            self.assertIn("## Status\nFailure", record)
            self.assertIn("## Summary\nPlan execution failed with exit code 1", record)
            self.assertIn("## Agent Output", record)
            self.assertIn(diagnostic_line, record)

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_agent_output_truncation_marker_and_byte_budget(self, mock_get_repo_url, mock_get_runner, mock_run_cmd):
        """Test that agent output exceeding HOLON_AGENT_LOG_BYTES is truncated with explicit marker."""
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        head_marker = "EARLY_OUTPUT_HEAD_MARKER_" * 20
        tail_marker = "LATE_OUTPUT_TAIL_DIAGNOSTIC_MARKER"
        large_output = head_marker + ("\n" + "x" * 100) * 30 + "\n" + tail_marker + "\n"

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            if "clone" in args:
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            if "agy" in args:
                mock_res.returncode = 1
                mock_res.stdout = large_output
                mock_res.stderr = ""
            else:
                mock_res.returncode = 0
                mock_res.stdout = ""
                mock_res.stderr = ""
            return mock_res

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(
                os.environ,
                {"HOLON_REPO_DIR": tmp_dir, "HOLON_SKIP_PUSH": "1", "HOLON_AGENT_LOG_BYTES": "1024"},
            ),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir"),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            exec_dir = os.path.join(tmp_dir, "executions")
            exec_files = os.listdir(exec_dir)
            with open(os.path.join(exec_dir, exec_files[0])) as ef:
                record = ef.read()

            self.assertIn("[Agent output truncated:", record)
            notice = re.search(r"\[Agent output truncated: (\d+) bytes dropped; showing tail (\d+) bytes\]", record)
            self.assertIsNotNone(notice, "truncation notice must report both byte counts")
            tail_kept = int(notice.group(2))
            self.assertLessEqual(tail_kept, 1024, "kept tail must stay within HOLON_AGENT_LOG_BYTES")
            self.assertGreater(tail_kept, 0, "bounding must retain the newest output")
            self.assertIn(tail_marker, record)
            self.assertNotIn("EARLY_OUTPUT_HEAD_MARKER_", record)

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_agent_output_secret_redaction(self, mock_get_repo_url, mock_get_runner, mock_run_cmd):
        """Test that literal token values from GITHUB_TOKEN, GH_TOKEN, and HOLON_AGENT_KEY are stripped."""
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        secret_gh = "ghp_secretTokenVal12345"
        secret_gh_tok = "gho_otherTokenVal67890"
        secret_key = "ak_superSecretAgentKey"

        agent_stdout = f"Agent started with token {secret_gh} and GH_TOKEN={secret_gh_tok}\n"
        agent_stderr = f"Agent failed authentication: key={secret_key}\n"

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            if "clone" in args:
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            if "agy" in args:
                mock_res.returncode = 1
                mock_res.stdout = agent_stdout
                mock_res.stderr = agent_stderr
            else:
                mock_res.returncode = 0
                mock_res.stdout = ""
                mock_res.stderr = ""
            return mock_res

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(
                os.environ,
                {
                    "HOLON_REPO_DIR": tmp_dir,
                    "HOLON_SKIP_PUSH": "1",
                    "GITHUB_TOKEN": secret_gh,
                    "GH_TOKEN": secret_gh_tok,
                    "HOLON_AGENT_KEY": secret_key,
                },
            ),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir"),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            exec_dir = os.path.join(tmp_dir, "executions")
            exec_files = os.listdir(exec_dir)
            with open(os.path.join(exec_dir, exec_files[0])) as ef:
                record = ef.read()

            with open(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl")) as lf:
                ledger_content = lf.read()

            for secret in (secret_gh, secret_gh_tok, secret_key):
                self.assertNotIn(secret, record, f"Secret {secret} leaked into execution markdown record!")
                self.assertNotIn(secret, ledger_content, f"Secret {secret} leaked into executions.jsonl!")

            self.assertIn("*******", record)

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_agent_output_truncation_never_severs_secret(self, mock_get_repo_url, mock_get_runner, mock_run_cmd):
        """Test that the byte-tail cut cannot strand a credential fragment in the committed record.

        Regression: bounding the tail before redacting severed a secret straddling the boundary,
        and neither the literal sweep (which matches whole values only) nor the key-name regex
        (whose anchor the same cut also breaks) could remove what survived on the kept side.
        """
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        budget = 1024
        secret = "ghp_StraddleS3cr3tXYzWq7Q4tR9LMNop"
        straddle = 5
        # boundary = len(output) - budget must land `straddle` bytes into the secret, which starts
        # after the "GH_TOKEN=" anchor: suffix_len = budget + straddle - len(secret) - 1.
        prefix = "noise line\n" * 400
        suffix = "y" * (budget + straddle - len(secret) - 1)
        agent_stdout = f"{prefix}GH_TOKEN={secret}\n{suffix}"
        self.assertEqual(
            len(agent_stdout) - budget,
            len(prefix) + len("GH_TOKEN=") + straddle,
            "Test geometry must put the truncation boundary inside the secret.",
        )

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            if "clone" in args:
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            if "agy" in args:
                mock_res.returncode = 1
                mock_res.stdout = agent_stdout
                mock_res.stderr = ""
            else:
                mock_res.returncode = 0
                mock_res.stdout = ""
                mock_res.stderr = ""
            return mock_res

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(
                os.environ,
                {
                    "HOLON_REPO_DIR": tmp_dir,
                    "HOLON_SKIP_PUSH": "1",
                    "HOLON_AGENT_LOG_BYTES": str(budget),
                    "GITHUB_TOKEN": secret,
                    "GH_TOKEN": secret,
                    "HOLON_AGENT_KEY": secret,
                },
            ),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir"),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            exec_dir = os.path.join(tmp_dir, "executions")
            exec_files = os.listdir(exec_dir)
            with open(os.path.join(exec_dir, exec_files[0])) as ef:
                record = ef.read()
            with open(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl")) as lf:
                ledger_content = lf.read()

            self.assertIn("[Agent output truncated:", record)
            self.assertNotIn(secret, record)
            for start in range(len(secret) - 7):
                window = secret[start : start + 8]
                self.assertNotIn(window, record, f"Secret fragment {window!r} leaked into the execution record.")
                self.assertNotIn(window, ledger_content, f"Secret fragment {window!r} leaked into the ledger row.")

    def test_agent_output_byte_bound_never_strands_secret_fragment(self):
        """Test that no credential fragment survives the pipeline at any straddle offset.

        Also pins why the ordering and the line-aligned cut both matter: a raw byte cut followed by
        redaction strands a fragment for a non-empty set of offsets.
        """
        from sandbox_executor.entrypoint.executor import redact_agent_secrets, sanitize_agent_output

        budget = 256
        secret = "ghp_StraddleS3cr3tXYzWq7Q4tR9LMNop"
        windows = [secret[start : start + 8] for start in range(len(secret) - 7)]

        truncated_cases = 0
        severed_offsets = []
        with patch.dict(os.environ, {"GITHUB_TOKEN": secret, "GH_TOKEN": secret}):
            for offset in range(1, len(secret) - 1):
                suffix = "y" * (budget + offset - len(secret) - 1)
                stream = f"GH_TOKEN={secret}\n{suffix}"
                self.assertGreater(len(stream), budget, "the byte budget must be exceeded for this case")

                # Shipped order: redact the whole stream, then bound, then sweep again.
                bounded, truncated, _dropped = sanitize_agent_output(redact_agent_secrets(stream), budget)
                truncated_cases += 1 if truncated else 0
                result = redact_agent_secrets(bounded)
                for window in windows:
                    self.assertNotIn(window, result, f"Secret fragment {window!r} survived at offset {offset}.")

                # Counter-example, kept so this test cannot silently become vacuous: the superseded
                # raw byte cut followed by redaction, emulated directly because sanitize_agent_output
                # now refuses to split a line.
                raw_cut = stream.encode()[-budget:].decode("utf-8", errors="replace")
                if any(w in redact_agent_secrets(raw_cut) for w in windows):
                    severed_offsets.append(offset)

        self.assertGreater(truncated_cases, 0, "some offsets must actually exercise truncation")
        self.assertTrue(
            severed_offsets,
            "bounding before redacting must demonstrably strand a fragment, or this test proves nothing",
        )

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    @patch("sandbox_executor.entrypoint.executor.sanitize_agent_output")
    def test_agent_output_logging_failure_fault_tolerance(
        self, mock_sanitize, mock_get_repo_url, mock_get_runner, mock_run_cmd
    ):
        """Test that logging/sanitizing failure does not alter execution status or raise out of main."""
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        mock_sanitize.side_effect = RuntimeError("Disk failure")

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            if "clone" in args:
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            if "agy" in args:
                mock_res.returncode = 1
                mock_res.stdout = "Failure output"
                mock_res.stderr = "Error details"
            else:
                mock_res.returncode = 0
                mock_res.stdout = ""
                mock_res.stderr = ""
            return mock_res

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir, "HOLON_SKIP_PUSH": "1"}),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir"),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            # Assert executor.main() does not raise
            executor.main()

            with open(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl")) as lf:
                entry = json.loads(lf.readline())
            self.assertEqual(entry["status"], "failure")
            self.assertEqual(entry["summary"], "Plan execution failed with exit code 1")
            self.assertFalse(entry["agent_output_truncated"])
            self.assertEqual(entry["agent_output_bytes"], 0)

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_executions_ledger_optional_keys(self, mock_get_repo_url, mock_get_runner, mock_run_cmd):
        """Test that executions.jsonl records optional keys without breaking schema invariants."""
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            if "clone" in args:
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            if "agy" in args:
                mock_res.returncode = 0
                mock_res.stdout = "Task complete.\n"
                mock_res.stderr = ""
            else:
                mock_res.returncode = 0
                mock_res.stdout = ""
                mock_res.stderr = ""
            return mock_res

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir, "HOLON_SKIP_PUSH": "1"}),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir"),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            with open(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl")) as lf:
                entry = json.loads(lf.readline())

            required_keys = {
                "execution_id",
                "plan_branch",
                "agent",
                "agent_version",
                "model",
                "status",
                "summary",
                "execution_file",
                "created_at",
            }
            self.assertTrue(required_keys.issubset(entry.keys()))
            self.assertIn("agent_output_truncated", entry)
            self.assertIn("agent_output_bytes", entry)
            self.assertIsInstance(entry["agent_output_truncated"], bool)
            self.assertIsInstance(entry["agent_output_bytes"], int)
            self.assertFalse(entry["agent_output_truncated"])
            self.assertGreater(entry["agent_output_bytes"], 0)

    def test_sanitize_agent_output_aligns_tail_to_line_boundary(self):
        """Test that truncation advances to a line boundary instead of splitting a line in half."""
        from sandbox_executor.entrypoint.executor import sanitize_agent_output

        # A newline 59 bytes into the retained window: the tail must start on the next line.
        raw = "a" * 300 + "\n" + "b" * 40
        out, trunc, dropped = sanitize_agent_output(raw, max_bytes=100)
        self.assertTrue(trunc)
        self.assertEqual(dropped, len(raw) - 40)
        self.assertTrue(out.startswith("[Agent output truncated: 301 bytes dropped; showing tail 40 bytes]\n"))
        self.assertEqual(out.split("\n", 1)[1], "b" * 40)

        # A line with no newline inside the look-ahead window keeps the raw byte cut.
        out, trunc, dropped = sanitize_agent_output("z" * 300, max_bytes=100)
        self.assertTrue(trunc)
        self.assertEqual(dropped, 200)
        self.assertTrue(out.endswith("z" * 100))

    def test_agent_output_bound_retains_budget_of_newest_output(self):
        """Test that bounding keeps a byte budget worth of newest output on very large streams.

        Regression guard: redacting the whole stream before bounding let redact_text's input cap
        discard the newest diagnostics, retaining ~50 KB of a 64 KB budget on large agent output.
        """
        from sandbox_executor.entrypoint.executor import (
            redact_agent_secrets,
            redact_env_literals,
            sanitize_agent_output,
        )

        budget = 65536
        align_window = 4096  # executor._TAIL_ALIGN_WINDOW, inlined so this holds across revisions
        stream = "noise line 000000\n" * 12000  # ~204 KB, well above redact_text's 100k input cap
        self.assertGreater(len(stream), 100_000)

        bounded, trunc, _dropped = sanitize_agent_output(redact_env_literals(stream), budget)
        self.assertTrue(trunc)
        kept = redact_agent_secrets(bounded)

        expected_tail = stream[-(budget - align_window) :]
        self.assertTrue(
            kept.endswith(expected_tail),
            "the byte budget must be filled with the newest output, not an input-capped fraction of it",
        )

    def test_sanitize_agent_output_helper(self):
        """Test sanitize_agent_output behavior for budget boundaries and markers."""
        from sandbox_executor.entrypoint.executor import sanitize_agent_output

        # Below limit
        out, trunc, dropped = sanitize_agent_output("short output", max_bytes=100)
        self.assertEqual(out, "short output")
        self.assertFalse(trunc)
        self.assertEqual(dropped, 0)

        # Above limit
        raw = "0123456789" * 10  # 100 bytes
        out, trunc, dropped = sanitize_agent_output(raw, max_bytes=20)
        self.assertTrue(trunc)
        self.assertEqual(dropped, 80)
        self.assertTrue(out.startswith("[Agent output truncated: 80 bytes dropped; showing tail 20 bytes]\n"))
        self.assertTrue(out.endswith(raw[-20:]))

        # Default fallback for zero/negative budget
        out, trunc, dropped = sanitize_agent_output("test", max_bytes=0)
        self.assertEqual(out, "test")
        self.assertFalse(trunc)

    def test_redact_agent_secrets_helper(self):
        """Test redact_agent_secrets strips both regex patterns and literal tokens."""
        from sandbox_executor.entrypoint.executor import redact_agent_secrets

        env = {
            "GITHUB_TOKEN": "ghp_alpha123",
            "GH_TOKEN": "gho_beta456",
            "HOLON_AGENT_KEY": "ak_gamma789",
        }
        with patch.dict(os.environ, env):
            sample = (
                "Call: https://x-access-token:ghp_alpha123@github.com/repo.git\n"
                "Literal: ghp_alpha123 and gho_beta456 and ak_gamma789\n"
                "Header: Bearer custom_secret_bearer_token\n"
            )
            redacted = redact_agent_secrets(sample)
            self.assertNotIn("ghp_alpha123", redacted)
            self.assertNotIn("gho_beta456", redacted)
            self.assertNotIn("ak_gamma789", redacted)
            self.assertNotIn("custom_secret_bearer_token", redacted)
            self.assertIn("*******", redacted)

    def _create_bare_remote_with_plan(self, base_dir: str, plan_branch: str) -> tuple[str, str]:
        bare_dir = os.path.join(base_dir, "bare_remote.git")
        subprocess.run(["git", "init", "--bare", "-b", "main", bare_dir], check=True, capture_output=True)
        seed_dir = os.path.join(base_dir, "seed_repo")
        subprocess.run(["git", "init", "-b", "main", seed_dir], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Seed User"], cwd=seed_dir, check=True)
        subprocess.run(["git", "config", "user.email", "seed@example.com"], cwd=seed_dir, check=True)

        src_dir = os.path.join(seed_dir, "src")
        os.makedirs(src_dir, exist_ok=True)
        with open(os.path.join(src_dir, "codebase.py"), "w") as f:
            f.write("# Original codebase file\ndef app(): pass\n")

        ledger_dir = os.path.join(seed_dir, "holon-knowledge/ledger")
        os.makedirs(ledger_dir, exist_ok=True)
        with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
            f.write(
                json.dumps(
                    {
                        "plan_id": "P-123",
                        "intent_branch": "I-456/_",
                        "entropy": 2.0,
                        "entropy_budget": 5.0,
                    }
                )
                + "\n"
            )
        with open(os.path.join(ledger_dir, "intents.jsonl"), "w") as f:
            f.write(json.dumps({"branch": "I-456/_", "slug": "intent-456"}) + "\n")

        subprocess.run(["git", "add", "-A"], cwd=seed_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial plan commit"], cwd=seed_dir, check=True, capture_output=True)
        subprocess.run(["git", "branch", "-M", plan_branch], cwd=seed_dir, check=True, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", bare_dir], cwd=seed_dir, check=True, capture_output=True)
        subprocess.run(["git", "push", "-u", "origin", plan_branch], cwd=seed_dir, check=True, capture_output=True)

        tip_res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=seed_dir, capture_output=True, text=True, check=True)
        plan_tip = tip_res.stdout.strip()
        return bare_dir, plan_tip

    @patch("sandbox_executor.entrypoint.executor.shutil.rmtree")
    @patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    def test_git_recovery_corrupted_git_preserves_parent_history_and_worktree(
        self, mock_get_runner, mock_cleanup, mock_rmtree
    ):
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            plan_branch = "I-456/P-123/_"
            bare_dir, plan_tip = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = os.path.join(base_dir, "workspace")

            mock_runner = MagicMock()
            mock_runner.get_version.return_value = "1.0.0"

            def build_cmd_side_effect(*args, **kwargs):
                agent_script = (
                    "import os, shutil; "
                    "os.makedirs('src', exist_ok=True); "
                    "open('src/agent_edit.py', 'w').write('# agent edit'); "
                    "shutil.rmtree('.git/objects')"
                )
                return ["python3", "-c", agent_script]

            mock_runner.build_cmd.side_effect = build_cmd_side_effect
            mock_runner.validate.return_value = None
            mock_get_runner.return_value = mock_runner

            with (
                patch.dict(os.environ, {"HOLON_REPO_DIR": workspace_dir}),
                patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=workspace_dir),
                patch("sandbox_executor.entrypoint.executor.get_repo_url", return_value=bare_dir),
                patch("sys.argv", ["executor.py", plan_branch, "antigravity-agent", "gemini-3.5-flash"]),
            ):
                executor.main()

            mock_rmtree.assert_not_called()

            parent_res = subprocess.run(
                ["git", "rev-parse", "HEAD^"],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(parent_res.stdout.strip(), plan_tip)

            tree_res = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", "HEAD"],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                check=True,
            )
            tree_files = tree_res.stdout.splitlines()
            self.assertIn("src/codebase.py", tree_files)
            self.assertIn("src/agent_edit.py", tree_files)
            self.assertIn("holon-knowledge/ledger/executions.jsonl", tree_files)
            self.assertTrue(any(f.startswith("executions/E-") and f.endswith(".md") for f in tree_files))

            backup_dirs = [d for d in os.listdir(workspace_dir) if d.startswith(".git-unusable-")]
            self.assertEqual(len(backup_dirs), 1)

            exclude_path = os.path.join(workspace_dir, ".git", "info", "exclude")
            self.assertTrue(os.path.exists(exclude_path))
            with open(exclude_path) as ef:
                content = ef.read()
                self.assertIn(backup_dirs[0], content)
                self.assertIn(".git-unusable-*", content)

            branches_res = subprocess.run(
                ["git", "branch", "-a"], cwd=bare_dir, capture_output=True, text=True, check=True
            )
            self.assertIn("E-", branches_res.stdout)

    def _clone_workspace(self, base_dir: str, bare_dir: str, plan_branch: str) -> str:
        workspace_dir = os.path.join(base_dir, "workspace")
        subprocess.run(
            ["git", "clone", "--branch", plan_branch, bare_dir, workspace_dir],
            check=True,
            capture_output=True,
        )
        return workspace_dir

    @patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir")
    def test_benign_dubious_ownership_is_repaired_without_rebuild(self, mock_cleanup):
        plan_branch = "I-456/P-123/_"
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            bare_dir, _ = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = self._clone_workspace(base_dir, bare_dir, plan_branch)

            dubious_err = f"fatal: detected dubious ownership in repository at '{workspace_dir}'"
            self.assertTrue(executor._repair_git_repo(workspace_dir, dubious_err, "main"))
            with open(os.path.join(workspace_dir, ".git", "config")) as cf:
                self.assertIn(f"directory = {workspace_dir}", cf.read())
            self.assertTrue(executor._probe_git_repo(workspace_dir)[0])
            self.assertEqual([d for d in os.listdir(workspace_dir) if d.startswith(".git-unusable-")], [])

    @patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir")
    def test_lock_repair_removes_only_abandoned_locks(self, mock_cleanup):
        plan_branch = "I-456/P-123/_"
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            bare_dir, _ = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = self._clone_workspace(base_dir, bare_dir, plan_branch)
            git_dir = os.path.join(workspace_dir, ".git")

            abandoned = os.path.join(git_dir, "index.lock")
            with open(abandoned, "w") as lf:
                lf.write("abandoned by a crashed agent")
            stale_age = time.time() - 3600
            os.utime(abandoned, (stale_age, stale_age))
            # A young ref lock and a protected config lock may belong to live git processes.
            young = os.path.join(git_dir, "refs", "heads", "in-flight.lock")
            with open(young, "w") as lf:
                lf.write("held by a running git process")
            protected = os.path.join(git_dir, "config.lock")
            with open(protected, "w") as lf:
                lf.write("held by git config")
            os.utime(protected, (stale_age, stale_age))

            healthy, err = executor._probe_git_repo(workspace_dir)
            self.assertFalse(healthy)
            self.assertIn("index.lock", err)

            removed, still_held = executor._remove_abandoned_locks(workspace_dir)
            self.assertEqual([abandoned], removed)
            self.assertIn(young, still_held)
            self.assertFalse(os.path.exists(abandoned))
            self.assertTrue(os.path.exists(young))
            self.assertTrue(os.path.exists(protected))

            self.assertTrue(executor._repair_git_repo(workspace_dir, err, "main"))
            self.assertTrue(executor._probe_git_repo(workspace_dir)[0])
            self.assertEqual([d for d in os.listdir(workspace_dir) if d.startswith(".git-unusable-")], [])

    @patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    def test_leaked_git_dir_env_is_stripped_from_git_calls(self, mock_get_runner, mock_cleanup):
        plan_branch = "I-456/P-123/_"
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            bare_dir, _ = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = self._clone_workspace(base_dir, bare_dir, plan_branch)

            mock_runner = MagicMock()
            mock_runner.get_version.return_value = "1.0.0"
            mock_runner.build_cmd.return_value = ["python3", "-c", "print('agent ran')"]
            mock_runner.validate.return_value = None
            mock_get_runner.return_value = mock_runner

            with (
                patch.dict(
                    os.environ,
                    {
                        "HOLON_REPO_DIR": workspace_dir,
                        "HOLON_SKIP_PUSH": "1",
                        "GIT_DIR": "/invalid/nonexistent/git/dir",
                    },
                ),
                patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=workspace_dir),
                patch("sandbox_executor.entrypoint.executor.get_repo_url", return_value=bare_dir),
                patch("sys.argv", ["executor.py", plan_branch, "antigravity-agent", "gemini-3.5-flash"]),
            ):
                executor.main()

            self.assertFalse(os.path.exists(os.path.join(workspace_dir, "GIT_DIR")))
            self.assertEqual([d for d in os.listdir(workspace_dir) if d.startswith(".git-unusable-")], [])
            self.assertTrue(executor._probe_git_repo(workspace_dir)[0])

    @patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir")
    def test_missing_head_is_restored_on_the_execution_branch(self, mock_cleanup):
        plan_branch = "I-456/P-123/_"
        exec_branch = "I-456/P-123/E-1790000000-antigravity-agent-gemini-3.5-flash/_"
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            bare_dir, plan_tip = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = self._clone_workspace(base_dir, bare_dir, plan_branch)
            os.remove(os.path.join(workspace_dir, ".git", "HEAD"))

            self.assertFalse(executor._probe_git_repo(workspace_dir)[0])
            self.assertTrue(
                executor._repair_git_repo(
                    workspace_dir,
                    "dangling HEAD",
                    default_branch=exec_branch,
                    plan_branch=plan_branch,
                )
            )

            head_ref = subprocess.run(
                ["git", "symbolic-ref", "HEAD"],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(
                head_ref,
                f"refs/heads/{exec_branch}",
                "HEAD was restored onto an arbitrary branch instead of the execution branch",
            )
            head_tip = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(head_tip, plan_tip)
            self.assertEqual([d for d in os.listdir(workspace_dir) if d.startswith(".git-unusable-")], [])

    @patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    def test_unrecoverable_remote_branch_refusal_to_push(self, mock_get_runner, mock_cleanup):
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            plan_branch = "I-456/P-123/_"
            bare_dir, _ = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = os.path.join(base_dir, "workspace")

            subprocess.run(
                ["git", "clone", "--branch", plan_branch, bare_dir, workspace_dir],
                check=True,
                capture_output=True,
            )

            mock_runner = MagicMock()
            mock_runner.get_version.return_value = "1.0.0"

            def build_cmd_side_effect(*args, **kwargs):
                # Delete plan branch on bare remote during agent run so recovery fetch fails
                subprocess.run(
                    ["git", "branch", "-D", plan_branch],
                    cwd=bare_dir,
                    check=True,
                    capture_output=True,
                )
                return ["python3", "-c", "import shutil; shutil.rmtree('.git/objects')"]

            mock_runner.build_cmd.side_effect = build_cmd_side_effect
            mock_runner.validate.return_value = None
            mock_get_runner.return_value = mock_runner

            with (
                patch.dict(os.environ, {"HOLON_REPO_DIR": workspace_dir, "HOLON_GIT_FETCH_RETRIES": "1"}),
                patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=workspace_dir),
                patch("sandbox_executor.entrypoint.executor.get_repo_url", return_value=bare_dir),
                patch("sys.argv", ["executor.py", plan_branch, "antigravity-agent", "gemini-3.5-flash"]),
            ):
                executor.main()

            branches_res = subprocess.run(
                ["git", "branch", "-a"], cwd=bare_dir, capture_output=True, text=True, check=True
            )
            self.assertNotIn("E-", branches_res.stdout)

            backup_dirs = [d for d in os.listdir(workspace_dir) if d.startswith(".git-unusable-")]
            self.assertEqual(len(backup_dirs), 1)

            ledger_file = os.path.join(workspace_dir, "holon-knowledge/ledger/executions.jsonl")
            self.assertTrue(os.path.exists(ledger_file))
            with open(ledger_file) as f:
                lines = [json.loads(line) for line in f if line.strip()]
            self.assertTrue(any(entry.get("status") == "failure" for entry in lines))
            failure_entry = [entry for entry in lines if entry.get("status") == "failure"][-1]
            self.assertIn("Git recovery failure", failure_entry.get("summary", ""))
            self.assertIn(backup_dirs[0], failure_entry.get("summary", ""))
