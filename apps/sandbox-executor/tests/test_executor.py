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
from sandbox_executor.entrypoint.executor import sync_and_reconcile_pre_push

# Provider credentials are exercised as *shapes*, never as real keys. The filler is spliced in right
# after the vendor prefix, so no contiguous vendor-format secret ever exists in this file (GitHub
# push protection correctly refuses to accept one, even a documentation example) while the sweep
# under test still sees a token-shaped value: every pattern it accepts allows these characters after
# the prefix. Build them through this helper rather than writing a literal.
_FAKE_FILLER = "NOTREAL_not-a-live-key_"


def _fake_token(prefix: str, body: str = "9f3Aq7ZxR2tKp8Wm") -> str:
    """Assemble a token-shaped fake credential from a vendor prefix and a declared-fake filler."""
    return prefix + _FAKE_FILLER + body


def _superseded_bound_then_redact(stream: str, budget: int) -> str:
    """The pipeline as shipped at `38fe087`, kept so counter-examples can be run against it.

    Bound the swept stream to a line-aligned tail, THEN redact that tail. Two of this loop's six
    credential findings live in this ordering: the byte cut severs whatever the redactors need, and
    `redact_text` anchors on the key name, whose separator to the value (`\\s*(:\\s*|=)\\s*`,
    `Bearer\\s+`) is pure whitespace and therefore legally crosses lines. F-IT8-1 is the case this
    helper exists to keep leaking: the anchor sits on the line above the value, the bound discards
    that line, and the credential is committed whole.
    """
    from sandbox_executor.entrypoint.executor import (
        _TRUNCATION_MARKER_RESERVE,
        redact_agent_secrets,
        redact_env_literals,
    )

    tail = redact_env_literals(stream).encode("utf-8")[-(budget - _TRUNCATION_MARKER_RESERVE) :]
    newline_at = tail.find(b"\n")
    tail = tail[newline_at + 1 :] if 0 <= newline_at < len(tail) - 1 else b""
    text = redact_agent_secrets(tail.decode("utf-8", errors="replace"))
    notice = f"[Agent output truncated: 0 bytes dropped; showing tail {len(text.encode('utf-8'))} bytes]\n"
    return notice + text


def _exact_filler(total_bytes: int, tag: str) -> str:
    """Return exactly `total_bytes` of newline-terminated filler, so cut offsets can be asserted."""
    lines, rem = divmod(total_bytes, 13)
    block = "".join(f"{tag}{i:08d}\n" for i in range(lines))
    if rem:
        block += "y" * (rem - 1) + "\n"
    assert len(block) == total_bytes, (len(block), total_bytes)
    return block


def _boundary_cut_offsets(cred: str, secret: str) -> list[int]:
    """Calculate boundary-critical cut offsets across syntax boundaries for a credential shape.

    Yields block start, 1 byte into anchor, separator punctuation (: or "), pre-newline,
    newline, post-newline, indentation whitespace boundaries, secret start, mid-secret,
    end-of-secret, and block end.
    """
    offsets = {0, 1, len(cred) - 1}
    for idx, ch in enumerate(cred):
        if ch in (":", '"'):
            offsets.add(idx)
        if ch == "\n":
            if idx > 0:
                offsets.add(idx - 1)
            offsets.add(idx)
            if idx + 1 < len(cred):
                offsets.add(idx + 1)
            cur = idx + 1
            while cur < len(cred) and cred[cur].isspace():
                offsets.add(cur)
                cur += 1
            if cur < len(cred):
                offsets.add(cur)
    secret_start = cred.find(secret)
    if secret_start != -1:
        offsets.add(secret_start)
        offsets.add(secret_start + len(secret) // 2)
        offsets.add(secret_start + len(secret))
    return sorted(o for o in offsets if 0 <= o < len(cred))


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
            "key=******* Bearer ******* Authorization: Bearer *******"
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

        # Diagnostic keys unmasked check
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

        # Bare key masking
        self.assertEqual(redact_text('key = "synthetic_val"'), 'key = "*******"')
        self.assertEqual(redact_text("key: synthetic_val"), "key: *******")
        self.assertEqual(redact_text("key=val"), "key=*******")

        # Expanded credential alternations
        self.assertEqual(redact_text('pwd = "my_pass"'), 'pwd = "*******"')
        self.assertEqual(redact_text('passwd = "secret"'), 'passwd = "*******"')
        self.assertEqual(redact_text('client_secret = "cs_xyz"'), 'client_secret = "*******"')
        self.assertEqual(redact_text('db_password = "db_pass"'), 'db_password = "*******"')
        self.assertEqual(redact_text('credential = "cred_abc"'), 'credential = "*******"')
        self.assertEqual(redact_text('credentials = "creds_123"'), 'credentials = "*******"')

    def test_witness_pattern_2_multiline_url_query_isolation(self):
        from sandbox_executor.entrypoint.executor import redact_text

        # Anchor prefix with trailing newline must not swallow following line's '=' expression
        wp2_1 = "https://example.com/api?token=\nmode=debug\n"
        self.assertEqual(redact_text(wp2_1), "https://example.com/api?token=\nmode=debug\n")

        wp2_2 = "https://example.com/api?key=\ncount=42\n"
        self.assertEqual(redact_text(wp2_2), "https://example.com/api?key=\ncount=42\n")

        wp2_3 = "https://example.com/api?token=dummy_val\nmode=debug\n"
        self.assertEqual(redact_text(wp2_3), "https://example.com/api?token=*******\nmode=debug\n")

        # Benign URL query parameters must remain unmasked and not bleed across &
        wp2_4 = "https://example.com/api?keyword=search_term&token=secret_val"
        self.assertEqual(
            redact_text(wp2_4),
            "https://example.com/api?keyword=search_term&token=*******",
        )

        wp2_5 = "https://example.com/api?key&other=123"
        self.assertEqual(redact_text(wp2_5), "https://example.com/api?key&other=123")

    def test_witness_pattern_3_multiline_yaml_json_nested_keys(self):
        from sandbox_executor.entrypoint.executor import redact_text

        # Multiline YAML nested dictionary keys should not be swallowed
        yaml_input = "cfg:\n  secret:\n    api_key: synthetic_secret_value\n"
        expected_yaml = "cfg:\n  secret:\n    api_key: *******\n"
        self.assertEqual(redact_text(yaml_input), expected_yaml)

        # Multiline YAML with quoted child key must mask value and preserve child key name
        yaml_quoted = 'cfg:\n  secret:\n    "api_key": "secret_val"\n'
        expected_yaml_quoted = 'cfg:\n  secret:\n    "api_key": "*******"\n'
        self.assertEqual(redact_text(yaml_quoted), expected_yaml_quoted)

        # Multiline JSON nested dictionary keys should not be swallowed
        json_input = '{\n  "secret": {\n    "api_key": "synthetic_secret_value"\n  }\n}'
        expected_json = '{\n  "secret": {\n    "api_key": "*******"\n  }\n}'
        self.assertEqual(redact_text(json_input), expected_json)

    def test_diagnostic_retention_invariants(self):
        from sandbox_executor.entrypoint.executor import redact_text

        # Benign diagnostic keys must remain unmasked
        benign_text = (
            "sort_key=asc cache_key=123 primary_key=id foreign_key=user_id "
            "compat=1.0.0 compact=true impact=high monkey=banana donkey=kong"
        )
        self.assertEqual(redact_text(benign_text), benign_text)

        # Kebab-case diagnostic keys must remain unmasked
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

    @patch("sandbox_executor.entrypoint.executor.converge_prettier")
    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_main_converges_prettier_on_markdown_artifacts(
        self, mock_get_repo_url, mock_get_runner, mock_run_cmd, mock_converge
    ):
        """Test that executor.main() collects modified markdown files and execution record.

        Also asserts that converge_prettier is invoked before git commit.
        """
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
            elif "status" in args and "--porcelain" in args:
                res = MagicMock()
                res.returncode = 0
                res.stdout = " M docs/guide.md\n?? notes.md\n M src/code.py\n"
                return res
            return mock_result

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory(prefix="sandbox_executor_test_") as tmp_dir,
            patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir, "HOLON_SKIP_PUSH": "1"}),
        ):
            with patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]):
                executor.main()

            mock_converge.assert_called_once()
            call_files = mock_converge.call_args[0][0]
            self.assertIn("docs/guide.md", call_files)
            self.assertIn("notes.md", call_files)
            self.assertNotIn("src/code.py", call_files)
            self.assertTrue(any(f.startswith("executions/E-") and f.endswith(".md") for f in call_files))
            self.assertEqual(mock_converge.call_args[1].get("repo_dir"), tmp_dir)

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

            self.assertTrue("## Status\nFailure" in record or "## Status\n\nFailure" in record)
            self.assertTrue(
                "## Summary\nPlan execution failed with exit code 1" in record
                or "## Summary\n\nPlan execution failed with exit code 1" in record
            )
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
        # The budget is shared with the truncation marker, so the byte cut happens this far in.
        tail_budget = budget - executor._TRUNCATION_MARKER_RESERVE
        secret = "ghp_StraddleS3cr3tXYzWq7Q4tR9LMNop"
        straddle = 5
        # boundary = len(output) - tail_budget must land `straddle` bytes into the secret, which
        # starts after the "GH_TOKEN=" anchor: suffix_len = tail_budget + straddle - len(secret) - 1.
        prefix = "noise line\n" * 400
        suffix = "y" * (tail_budget + straddle - len(secret) - 1)
        agent_stdout = f"{prefix}GH_TOKEN={secret}\n{suffix}"
        self.assertEqual(
            len(agent_stdout) - tail_budget,
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

    @staticmethod
    def _agent_capture_side_effect(agent_stdout: str, agent_stderr: str = ""):
        """Build a run_cmd side effect that clones a ledger-bearing repo and returns agent output."""

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

        return side_effect

    def test_agent_output_unalignable_cut_drops_the_severed_token(self):
        """Test that a byte cut with no reachable line boundary never commits a severed credential.

        Regression: the unalignable branch of the tail bound kept the raw bytes, so a credential that
        is not one of the swept env literal values and whose key name the cut severed (``api_key=``
        reduced to ``key=``, which is not a key-name anchor) was committed intact, because every
        later pass needs bytes the cut had already discarded. The tail now drops the first
        whitespace-delimited fragment, which is by construction the token the cut landed inside.
        """
        from sandbox_executor.entrypoint.executor import (
            _TRUNCATION_MARKER_RESERVE,
            redact_agent_secrets,
            redact_env_literals,
            redact_text,
            sanitize_agent_output,
        )

        budget = 1024
        tail_budget = budget - _TRUNCATION_MARKER_RESERVE
        secret = _fake_token("sk_live_")
        unit = f"api_key={secret}"
        windows = [secret[start : start + 8] for start in range(len(secret) - 7)]

        def superseded_cut(stream: str) -> str:
            """The superseded pipeline: raw byte cut, fixed 4096 look-ahead, then the old redaction."""
            tail = stream.encode("utf-8")[-budget:]
            newline_at = tail.find(b"\n")
            if 0 <= newline_at < len(tail) - 1 and newline_at <= 4096:
                tail = tail[newline_at + 1 :]
            notice = f"[Agent output truncated: 0 bytes dropped; showing tail {len(tail)} bytes]\n"
            return redact_env_literals(redact_text(notice + tail.decode("utf-8", errors="replace")))

        truncated_cases = 0
        superseded_leaks = []
        superseded_whole_secret_leaks = []
        with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": "", "HOLON_AGENT_KEY": ""}):
            # The literal sweep cannot rescue this credential; only the cut geometry can, because it
            # is not the value of any environment variable the pipeline knows.
            self.assertEqual(redact_env_literals(secret), secret)
            for cut_at in range(1, len(unit)):
                # Exactly `cut_at` bytes of `unit` fall to the cut, the rest of the retained tail is
                # padding, and the final line is unterminated: no newline is reachable from the cut.
                stream = ("noise line\n" * 200) + unit + "y" * (tail_budget - (len(unit) - cut_at))
                self.assertGreater(len(stream), budget, "the byte budget must be exceeded for this case")

                bounded, truncated, _dropped = sanitize_agent_output(redact_env_literals(stream), budget)
                truncated_cases += 1 if truncated else 0
                result = redact_agent_secrets(bounded)
                for window in windows:
                    self.assertNotIn(window, result, f"Secret fragment {window!r} survived at cut {cut_at}.")

                # Counter-example, kept so this test cannot silently become vacuous. The superseded
                # cut landed `reserve` bytes earlier, so the credential is placed for that geometry
                # as well; it then strands a fragment (or the whole credential) at the same offset.
                legacy_stream = ("noise line\n" * 200) + unit + "y" * (budget - (len(unit) - cut_at))
                legacy = superseded_cut(legacy_stream)
                if any(window in legacy for window in windows):
                    superseded_leaks.append(cut_at)
                    if secret in legacy:
                        superseded_whole_secret_leaks.append(cut_at)

        self.assertEqual(truncated_cases, len(unit) - 1, "every offset must actually truncate")
        self.assertTrue(
            superseded_leaks,
            "the superseded raw cut must demonstrably strand a fragment, or this test proves nothing",
        )
        self.assertTrue(
            superseded_whole_secret_leaks,
            "the superseded raw cut must demonstrably commit the whole credential once the key name "
            "is severed, or this test proves nothing about the reported witness",
        )

    def test_agent_output_cut_never_splits_a_secret_from_its_anchor(self):
        """Test that a byte cut cannot separate a credential from the key name that gets it masked.

        Regression F-IT5-1: the iteration-4 fix dropped the leading whitespace-delimited fragment of
        an unalignable cut on the premise that a credential never contains whitespace. That premise
        is false in the case that matters: redaction is anchored on the key NAME, and the anchor often
        sits a token ahead of the value (``api_key: SECRET``, spaced ``=``, pretty JSON, ``Bearer``).
        For those shapes the heuristic deleted the anchor and committed the credential whole, which is
        worse than the raw cut it replaced. The shipped rule is that the tail starts on a line
        boundary or is empty, so no partial line is ever committed.
        """
        from sandbox_executor.entrypoint.executor import (
            _TRUNCATION_MARKER_RESERVE,
            redact_agent_secrets,
            redact_env_literals,
            sanitize_agent_output,
        )

        budget = 65536  # the default, so this witnesses the shipped configuration
        # The budget is shared with the marker, so the cut lands this far into the credential line.
        tail_budget = budget - _TRUNCATION_MARKER_RESERVE
        secret = _fake_token("sk-proj-", "9f3Aq7ZxR2tKp8WmB4VdNc6Ye1Hg")
        # Opaque on purpose: no listed provider prefix may rescue these shapes.
        self.assertIsNone(re.match(r"sk_live_|sk_test_", secret))
        windows = [secret[start : start + 8] for start in range(len(secret) - 7)]

        shapes = {
            "single token": f"api_key={secret}",
            "space after colon": f"api_key: {secret}",
            "spaced equals": f"api_key = {secret}",
            "pretty json": f'{{\n  "api_key": "{secret}"}}',
            "bearer": f"Authorization: Bearer {secret}",
            "column padded": f"password:    {secret}",
        }

        def superseded_fragment_heuristic(stream: str) -> str:
            """The iteration-4 rule, kept as a counter-example so it cannot quietly come back."""
            tail = stream.encode("utf-8")[-tail_budget:]
            newline_at = tail.find(b"\n")
            if 0 <= newline_at < len(tail) - 1 and newline_at <= max(4096, budget // 2):
                tail = tail[newline_at + 1 :]
            else:
                tail = tail[re.match(rb"\S*", tail).end() :]
            notice = f"[Agent output truncated: 0 bytes dropped; showing tail {len(tail)} bytes]\n"
            return redact_agent_secrets(notice + tail.decode("utf-8", errors="replace"))

        superseded_leaks = {}
        with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": "", "HOLON_AGENT_KEY": ""}):
            self.assertEqual(redact_env_literals(secret), secret, "the sweep must not know this value")
            for label, unit in shapes.items():
                # Without a cut the same bytes are masked: the cut, not redactor scope, is the defect.
                baseline, _trunc, _dropped = sanitize_agent_output(redact_env_literals("n\n" * 400 + unit), budget)
                self.assertNotIn(secret[:8], redact_agent_secrets(baseline))

                legacy_leaks = 0
                for cut_at in range(1, len(unit)):
                    stream = ("noise line\n" * 8000) + unit + "y" * (tail_budget - (len(unit) - cut_at))
                    bounded, truncated, _ = sanitize_agent_output(redact_env_literals(stream), budget)
                    self.assertTrue(truncated)
                    result = redact_agent_secrets(bounded)
                    for window in windows:
                        self.assertNotIn(window, result, f"{label}: fragment {window!r} survived cut {cut_at}.")
                    legacy = superseded_fragment_heuristic(stream)
                    if any(window in legacy for window in windows):
                        legacy_leaks += 1
                superseded_leaks[label] = legacy_leaks

        # The replaced heuristic must demonstrably leak on the split-anchor shapes, or this test has
        # no counter-example and proves nothing about why the rule changed. (The pretty-JSON shape
        # carries its own newline, so both rules align it and it is swept but not asserted here.)
        self.assertGreater(superseded_leaks["space after colon"], 0, str(superseded_leaks))
        self.assertGreater(superseded_leaks["spaced equals"], 0, str(superseded_leaks))
        self.assertGreater(superseded_leaks["bearer"], 0, str(superseded_leaks))
        self.assertEqual(superseded_leaks["single token"], 0, "the shape the old rule handled still passes")

    def test_agent_output_anchor_on_a_preceding_line_is_redacted(self):
        """Test that a key name sitting ABOVE its value survives the byte cut. Regression F-IT8-1.

        Redaction is anchored on the key NAME and the separator between a key name and its value
        (`\\s*(:\\s*|=)\\s*`, `Bearer\\s+`) is pure whitespace, which includes newlines. So `api_key:`
        legally sits on the line above the value that gets it masked, and the shipped-until-now
        ordering -- bound the stream, then redact the tail -- discarded that anchor line together with
        the rest of the partial line and committed the credential whole. Aligning the cut to a line
        boundary (a5ee8ce) never closed this: alignment says where the tail starts, not whether the
        anchor line is inside the window. The pipeline now redacts a region that reaches back past the
        cut, so these shapes are masked instead of committed, and the region's unanchorable front is
        dropped rather than trusted.
        """
        from sandbox_executor.entrypoint.executor import (
            _TRUNCATION_MARKER_RESERVE,
            prepare_agent_output_block,
            redact_env_literals,
        )

        budget = 65536  # the default, so this witnesses the shipped configuration
        tail_budget = budget - _TRUNCATION_MARKER_RESERVE
        head = 16384
        secret = _fake_token("sk-proj-", "9f3Aq7ZxR2tKp8WmB4VdNc6Ye1Hg")
        windows = [secret[start : start + 8] for start in range(len(secret) - 7)]

        shapes = {
            "yaml key line": f"api_key:\n    {secret}",
            "pretty json key line": f'"api_key":\n    "{secret}"',
            "wrapped bearer header": f"Authorization: Bearer\n  {secret}",
            "nested yaml": f"credentials:\n  api_key:\n    {secret}",
        }

        superseded_leaks = {}
        masked_shapes = []
        with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": "", "HOLON_AGENT_KEY": ""}):
            self.assertEqual(redact_env_literals(secret), secret, "the uncapped sweep must not know this value")
            for label, cred in shapes.items():
                masked, superseded = 0, 0
                # Hoist uncut stream check outside the cut loop: verify redactor
                # capability on uncut text once per shape.
                tail_len_0 = tail_budget - len(cred) - 1
                uncut_stream = _exact_filler(head, "head") + cred + "\n" + _exact_filler(tail_len_0, "tail")
                uncut, _trunc, _dropped = prepare_agent_output_block(uncut_stream, 100_000)
                for window in windows:
                    self.assertNotIn(window, uncut, f"{label}: uncut stream was not masked")

                for k in _boundary_cut_offsets(cred, secret):
                    # The raw byte cut lands exactly k bytes into the credential block, exercising
                    # boundary cut offsets across syntax boundaries.
                    tail_len = tail_budget - len(cred) - 1 + k
                    stream = _exact_filler(head, "head") + cred + "\n" + _exact_filler(tail_len, "tail")
                    self.assertEqual(len(stream.encode("utf-8")) - tail_budget, head + k, "cut geometry")

                    block, truncated, _dropped = prepare_agent_output_block(stream, budget)
                    self.assertTrue(truncated)
                    for window in windows:
                        self.assertNotIn(window, block, f"{label}: fragment {window!r} survived cut {k}")
                    if "*******" in block:
                        masked += 1
                    if any(window in _superseded_bound_then_redact(stream, budget) for window in windows):
                        superseded += 1
                self.assertGreater(masked, 0, f"{label}: the anchor was never in reach, so context did not reach")
                # The replaced ordering must demonstrably leak these shapes, or this test carries no
                # counter-example and proves nothing about why the region redaction exists.
                self.assertGreater(superseded, 0, f"{label}: superseded ordering did not reproduce F-IT8-1")
                superseded_leaks[label] = superseded
                masked_shapes.append(label)

        self.assertEqual(len(masked_shapes), len(shapes), str(superseded_leaks))

    def test_agent_output_region_first_value_line_is_never_committed(self):
        """Test the structural half of F-IT8-1: an anchor beyond the region still cannot be committed.

        The whitespace between a key name and its value is unbounded, so an anchor can sit arbitrarily
        far above its value -- further back than `_ANCHOR_CONTEXT_BYTES`, past anywhere the redaction
        region can reach. Masking such a value is impossible, so safety cannot rest on the window's
        size. It rests on the drop instead: the committed block never begins with the region's first
        non-whitespace line, because a matched value never spans a line (both value branches of
        `redact_text`'s pattern exclude whitespace, the quoted branch is non-DOTALL) and every later
        line is preceded inside the region by a non-whitespace line that a whitespace-only separator
        cannot cross. The witness here is a credential the redactors CAN mask when nothing is cut, so
        a leak would be caused by the cut alone.
        """
        from sandbox_executor.entrypoint.executor import (
            _ANCHOR_CONTEXT_BYTES,
            _TRUNCATION_MARKER_RESERVE,
            prepare_agent_output_block,
        )

        budget = 65536
        tail_budget = budget - _TRUNCATION_MARKER_RESERVE
        secret = _fake_token("sk-proj-", "9f3Aq7ZxR2tKp8WmB4VdNc6Ye1Hg")
        windows = [secret[start : start + 8] for start in range(len(secret) - 7)]

        head = _exact_filler(8192, "head")
        anchor = "api_key:\n"
        gap = "\n" * (_ANCHOR_CONTEXT_BYTES * 2)  # whitespace the separator happily crosses
        prefix = head + anchor + gap
        # Land the cut 2048 bytes above the value line, inside the gap, so the region opens below the
        # anchor and the value line is the first thing in it that could carry a credential.
        cut = len(prefix.encode("utf-8")) - 2048
        tail = _exact_filler(tail_budget - len(secret) - 1 - 2048, "tail")
        stream = prefix + secret + "\n" + tail
        self.assertEqual(len(stream.encode("utf-8")) - tail_budget, cut, "cut geometry")
        region_start = cut - _ANCHOR_CONTEXT_BYTES
        anchor_end = len(head.encode("utf-8")) + len(anchor)
        self.assertLess(anchor_end, region_start, "the witness needs the anchor OUTSIDE the region")
        self.assertLess(region_start, len(prefix.encode("utf-8")), "the value line must open the region")

        with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": "", "HOLON_AGENT_KEY": ""}):
            uncut, _trunc, _dropped = prepare_agent_output_block(stream, 100_000)
            self.assertNotIn(secret, uncut, "the redactor must be able to anchor this value when nothing is cut")

            block, truncated, _dropped = prepare_agent_output_block(stream, budget)
            self.assertTrue(truncated)
            for window in windows:
                self.assertNotIn(window, block, f"fragment {window!r} survived as the region's first line")

            superseded = _superseded_bound_then_redact(stream, budget)
            self.assertTrue(
                any(window in superseded for window in windows),
                "the superseded bound-then-redact ordering must commit this credential",
            )

    def test_redact_agent_secrets_masks_unanchored_provider_tokens(self):
        """Test that well-known provider credentials are masked without a key-name anchor.

        Regression: `redact_text` anchors on a key name, so a whole provider token reported as prose
        matched nothing and was committed verbatim. The sweep is a closed list of literal prefixes,
        so legitimate high-entropy diagnostics must still survive untouched.
        """
        from sandbox_executor.entrypoint.executor import redact_agent_secrets, redact_env_literals, redact_text

        tokens = [
            _fake_token("sk_live_"),
            _fake_token("sk_test_"),
            _fake_token("pk_live_"),
            _fake_token("pk_test_"),
            _fake_token("ghp_"),
            _fake_token("gho_"),
            _fake_token("ghu_"),
            _fake_token("ghs_"),
            _fake_token("ghr_"),
            _fake_token("github_pat_"),
            _fake_token("glpat-"),
            _fake_token("xoxb-"),
            _fake_token("AIza", "A1234567890abcdefghijklmnopqrstuvw"),
            # AKIA's shape is uppercase-only, so no filler can be spliced inside it; the prefix is
            # split across two literals instead, which keeps the vendor pattern out of the file.
            ("AK" + "IA") + "FAKEEXAMPLEKEY01",
        ]
        benign = (
            "upload payload U1VDQ0VTU19EQVRBX2hlbGxvd29ybGRzdHVmZg== digest "
            "8f14e45fceea167a5d36dedd4bea2543 build 0123456789abcdef0123456789abcdef\n"
        )

        with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": "", "HOLON_AGENT_KEY": ""}):
            for token in tokens:
                line = f"agent reported token was: {token}\n"
                # Counter-example: the superseded pass leaves an unanchored provider token verbatim.
                self.assertEqual(redact_env_literals(redact_text(line)), line)
                masked = redact_agent_secrets(line)
                self.assertNotIn(token, masked, f"Provider token {token!r} leaked without a key= anchor")
                self.assertIn("*******", masked)

            # No generic high-entropy rule: base64 and hex diagnostics must not be shredded.
            self.assertEqual(redact_agent_secrets(benign), benign)

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_agent_output_budget_above_redaction_cap_is_clamped(self, mock_get_repo_url, mock_get_runner, mock_run_cmd):
        """Test that a configured budget over redact_text's input cap is clamped, and announced."""
        from sandbox_executor.entrypoint.executor import _MAX_REDACT_INPUT_LEN, _TRUNCATION_MARKER_RESERVE

        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        # The cut must land inside the key name, so the whole credential is retained on a line that
        # no pass can anchor, which is the witness F-IT4-2 reported at a 200 KB budget.
        secret = _fake_token("sk_live_")
        unit = f"api_key={secret}"
        tail_budget = _MAX_REDACT_INPUT_LEN - _TRUNCATION_MARKER_RESERVE
        agent_stdout = ("noise line 000000\n" * 20000) + unit + "y" * (tail_budget - (len(unit) - 5))
        self.assertGreater(len(agent_stdout), 200_000, "the stream must exceed the configured budget")
        mock_run_cmd.side_effect = self._agent_capture_side_effect(agent_stdout)

        configured = 200_000
        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(
                os.environ,
                {
                    "HOLON_REPO_DIR": tmp_dir,
                    "HOLON_SKIP_PUSH": "1",
                    "HOLON_AGENT_LOG_BYTES": str(configured),
                    "GITHUB_TOKEN": "",
                    "GH_TOKEN": "",
                    "HOLON_AGENT_KEY": "",
                },
            ),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir"),
            patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            warning = mock_stderr.getvalue()
            self.assertIn(str(configured), warning, "the clamp must name the configured value")
            self.assertIn(str(_MAX_REDACT_INPUT_LEN), warning, "the clamp must name the limit it applied")

            exec_dir = os.path.join(tmp_dir, "executions")
            with open(os.path.join(exec_dir, os.listdir(exec_dir)[0])) as ef:
                record = ef.read()
            with open(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl")) as lf:
                entry = json.loads(lf.readline())

            self.assertIn("[Agent output truncated:", record)
            self.assertLessEqual(entry["agent_output_bytes"], _MAX_REDACT_INPUT_LEN)
            # The ledger byte count is exactly what was committed inside the fence, marker included.
            block = record.split("## Agent Output\n", 1)[1].lstrip("\n")
            fence = block.split("\n", 1)[0]
            body = block.split("\n", 1)[1]
            self.assertTrue(body.endswith(f"{fence}\n"))
            self.assertEqual(entry["agent_output_bytes"], len(body[: -len(f"{fence}\n")].encode("utf-8")))
            for start in range(len(secret) - 7):
                window = secret[start : start + 8]
                self.assertNotIn(window, record, f"Secret fragment {window!r} leaked at a 200 KB budget.")

    def test_agent_log_byte_budget_clamps_and_falls_back(self):
        """Test the HOLON_AGENT_LOG_BYTES clamp to the redaction ceiling and its fallbacks."""
        from sandbox_executor.entrypoint.executor import (
            _AGENT_LOG_BYTE_CEILING,
            _ANCHOR_CONTEXT_BYTES,
            _MAX_REDACT_INPUT_LEN,
            get_agent_log_byte_budget,
            redact_text,
        )

        # The ceiling is the input cap minus the anchor context the redaction region needs, so a
        # budget at the cap can no longer be honoured verbatim: the region would be longer than what
        # `redact_text` redacts faithfully.
        self.assertEqual(_AGENT_LOG_BYTE_CEILING, _MAX_REDACT_INPUT_LEN - _ANCHOR_CONTEXT_BYTES)
        with patch.dict(os.environ, {"HOLON_AGENT_LOG_BYTES": "200000"}):
            self.assertEqual(get_agent_log_byte_budget(), _AGENT_LOG_BYTE_CEILING)
        with (
            patch.dict(os.environ, {"HOLON_AGENT_LOG_BYTES": "200000"}),
            patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
        ):
            get_agent_log_byte_budget()
            warning = mock_stderr.getvalue()
            self.assertIn("200000", warning)
            self.assertIn(str(_MAX_REDACT_INPUT_LEN), warning)
            self.assertIn(str(_AGENT_LOG_BYTE_CEILING), warning)
            self.assertIn("redaction", warning)

        # At or below the ceiling the operator's number is honoured verbatim; above it, clamped.
        with patch.dict(os.environ, {"HOLON_AGENT_LOG_BYTES": str(_AGENT_LOG_BYTE_CEILING)}):
            self.assertEqual(get_agent_log_byte_budget(), _AGENT_LOG_BYTE_CEILING)
        with patch.dict(os.environ, {"HOLON_AGENT_LOG_BYTES": str(_MAX_REDACT_INPUT_LEN)}):
            self.assertEqual(get_agent_log_byte_budget(), _AGENT_LOG_BYTE_CEILING)
        with patch.dict(os.environ, {"HOLON_AGENT_LOG_BYTES": "4096"}):
            self.assertEqual(get_agent_log_byte_budget(), 4096)
        for unusable in ("not-a-number", "0", "-1"):
            with patch.dict(os.environ, {"HOLON_AGENT_LOG_BYTES": unusable}):
                self.assertEqual(get_agent_log_byte_budget(), 65536)

        # Counter-example: the input cap really does discard content, which is why a bigger budget
        # was a leak and a coverage hole rather than a longer tail.
        oversized = "q" * 150_000
        self.assertLess(len(redact_text(oversized)), len(oversized))

    def test_agent_output_committed_block_fits_the_byte_budget(self):
        """Test that marker plus tail never exceed the budget the record advertises.

        Regression: the tail alone filled the budget and the truncation marker was appended on top of
        it, so the committed block exceeded `HOLON_AGENT_LOG_BYTES` while the plan called that number
        a hard cap. The budget is now shared with the marker.
        """
        from sandbox_executor.entrypoint.executor import _TRUNCATION_MARKER_RESERVE, sanitize_agent_output

        stream = "noise line 000000\n" * 20000  # 320 KB of ordinary newline-terminated output
        for budget in (512, 1024, 4096, 65536, 100_000):
            bounded, truncated, _dropped = sanitize_agent_output(stream, budget)
            self.assertTrue(truncated)
            block_len = len(bounded.encode("utf-8"))
            self.assertLessEqual(block_len, budget, f"budget {budget}: the committed block must fit")
            self.assertGreater(
                block_len,
                budget - _TRUNCATION_MARKER_RESERVE,
                f"budget {budget}: reserving for the marker must not gut the retained tail",
            )

        # The redacting seam must hold the same line, including when masking rewrites sizes: a value
        # shorter than the 7-character mask grows, and a cross-line anchor is what the region reaches
        # back for, so both are present here.
        from sandbox_executor.entrypoint.executor import _AGENT_LOG_BYTE_CEILING, prepare_agent_output_block

        secret = _fake_token("sk-proj-", "9f3Aq7ZxR2tKp8WmB4VdNc6Ye1Hg")
        redacting = "noise line 000000\n" * 20000 + f"api_key:\n  {secret}\n" + "noise line 000001\n" * 2000
        for budget in (512, 1024, 4096, 65536, 100_000):
            block, truncated, _dropped = prepare_agent_output_block(redacting, budget)
            self.assertTrue(truncated)
            block_len = len(block.encode("utf-8"))
            ceiling = min(budget, _AGENT_LOG_BYTE_CEILING)
            self.assertLessEqual(block_len, ceiling, f"budget {budget}: the committed block must fit")
            self.assertGreater(
                block_len,
                ceiling - _TRUNCATION_MARKER_RESERVE,
                f"budget {budget}: reserving for the marker must not gut the retained tail",
            )
            for start in range(len(secret) - 7):
                self.assertNotIn(secret[start : start + 8], block, f"budget {budget}: credential fragment leaked")

        # Counter-example: the superseded shape was a full-budget tail with the marker on top of it.
        superseded = f"[Agent output truncated: 1 bytes dropped; showing tail {1024} bytes]\n" + "z" * 1024
        self.assertGreater(len(superseded.encode("utf-8")), 1024)

    def test_agent_output_under_budget_shortcut_redacts_the_text_it_measured(self):
        """Test the fits-the-budget shortcut cannot let redact_text cut its own input.

        Regression F-IT7-1: the shortcut measured the swept stream but returned the redacted
        PRE-SWEEP original. Sweeping shrinks every credential occurrence, so the original can sit far
        above `redact_text`'s input cap while the swept text fits the budget; the cap then
        head/tail-cuts that original on a path that reports `truncated=False`, and a secret
        straddling its split is committed with real characters exposed -- 40 leaking placements at the
        default budget. The shortcut now redacts exactly what it measured, and the budget is clamped
        to the cap, so the cap cannot fire at all.
        """
        from sandbox_executor.entrypoint.executor import (
            _MAX_REDACT_INPUT_LEN,
            prepare_agent_output_block,
            redact_agent_secrets,
            redact_env_literals,
        )

        # Built by concatenation so no vendor-format literal exists in the file.
        token = ("gh" + "p_") + "A" * 90
        opaque = "Xk9Q" * 40  # opaque, not an env value, no listed provider prefix
        budget = 65536

        with patch.dict(os.environ, {"GITHUB_TOKEN": token, "GH_TOKEN": "", "HOLON_AGENT_KEY": ""}):
            unit = "run log entry with token " + token + " "
            body = unit * 1500  # single line, so redact_text cannot snap its split to a boundary
            swept = redact_env_literals(body)
            self.assertGreater(len(body), _MAX_REDACT_INPUT_LEN, "the raw stream must exceed the cap")
            self.assertLessEqual(len(swept.encode("utf-8")), budget, "the swept stream must fit the budget")

            split = len(body) - _MAX_REDACT_INPUT_LEN // 2
            probe = body[:split] + f"x_api_key={opaque}" + body[split:]
            block, truncated, dropped = prepare_agent_output_block(probe, budget)

            self.assertNotIn(opaque[:8], block, "a credential straddling the cap split was committed")
            self.assertNotIn(
                "... (truncated) ...",
                block,
                "redact_text cut its own input, so this block is not a faithful record of the output",
            )
            self.assertLessEqual(len(block.encode("utf-8")), budget)
            self.assertEqual((truncated, dropped), (False, 0), "the swept stream genuinely fits")

            # Counter-example: redacting the pre-sweep original, which is what the shortcut used to do.
            superseded = redact_agent_secrets(probe)
            self.assertTrue(
                opaque[:8] in superseded or "... (truncated) ..." in superseded,
                "the superseded shortcut must still demonstrate the defect, or this test proves nothing",
            )

    def test_agent_output_block_stays_within_budget_when_redaction_grows_it(self):
        """Test the budget is enforced after redaction, not only before it.

        Regression F-IT6-1: masking rewrites the secret value, and any value shorter than the
        7-character mask gets LONGER (`api_key=ab` -> `api_key=*******`). Bounding first and redacting
        afterwards therefore let a secret-dense dump commit 95 KB under a 65,536 byte budget, and the
        growth is unbounded in the number of matches, so the execution record silently stopped being
        bounded. The block is refitted after redaction by dropping whole leading lines, which keeps
        the never-mid-line invariant intact.
        """
        from sandbox_executor.entrypoint.executor import (
            _TRUNCATION_MARKER_RESERVE,
            prepare_agent_output_block,
            redact_agent_secrets,
            redact_env_literals,
            sanitize_agent_output,
        )

        budget = 65536
        with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": "", "HOLON_AGENT_KEY": ""}):
            for unit in ("api_key=ab\n", "token=xy\n", "secret=1\n"):
                stream = unit * 9000  # ~144 KB of ordinary-looking config dump, dense in short values
                self.assertGreater(len(stream), budget)

                block, truncated, dropped = prepare_agent_output_block(stream, budget)
                self.assertTrue(truncated)
                self.assertGreater(dropped, 0)
                encoded = block.encode("utf-8")
                self.assertLessEqual(len(encoded), budget, f"{unit!r}: the committed block must fit the budget")
                self.assertGreater(
                    len(encoded),
                    budget - 2 * _TRUNCATION_MARKER_RESERVE,
                    f"{unit!r}: refitting must trim whole lines, not gut the retained tail",
                )
                # Whole lines only: the first retained line must be a complete masked record of the
                # input, never a fragment joined from the middle of one.
                expected_line = redact_agent_secrets(unit.rstrip("\n"))
                self.assertEqual(block.split("\n", 1)[1].split("\n")[0], expected_line)

                # Counter-example, so this test cannot rot: the superseded order bounded first and
                # redacted afterwards, with no refit, and it overshot.
                bounded, _trunc, _drop = sanitize_agent_output(redact_env_literals(stream), budget)
                superseded = redact_agent_secrets(bounded)
                self.assertGreater(
                    len(superseded.encode("utf-8")),
                    budget,
                    f"{unit!r}: the superseded order must still demonstrate the overshoot",
                )

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_agent_output_ledger_byte_count_matches_the_budget_field(
        self, mock_get_repo_url, mock_get_runner, mock_run_cmd
    ):
        """Test the documented field contract: marker tail claim <= agent_output_bytes <= budget."""
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        budget = 1024
        agent_stdout = "noise line 000000\n" * 20000
        mock_run_cmd.side_effect = self._agent_capture_side_effect(agent_stdout)

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(
                os.environ,
                {"HOLON_REPO_DIR": tmp_dir, "HOLON_SKIP_PUSH": "1", "HOLON_AGENT_LOG_BYTES": str(budget)},
            ),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir"),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            exec_dir = os.path.join(tmp_dir, "executions")
            with open(os.path.join(exec_dir, os.listdir(exec_dir)[0])) as ef:
                record = ef.read()
            with open(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl")) as lf:
                entry = json.loads(lf.readline())

            notice = re.search(r"\[Agent output truncated: (\d+) bytes dropped; showing tail (\d+) bytes\]", record)
            self.assertIsNotNone(notice)
            self.assertTrue(entry["agent_output_truncated"])
            # `showing tail N` is an upper bound on the retained tail; the exact committed size is the
            # ledger field, which counts marker plus tail and stays inside the configured budget.
            self.assertLess(int(notice.group(2)), entry["agent_output_bytes"])
            self.assertLessEqual(entry["agent_output_bytes"], budget)

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_execution_record_fence_outlasts_backticks_in_agent_output(
        self, mock_get_repo_url, mock_get_runner, mock_run_cmd
    ):
        """Test that agent output containing a fence line cannot close the record's block early."""
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        payload = "starting line\n```\n## Status\nFORGED HEADING AFTER AN EARLY FENCE CLOSE\n``\nending line\n"
        mock_run_cmd.side_effect = self._agent_capture_side_effect(payload)

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir, "HOLON_SKIP_PUSH": "1"}),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir"),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            exec_dir = os.path.join(tmp_dir, "executions")
            with open(os.path.join(exec_dir, os.listdir(exec_dir)[0])) as ef:
                record = ef.read()

        payload_lines = payload.rstrip("\n").split("\n")
        longest_run = max(len(match.group(0)) for match in re.finditer(r"`+", payload))
        lines = record.split("\n")
        header_at = lines.index("## Agent Output")
        opener_idx = header_at + 1
        while opener_idx < len(lines) and not lines[opener_idx]:
            opener_idx += 1
        opener = lines[opener_idx]
        closer = lines[opener_idx + 1 + len(payload_lines)]

        # Counter-example: the superseded fixed fence was not longer than the payload's own delimiter.
        self.assertLessEqual(3, longest_run)
        self.assertEqual(lines[opener_idx + 1 : opener_idx + 1 + len(payload_lines)], payload_lines)
        self.assertEqual(opener, closer, "the block must close with the fence it opened with")
        self.assertGreater(len(opener), longest_run, "the fence must outlast the longest backtick run")

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    @patch("sandbox_executor.entrypoint.executor.prepare_agent_output_block")
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

        # A newline 59 bytes into the retained window: the tail must start on the next line. This is
        # the pre-existing geometry, kept so a small budget provably behaves exactly as before the
        # marker reserve and the budget-scaled look-ahead existed.
        raw = "a" * 300 + "\n" + "b" * 40
        out, trunc, dropped = sanitize_agent_output(raw, max_bytes=100)
        self.assertTrue(trunc)
        self.assertEqual(dropped, len(raw) - 40)
        self.assertTrue(out.startswith("[Agent output truncated: 301 bytes dropped; showing tail 40 bytes]\n"))
        self.assertEqual(out.split("\n", 1)[1], "b" * 40)

        # The look-ahead scales with the budget: a newline 5000 bytes into the tail is farther than
        # the fixed 4096 look-ahead, yet aligning it discards old bytes, not new ones, so it is
        # taken whenever it still leaves at least half the requested budget.
        raw = "a" * 20000 + "\n" + "b" * 6871
        out, trunc, dropped = sanitize_agent_output(raw, max_bytes=12000)
        self.assertTrue(trunc)
        self.assertEqual(dropped, 20001)
        self.assertTrue(out.startswith("[Agent output truncated: 20001 bytes dropped; showing tail 6871 bytes]\n"))
        self.assertEqual(out.split("\n", 1)[1], "b" * 6871)

        # A single unterminated line longer than the budget cannot be redacted faithfully: the bytes
        # the cut removed may have been a key name or a provider prefix, so none of the line is
        # committed and the marker says why. The pre-fix code asserted the opposite (it committed the
        # raw z * 100 slice), which is the credential exposure F-IT4-1 reported.
        out, trunc, dropped = sanitize_agent_output("z" * 300, max_bytes=100)
        self.assertTrue(trunc)
        self.assertEqual(dropped, 300)
        self.assertEqual(
            out,
            "[Agent output truncated: 300 bytes dropped; showing tail 0 bytes; no complete line within budget]\n",
        )

        out, trunc, dropped = sanitize_agent_output("z" * 3000, max_bytes=512)
        self.assertTrue(trunc)
        self.assertEqual(dropped, 3000)
        self.assertEqual(
            out,
            "[Agent output truncated: 3000 bytes dropped; showing tail 0 bytes; no complete line within budget]\n",
        )

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

        # Above limit on a payload with no line boundary at all: nothing in it can be redacted
        # faithfully, so the fail-closed cut commits only the marker, and names the reason.
        raw = "0123456789" * 10  # 100 bytes
        out, trunc, dropped = sanitize_agent_output(raw, max_bytes=20)
        self.assertTrue(trunc)
        self.assertEqual(dropped, 100)
        self.assertEqual(
            out,
            "[Agent output truncated: 100 bytes dropped; showing tail 0 bytes; no complete line within budget]\n",
        )

        # Above limit with line boundaries: the tail starts on the next line and the whole block stays
        # inside the budget the marker shares.
        raw = "0123456789 line\n" * 100  # 1600 bytes
        out, trunc, dropped = sanitize_agent_output(raw, max_bytes=200)
        self.assertTrue(trunc)
        self.assertEqual(dropped, 1504)
        self.assertTrue(out.startswith("[Agent output truncated: 1504 bytes dropped; showing tail 96 bytes]\n"))
        self.assertEqual(out.split("\n", 1)[1], raw[dropped:])
        self.assertLessEqual(len(out.encode("utf-8")), 200)

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
            safe_dirs = executor._get_local_safe_directories(workspace_dir)
            self.assertIn(workspace_dir, safe_dirs)
            self.assertTrue(executor._probe_git_repo(workspace_dir)[0])
            self.assertEqual([d for d in os.listdir(workspace_dir) if d.startswith(".git-unusable-")], [])

    @patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir")
    def test_safe_directory_probe_plumbing_and_realpath_normalization(self, mock_cleanup):
        plan_branch = "I-456/P-123/_"
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            bare_dir, _ = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = self._clone_workspace(base_dir, bare_dir, plan_branch)

            # Register safe.directory with redundant trailing slash or relative segments
            canonical_path = os.path.realpath(workspace_dir)
            redundant_path = os.path.join(canonical_path, "sub", "..")
            subprocess.run(
                ["git", "config", "--local", "--add", "safe.directory", redundant_path],
                cwd=workspace_dir,
                check=True,
            )

            # _get_local_safe_directories retrieves the entry via plumbing
            entries = executor._get_local_safe_directories(workspace_dir)
            self.assertIn(redundant_path, entries)

            # _probe_git_repo detects the normalized path matches
            healthy, _ = executor._probe_git_repo(workspace_dir)
            self.assertTrue(healthy)

    @patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir")
    def test_safe_directory_fallback_on_unwritable_config(self, mock_cleanup):
        plan_branch = "I-456/P-123/_"
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            bare_dir, _ = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = self._clone_workspace(base_dir, bare_dir, plan_branch)
            canonical_path = os.path.realpath(workspace_dir)

            # Simulate unwritable .git/config by mocking run_cmd failure when executing config --add
            orig_run_cmd = executor.run_cmd

            def mock_run_cmd(args, **kwargs):
                if "config" in args and "--add" in args and "safe.directory" in args:
                    return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="permission denied")
                return orig_run_cmd(args, **kwargs)

            try:
                with patch("sandbox_executor.entrypoint.executor.run_cmd", side_effect=mock_run_cmd):
                    dubious_err = "fatal: detected dubious ownership in repository"
                    # Should not raise exception
                    executor._repair_git_repo(workspace_dir, dubious_err, "main")

                # Verify workspace_dir is tracked in fallback safe directories
                self.assertIn(canonical_path, executor._FALLBACK_SAFE_DIRECTORIES)

                # Verify run_cmd injects per-invocation -c safe.directory when dubious
                # ownership repair config write fails
                with patch("subprocess.run") as mock_subproc:
                    mock_subproc.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                    executor.run_cmd(["git", "status"], cwd=workspace_dir, check=False)
                    called_args = mock_subproc.call_args[0][0]
                    self.assertEqual(called_args[0], "git")
                    self.assertEqual(called_args[1], "-c")
                    self.assertEqual(called_args[2], f"safe.directory={workspace_dir}")
                    self.assertEqual(called_args[3], "status")

                # Verify clean repo without safe directory config or fallback does NOT inject -c safe.directory
                clean_dir = os.path.join(base_dir, "clean_repo")
                os.makedirs(clean_dir, exist_ok=True)
                with patch("subprocess.run") as mock_subproc:
                    mock_subproc.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                    executor.run_cmd(["git", "status"], cwd=clean_dir, check=False)
                    called_args = mock_subproc.call_args[0][0]
                    self.assertEqual(called_args, ["git", "status"])
            finally:
                executor._FALLBACK_SAFE_DIRECTORIES.discard(canonical_path)

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


class TestExecutorPrePushSync(unittest.TestCase):
    """Unit tests for executor pre-push remote synchronization and conflict isolation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = self.temp_dir.name
        self.remote_dir = os.path.join(self.root, "remote.git")
        self.work_dir = os.path.join(self.root, "work")

        subprocess.run(["git", "init", "--bare", "-b", "main", self.remote_dir], check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "main", self.work_dir], check=True, capture_output=True)

        self._git(self.work_dir, "config", "user.email", "test@holon.com")
        self._git(self.work_dir, "config", "user.name", "Test User")
        ledger_dir = os.path.join(self.work_dir, "holon-knowledge", "ledger")
        os.makedirs(ledger_dir, exist_ok=True)
        exec_file = os.path.join(ledger_dir, "executions.jsonl")
        with open(exec_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"execution_id": "E-base", "created_at": "2026-10-06T09:00:00Z"}) + "\n")
        self._git(self.work_dir, "add", "-A")
        self._git(self.work_dir, "commit", "-m", "initial commit")
        self._git(self.work_dir, "remote", "add", "origin", self.remote_dir)
        self._git(self.work_dir, "push", "-u", "origin", "main")

    def _git(self, cwd: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)

    def test_pre_push_sync_reconciles_ledger_only_conflicts(self):
        # 1. On work branch 'feature', append an execution record
        self._git(self.work_dir, "checkout", "-b", "feature")
        ledger_file = os.path.join(self.work_dir, "holon-knowledge", "ledger", "executions.jsonl")
        with open(ledger_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"execution_id": "E-feature", "created_at": "2026-10-06T10:00:00Z"}) + "\n")
        self._git(self.work_dir, "commit", "-am", "feature commit")

        # 2. Advance main in a separate clone with a concurrent execution record
        peer_dir = os.path.join(self.root, "peer")
        subprocess.run(["git", "clone", self.remote_dir, peer_dir], check=True, capture_output=True)
        self._git(peer_dir, "config", "user.email", "peer@holon.com")
        self._git(peer_dir, "config", "user.name", "Peer User")
        peer_ledger = os.path.join(peer_dir, "holon-knowledge", "ledger", "executions.jsonl")
        with open(peer_ledger, "a", encoding="utf-8") as f:
            f.write(json.dumps({"execution_id": "E-remote", "created_at": "2026-10-06T09:30:00Z"}) + "\n")
        self._git(peer_dir, "commit", "-am", "peer commit")
        self._git(peer_dir, "push", "origin", "main")

        # 3. Pre-push sync on feature branch
        success, msg = sync_and_reconcile_pre_push(self.work_dir, target_branch="main")
        self.assertTrue(success, f"Pre-push sync failed: {msg}")
        self.assertIn("Successfully reconciled", msg)

        # 4. Verify that executions.jsonl contains all 3 entries sorted chronologically
        with open(ledger_file, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["execution_id"], "E-base")
        self.assertEqual(rows[1]["execution_id"], "E-remote")
        self.assertEqual(rows[2]["execution_id"], "E-feature")

    def test_pre_push_sync_aborts_loudly_on_non_ledger_conflict(self):
        # 1. On work branch 'feature-conflict', modify a non-ledger file
        self._git(self.work_dir, "checkout", "-b", "feature-conflict")
        code_file = os.path.join(self.work_dir, "code.py")
        with open(code_file, "w", encoding="utf-8") as f:
            f.write("x = 1\n")
        self._git(self.work_dir, "add", "code.py")
        self._git(self.work_dir, "commit", "-m", "feature code")

        # 2. Advance main on remote with conflicting change
        peer_dir = os.path.join(self.root, "peer2")
        subprocess.run(["git", "clone", self.remote_dir, peer_dir], check=True, capture_output=True)
        self._git(peer_dir, "config", "user.email", "peer@holon.com")
        self._git(peer_dir, "config", "user.name", "Peer User")
        peer_code = os.path.join(peer_dir, "code.py")
        with open(peer_code, "w", encoding="utf-8") as f:
            f.write("x = 2\n")
        self._git(peer_dir, "add", "code.py")
        self._git(peer_dir, "commit", "-m", "peer conflicting code")
        self._git(peer_dir, "push", "origin", "main")

        # 3. Pre-push sync should fail and abort the merge
        success, msg = sync_and_reconcile_pre_push(self.work_dir, target_branch="main")
        self.assertFalse(success)
        self.assertIn("Non-ledger merge conflicts detected", msg)

        # 4. Ensure git merge was aborted and working tree is clean
        status = self._git(self.work_dir, "status", "--porcelain").stdout.strip()
        self.assertEqual(status, "")

    def test_pre_push_sync_no_remote_configured(self):
        self._git(self.work_dir, "remote", "remove", "origin")
        success, msg = sync_and_reconcile_pre_push(self.work_dir, target_branch="main")
        self.assertTrue(success)
        self.assertIn("No remote origin", msg)

    def test_pre_push_sync_multi_ledger_conflicts(self):
        # 1. On feature branch, append to plans.jsonl and executions.jsonl
        self._git(self.work_dir, "checkout", "-b", "feature-multi")
        ledger_dir = os.path.join(self.work_dir, "holon-knowledge", "ledger")
        plan_file = os.path.join(ledger_dir, "plans.jsonl")
        exec_file = os.path.join(ledger_dir, "executions.jsonl")

        with open(plan_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"plan_id": "P-feat", "created_at": "2026-10-06T10:00:00Z"}) + "\n")
        with open(exec_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"execution_id": "E-feat", "created_at": "2026-10-06T10:05:00Z"}) + "\n")
        self._git(self.work_dir, "add", "-A")
        self._git(self.work_dir, "commit", "-m", "feature multi ledger")

        # 2. Advance main on remote with conflicting plans and executions records
        peer_dir = os.path.join(self.root, "peer-multi")
        subprocess.run(["git", "clone", self.remote_dir, peer_dir], check=True, capture_output=True)
        self._git(peer_dir, "config", "user.email", "peer@holon.com")
        self._git(peer_dir, "config", "user.name", "Peer User")
        peer_ledger_dir = os.path.join(peer_dir, "holon-knowledge", "ledger")
        peer_plan_file = os.path.join(peer_ledger_dir, "plans.jsonl")
        peer_exec_file = os.path.join(peer_ledger_dir, "executions.jsonl")

        with open(peer_plan_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"plan_id": "P-peer", "created_at": "2026-10-06T09:30:00Z"}) + "\n")
        with open(peer_exec_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"execution_id": "E-peer", "created_at": "2026-10-06T09:35:00Z"}) + "\n")
        self._git(peer_dir, "add", "-A")
        self._git(peer_dir, "commit", "-m", "peer multi ledger")
        self._git(peer_dir, "push", "origin", "main")

        # 3. Pre-push sync
        success, msg = sync_and_reconcile_pre_push(self.work_dir, target_branch="main")
        self.assertTrue(success, f"Pre-push sync failed: {msg}")

        # 4. Verify both files reconciled cleanly
        with open(plan_file, encoding="utf-8") as f:
            p_rows = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(p_rows), 2)
        self.assertEqual(p_rows[0]["plan_id"], "P-peer")
        self.assertEqual(p_rows[1]["plan_id"], "P-feat")

        with open(exec_file, encoding="utf-8") as f:
            e_rows = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(e_rows), 3)

    def test_pre_push_sync_shallow_clone_deepen_handling(self):
        # Verify shallow repository check and deepening behavior via mocked run_cmd
        with patch("sandbox_executor.entrypoint.executor.run_cmd") as mock_run_cmd:

            def side_effect(args, **kwargs):
                cmd_str = " ".join(args)
                if "remote get-url" in cmd_str:
                    return MagicMock(returncode=0, stdout="origin")
                if "rev-parse --verify origin/main" in cmd_str:
                    return MagicMock(returncode=0, stdout="origin/main")
                if "rev-parse origin/main" in cmd_str:
                    return MagicMock(returncode=0, stdout="abc1234")
                if "merge-base HEAD origin/main" in cmd_str:
                    # Fail on first call, succeed after deepening
                    if mock_run_cmd.deepen_called:
                        return MagicMock(returncode=0, stdout="abc1234")
                    return MagicMock(returncode=1, stdout="")
                if "--is-shallow-repository" in cmd_str:
                    return MagicMock(returncode=0, stdout="true")
                if "fetch --deepen=50" in cmd_str:
                    mock_run_cmd.deepen_called = True
                    return MagicMock(returncode=0, stdout="")
                return MagicMock(returncode=0, stdout="")

            mock_run_cmd.deepen_called = False
            mock_run_cmd.side_effect = side_effect

            success, msg = sync_and_reconcile_pre_push(self.work_dir, target_branch="main")
            self.assertTrue(success, msg)
            self.assertTrue(mock_run_cmd.deepen_called)

    def test_pre_push_sync_recognizes_expanded_unmerged_prefixes(self):
        # Verify unmerged status check recognizes AU, UA, and DD prefixes
        with patch("sandbox_executor.entrypoint.executor.run_cmd") as mock_run_cmd:

            def side_effect(args, **kwargs):
                cmd_str = " ".join(args)
                if "remote get-url" in cmd_str:
                    return MagicMock(returncode=0, stdout="origin")
                if "rev-parse --verify origin/main" in cmd_str:
                    return MagicMock(returncode=0, stdout="origin/main")
                if "rev-parse origin/main" in cmd_str:
                    return MagicMock(returncode=0, stdout="tip123")
                if "merge-base HEAD origin/main" in cmd_str:
                    return MagicMock(returncode=0, stdout="base123")
                if "merge origin/main" in cmd_str:
                    return MagicMock(returncode=1, stdout="CONFLICT")
                if "diff --name-only --diff-filter=U" in cmd_str:
                    return MagicMock(returncode=0, stdout="")
                if "status --porcelain" in cmd_str:
                    return MagicMock(returncode=0, stdout="AU non_ledger_conflict.txt\n")
                if "merge --abort" in cmd_str:
                    return MagicMock(returncode=0, stdout="")
                return MagicMock(returncode=0, stdout="")

            mock_run_cmd.side_effect = side_effect
            success, msg = sync_and_reconcile_pre_push(self.work_dir, target_branch="main")
            self.assertFalse(success)
            self.assertIn("Non-ledger merge conflicts detected", msg)
            self.assertIn("non_ledger_conflict.txt", msg)
