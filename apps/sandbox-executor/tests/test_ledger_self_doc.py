from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

_APP_ROOT = Path(__file__).resolve().parents[1]
_LEDGER_DIR = _APP_ROOT / "src" / "sandbox_executor" / "ledger"

SCOPED_LEDGER_FILES: tuple[str, ...] = (
    "__init__.py",
    "fragments.py",
    "lock.py",
    "merged.py",
    "order.py",
    "query.py",
    "query_filters.py",
    "reconcile.py",
    "route.py",
    "rows.py",
)

_ENCODING_COOKIE_RE = re.compile(r"coding[:=]\s*[-\w.]+")
_PRAGMA_COMMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^#\s*noqa(?::|\b)", re.IGNORECASE),
    re.compile(r"^#\s*type:\s*ignore\b", re.IGNORECASE),
    re.compile(r"^#\s*pragma:\s*no\s+cover\b", re.IGNORECASE),
)

_DOCSTRING_NODE_TYPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _find_docstrings(source: str, relative_path: str) -> list[tuple[str, int, str]]:
    tree = ast.parse(source, filename=relative_path)
    violations: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, _DOCSTRING_NODE_TYPES):
            continue
        if ast.get_docstring(node, clean=False) is None:
            continue
        if isinstance(node, ast.Module):
            line_number = getattr(node.body[0], "lineno", 1) if node.body else 1
            node_name = "<module>"
        else:
            line_number = node.lineno
            node_name = node.name
        violations.append((relative_path, line_number, node_name))
    return violations


def _is_allowlisted_comment(comment_text: str, line_number: int) -> bool:
    if line_number == 1 and comment_text.startswith("#!"):
        return True
    if line_number in (1, 2) and _ENCODING_COOKIE_RE.search(comment_text):
        return True
    return any(pattern.match(comment_text) for pattern in _PRAGMA_COMMENT_PATTERNS)


def _find_disallowed_comments(source: str, relative_path: str) -> list[tuple[str, int, str]]:
    violations: list[tuple[str, int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        line_number = token.start[0]
        if not _is_allowlisted_comment(token.string, line_number):
            violations.append((relative_path, line_number, token.string))
    return violations


def test_scoped_ledger_modules_match_package_directory() -> None:
    actual = tuple(sorted(path.name for path in _LEDGER_DIR.glob("*.py")))
    assert actual == SCOPED_LEDGER_FILES
    for filename in SCOPED_LEDGER_FILES:
        assert (_LEDGER_DIR / filename).is_file()


@pytest.mark.parametrize("filename", SCOPED_LEDGER_FILES)
def test_ledger_module_has_no_docstrings(filename: str) -> None:
    path = _LEDGER_DIR / filename
    source = path.read_text(encoding="utf-8")
    relative_path = str(path.relative_to(_APP_ROOT))
    assert _find_docstrings(source, relative_path) == []


@pytest.mark.parametrize("filename", SCOPED_LEDGER_FILES)
def test_ledger_module_has_no_disallowed_comments(filename: str) -> None:
    path = _LEDGER_DIR / filename
    source = path.read_text(encoding="utf-8")
    relative_path = str(path.relative_to(_APP_ROOT))
    assert _find_disallowed_comments(source, relative_path) == []


def test_lock_preserves_type_ignore_pragma() -> None:
    lock_source = (_LEDGER_DIR / "lock.py").read_text(encoding="utf-8")
    comments = [
        token.string
        for token in tokenize.generate_tokens(io.StringIO(lock_source).readline)
        if token.type == tokenize.COMMENT
    ]
    assert "# type: ignore[assignment]" in comments


@pytest.mark.parametrize(
    ("comment_text", "line_number"),
    [
        ("#!/usr/bin/env python3", 1),
        ("# -*- coding: utf-8 -*-", 1),
        ("# coding=utf-8", 2),
        ("# type: ignore", 10),
        ("# type: ignore[assignment]", 19),
        ("# noqa", 12),
        ("# noqa: E501", 12),
        ("# pragma: no cover", 25),
        ("# PRAGMA: NO COVER", 25),
    ],
)
def test_allowlist_accepts_pragmas_shebangs_and_encoding_cookies(comment_text: str, line_number: int) -> None:
    assert _is_allowlisted_comment(comment_text, line_number)


@pytest.mark.parametrize(
    ("comment_text", "line_number"),
    [
        ("# Explanatory prose comment", 5),
        ("# (LedgerQuery field, predicate) in evaluation order.", 91),
        ("#!/usr/bin/env python3", 2),
        ("# coding: utf-8", 3),
        ("# type information for callers", 10),
        ("# pragma note about coverage", 10),
        ("# noquality check needed", 10),
    ],
)
def test_allowlist_rejects_non_pragma_comments(comment_text: str, line_number: int) -> None:
    assert not _is_allowlisted_comment(comment_text, line_number)
