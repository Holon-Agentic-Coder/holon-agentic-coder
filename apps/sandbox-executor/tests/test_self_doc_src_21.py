from __future__ import annotations

from pathlib import Path

import pytest
from scripts.self_explaining_comments import is_allowed_comment

from tests.self_doc_scanner import find_comments, find_docstrings_and_string_statements

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"

SCOPE_MODULES: tuple[str, ...] = (
    "flow/ledger_worktree.py",
    "flow/merge_git.py",
    "flow/partial_head.py",
    "flow/partial_salvage.py",
    "flow/pipeline.py",
    "flow/plan_refresh.py",
    "flow/pr_changed_files.py",
    "flow/process_reaper.py",
    "flow/resume_guard.py",
    "flow/review_restart.py",
)


def disallowed_comments(source: str) -> list[tuple[int, str]]:
    return [(line, text) for line, text in find_comments(source) if not is_allowed_comment(text, line)]


def read_scope_module(relative_path: str) -> str:
    return (PACKAGE_DIR / relative_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("relative_path", SCOPE_MODULES)
def test_scope_module_exists(relative_path: str) -> None:
    assert (PACKAGE_DIR / relative_path).is_file(), relative_path


@pytest.mark.parametrize("relative_path", SCOPE_MODULES)
def test_scope_module_has_no_docstrings(relative_path: str) -> None:
    findings = find_docstrings_and_string_statements(read_scope_module(relative_path))
    assert findings == [], f"{relative_path}: {findings}"


@pytest.mark.parametrize("relative_path", SCOPE_MODULES)
def test_scope_module_has_only_allowed_comments(relative_path: str) -> None:
    findings = disallowed_comments(read_scope_module(relative_path))
    assert findings == [], f"{relative_path}: {findings}"


def test_prepended_module_docstring_is_detected() -> None:
    source = '"""Explains the module."""\n' + read_scope_module("flow/merge_git.py")
    assert find_docstrings_and_string_statements(source)


def test_explanatory_comment_is_flagged() -> None:
    source = read_scope_module("flow/merge_git.py") + "# explains the code\n"
    assert disallowed_comments(source) != []


def test_reason_and_format_directive_comments_are_allowed() -> None:
    source = read_scope_module("flow/merge_git.py") + "# why: reason\n# fmt: off\n"
    assert disallowed_comments(source) == []
