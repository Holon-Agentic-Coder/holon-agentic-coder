import contextlib
import io
import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.agent_runner import (
    _check_forbidden_root,
    _clear_dir_contents,
    _handle_remove_readonly,
    cleanup_repo_dir,
)
from sandbox_executor.budgets import BudgetBreach
from sandbox_executor.entrypoint import executor
from sandbox_executor.entrypoint.executor import sync_and_reconcile_pre_push
from sandbox_executor.turns import TurnResult

from tests.leak_assertions import make_gh_probe


def _turn_via_run_cmd(cmd, **kwargs):
    """Route a supervised agent turn through ``executor.run_cmd`` so tests that fake run_cmd keep faking the agent.

    The real turn supervisor spawns processes; its own behaviour is covered in test_turn_supervisor.py.
    """
    res = executor.run_cmd(cmd, cwd=kwargs.get("cwd"), check=False)
    stdout = res.stdout if isinstance(getattr(res, "stdout", None), str) else ""
    stderr = res.stderr if isinstance(getattr(res, "stderr", None), str) else ""
    returncode = res.returncode if isinstance(getattr(res, "returncode", None), int) else 0
    return TurnResult(returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture(autouse=True)
def _agent_turns_use_run_cmd():
    with patch("sandbox_executor.entrypoint.executor.run_turn", side_effect=_turn_via_run_cmd) as fake:
        yield fake


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


def _assert_supersedes(testcase, rev1, rev2):
    """Reader contract (docs/ledger_schema.md): a superseding row repeats every authoritative field it replaces."""
    for key in ("execution_notes", "execution_notes_valid", "elapsed_seconds", "budget_seconds"):
        testcase.assertIn(key, rev1)
    changed = {"status", "summary", "created_at", "ledger_revision"}
    testcase.assertEqual(
        {k: v for k, v in rev1.items() if k not in changed}, {k: v for k, v in rev2.items() if k not in changed}
    )


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

    def test_safe_float(self):
        self.assertEqual(executor._safe_float(None, 5.0), 5.0)
        self.assertEqual(executor._safe_float(None), 0.0)
        self.assertEqual(executor._safe_float(3.14), 3.14)
        self.assertEqual(executor._safe_float("2.5"), 2.5)
        self.assertEqual(executor._safe_float("invalid", 1.0), 1.0)

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

    def _run_main_with_agent_diff(self, diff_stdout: str, untracked_stdout: str = "", tip_stdout: str = "tip-sha\n"):
        """Run executor.main() with a successful agent whose working-tree diff is diff_stdout.

        Returns (summary_rows, run_cmd_argv_list).
        """
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_runner.build_cmd.return_value = ["agy", "--model", "gemini-3.5-flash", "prompt"]

        def side_effect(args, cwd=None, **kwargs):
            if "clone" in args:
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(json.dumps({"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 2.0}) + "\n")
            if args[:3] == ["git", "rev-parse", "HEAD"]:
                return MagicMock(returncode=0 if tip_stdout else 1, stdout=tip_stdout, stderr="")
            if "diff" in args and "--name-only" in args:
                return MagicMock(returncode=0, stdout=diff_stdout, stderr="")
            if "ls-files" in args and "--others" in args:
                return MagicMock(returncode=0, stdout=untracked_stdout, stderr="")
            return MagicMock(returncode=0, stdout="OK", stderr="")

        with (
            patch("sandbox_executor.entrypoint.executor.run_cmd", side_effect=side_effect) as mock_run_cmd,
            patch("sandbox_executor.entrypoint.executor.get_runner", return_value=mock_runner),
            patch("sandbox_executor.entrypoint.executor.get_repo_url", return_value="/mock/repo"),
            tempfile.TemporaryDirectory(prefix="sandbox_executor_test_") as tmp_dir,
            patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir, "HOLON_SKIP_PUSH": "1"}),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]),
        ):
            executor.main()
            with open(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl")) as ef:
                rows = [json.loads(line) for line in ef if line.strip()]
            argv = [c.args[0] for c in mock_run_cmd.call_args_list if c.args]
        return [r for r in rows if r.get("entry_type") == "summary"], argv

    def test_main_empty_diff_records_failed_and_skips_code_commit(self):
        """An agent run that only touches ledgers and plans is recorded as failed, never success."""
        summaries, argv = self._run_main_with_agent_diff(
            "holon-knowledge/ledger/executions.jsonl\0plans/P-123.md\0", untracked_stdout="plans/notes.md\0"
        )
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["status"], "failed")
        self.assertEqual(summaries[0]["failure_reason"], "empty_diff")
        self.assertIn("no changes outside holon-knowledge/ledger/, plans/ and executions/", summaries[0]["summary"])
        self.assertNotIn("test_pass_rate", summaries[0])
        self.assertFalse(any(cmd[:3] == ["git", "add", "-A"] for cmd in argv))
        diff_calls = [cmd for cmd in argv if "diff" in cmd and "--name-only" in cmd]
        self.assertTrue(diff_calls and "tip-sha" in diff_calls[0])

    def test_main_non_empty_diff_still_succeeds(self):
        """A real source change keeps the existing success path, including git add -A."""
        summaries, argv = self._run_main_with_agent_diff("holon-knowledge/ledger/executions.jsonl\0src/app.py\0")
        self.assertEqual(summaries[0]["status"], "success")
        self.assertNotIn("failure_reason", summaries[0])
        self.assertTrue(any(cmd[:3] == ["git", "add", "-A"] for cmd in argv))

    def test_main_empty_diff_guard_fails_closed_without_plan_tip(self):
        """If the plan tip cannot be resolved the diff is unverifiable, so the run is recorded as failed."""
        summaries, argv = self._run_main_with_agent_diff("src/app.py\0", tip_stdout="")
        self.assertEqual(summaries[0]["status"], "failed")
        self.assertEqual(summaries[0]["failure_reason"], "diff_unverified")
        self.assertFalse(any(cmd[:3] == ["git", "add", "-A"] for cmd in argv))

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
            self.assertFalse(any(f.startswith("executions/") for f in call_files))
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
    def test_main_over_budget_flow_writes_no_intent_rows(self, mock_get_repo_url, mock_get_runner, mock_run_cmd):
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
                rows = [json.loads(line) for line in ef if line.strip()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "over_budget")
            self.assertEqual(rows[0]["entropy"], 8.0)
            self.assertEqual(rows[0]["entropy_budget"], 5.0)
            self.assertNotIn("sub_intents", rows[0])
            self.assertIn("exceeds the entropy budget", rows[0]["summary"])
            # The agent never ran, and the intents ledger still holds only the parent row.
            mock_runner.build_cmd.assert_not_called()
            with open(os.path.join(ledger_dir, "intents.jsonl")) as inf:
                intent_rows = [json.loads(line) for line in inf if line.strip()]
            self.assertEqual([r["slug"] for r in intent_rows], ["parent-intent"])

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
        # The simulated agent changes a source file, so the empty-diff guard lets the run succeed.
        mock_tip = MagicMock(returncode=0, stdout="plan-tip-sha\n")
        mock_diff = MagicMock(returncode=0, stdout="src/app.py\0")

        def run_cmd_side_effect(args, cwd=None, **kwargs):
            if args[:3] == ["git", "rev-parse", "HEAD"]:
                return mock_tip
            if "diff" in args and "--name-only" in args:
                return mock_diff
            return mock_result

        mock_run_cmd.side_effect = run_cmd_side_effect

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

            self.assertFalse(os.path.exists(os.path.join(tmp_dir, "executions")))
            with open(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl")) as lf:
                entry = json.loads(lf.readline())
            self.assertEqual(entry["status"], "failure")
            self.assertEqual(entry["summary"], "Plan execution failed with exit code 1")

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

            self.assertFalse(os.path.exists(os.path.join(tmp_dir, "executions")))
            with open(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl")) as lf:
                entry = json.loads(lf.readline())
            self.assertTrue(entry["agent_output_truncated"])
            self.assertLessEqual(entry["agent_output_bytes"], 1024)
            self.assertGreater(entry["agent_output_bytes"], 0)

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

            self.assertFalse(os.path.exists(os.path.join(tmp_dir, "executions")))
            with open(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl")) as lf:
                ledger_content = lf.read()

            for secret in (secret_gh, secret_gh_tok, secret_key):
                self.assertNotIn(secret, ledger_content, f"Secret {secret} leaked into executions.jsonl!")

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
        straddle_value = make_gh_probe()
        straddle = 5
        # boundary = len(output) - tail_budget must land `straddle` bytes into the value, which
        # starts after the "GH_TOKEN=" anchor: suffix_len = tail_budget + straddle - len(straddle_value) - 1.
        prefix = "noise line\n" * 400
        suffix = "y" * (tail_budget + straddle - len(straddle_value) - 1)
        agent_stdout = f"{prefix}GH_TOKEN={straddle_value}\n{suffix}"
        self.assertEqual(
            len(agent_stdout) - tail_budget,
            len(prefix) + len("GH_TOKEN=") + straddle,
            "Test geometry must put the truncation boundary inside the value.",
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
                    "GITHUB_TOKEN": straddle_value,
                    "GH_TOKEN": straddle_value,
                    "HOLON_AGENT_KEY": straddle_value,
                },
            ),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir"),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            self.assertFalse(os.path.exists(os.path.join(tmp_dir, "executions")))
            with open(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl")) as lf:
                ledger_content = lf.read()

            self.assertNotIn(straddle_value, ledger_content)
            for start in range(len(straddle_value) - 7):
                window = straddle_value[start : start + 8]
                self.assertNotIn(window, ledger_content, f"Secret fragment {window!r} leaked into the ledger row.")

    def test_agent_output_byte_bound_never_strands_secret_fragment(self):
        """Test that no credential fragment survives the pipeline at any straddle offset.

        Also pins why the ordering and the line-aligned cut both matter: a raw byte cut followed by
        redaction strands a fragment for a non-empty set of offsets.
        """
        from sandbox_executor.entrypoint.executor import redact_agent_secrets, sanitize_agent_output

        budget = 256
        straddle_value = make_gh_probe()
        windows = [straddle_value[start : start + 8] for start in range(len(straddle_value) - 7)]

        truncated_cases = 0
        severed_offsets = []
        with patch.dict(os.environ, {"GITHUB_TOKEN": straddle_value, "GH_TOKEN": straddle_value}):
            for offset in range(1, len(straddle_value) - 1):
                suffix = "y" * (budget + offset - len(straddle_value) - 1)
                stream = f"GH_TOKEN={straddle_value}\n{suffix}"
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

            self.assertFalse(os.path.exists(os.path.join(tmp_dir, "executions")))
            with open(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl")) as lf:
                entry = json.loads(lf.readline())

            self.assertTrue(entry["agent_output_truncated"])
            self.assertLessEqual(entry["agent_output_bytes"], _MAX_REDACT_INPUT_LEN)
            for start in range(len(secret) - 7):
                window = secret[start : start + 8]
                self.assertNotIn(window, json.dumps(entry), f"Secret fragment {window!r} leaked at a 200 KB budget.")

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

            self.assertFalse(os.path.exists(os.path.join(tmp_dir, "executions")))
            with open(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl")) as lf:
                entry = json.loads(lf.readline())

            self.assertTrue(entry["agent_output_truncated"])
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

            self.assertFalse(os.path.exists(os.path.join(tmp_dir, "executions")))
            with open(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl")) as lf:
                entry = json.loads(lf.readline())
            self.assertEqual(entry["status"], "failure")
            self.assertGreater(entry["agent_output_bytes"], 0)

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
                "created_at",
            }
            self.assertTrue(required_keys.issubset(entry.keys()))
            self.assertNotIn("execution_file", entry)
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
        """Test sanitize_agent_output behaviour for budget boundaries and markers."""
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
                "Call: https://x-access-token:" + "gh" + "p_alpha123@github.com/repo.git\n"
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
            self.assertFalse(any(f.startswith("executions/") for f in tree_files))
            self.assertFalse(os.path.exists(os.path.join(workspace_dir, "executions")))

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

    @patch("sandbox_executor.entrypoint.executor.shutil.rmtree")
    @patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    def test_git_recovery_failure_superseding_row_carries_agent_output_accounting(
        self, mock_get_runner, mock_cleanup, mock_rmtree
    ):
        """Regression test for Bean 0064: superseding ledger_revision: 2 row carries agent output accounting."""
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            plan_branch = "I-456/P-123/_"
            bare_dir, _plan_tip = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = os.path.join(base_dir, "workspace")

            mock_runner = MagicMock()
            mock_runner.get_version.return_value = "1.0.0"

            # Simulate agent run with output, and corrupted git objects
            def build_cmd_side_effect(*args, **kwargs):
                agent_script = (
                    "import os, shutil; print('Agent stdout line for accounting'); shutil.rmtree('.git/objects')"
                )
                return ["python3", "-c", agent_script]

            mock_runner.build_cmd.side_effect = build_cmd_side_effect
            mock_runner.validate.return_value = None
            mock_get_runner.return_value = mock_runner

            # Force git recovery verification to fail by breaking ancestry_check or tree_valid
            orig_run_cmd = executor.run_cmd

            def failing_run_cmd(cmd, cwd=None, **kwargs):
                if isinstance(cmd, list) and len(cmd) >= 4 and cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
                    # Return non-zero to fail recovery verification
                    return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="not ancestor")
                return orig_run_cmd(cmd, cwd=cwd, **kwargs)

            with (
                patch.dict(os.environ, {"HOLON_REPO_DIR": workspace_dir}),
                patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=workspace_dir),
                patch("sandbox_executor.entrypoint.executor.get_repo_url", return_value=bare_dir),
                patch("sandbox_executor.entrypoint.executor.run_cmd", side_effect=failing_run_cmd),
                patch("sys.argv", ["executor.py", plan_branch, "antigravity-agent", "gemini-3.5-flash"]),
            ):
                executor.main()

            ledger_file = os.path.join(workspace_dir, "holon-knowledge", "ledger", "executions.jsonl")
            self.assertTrue(os.path.exists(ledger_file))
            with open(ledger_file) as f:
                entries = [json.loads(line) for line in f if line.strip()]

            # There should be 2 entries: rev 1 and rev 2
            self.assertEqual(len(entries), 2)
            rev1 = entries[0]
            rev2 = entries[1]

            self.assertEqual(rev1["ledger_revision"], 1)
            self.assertEqual(rev2["ledger_revision"], 2)
            self.assertEqual(rev2["status"], "failure")
            self.assertIn("Git recovery failure", rev2["summary"])

            # Reader contract: superseding row repeats authoritative fields
            self.assertNotIn("execution_file", rev1)
            self.assertNotIn("execution_file", rev2)
            self.assertEqual(rev2["agent_output_truncated"], rev1["agent_output_truncated"])
            self.assertEqual(rev2["agent_output_bytes"], rev1["agent_output_bytes"])
            self.assertGreater(rev2["agent_output_bytes"], 0)
            # C4-I2: the superseding row repeats every authoritative field of revision 1, including the Bean 0138/0139
            # notes and budget fields; only status, summary, created_at and ledger_revision may differ.
            _assert_supersedes(self, rev1, rev2)

    @patch("sandbox_executor.entrypoint.executor.sync_and_reconcile_pre_push")
    @patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_pre_push_sync_failure_superseding_row_carries_agent_output_accounting(
        self, mock_get_repo_url, mock_get_runner, mock_cleanup, mock_sync
    ):
        """Test that pre-push sync failure rev-2 row carries agent output accounting and omits execution_file."""
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.1.22"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        mock_sync.return_value = (False, "unresolvable conflicts")

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
                mock_res.stdout = "Task complete with output.\n"
                mock_res.stderr = ""
            elif "diff" in args and "--cached" in args and "--quiet" in args:
                mock_res.returncode = 1
                mock_res.stdout = ""
                mock_res.stderr = ""
            elif "rev-parse" in args and "--verify" in args and "HEAD^" in args:
                mock_res.returncode = 0
                mock_res.stdout = "parent-commit-sha\n"
            else:
                mock_res.returncode = 0
                mock_res.stdout = ""
                mock_res.stderr = ""
            return mock_res

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir, "HOLON_SKIP_PUSH": "1"}),
            patch("sandbox_executor.entrypoint.executor.run_cmd", side_effect=side_effect),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            ledger_file = os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl")
            self.assertTrue(os.path.exists(ledger_file))
            with open(ledger_file) as lf:
                entries = [json.loads(line) for line in lf if line.strip()]

            self.assertEqual(len(entries), 2)
            rev1, rev2 = entries[0], entries[1]
            self.assertEqual(rev1["ledger_revision"], 1)
            self.assertEqual(rev2["ledger_revision"], 2)
            self.assertEqual(rev2["status"], "failure")
            self.assertIn("Pre-push sync failure", rev2["summary"])
            self.assertNotIn("execution_file", rev1)
            self.assertNotIn("execution_file", rev2)
            self.assertEqual(rev2["agent_output_truncated"], rev1["agent_output_truncated"])
            self.assertEqual(rev2["agent_output_bytes"], rev1["agent_output_bytes"])
            self.assertGreater(rev2["agent_output_bytes"], 0)
            # C4-I2: the superseding row repeats every authoritative field of revision 1, including the Bean 0138/0139
            # notes and budget fields; only status, summary, created_at and ledger_revision may differ.
            _assert_supersedes(self, rev1, rev2)

    @patch("sandbox_executor.entrypoint.executor.sync_and_reconcile_pre_push")
    @patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_pre_push_sync_failure_after_decomposition_supersedes_the_decomposed_row(
        self, mock_get_repo_url, mock_get_runner, mock_cleanup, mock_sync
    ):
        """C4-I2: the decompose path has no agent-output accounting; its superseding row is built from its own row."""
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_get_runner.return_value = mock_runner
        mock_sync.return_value = (False, "unresolvable conflicts")

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = ""
            mock_res.stderr = ""
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
                                "plan_file": "plans/P-123.md",
                            }
                        )
                        + "\n"
                    )
                os.makedirs(os.path.join(cwd, "plans"), exist_ok=True)
                with open(os.path.join(cwd, "plans", "P-123.md"), "w") as f:
                    f.write("# Plan\n\n## Sub-intent list\n\n- Create DB schema\n- Build API routes\n")
            elif "diff" in args and "--cached" in args and "--quiet" in args:
                mock_res.returncode = 1
            return mock_res

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir, "HOLON_SKIP_PUSH": "1"}),
            patch("sandbox_executor.entrypoint.executor.run_cmd", side_effect=side_effect),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]),
        ):
            executor.main()

            ledger_file = os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl")
            with open(ledger_file) as lf:
                summaries = [
                    row
                    for row in (json.loads(line) for line in lf if line.strip())
                    if row.get("entry_type") == "summary"
                ]

        self.assertEqual([row["status"] for row in summaries], ["decomposed", "failure"])
        decomposed, failure = summaries
        self.assertEqual(failure["ledger_revision"], 2)
        self.assertIn("Pre-push sync failure", failure["summary"])
        self.assertEqual(failure["sub_intents"], decomposed["sub_intents"])
        self.assertEqual([item["slug"] for item in decomposed["sub_intents"]], ["create-db-schema", "build-api-routes"])
        self.assertEqual(failure["execution_id"], decomposed["execution_id"])

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

            # _get_local_safe_directories retrieves the entry via plumbing passing -c safe.directory
            with patch("subprocess.run", wraps=subprocess.run) as mock_subproc:
                entries = executor._get_local_safe_directories(workspace_dir)
                self.assertIn(redundant_path, entries)
                plumbing_cmd = mock_subproc.call_args[0][0]
                self.assertEqual(plumbing_cmd[:3], ["git", "-c", f"safe.directory={workspace_dir}"])

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

                # Verify run_cmd injects -c safe.directory even when other -c flags are present
                with patch("subprocess.run") as mock_subproc:
                    mock_subproc.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                    executor.run_cmd(["git", "-c", "core.quotePath=false", "status"], cwd=workspace_dir, check=False)
                    called_args = mock_subproc.call_args[0][0]
                    self.assertEqual(called_args[0], "git")
                    self.assertEqual(called_args[1], "-c")
                    self.assertEqual(called_args[2], f"safe.directory={workspace_dir}")
                    self.assertEqual(called_args[3:], ["-c", "core.quotePath=false", "status"])

                # Verify run_cmd does not duplicate safe.directory if already specified
                with patch("subprocess.run") as mock_subproc:
                    mock_subproc.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                    executor.run_cmd(
                        ["git", "-c", f"safe.directory={workspace_dir}", "status"], cwd=workspace_dir, check=False
                    )
                    called_args = mock_subproc.call_args[0][0]
                    self.assertEqual(called_args, ["git", "-c", f"safe.directory={workspace_dir}", "status"])

                # Verify fallback skips _get_local_safe_directories subprocess call
                with (
                    patch("sandbox_executor.entrypoint.executor._get_local_safe_directories") as mock_local_safe,
                    patch("subprocess.run") as mock_subproc,
                ):
                    mock_subproc.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                    executor.run_cmd(["git", "status"], cwd=workspace_dir, check=False)
                    mock_local_safe.assert_not_called()

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

    @patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    def test_recovery_fetch_diagnostic_is_redacted(self, mock_get_runner, mock_cleanup):
        """The printed `git fetch` diagnostic goes through the shared redactor, not raw stderr."""
        with tempfile.TemporaryDirectory(prefix="sandbox_test_") as base_dir:
            plan_branch = "I-456/P-123/_"
            bare_dir, _ = self._create_bare_remote_with_plan(base_dir, plan_branch)
            workspace_dir = os.path.join(base_dir, "workspace")
            subprocess.run(
                ["git", "clone", "--branch", plan_branch, bare_dir, workspace_dir],
                check=True,
                capture_output=True,
            )
            # Fake, runtime-built value: git echoes the unreachable remote path in its fetch error.
            fake_value = "ghp_" + "Z" * 36
            unreachable_remote = os.path.join(base_dir, f"missing-{fake_value}.git")

            mock_runner = MagicMock()
            mock_runner.get_version.return_value = "1.0.0"
            mock_runner.build_cmd.return_value = ["python3", "-c", "import shutil; shutil.rmtree('.git/objects')"]
            mock_runner.validate.return_value = None
            mock_get_runner.return_value = mock_runner

            captured = io.StringIO()
            with (
                patch.dict(os.environ, {"HOLON_REPO_DIR": workspace_dir, "HOLON_GIT_FETCH_RETRIES": "1"}),
                patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=workspace_dir),
                patch(
                    "sandbox_executor.entrypoint.executor.get_repo_url",
                    side_effect=[bare_dir, unreachable_remote],
                ),
                patch("sys.argv", ["executor.py", plan_branch, "antigravity-agent", "gemini-3.5-flash"]),
                contextlib.redirect_stderr(captured),
            ):
                executor.main()

            output = captured.getvalue()
            self.assertIn("Fetch diagnostic error:", output)
            self.assertNotIn(fake_value, output)


class TestExecutorPrePushSync(unittest.TestCase):
    """Unit tests for executor pre-push remote synchronisation and conflict isolation."""

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
        # Verify shallow repository check and deepening behaviour via mocked run_cmd
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
        # Verify unmerged status check recognises AU, UA, and DD prefixes
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

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.get_repo_url")
    def test_execution_run_records_action_and_summary_records_with_entry_type(
        self, mock_get_repo_url, mock_get_runner, mock_run_cmd
    ):
        """Test that execution run with action logging enabled records both action and summary records."""
        mock_get_repo_url.return_value = "/mock/repo"
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.3.3"
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
                                "plan_id": "P-777",
                                "intent_branch": "I-777/_",
                                "entropy": 1.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            if "agy" in args:
                mock_res.returncode = 0
                mock_res.stdout = "Task complete.\n"
                mock_res.stderr = ""
            elif "diff" in args and "--cached" in args and "--quiet" in args:
                mock_res.returncode = 1
                mock_res.stdout = ""
                mock_res.stderr = ""
            elif "rev-parse" in args and "--verify" in args and "HEAD^" in args:
                mock_res.returncode = 0
                mock_res.stdout = "parent-commit-sha\n"
            elif args[:3] == ["git", "rev-parse", "HEAD"]:
                mock_res.returncode = 0
                mock_res.stdout = "plan-tip-sha\n"
            elif "diff" in args and "--name-only" in args:
                # The simulated agent changed a source file, so the empty-diff guard allows success.
                mock_res.returncode = 0
                mock_res.stdout = "src/feature.py\0"
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
                    "HOLON_ACTION_LOGGING": "1",
                    "HOLON_ACTION_ORIGIN": "pr_review",
                    "HOLON_REVIEW_SEVERITY": "critical",
                    "HOLON_REVIEW_ITERATION": "2",
                    "HOLON_REVIEW_FINDING_IDS": "FIND-777",
                },
            ),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir"),
            patch("sys.argv", ["executor.py", "I-777/P-777/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            ledger_file = os.path.join(tmp_dir, "holon-knowledge", "ledger", "executions.jsonl")
            self.assertTrue(os.path.exists(ledger_file))
            with open(ledger_file, encoding="utf-8") as f:
                entries = [json.loads(line) for line in f if line.strip()]

            # Should contain action records and summary record
            action_entries = [e for e in entries if e.get("entry_type") == "action"]
            summary_entries = [e for e in entries if e.get("entry_type") == "summary"]

            self.assertGreaterEqual(len(action_entries), 1)
            self.assertEqual(len(summary_entries), 1)

            # Check action entry fields
            cmd_action = action_entries[0]
            self.assertEqual(cmd_action["entry_type"], "action")
            self.assertEqual(cmd_action["event_type"], "command_execution")
            self.assertEqual(cmd_action["action_origin"], "pr_review")
            self.assertEqual(cmd_action["review_severity"], "critical")
            self.assertEqual(cmd_action["review_iteration"], 2)
            self.assertEqual(cmd_action["review_finding_ids"], ["FIND-777"])
            self.assertEqual(cmd_action["outcome"], "success")

            # Check summary entry
            summary = summary_entries[0]
            self.assertEqual(summary["entry_type"], "summary")
            self.assertEqual(summary["status"], "success")


VALID_NOTES = """# Execution Record: E-test

## Summary

Implemented the plan. {filler}

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 1     |
| entropy_actual        | 1.0   |
| impact_actual         | 8.0   |
| cost_actual           | 2.0   |
| learning_value_actual | 2.0   |

## Steps Executed

- Step 1 completed.

## Tests Run

- `uv run pytest` passed.
""".format(filler="Details of the change. " * 10)


class TestExecutorIncrementalNotes(unittest.TestCase):
    """Bean 0138/0139: execution notes written by the agent, truncation tolerance, re-prompt and budget failure."""

    def run_executor(self, turns, env=None):
        scripted = list(turns)
        prompts = []
        git_cmds = []

        def fake_run_cmd(args, cwd=None, **kwargs):
            git_cmds.append(list(args))
            if "clone" in args:
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(json.dumps({"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 2.0}) + "\n")
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            # #95 empty-diff guard: the simulated agent changed a source file next to its execution notes, so a
            # successful run stays a success (the notes alone are metadata and would be an empty diff).
            if args[:3] == ["git", "rev-parse", "HEAD"]:
                result.stdout = "plan-tip-sha\n"
            elif "diff" in args and "--name-only" in args:
                result.stdout = "executions/E-1-agent-model.md\0src/app.py\0"
            return result

        def fake_run_turn(cmd, **kwargs):
            prompts.append(cmd[-1])
            self.assertTrue(scripted, "executor ran more agent turns than scripted")
            return scripted.pop(0)(kwargs["watch_path"])

        runner = MagicMock()
        runner.get_version.return_value = "1.0.0"
        runner.build_cmd.side_effect = lambda model, pf, inf, prompt: ["agy", prompt]
        tmp = tempfile.TemporaryDirectory(prefix="sandbox_executor_test_")
        self.addCleanup(tmp.cleanup)
        exit_code = 0
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, {"HOLON_REPO_DIR": tmp.name, "HOLON_SKIP_PUSH": "1", **(env or {})}),
            patch.object(executor, "run_cmd", side_effect=fake_run_cmd),
            patch.object(executor, "run_turn", side_effect=fake_run_turn),
            patch.object(executor, "get_runner", return_value=runner),
            patch.object(executor, "get_repo_url", return_value="/mock/repo"),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]),
            patch("sys.stdout", stdout),
            patch("sys.stderr", stderr),
        ):
            try:
                executor.main()
            except SystemExit as exc:
                exit_code = exc.code
        with open(os.path.join(tmp.name, "holon-knowledge/ledger/executions.jsonl")) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        return {
            "exit_code": exit_code,
            "entries": entries,
            "prompts": prompts,
            "git_cmds": git_cmds,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
        }

    @staticmethod
    def write_notes(content, returncode=0, stop_reason=None, breach=None, stdout="", stderr=""):
        def turn(path):
            if content is not None:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(content)
            return TurnResult(
                returncode=returncode, stop_reason=stop_reason, breach=breach, stdout=stdout, stderr=stderr
            )

        return turn

    def summary(self, run):
        return next(e for e in run["entries"] if e.get("entry_type") == "summary")

    def test_prompt_carries_the_notes_write_protocol(self):
        run = self.run_executor([self.write_notes(VALID_NOTES)])
        self.assertIn("WRITE PROTOCOL", run["prompts"][0])
        self.assertIn("executions/E-", run["prompts"][0])

    def test_valid_notes_after_truncated_final_message_count_as_success(self):
        run = self.run_executor([self.write_notes(VALID_NOTES, returncode=1, stop_reason="length")])
        summary = self.summary(run)
        self.assertEqual(summary["status"], "success")
        self.assertTrue(summary["execution_notes_valid"])
        self.assertIn("treating the exit status as informational", run["stdout"])
        self.assertTrue(any(c[:3] == ["git", "add", "-A"] for c in run["git_cmds"]))
        self.assertRegex(run["stdout"], r"execute: ok \(\d+\.\d min / 45 min\)")

    def test_missing_notes_logs_warning_without_failing(self):
        run = self.run_executor([self.write_notes(None)])
        summary = self.summary(run)
        self.assertEqual(summary["status"], "success")
        self.assertFalse(summary["execution_notes_valid"])
        self.assertIn("WARNING: agent did not write executions/E-", run["stderr"])
        self.assertEqual(len(run["prompts"]), 1)

    def test_partial_notes_trigger_one_targeted_reprompt(self):
        partial = VALID_NOTES.split("## Tests Run")[0]
        run = self.run_executor([self.write_notes(partial), self.write_notes("## Tests Run\n\n- passed\n")])
        self.assertEqual(len(run["prompts"]), 2)
        reprompt = run["prompts"][1]
        self.assertIn("`## Tests Run`", reprompt)
        self.assertNotIn("`## Summary`", reprompt.split("missing required content")[-1])
        self.assertIn("Do not modify any code", reprompt)
        self.assertTrue(self.summary(run)["execution_notes_valid"])

    def test_budget_failure_records_one_act_budget_and_exits_non_zero(self):
        stall = BudgetBreach("execute", "stall", 90, 92)
        run = self.run_executor(
            [self.write_notes("# Execution Record: E-1\n", breach=stall), self.write_notes(None, breach=stall)]
        )
        self.assertEqual(run["exit_code"], 1)
        budget = [e for e in run["entries"] if str(e.get("action_id", "")).startswith("ACT-BUDGET-")]
        self.assertEqual(len(budget), 1)
        self.assertEqual(budget[0]["stage"], "execute")
        self.assertEqual(budget[0]["budget_name"], "stall")
        self.assertEqual(budget[0]["outcome"], "failure")
        self.assertEqual(self.summary(run)["status"], "failure")
        # The partial notes are salvaged into the commit; code changes are not staged on failure.
        add_cmds = [c for c in run["git_cmds"] if c[:2] == ["git", "add"]]
        self.assertTrue(any(any(arg.startswith("executions/E-") for arg in c) for c in add_cmds))
        self.assertFalse(any(c[:3] == ["git", "add", "-A"] for c in add_cmds))

    def test_breach_then_notes_only_resume_is_not_success(self):
        # I1: the notes are usable from the start by design, the implementation turn breaches, and the notes-only
        # resume exits 0. The execution must still be a recorded failure and the partial code must not be committed.
        turn_breach = BudgetBreach("execute", "turn", 300, 301)
        head = VALID_NOTES.split("## Steps Executed")[0]
        tail = "## Steps Executed" + VALID_NOTES.split("## Steps Executed")[1]
        run = self.run_executor(
            [self.write_notes(head, returncode=-15, breach=turn_breach), self.write_notes(tail, returncode=0)]
        )
        self.assertEqual(len(run["prompts"]), 2)
        self.assertIn("Do not modify any code", run["prompts"][1])
        summary = self.summary(run)
        self.assertEqual(summary["status"], "failure")
        self.assertIn("turn budget", summary["summary"])
        budget = [e for e in run["entries"] if str(e.get("action_id", "")).startswith("ACT-BUDGET-")]
        self.assertEqual(len(budget), 1)
        self.assertEqual(budget[0]["budget_name"], "turn")
        self.assertIn("implementation turn did not finish", budget[0]["action"])
        self.assertEqual(run["exit_code"], 1)
        add_cmds = [c for c in run["git_cmds"] if c[:2] == ["git", "add"]]
        self.assertFalse(any(c[:3] == ["git", "add", "-A"] for c in add_cmds))
        commits = [c for c in run["git_cmds"] if c[:2] == ["git", "commit"]]
        self.assertTrue(all("completed for plan" not in " ".join(c) for c in commits))

    def test_breached_notes_only_reprompt_after_clean_implementation_turn_is_success(self):
        # C3-I1: the implementation turn finished cleanly; only the notes-only re-prompt breached. The code is complete,
        # so the execution is a success, the code is staged and no ACT-BUDGET is recorded.
        partial = VALID_NOTES.split("## Tests Run")[0]
        stall = BudgetBreach("execute", "stall", 90, 91)
        run = self.run_executor([self.write_notes(partial), self.write_notes(None, returncode=-15, breach=stall)])
        self.assertEqual(len(run["prompts"]), 2)
        summary = self.summary(run)
        self.assertEqual(summary["status"], "success")
        self.assertTrue(summary["execution_notes_valid"])
        self.assertEqual(run["exit_code"], 0)
        self.assertFalse([e for e in run["entries"] if str(e.get("action_id", "")).startswith("ACT-BUDGET-")])
        self.assertTrue(any(c[:3] == ["git", "add", "-A"] for c in run["git_cmds"]))
        self.assertIn("notes-only follow-up turn stopped", run["stderr"])

    def test_breached_reprompt_with_unusable_notes_after_clean_implementation_turn_is_success(self):
        stall = BudgetBreach("execute", "stall", 90, 91)
        run = self.run_executor(
            [self.write_notes("# Execution Record: E-1\n"), self.write_notes(None, returncode=-15, breach=stall)]
        )
        summary = self.summary(run)
        self.assertEqual(summary["status"], "success")
        self.assertFalse(summary["execution_notes_valid"])
        self.assertEqual(run["exit_code"], 0)
        self.assertFalse([e for e in run["entries"] if str(e.get("action_id", "")).startswith("ACT-BUDGET-")])

    def test_breach_without_stage_time_for_resume_does_not_claim_a_resume(self):
        stage = BudgetBreach("execute", "stage", 2700, 2700)
        run = self.run_executor(
            [self.write_notes("# Execution Record: E-1\n", returncode=-15, breach=stage)],
            env={"HOLON_STAGE_BUDGET_SECONDS": "0.000001"},
        )
        self.assertEqual(len(run["prompts"]), 1)
        budget = [e for e in run["entries"] if str(e.get("action_id", "")).startswith("ACT-BUDGET-")]
        self.assertEqual(len(budget), 1)
        self.assertNotIn("resume did not recover", budget[0]["action"])
        self.assertEqual(run["exit_code"], 1)

    def test_ledger_output_and_error_context_describe_the_implementation_turn(self):
        # C3-I2: after a re-prompt, agent_output_bytes and the command action describe the implementation turn.
        partial = VALID_NOTES.split("## Tests Run")[0]
        impl_out = "implementation output line\n" * 20
        run = self.run_executor(
            [
                self.write_notes(partial, returncode=1, stop_reason="length", stdout=impl_out, stderr="impl err"),
                self.write_notes("## Tests Run\n\n- passed\n", returncode=0, stdout="ok\n"),
            ],
            env={"HOLON_ACTION_LOGGING": "1"},
        )
        summary = self.summary(run)
        self.assertEqual(summary["agent_output_bytes"], len((impl_out + "impl err").encode("utf-8")))
        actions = [e for e in run["entries"] if e.get("event_type") == "command_execution"]
        self.assertEqual(len(actions), 1)
        self.assertIn("impl err", actions[0]["error_context"])

    def test_missing_notes_warning_does_not_claim_output_is_in_the_ledger(self):
        run = self.run_executor([self.write_notes(None)])
        self.assertNotIn("agent output captured in the execution ledger", run["stderr"])
        self.assertIn("agent_output_bytes", run["stderr"])

    def test_breach_with_complete_notes_is_not_success(self):
        stage = BudgetBreach("execute", "stage", 2700, 2700)
        run = self.run_executor([self.write_notes(VALID_NOTES, returncode=-15, breach=stage)])
        self.assertEqual(len(run["prompts"]), 1)
        self.assertEqual(self.summary(run)["status"], "failure")
        self.assertEqual(run["exit_code"], 1)
        self.assertRegex(run["stdout"], r"execute: failed \(\d+\.\d min / 45 min\)")

    def test_generic_exit_one_with_usable_notes_is_failure(self):
        # Exit 1 without a structured truncation report is an agent error (auth/API), not a cut-off final message.
        run = self.run_executor([self.write_notes(VALID_NOTES, returncode=1)])
        summary = self.summary(run)
        self.assertEqual(summary["status"], "failure")
        self.assertNotIn("treating the exit status as informational", run["stdout"])
        self.assertFalse(any(c[:3] == ["git", "add", "-A"] for c in run["git_cmds"]))

    def test_truncation_with_incomplete_notes_is_failure(self):
        partial = VALID_NOTES.split("## Tests Run")[0]
        run = self.run_executor(
            [self.write_notes(partial, returncode=1, stop_reason="length"), self.write_notes(None, returncode=0)]
        )
        self.assertEqual(self.summary(run)["status"], "failure")


# --- Entropy budget handoff and explicit sub-intent lists (no fabricated child work) ---------------------------------

# The legacy capitalised plural heading, assembled from fragments so this file never spells it out.
LEGACY_HEADING_WORD = "Sub" + "-" + "Intents"


@pytest.mark.parametrize(
    ("plan_row", "intent_row", "expected"),
    [
        ({"entropy": 12.6, "entropy_budget": 16.0}, None, executor.DECISION_EXECUTE),
        ({"entropy": 12.6}, {"entropy_budget": 16.0}, executor.DECISION_EXECUTE),
        ({"entropy": 12.6}, {"entropy_budget": 5.0}, executor.DECISION_OVER_BUDGET),
        ({"entropy": 12.6, "entropy_budget": 5.0}, {"entropy_budget": 16.0}, executor.DECISION_OVER_BUDGET),
        ({"entropy": 12.6}, None, executor.DECISION_EXECUTE),
        ({"entropy": 16.0}, None, executor.DECISION_OVER_BUDGET),
        ({"entropy": None, "entropy_budget": None}, None, executor.DECISION_EXECUTE),
    ],
)
def test_should_decompose_uses_the_persisted_budget(plan_row, intent_row, expected):
    decision = executor.should_decompose(plan_row, "# Plan\nStandard plan without a list", intent_row)

    assert decision.decision == expected
    assert decision.sub_intents == []


def test_should_decompose_regression_intent_budget_16_with_entropy_12_6_executes():
    decision = executor.should_decompose({"plan_id": "P-1", "entropy": 12.6}, "# Plan\n", {"entropy_budget": 16})

    assert decision.decision == executor.DECISION_EXECUTE
    assert decision.entropy == 12.6
    assert decision.entropy_budget == 16.0


def test_should_decompose_over_budget_never_invents_child_work():
    decision = executor.should_decompose({"plan_id": "P-1", "entropy": 12.6, "entropy_budget": 5.0}, "# Plan\n")

    assert decision.decision == executor.DECISION_OVER_BUDGET
    assert decision.sub_intents == []


@pytest.mark.parametrize("heading", ["## Sub-intent list", "### Sub-intent list", f"## {LEGACY_HEADING_WORD}"])
@pytest.mark.parametrize("marker", ["- ", "* ", "1. "])
def test_should_decompose_honours_an_explicit_list(heading, marker):
    plan = (
        f"# Plan\n\n{heading}\n\n{marker}Create DB schema\n{marker}Build API routes\n"
        "\n## Step 1: Other\n\n- Not a child\n"
    )

    decision = executor.should_decompose({"entropy": 2.0, "entropy_budget": 5.0}, plan)

    assert decision.decision == executor.DECISION_DECOMPOSED
    assert decision.sub_intents == [
        {"slug": "create-db-schema", "description": "Create DB schema", "goal": "Create DB schema"},
        {"slug": "build-api-routes", "description": "Build API routes", "goal": "Build API routes"},
    ]


def test_should_decompose_explicit_list_wins_over_budget_and_dedupes_slugs():
    plan = "# Plan\n\n## Sub-intent list\n\n- Write tests\n-   \n- Write tests\n"

    decision = executor.should_decompose({"entropy": 40.0, "entropy_budget": 5.0}, plan)

    assert decision.decision == executor.DECISION_DECOMPOSED
    assert [item["slug"] for item in decision.sub_intents] == ["write-tests", "write-tests-2"]


def test_parse_sub_intent_list_folds_nested_bullets_into_their_parent_item():
    plan = (
        "# Plan\n\n## Sub-intent list\n\n- Add the parser\n  - covers tokens\n  - covers errors\n"
        "- Add the CLI flag\n    wired into the argument parser\n\n## Risks\n\n- not a child\n"
    )

    items = executor.parse_sub_intent_list(plan)

    assert items == [
        {
            "slug": "add-the-parser",
            "description": "Add the parser\n- covers tokens\n- covers errors",
            "goal": "Add the parser",
        },
        {
            "slug": "add-the-cli-flag",
            "description": "Add the CLI flag\nwired into the argument parser",
            "goal": "Add the CLI flag",
        },
    ]


@pytest.mark.parametrize("indent", [" ", "  ", "   "])
def test_parse_sub_intent_list_reads_a_top_level_list_indented_one_to_three_spaces(indent):
    plan = (
        f"# Plan\n\n## Sub-intent list\n\n{indent}- Add the parser\n{indent}  - covers tokens\n"
        f"{indent}\t- covers errors\n{indent}* Add the CLI flag\n{indent}    1. wired in\n\n## Risks\n\n- not a child\n"
    )

    items = executor.parse_sub_intent_list(plan)

    assert items == [
        {
            "slug": "add-the-parser",
            "description": "Add the parser\n- covers tokens\n- covers errors",
            "goal": "Add the parser",
        },
        {"slug": "add-the-cli-flag", "description": "Add the CLI flag\n1. wired in", "goal": "Add the CLI flag"},
    ]
    decision = executor.should_decompose({"entropy": 40.0, "entropy_budget": 5.0}, plan)
    assert decision.decision == executor.DECISION_DECOMPOSED


def test_parse_sub_intent_list_ignores_items_indented_four_or_more_spaces():
    plan = "# Plan\n\n## Sub-intent list\n\n    - indented code, not a list\n"

    assert executor.parse_sub_intent_list(plan) == []


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_parse_sub_intent_list_ignores_a_list_quoted_in_a_fence(fence):
    plan = f"# Plan\n\n{fence}markdown\n## Sub-intent list\n\n- Quoted example\n{fence}\n\n## Risks\n\n- none\n"

    assert executor.parse_sub_intent_list(plan) == []
    decision = executor.should_decompose({"entropy": 2.0, "entropy_budget": 5.0}, plan)
    assert decision.decision == executor.DECISION_EXECUTE


def test_parse_sub_intent_list_ignores_a_fence_under_the_section_heading():
    plan = "# Plan\n\n## Sub-intent list\n\n```text\n- Example child\n```\n"

    assert executor.parse_sub_intent_list(plan) == []


@pytest.mark.parametrize(
    "inner",
    ["```", "~~~~", "~~~~~", "``` not a closer"],
    ids=["shorter-backticks", "other-char", "other-char-longer", "trailing-text"],
)
def test_parse_sub_intent_list_only_closes_a_fence_on_the_same_char_at_least_as_long(inner):
    plan = f"# Plan\n\n## Sub-intent list\n\n- First\n````md\n{inner}\n- Nested example\n{inner}\n````\n- Second\n"

    assert [item["goal"] for item in executor.parse_sub_intent_list(plan)] == ["First", "Second"]


def test_parse_sub_intent_list_reads_a_real_list_after_a_closed_fence():
    plan = (
        "# Plan\n\n~~~~markdown\n## Sub-intent list\n- Quoted example\n~~~\n- still quoted\n~~~~~\n\n"
        "## Sub-intent list\n\n```\n- fenced, not a child\n```\n- Real child\n- Another real child\n"
    )

    assert [item["goal"] for item in executor.parse_sub_intent_list(plan)] == ["Real child", "Another real child"]


def test_parse_sub_intent_list_backtick_info_string_with_backtick_is_not_a_fence():
    plan = "# Plan\n\n## Sub-intent list\n\n``` inline `code` ```\n- Real child\n"

    assert [item["goal"] for item in executor.parse_sub_intent_list(plan)] == ["Real child"]


def test_parse_sub_intent_list_skips_placeholder_items():
    plan = "# Plan\n\n## Sub-intent list\n\n- None\n- N/A\n"

    assert executor.parse_sub_intent_list(plan) == []
    decision = executor.should_decompose({"entropy": 40.0, "entropy_budget": 5.0}, plan)
    assert decision.decision == executor.DECISION_OVER_BUDGET


def test_should_decompose_ignores_per_step_recommendation_bullets():
    plan = (
        "# Plan\n\n## Step 1: Thing\n\n- **Sub\u2011intent recommendation:** YES\n- **Sub\u2011intent:** NONE\n"
        "\n### Sub-intent notes\n\n- not a list either\n"
    )

    decision = executor.should_decompose({"entropy": 2.0, "entropy_budget": 5.0}, plan)

    assert decision.decision == executor.DECISION_EXECUTE
    assert decision.sub_intents == []


def _run_executor_main(tmp_path, plan_row: dict, plan_md: str | None, intent_row: dict) -> list[dict]:
    """Run executor.main() with mocked git and runner; return the executions.jsonl rows it wrote."""
    runner = MagicMock()
    runner.get_version.return_value = "1.0.0"

    def side_effect(args, cwd=None, **kwargs):
        res = MagicMock(returncode=0, stdout="", stderr="")
        if "clone" in args:
            ledger = os.path.join(cwd, "holon-knowledge/ledger")
            os.makedirs(ledger, exist_ok=True)
            with open(os.path.join(ledger, "plans.jsonl"), "w") as f:
                f.write(json.dumps(plan_row) + "\n")
            with open(os.path.join(ledger, "intents.jsonl"), "w") as f:
                f.write(json.dumps(intent_row) + "\n")
            if plan_md is not None:
                os.makedirs(os.path.join(cwd, "plans"), exist_ok=True)
                with open(os.path.join(cwd, plan_row["plan_file"]), "w") as f:
                    f.write(plan_md)
        return res

    repo = str(tmp_path / "repo")
    with (
        patch.dict(os.environ, {"HOLON_REPO_DIR": repo, "HOLON_SKIP_PUSH": "1"}),
        patch("sandbox_executor.entrypoint.executor.run_cmd", side_effect=side_effect),
        patch("sandbox_executor.entrypoint.executor.get_runner", return_value=runner),
        patch("sandbox_executor.entrypoint.executor.get_repo_url", return_value="/mock/repo"),
        patch("sandbox_executor.entrypoint.executor.cleanup_repo_dir"),
        patch("sys.argv", ["executor.py", "I-456/P-123/_", "claude-agent", "claude-opus-5-5"]),
    ):
        executor.main()
    runner.build_cmd.assert_not_called()
    ledger = os.path.join(repo, "holon-knowledge/ledger")
    with open(os.path.join(ledger, "intents.jsonl")) as f:
        assert [json.loads(line) for line in f if line.strip()] == [intent_row]
    with open(os.path.join(ledger, "executions.jsonl")) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_main_decomposed_records_sub_intents_without_intent_rows(tmp_path):
    plan_row = {"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 9.0, "plan_file": "plans/P-123.md"}
    intent_row = {"branch": "I-456", "slug": "parent", "entropy_budget": 5.0}
    plan_md = "# Plan\n\n## Sub-intent list\n\n1. Add the parser\n2. Add the CLI flag\n"

    rows = _run_executor_main(tmp_path, plan_row, plan_md, intent_row)

    summaries = [r for r in rows if r.get("entry_type") == "summary"]
    assert len(summaries) == 1
    assert summaries[0]["status"] == "decomposed"
    assert summaries[0]["entropy_budget"] == 5.0
    assert [s["slug"] for s in summaries[0]["sub_intents"]] == ["add-the-parser", "add-the-cli-flag"]


def test_main_over_budget_reads_intent_budget_when_plan_row_has_none(tmp_path):
    plan_row = {"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 12.6, "plan_file": "plans/P-123.md"}
    intent_row = {"branch": "I-456", "slug": "parent", "entropy_budget": 5.0}

    rows = _run_executor_main(tmp_path, plan_row, "# Plan\n", intent_row)

    summaries = [r for r in rows if r.get("entry_type") == "summary"]
    assert [s["status"] for s in summaries] == ["over_budget"]
    assert summaries[0]["entropy_budget"] == 5.0
    assert "sub_intents" not in summaries[0]


def test_no_placeholder_sub_intent_text_in_source():
    """The removed fallback goals must not reappear anywhere under src/ (phrases built from fragments)."""
    phrases = [
        "Implement component " + "core logic",
        "Implement integration tests " + "and validation",
        "sub-intent-" + "part-",
    ]
    src_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    offenders = []
    for dirpath, _dirs, files in os.walk(src_root):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            offenders.extend(f"{path}: {phrase}" for phrase in phrases if phrase in text)
    assert offenders == []
