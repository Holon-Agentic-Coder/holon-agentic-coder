import os
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor.agent_runner import (
    _check_forbidden_root,
    _clear_dir_contents,
    _handle_remove_readonly,
    cleanup_repo_dir,
)
from sandbox_executor.entrypoint import executor

from tests.executor_fixtures import (
    GitRemoteHarness,
    # why: imported for their autouse fixtures
    _agent_turns_use_run_cmd,  # noqa: F401
    _workspace_is_mounted,  # noqa: F401
)


class TestExecutor(GitRemoteHarness, unittest.TestCase):
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

        # why: a positional value after a chained flag is not masked (known limitation, by design)
        chained = redact_args(["--token", "--verbose", "secret"])
        self.assertEqual(chained, ["--token", "--verbose", "secret"])

        equal_fmt = redact_args(["--token=secret123", "--password=mypass", "--author=alice", "--github-token=gh123"])
        self.assertEqual(
            equal_fmt, ["--token=*******", "--password=*******", "--author=alice", "--github-token=*******"]
        )

        trailing = redact_args(["git", "clone", "--token"])
        self.assertEqual(trailing, ["git", "clone", "--token"])

        secret_dash = redact_args(["--token", "-secret-value"])
        self.assertEqual(secret_dash, ["--token", "-secret-value"])

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
            "key=******* Bearer ******* Authorization: Bearer *******"
        )
        self.assertEqual(redacted_ext, expected_ext)

        quoted_text = 'token="secret" github_token=\'secret_abc\' {"api_key": "secret"} "auth_key": \'secret\''
        redacted_quoted = redact_text(quoted_text)
        expected_quoted = 'token="*******" github_token=\'*******\' {"api_key": "*******"} "auth_key": \'*******\''
        self.assertEqual(redacted_quoted, expected_quoted)

        quoted_spaces = (
            'token="secret with spaces" api_key=\'my secret key\' {"auth_token": "bearer token with spaces"}'
        )
        redacted_spaces = redact_text(quoted_spaces)
        expected_spaces = 'token="*******" api_key=\'*******\' {"auth_token": "*******"}'
        self.assertEqual(redacted_spaces, expected_spaces)

        diagnostic_text = "sort_key=xyz cache_key=xyz"
        self.assertEqual(redact_text(diagnostic_text), diagnostic_text)

        benign_text = (
            "--pattern=*.py --author=alice --path=/tmp/test git log -p "
            "monkey=banana donkey=kong compat=1.0.0 compact=true impact=high"
        )
        self.assertEqual(redact_text(benign_text), benign_text)

        self.assertEqual(redact_text(""), "")

    def test_witness_pattern_1_bare_keys_and_credentials(self):
        from sandbox_executor.entrypoint.executor import redact_text

        self.assertEqual(redact_text('key = "synthetic_val"'), 'key = "*******"')
        self.assertEqual(redact_text("key: synthetic_val"), "key: *******")
        self.assertEqual(redact_text("key=val"), "key=*******")

        self.assertEqual(redact_text('pwd = "my_pass"'), 'pwd = "*******"')
        self.assertEqual(redact_text('passwd = "secret"'), 'passwd = "*******"')
        self.assertEqual(redact_text('client_secret = "cs_xyz"'), 'client_secret = "*******"')
        self.assertEqual(redact_text('db_password = "db_pass"'), 'db_password = "*******"')
        self.assertEqual(redact_text('credential = "cred_abc"'), 'credential = "*******"')
        self.assertEqual(redact_text('credentials = "creds_123"'), 'credentials = "*******"')

    def test_witness_pattern_2_multiline_url_query_isolation(self):
        from sandbox_executor.entrypoint.executor import redact_text

        wp2_1 = "https://example.com/api?token=\nmode=debug\n"
        self.assertEqual(redact_text(wp2_1), "https://example.com/api?token=\nmode=debug\n")

        wp2_2 = "https://example.com/api?key=\ncount=42\n"
        self.assertEqual(redact_text(wp2_2), "https://example.com/api?key=\ncount=42\n")

        wp2_3 = "https://example.com/api?token=dummy_val\nmode=debug\n"
        self.assertEqual(redact_text(wp2_3), "https://example.com/api?token=*******\nmode=debug\n")

        wp2_4 = "https://example.com/api?keyword=search_term&token=secret_val"
        self.assertEqual(
            redact_text(wp2_4),
            "https://example.com/api?keyword=search_term&token=*******",
        )

        wp2_5 = "https://example.com/api?key&other=123"
        self.assertEqual(redact_text(wp2_5), "https://example.com/api?key&other=123")

    def test_witness_pattern_3_multiline_yaml_json_nested_keys(self):
        from sandbox_executor.entrypoint.executor import redact_text

        yaml_input = "cfg:\n  secret:\n    api_key: synthetic_secret_value\n"
        expected_yaml = "cfg:\n  secret:\n    api_key: *******\n"
        self.assertEqual(redact_text(yaml_input), expected_yaml)

        yaml_quoted = 'cfg:\n  secret:\n    "api_key": "secret_val"\n'
        expected_yaml_quoted = 'cfg:\n  secret:\n    "api_key": "*******"\n'
        self.assertEqual(redact_text(yaml_quoted), expected_yaml_quoted)

        json_input = '{\n  "secret": {\n    "api_key": "synthetic_secret_value"\n  }\n}'
        expected_json = '{\n  "secret": {\n    "api_key": "*******"\n  }\n}'
        self.assertEqual(redact_text(json_input), expected_json)

    def test_diagnostic_retention_invariants(self):
        from sandbox_executor.entrypoint.executor import redact_text

        benign_text = (
            "sort_key=asc cache_key=123 primary_key=id foreign_key=user_id "
            "compat=1.0.0 compact=true impact=high monkey=banana donkey=kong"
        )
        self.assertEqual(redact_text(benign_text), benign_text)

        kebab_text = "sort-key=asc cache-key=123 --sort-key=val"
        self.assertEqual(redact_text(kebab_text), kebab_text)

    def test_delimiter_whitespace_preservation(self):
        from sandbox_executor.entrypoint.executor import redact_text

        self.assertEqual(redact_text('key = "val"'), 'key = "*******"')
        self.assertEqual(redact_text("key  :  val"), "key  :  *******")
        self.assertEqual(redact_text('api_key   =   "secret"'), 'api_key   =   "*******"')
        self.assertEqual(redact_text('token : "secret"'), 'token : "*******"')

    def test_redact_text_oversized_input(self):
        from sandbox_executor.entrypoint.executor import _MAX_REDACT_INPUT_LEN, redact_text

        big_text = "token=secret " + "x" * (_MAX_REDACT_INPUT_LEN) + " tail end"
        result = redact_text(big_text)

        half_len = _MAX_REDACT_INPUT_LEN // 2

        expected_head = "token=******* " + "x" * (half_len - len("token=secret "))

        expected_tail = "x" * (half_len - len(" tail end")) + " tail end"

        expected = expected_head + "\n... (truncated) ...\n" + expected_tail
        self.assertEqual(result, expected)

    def test_safe_float(self):
        self.assertEqual(executor._safe_float(None, 5.0), 5.0)
        self.assertEqual(executor._safe_float(None), 0.0)
        self.assertEqual(executor._safe_float(3.14), 3.14)
        self.assertEqual(executor._safe_float("2.5"), 2.5)
        self.assertEqual(executor._safe_float("invalid", 1.0), 1.0)

    def test_run_cmd_raises_called_process_error(self):
        with self.assertRaises(subprocess.CalledProcessError) as ctx:
            executor.run_cmd(["false"], check=True)
        self.assertEqual(ctx.exception.returncode, 1)

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
        _check_forbidden_root("/private/var/folders/xx/yyyy/T/workspace")

    def test_check_forbidden_root_linux_var_allowed(self):
        _check_forbidden_root("/var/tmp/workspace")

        with self.assertRaises(RuntimeError) as ctx:
            _check_forbidden_root("/var/log")
        self.assertIn("Refusing to perform operation on system root-level directory", str(ctx.exception))

    @patch("sandbox_executor.agent_runner.os.path.realpath")
    @patch("sandbox_executor.agent_runner.os.path.abspath")
    def test_check_forbidden_root_symlinks(self, mock_abspath, mock_realpath):
        mock_abspath.return_value = "/var/folders/etc_symlink"
        mock_realpath.return_value = "/private/etc"
        with self.assertRaises(RuntimeError) as ctx:
            _check_forbidden_root("/var/folders/etc_symlink")
        self.assertIn("Refusing to perform operation on system root-level directory", str(ctx.exception))

    @patch("sandbox_executor.agent_runner.os.listdir", return_value=[])
    @patch("sandbox_executor.agent_runner.os.path.isdir", return_value=True)
    def test_clear_dir_contents_allowed_roots(self, mock_isdir, mock_listdir):
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
                _clear_dir_contents(tmp_dir, raise_on_error=False)
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

        mock_isdir.return_value = True
        _handle_remove_readonly(mock_func, "/fake/dir", PermissionError("error"))
        mock_chmod.assert_called_with("/fake/dir", stat.S_IWUSR | stat.S_IRUSR | stat.S_IXUSR, follow_symlinks=False)
        mock_func.assert_called_with("/fake/dir")

        mock_chmod.reset_mock()
        mock_func.reset_mock()

        mock_isdir.return_value = False
        _handle_remove_readonly(mock_func, "/fake/file.txt", PermissionError("error"))
        mock_chmod.assert_called_with("/fake/file.txt", stat.S_IWUSR | stat.S_IRUSR, follow_symlinks=False)
        mock_func.assert_called_with("/fake/file.txt")

    @patch("sandbox_executor.entrypoint.executor.get_runner")
    def test_leaked_git_dir_env_is_stripped_from_git_calls(self, mock_get_runner):
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
                        "HOLON_TARGET_WORKTREE": workspace_dir,
                        "GIT_DIR": "/invalid/nonexistent/git/dir",
                    },
                ),
                patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=workspace_dir),
                patch("sys.argv", ["executor.py", plan_branch, "antigravity-agent", "gemini-3.5-flash"]),
            ):
                executor.main()

            self.assertFalse(os.path.exists(os.path.join(workspace_dir, "GIT_DIR")))
            self.assertEqual([d for d in os.listdir(workspace_dir) if d.startswith(".git-unusable-")], [])
            self.assertTrue(executor._probe_git_repo(workspace_dir)[0])
