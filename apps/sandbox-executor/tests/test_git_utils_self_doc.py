from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"

SCOPED_MODULES: tuple[str, ...] = (
    "git.py",
    "git_revisions.py",
    "naming.py",
    "plan_attempt.py",
    "plan_paths.py",
    "pr_patch_paths.py",
    "ref_names.py",
    "worktree_head.py",
)

_ENCODING_COMMENT_RE = re.compile(r"^#.*?coding[:=]\s*[-\w.]+")
_TOOL_PRAGMA_RE = re.compile(
    r"^#\s*(?:noqa(?::\s*[A-Za-z0-9_,\s-]+)?|type:\s*ignore(?:\[[^\]]+\])?|pragma:\s*no\s+cover)\s*$"
)
_DOCSTRING_NODE_TYPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _is_allowed_comment(comment_text: str, line_number: int) -> bool:
    if line_number == 1 and comment_text.startswith("#!"):
        return True
    if line_number in (1, 2) and _ENCODING_COMMENT_RE.match(comment_text):
        return True
    return bool(_TOOL_PRAGMA_RE.match(comment_text))


@pytest.mark.parametrize("module_name", SCOPED_MODULES)
def test_scoped_module_exists(module_name: str) -> None:
    module_path = SRC_DIR / module_name
    assert module_path.is_file(), f"Missing scoped module: {module_path}"


@pytest.mark.parametrize("module_name", SCOPED_MODULES)
def test_scoped_module_has_no_docstrings(module_name: str) -> None:
    module_path = SRC_DIR / module_name
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(module_path))

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, _DOCSTRING_NODE_TYPES):
            continue
        if ast.get_docstring(node, clean=False) is not None:
            node_name = "<module>" if isinstance(node, ast.Module) else node.name
            line_number = 1 if isinstance(node, ast.Module) else node.lineno
            offenders.append(f"{module_path}:{line_number} ({node_name})")

    assert not offenders, "Unexpected docstrings found:\n" + "\n".join(offenders)


@pytest.mark.parametrize("module_name", SCOPED_MODULES)
def test_scoped_module_has_no_non_pragma_comments(module_name: str) -> None:
    module_path = SRC_DIR / module_name
    source = module_path.read_text(encoding="utf-8")

    offenders: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        line_number = token.start[0]
        if not _is_allowed_comment(token.string, line_number):
            offenders.append(f"{module_path}:{line_number}: {token.string}")

    assert not offenders, "Unexpected non-pragma comments found:\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    ("comment_text", "line_number", "expected"),
    [
        ("#!/usr/bin/env python3", 1, True),
        ("#!/usr/bin/env python3", 2, False),
        ("# -*- coding: utf-8 -*-", 1, True),
        ("# coding=utf-8", 2, True),
        ("# coding=utf-8", 3, False),
        ("# noqa", 10, True),
        ("# noqa: E501", 10, True),
        ("# noqa: E501, F401", 10, True),
        ("# type: ignore", 10, True),
        ("# type: ignore[arg-type]", 10, True),
        ("# pragma: no cover", 10, True),
        ("# explanation", 10, False),
        ("#: doc comment", 10, False),
        ("# explanation mentioning noqa", 10, False),
        ("# explanation mentioning type: ignore", 10, False),
    ],
)
def test_comment_allowlist_rules(comment_text: str, line_number: int, expected: bool) -> None:
    assert _is_allowed_comment(comment_text, line_number) is expected
