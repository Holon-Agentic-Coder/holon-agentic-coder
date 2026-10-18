"""Unit tests for pre-commit credential and scratch file detection (Bean 0110)."""

from pathlib import Path

from scripts.pre_commit_check import (
    check_file_credentials,
    is_scratch_file,
    normalise_path,
    run_checks,
)


class TestPreCommitCheck:
    """Test suite for scripts/pre_commit_check.py."""

    def test_normalise_path_preserves_leading_dot(self):
        # Critical Bean 0110 invariant: must preserve leading dot for hidden directories
        assert normalise_path("./.index/action_index.db") == ".index/action_index.db"
        assert normalise_path("./.agents/AGENTS.md") == ".agents/AGENTS.md"
        assert normalise_path(".agents/AGENTS.md") == ".agents/AGENTS.md"
        assert normalise_path("./tmp/scratch.diff") == "tmp/scratch.diff"
        assert normalise_path("./src/main.py") == "src/main.py"

    def test_scratch_file_detection(self):
        # Forbidden paths
        assert is_scratch_file("tmp/doc_improvements.md")[0] is True
        assert is_scratch_file("scratch/scratch_script.py")[0] is True
        assert is_scratch_file("foo.test_tmp.py")[0] is True
        assert is_scratch_file("data.bak")[0] is True
        assert is_scratch_file("file.tmp")[0] is True
        assert is_scratch_file("file.orig")[0] is True

    def test_whitelist_allows_legitimate_test_and_system_files(self):
        # Whitelisted test files
        assert is_scratch_file("tests/test_flow.py")[0] is False
        assert is_scratch_file("apps/sandbox-executor/tests/test_pre_commit_check.py")[0] is False
        assert is_scratch_file("apps/sandbox-executor/tests/test_review_loop.py")[0] is False

        # Legitimate hidden and system files
        assert is_scratch_file(".index/action_index.db")[0] is False
        assert is_scratch_file(".agents/AGENTS.md")[0] is False
        assert is_scratch_file("apps/sandbox-executor/src/sandbox_executor/flow.py")[0] is False

    def test_credential_detection(self, tmp_path: Path):
        # Create a temporary file with a hardcoded secret
        secret_file = tmp_path / "leaked_secret.py"
        secret_file.write_text(
            'API_KEY = "sk-proj-abcdef1234567890abcdef1234567890abcdef1234567890"\n',
            encoding="utf-8",
        )
        violations = check_file_credentials(str(secret_file))
        assert len(violations) > 0
        assert "OpenAI API Key" in violations[0]

    def test_credential_detection_ignores_placeholders(self, tmp_path: Path):
        placeholder_file = tmp_path / "mock_config.py"
        placeholder_file.write_text(
            'API_KEY = "***REDACTED***"\n'
            'TOKEN = "placeholder_token_value_here"\n'
            'TEST_SECRET = "example_secret_for_tests"\n',
            encoding="utf-8",
        )
        violations = check_file_credentials(str(placeholder_file))
        assert len(violations) == 0

    def test_run_checks_clean(self, tmp_path: Path):
        clean_file = tmp_path / "clean_code.py"
        clean_file.write_text("def hello():\n    return 'world'\n", encoding="utf-8")
        code = run_checks([str(clean_file)])
        assert code == 0

    def test_run_checks_scratch_detected(self):
        code = run_checks(["tmp/doc_improvements.md"])
        assert code == 1
