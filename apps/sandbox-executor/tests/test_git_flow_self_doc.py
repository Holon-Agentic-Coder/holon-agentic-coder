from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

GIT_FLOW_PATH = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "git_flow.py"
MAX_LINE_COUNT = 1008

_ENCODING_RE = re.compile(r"coding[:=]\s*[-\w.]+")
_TOOL_PRAGMA_RE = re.compile(r"^#\s*(?:noqa\b|type:\s*ignore\b|pragma:\s*no\s+cover\b)", re.IGNORECASE)
_DOCSTRING_NODE_TYPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _collect_docstrings(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, _DOCSTRING_NODE_TYPES) and ast.get_docstring(node, clean=False) is not None:
            lineno = getattr(node, "lineno", 1)
            name = getattr(node, "name", "<module>")
            violations.append((lineno, name))
    return sorted(violations)


def _is_allowlisted_comment(lineno: int, comment_text: str) -> bool:
    if lineno == 1 and comment_text.startswith("#!"):
        return True
    if lineno in (1, 2) and _ENCODING_RE.search(comment_text):
        return True
    return bool(_TOOL_PRAGMA_RE.match(comment_text))


def _collect_disallowed_comments(source: str) -> list[tuple[int, str]]:
    violations: list[tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        lineno = token.start[0]
        if not _is_allowlisted_comment(lineno, token.string):
            violations.append((lineno, token.string))
    return violations


def test_git_flow_module_exists() -> None:
    assert GIT_FLOW_PATH.is_file(), f"Expected git_flow module not found: {GIT_FLOW_PATH}"


def test_git_flow_has_no_docstrings() -> None:
    source = GIT_FLOW_PATH.read_text(encoding="utf-8")
    docstrings = _collect_docstrings(source)
    assert docstrings == [], f"git_flow.py still contains docstrings: {docstrings}"


def test_git_flow_has_no_non_pragma_comments() -> None:
    source = GIT_FLOW_PATH.read_text(encoding="utf-8")
    comments = _collect_disallowed_comments(source)
    assert comments == [], f"git_flow.py still contains non-pragma comments: {comments}"


def test_git_flow_line_count_reduced() -> None:
    source = GIT_FLOW_PATH.read_text(encoding="utf-8")
    line_count = len(source.splitlines())
    assert line_count < MAX_LINE_COUNT, f"git_flow.py has {line_count} lines; expected < {MAX_LINE_COUNT}"


@pytest.mark.parametrize(
    ("lineno", "comment_text", "expected"),
    [
        (1, "#!/usr/bin/env python3", True),
        (2, "#!/usr/bin/env python3", False),
        (1, "# -*- coding: utf-8 -*-", True),
        (2, "# coding=utf-8", True),
        (3, "# coding=utf-8", False),
        (10, "# noqa: E501", True),
        (10, "# type: ignore[arg-type]", True),
        (10, "# pragma: no cover", True),
        (10, "# PRAGMA: NO COVER", True),
        (10, "# Explanatory comment that should be rejected", False),
        (10, "# TODO: remove later", False),
        (10, "#: Sphinx doc comment that should be rejected", False),
    ],
)
def test_comment_allowlist_rules(lineno: int, comment_text: str, expected: bool) -> None:
    assert _is_allowlisted_comment(lineno, comment_text) is expected
