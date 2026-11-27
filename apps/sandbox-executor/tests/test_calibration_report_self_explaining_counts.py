from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sandbox_executor.calibration import report
from sandbox_executor.calibration.report import format_markdown_report, measure_self_explaining_counts

from tests.test_calibration_report import _golden_report_dict

SECTION_TITLE = "## 7. Self-Explaining Code Counts"
BASE_MODULE = "# Existing comment.\nVALUE = 1\n"
EXECUTION_MODULE = 'VALUE = 1\n\n\ndef added():\n    """Added docstring."""\n    return VALUE\n'
TEST_IDENTITY = ("-c", "user.name=Counts Test", "-c", "user.email=counts@example.invalid")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *TEST_IDENTITY, *args], cwd=repo, check=True, capture_output=True)


def commit_module(repo: Path, text: str, message: str) -> None:
    (repo / "module.py").write_text(text, encoding="utf-8")
    git(repo, "add", "module.py")
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def base_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    commit_module(repo, BASE_MODULE, "base")
    return repo


@pytest.fixture
def execution_repo(base_repo: Path) -> Path:
    git(base_repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(base_repo, "checkout", "-q", "-b", "execution")
    commit_module(base_repo, EXECUTION_MODULE, "execution")
    git(base_repo, "checkout", "-q", "main")
    return base_repo


def report_with_counts(counts: dict) -> str:
    return format_markdown_report({**_golden_report_dict(), "self_explaining_counts": counts})


def test_the_counting_script_is_found_in_the_holon_checkout():
    assert report.SELF_EXPLAINING_SCRIPT.is_file()


def test_report_states_added_docstring_and_removed_comment(execution_repo):
    counts = measure_self_explaining_counts(str(execution_repo), "execution")
    markdown = report_with_counts(counts)
    section = markdown.split(SECTION_TITLE, 1)[1]
    assert "added 1 docstring" in section
    assert "removed 1 comment" in section
    assert "added 0 comment(s)" in section
    assert "removed 0 docstring(s)" in section
    assert "- Changed files checked: 1" in section
    assert (execution_repo / "module.py").read_text(encoding="utf-8") == BASE_MODULE


def test_report_without_counts_has_no_section():
    assert SECTION_TITLE not in format_markdown_report(_golden_report_dict())


def test_missing_merge_base_reports_counts_unavailable(base_repo):
    counts = measure_self_explaining_counts(str(base_repo), "main")
    assert "origin/main" in counts["unavailable"]
    section = report_with_counts(counts).split(SECTION_TITLE, 1)[1]
    assert "- Counts unavailable:" in section
    assert "docstring(s)" not in section
