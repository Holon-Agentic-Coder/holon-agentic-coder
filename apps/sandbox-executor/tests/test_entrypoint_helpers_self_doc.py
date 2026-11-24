import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

ENTRYPOINT_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "entrypoint"

SCOPED_MODULES = (
    "changed_markdown.py",
    "pytest_summary.py",
    "rejected_attempt.py",
    "review_failure_detail.py",
    "review_markdown_gate.py",
    "review_verification.py",
    "sync_conflict.py",
    "taken_ids.py",
    "test_measurement.py",
    "uv_lock.py",
)

_ENCODING_COOKIE_RE = re.compile(r"coding[:=]\s*[-\w.]+")
_PRAGMA_RE = re.compile(
    r"^#\s*(?:noqa(?::\s*[A-Za-z0-9_,\s-]+)?|type:\s*ignore(?:\[[^\]]+\])?|pragma:\s*no\s+cover)\s*$"
)


def _collect_docstrings(source: str) -> list[tuple[str, int]]:
    tree = ast.parse(source)
    offenders: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and ast.get_docstring(node, clean=False) is not None
        ):
            label = "<module>" if isinstance(node, ast.Module) else node.name
            lineno = 1 if isinstance(node, ast.Module) else node.lineno
            offenders.append((label, lineno))
    return sorted(offenders, key=lambda item: item[1])


def _is_allowlisted_comment(comment: str, line_number: int) -> bool:
    if line_number == 1 and comment.startswith("#!"):
        return True
    if line_number in (1, 2) and _ENCODING_COOKIE_RE.search(comment) is not None:
        return True
    return _PRAGMA_RE.match(comment) is not None


def _collect_disallowed_comments(source: str) -> list[tuple[int, str]]:
    offenders: list[tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        line_number = token.start[0]
        if not _is_allowlisted_comment(token.string, line_number):
            offenders.append((line_number, token.string))
    return offenders


@pytest.mark.parametrize("filename", SCOPED_MODULES)
def test_scoped_module_has_no_docstrings(filename: str) -> None:
    module_path = ENTRYPOINT_DIR / filename
    assert module_path.is_file(), f"Expected module not found: {module_path}"
    offenders = _collect_docstrings(module_path.read_text(encoding="utf-8"))
    assert offenders == [], f"Unexpected docstrings in {filename}: {offenders}"


@pytest.mark.parametrize("filename", SCOPED_MODULES)
def test_scoped_module_has_no_disallowed_comments(filename: str) -> None:
    module_path = ENTRYPOINT_DIR / filename
    assert module_path.is_file(), f"Expected module not found: {module_path}"
    offenders = _collect_disallowed_comments(module_path.read_text(encoding="utf-8"))
    assert offenders == [], f"Unexpected comments in {filename}: {offenders}"


@pytest.mark.parametrize(
    ("comment", "line_number", "expected"),
    [
        ("#!/usr/bin/env python3", 1, True),
        ("#!/usr/bin/env python3", 2, False),
        ("# -*- coding: utf-8 -*-", 1, True),
        ("# coding=utf-8", 2, True),
        ("# coding: utf-8", 3, False),
        ("# noqa", 10, True),
        ("# noqa: E501, F401", 10, True),
        ("# type: ignore", 10, True),
        ("# type: ignore[arg-type]", 10, True),
        ("# pragma: no cover", 10, True),
        ("# explanatory comment", 10, False),
        ("#: Sphinx doc-comment", 10, False),
        ("# noqa because of reasons", 10, False),
        ("# type: ignore explanatory note", 10, False),
        ("# pragma: no cover extra text", 10, False),
    ],
)
def test_comment_allowlist_rules(comment: str, line_number: int, expected: bool) -> None:
    assert _is_allowlisted_comment(comment, line_number) is expected
