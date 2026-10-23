"""Unit tests for pre-commit credential and scratch file detection (Bean 0110)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.pre_commit_check import (
    check_file_credentials,
    check_spelling_orthography,
    find_github_credentials,
    is_scratch_file,
    normalise_path,
    run_checks,
)
from tests.leak_assertions import make_fake_github_token

REPO_ROOT = Path(__file__).resolve().parents[3]


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

    def test_spelling_orthography_detection(self, tmp_path: Path):
        bad_file = tmp_path / "bad_spelling.py"
        bad_file.write_text(
            "# Please initialize the worker and optimize performance.\n"
            "def worker():\n"
            "    # Behavior must be prioritized\n"
            "    pass\n",
            encoding="utf-8",
        )
        violations = check_spelling_orthography(str(bad_file))
        assert len(violations) >= 3
        assert any("initialize" in v for v in violations)
        assert any("optimize" in v for v in violations)
        assert any("Behavior" in v for v in violations)

    def test_spelling_orthography_preserves_license_invariants(self, tmp_path: Path):
        # Bean 0125 invariant: LICENSE files must NEVER be flagged or modified
        license_file = tmp_path / "LICENSE"
        license_file.write_text(
            "Apache License, Version 2.0\n"
            "You may not use this file except in compliance with the License.\n"
            'Licensed under the Apache License, Version 2.0 (the "License");\n'
            "Subject to the terms and conditions of this License, each Contributor hereby grants to You...\n"
            "including the right to optimize, serialize, or initialize code.\n",
            encoding="utf-8",
        )
        violations = check_spelling_orthography(str(license_file))
        assert len(violations) == 0

    def test_spelling_orthography_clean_australian_english(self, tmp_path: Path):
        clean_file = tmp_path / "au_code.py"
        clean_file.write_text(
            "# Please initialise the worker and optimise performance.\n"
            "def worker():\n"
            "    # Behaviour must be prioritised\n"
            "    pass\n",
            encoding="utf-8",
        )
        violations = check_spelling_orthography(str(clean_file))
        assert len(violations) == 0


class TestGitHubCredentialHygiene:
    """The URL-embedded credential pattern and the tracked-file scan for GitHub token shapes."""

    def test_url_embedded_credential_is_detected(self, tmp_path: Path):
        leaked = tmp_path / "clone.sh"
        token_body = make_fake_github_token()[4:]  # token-shaped, but not a provider prefix
        leaked.write_text(f"git clone https://x-access-token:{token_body}@github.com/myorg/myrepo.git\n")

        violations = check_file_credentials(str(leaked))
        assert any("URL-embedded GitHub credential" in v for v in violations)
        assert find_github_credentials([str(leaked)]) == [(str(leaked), 1, "URL-embedded GitHub credential")]

    @pytest.mark.parametrize(
        "line",
        [
            "git clone https://x-access-token:*******@github.com/myorg/myrepo.git",
            "git clone https://x-access-token:***REDACTED***@github.com/myorg/myrepo.git",
            "git clone https://x-access-token:${GITHUB_TOKEN}@github.com/myorg/myrepo.git",
            "git clone https://github.com/myorg/myrepo.git",
        ],
        ids=["masked", "legacy-marker", "variable-reference", "credential-free"],
    )
    def test_masked_and_referenced_forms_are_not_flagged(self, tmp_path: Path, line: str):
        doc = tmp_path / "notes.md"
        doc.write_text(line + "\n")
        assert find_github_credentials([str(doc)]) == []
        assert not any("URL-embedded" in v for v in check_file_credentials(str(doc)))

    def test_bare_github_token_is_detected_without_path_exemptions(self, tmp_path: Path):
        # Test-directory and placeholder exemptions apply to the per-commit guard, not to this scan.
        fixture = tmp_path / "tests" / "test_example.py"
        fixture.parent.mkdir()
        fixture.write_text(f'EXAMPLE_TOKEN = "{make_fake_github_token()}"\n')
        assert find_github_credentials([str(fixture)]) == [(str(fixture), 1, "GitHub Personal Access Token")]

    def test_tracked_files_contain_no_github_credentials(self, monkeypatch: pytest.MonkeyPatch):
        if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
            pytest.skip("the working tree is not a git checkout, so there is no tracked-file list to scan")
        listed = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
        monkeypatch.chdir(REPO_ROOT)

        findings = find_github_credentials(listed.stdout.splitlines())

        # Path, line and pattern name only: the matched value is never reported.
        assert findings == [], "GitHub credential shapes in tracked files: " + ", ".join(
            f"{path}:{line_no} ({name})" for path, line_no, name in findings
        )
