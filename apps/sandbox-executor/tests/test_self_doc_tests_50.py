from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

FLOW_TESTS_DIR = Path(__file__).resolve().parent / "flow"

GOVERNED_TEST_FILES: tuple[str, ...] = (
    "test_execution_recovery.py",
    "test_flow_repos_hermetic.py",
    "test_flow_worktree_run_lock.py",
    "test_git_refs.py",
    "test_git_revisions.py",
    "test_host_publish.py",
    "test_host_remote.py",
    "test_in_flight.py",
    "test_intent_questions.py",
    "test_ledger_interleave.py",
    "test_ledger_reconcile.py",
    "test_module_budget_discovery.py",
)

SHEBANG_LINE = 1
LAST_ENCODING_LINE = 2
ENCODING_PATTERN = re.compile(r"coding[:=]")
DIRECTIVE_PATTERN = re.compile(r"^(noqa\b|type:\s*\S|pragma:|fmt:\s*(off|on|skip)\b)")
DOCUMENTABLE_NODES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def opening_docstring_line(node: ast.AST) -> int | None:
    body = getattr(node, "body", [])
    if not body:
        return None
    first = body[0]
    holds_string = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
    if holds_string and isinstance(first.value.value, str):
        return first.lineno
    return None


def docstring_findings(source: str) -> list[int]:
    nodes = (node for node in ast.walk(ast.parse(source)) if isinstance(node, DOCUMENTABLE_NODES))
    lines = (opening_docstring_line(node) for node in nodes)
    return sorted(line for line in lines if line is not None)


def is_allowed_comment(text: str, line: int) -> bool:
    body = text.lstrip("#").strip()
    if body.startswith("why:"):
        return True
    if line == SHEBANG_LINE and text.startswith("#!"):
        return True
    if line <= LAST_ENCODING_LINE and ENCODING_PATTERN.search(text):
        return True
    return DIRECTIVE_PATTERN.match(body) is not None


def comment_findings(source: str) -> list[tuple[int, str]]:
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    comments = ((token.start[0], token.string) for token in tokens if token.type == tokenize.COMMENT)
    return [(line, text) for line, text in comments if not is_allowed_comment(text, line)]


@pytest.mark.parametrize("file_name", GOVERNED_TEST_FILES)
def test_governed_test_file_holds_no_docstring_or_disallowed_comment(file_name: str) -> None:
    source = (FLOW_TESTS_DIR / file_name).read_text(encoding="utf-8")
    docstrings = docstring_findings(source)
    comments = comment_findings(source)
    assert docstrings == [], f"{file_name}: docstrings at lines {docstrings}"
    assert comments == [], f"{file_name}: disallowed comments {comments}"


def test_checker_flags_a_docstring_and_a_describing_comment() -> None:
    source = 'def sample():\n    """Describe."""\n    # describes\n    return 1\n'
    assert docstring_findings(source) == [2]
    assert comment_findings(source) == [(3, "# describes")]


def test_checker_accepts_only_allowed_comments() -> None:
    source = (
        "#!/usr/bin/env python\n"
        "# -*- coding: utf-8 -*-\n"
        "# why: x\n"
        'SCRIPT = "#!/bin/sh"  # noqa: E501\n'
        "VALUE = len(SCRIPT)  # type: ignore[arg-type]\n"
        "# pragma: no cover\n"
        "# fmt: off\n"
    )
    assert docstring_findings(source) == []
    assert comment_findings(source) == []
