from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

SELF_DOC_TESTS_48_FILES: tuple[str, ...] = (
    "test_review_failure.py",
    "test_review_failure_detail.py",
    "test_review_loop_budget_guard.py",
    "test_review_loop_git_identity.py",
    "test_review_loop_worktree_head.py",
    "test_review_markdown_gate.py",
    "test_review_mounted_no_fetch.py",
    "test_review_report.py",
    "test_review_request_bulk_diff.py",
    "test_review_resolver_turn_budgets.py",
    "test_review_restart.py",
    "test_review_stacked_pr.py",
)

WHY_COMMENT = re.compile(r"^#\s*why:")
ENCODING_LINE = re.compile(r"coding[:=]")
TOOL_DIRECTIVE = re.compile(r"^#\s*(noqa\b|type:|pragma\b|fmt:)")
DOCSTRING_OWNER_TYPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
HASH = "#"


def docstring_owners(source: str) -> list[tuple[int, str]]:
    return [
        (getattr(node, "lineno", 1), getattr(node, "name", "<module>"))
        for node in ast.walk(ast.parse(source))
        if isinstance(node, DOCSTRING_OWNER_TYPES) and ast.get_docstring(node, clean=False) is not None
    ]


def is_allowed_comment(line: int, column: int, text: str) -> bool:
    if text.startswith(HASH + "!"):
        return line == 1 and column == 0
    if line <= 2 and ENCODING_LINE.search(text):
        return True
    return bool(WHY_COMMENT.match(text) or TOOL_DIRECTIVE.match(text))


def disallowed_comments(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not is_allowed_comment(token.start[0], token.start[1], token.string)
    ]


def _read_scope_file(file_name: str) -> str:
    return (TESTS_DIR / file_name).read_text(encoding="utf-8")


@pytest.mark.parametrize("file_name", SELF_DOC_TESTS_48_FILES, ids=SELF_DOC_TESTS_48_FILES)
def test_scope_file_has_no_docstring(file_name: str) -> None:
    findings = docstring_owners(_read_scope_file(file_name))
    assert findings == [], f"{file_name} holds docstrings: {findings}"


@pytest.mark.parametrize("file_name", SELF_DOC_TESTS_48_FILES, ids=SELF_DOC_TESTS_48_FILES)
def test_scope_file_has_only_why_or_directive_comments(file_name: str) -> None:
    findings = disallowed_comments(_read_scope_file(file_name))
    assert findings == [], f"{file_name} holds disallowed comments: {findings}"


def test_function_docstring_is_reported() -> None:
    source = 'def documented() -> None:\n    """Says what it does."""\n'
    assert docstring_owners(source) == [(1, "documented")]


def test_plain_comment_is_reported_while_reasons_and_directives_are_not() -> None:
    source = (
        HASH + "!/usr/bin/env python3\n"
        "value = 1  " + HASH + " plain comment\n"
        "reason = 2  " + HASH + " why: reason\n"
        "long = 3  " + HASH + " noqa: E501\n"
        "typed = 4  " + HASH + " type: ignore\n"
        "covered = 5  " + HASH + " pragma: no cover\n"
        "formatted = 6  " + HASH + " fmt: off\n"
    )
    assert disallowed_comments(source) == [(2, HASH + " plain comment")]


def test_hash_inside_string_literal_is_not_reported() -> None:
    source = 'heading = "' + HASH + ' Title"\n'
    assert disallowed_comments(source) == []


def test_acceptance_test_follows_its_own_rule() -> None:
    own_source = Path(__file__).read_text(encoding="utf-8")
    assert docstring_owners(own_source) == []
    assert disallowed_comments(own_source) == []
