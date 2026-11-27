from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts.self_explaining_comments import is_allowed_comment, python_comments, python_docstrings

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"

SCOPED_MODULES = (
    "flow/target_sync_branch.py",
    "flow/target_sync_hooks.py",
    "flow/target_sync_merge.py",
    "flow/target_sync_record.py",
    "flow/target_sync_result.py",
    "flow/target_sync_resume.py",
    "flow/test_evidence.py",
    "flow/unrecorded_branch_adoption.py",
    "flow/worktree_checkpoints.py",
    "flow/stages/__init__.py",
)


def scoped_source(relative_path: str) -> str:
    return (PACKAGE_DIR / relative_path).read_text(encoding="utf-8")


def disallowed_comments(source: str) -> list[tuple[int, str]]:
    return [
        (comment.first_line, comment.text)
        for comment in python_comments(source)
        if not is_allowed_comment(comment.text, comment.first_line)
    ]


@pytest.mark.parametrize("relative_path", SCOPED_MODULES)
def test_scoped_module_exists(relative_path: str) -> None:
    assert (PACKAGE_DIR / relative_path).is_file()


@pytest.mark.parametrize("relative_path", SCOPED_MODULES)
def test_scoped_module_parses(relative_path: str) -> None:
    ast.parse(scoped_source(relative_path))


@pytest.mark.parametrize("relative_path", SCOPED_MODULES)
def test_scoped_module_has_no_docstrings(relative_path: str) -> None:
    docstrings = [(found.first_line, found.kind) for found in python_docstrings(scoped_source(relative_path))]
    assert docstrings == [], f"{relative_path} holds docstrings: {docstrings}"


@pytest.mark.parametrize("relative_path", SCOPED_MODULES)
def test_scoped_module_keeps_only_why_and_directive_comments(relative_path: str) -> None:
    offending = disallowed_comments(scoped_source(relative_path))
    assert offending == [], f"{relative_path} holds disallowed comments: {offending}"


def test_a_prepended_module_docstring_is_detected() -> None:
    source = '"""Describe the module."""\n' + scoped_source(SCOPED_MODULES[0])
    assert [found.kind for found in python_docstrings(source)] == ["module docstring"]


def test_a_prose_comment_is_detected_but_why_and_noqa_are_allowed() -> None:
    source = scoped_source(SCOPED_MODULES[0])
    assert disallowed_comments(source + "# describe the next line\n") == [
        (source.count("\n") + 1, "# describe the next line")
    ]
    assert disallowed_comments(source + "# why: a reason the code cannot show\nVALUE = 1  # noqa: E501\n") == []
