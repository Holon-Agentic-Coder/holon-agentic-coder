from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"

SCOPED_MODULES: tuple[str, ...] = (
    "claude_credential.py",
    "claude_keychain.py",
    "claude_token_migration.py",
)

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


@pytest.mark.parametrize("module_name", SCOPED_MODULES)
def test_scoped_module_exists(module_name: str) -> None:
    module_path = SRC_DIR / module_name
    assert module_path.is_file(), f"Expected scoped module not found: {module_path}"


@pytest.mark.parametrize("module_name", SCOPED_MODULES)
def test_scoped_module_has_no_docstrings(module_name: str) -> None:
    source = (SRC_DIR / module_name).read_text(encoding="utf-8")
    docstrings = _collect_docstrings(source)
    assert docstrings == [], f"{module_name} still contains docstrings: {docstrings}"


@pytest.mark.parametrize("module_name", SCOPED_MODULES)
def test_scoped_module_has_no_non_pragma_comments(module_name: str) -> None:
    source = (SRC_DIR / module_name).read_text(encoding="utf-8")
    comments = _collect_disallowed_comments(source)
    assert comments == [], f"{module_name} still contains non-pragma comments: {comments}"


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
    ],
)
def test_comment_allowlist_rules(lineno: int, comment_text: str, expected: bool) -> None:
    assert _is_allowlisted_comment(lineno, comment_text) is expected
