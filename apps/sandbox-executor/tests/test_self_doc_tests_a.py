from __future__ import annotations

from pathlib import Path

import pytest

from tests.self_doc_scanner import find_disallowed_comments, find_docstrings_and_string_statements

TESTS_DIR = Path(__file__).resolve().parent

CLEANED_TEST_FILES: tuple[str, ...] = (
    "test_host_worktree_sandbox.py",
    "test_executor_agent_output_redaction.py",
    "test_url_credential_redaction.py",
    "test_makefile.py",
    "test_turn_supervisor.py",
    "test_knowledge.py",
    "test_calibrate_rerun.py",
    "test_executor_sub_intents.py",
    "hermetic_fixtures.py",
    "test_agent_runner.py",
    "flow/test_pipeline.py",
    "test_ledger_append_only.py",
)

CLEANED_TEST_FILE_PARAMS = [pytest.param(path, id=path) for path in CLEANED_TEST_FILES]


def read_cleaned_test_file(relative_path: str) -> str:
    return (TESTS_DIR / relative_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("relative_path", CLEANED_TEST_FILE_PARAMS)
def test_cleaned_test_file_exists(relative_path: str) -> None:
    assert (TESTS_DIR / relative_path).is_file()


@pytest.mark.parametrize("relative_path", CLEANED_TEST_FILE_PARAMS)
def test_cleaned_test_file_has_no_docstrings(relative_path: str) -> None:
    findings = find_docstrings_and_string_statements(read_cleaned_test_file(relative_path))
    assert findings == [], f"{relative_path}: {findings}"


@pytest.mark.parametrize("relative_path", CLEANED_TEST_FILE_PARAMS)
def test_cleaned_test_file_has_only_permitted_comments(relative_path: str) -> None:
    findings = find_disallowed_comments(read_cleaned_test_file(relative_path), allow_why=True)
    assert findings == [], f"{relative_path}: {findings}"
