from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

COMMANDS_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "cli" / "commands"

SCOPED_MODULES = (
    "_flow_resume.py",
    "_flow_worktree.py",
    "_host_publish.py",
    "_role.py",
    "_worktree_guard.py",
    "doctor.py",
    "execute.py",
    "init.py",
    "intent.py",
    "plan.py",
)

_PEP263_ENCODING_RE = re.compile(r"^#.*?coding[:=]\s*[-\w.]+")
_TOOL_PRAGMA_RE = re.compile(
    r"^#\s*(?:noqa(?:\b|:|$)|type:\s*ignore\b|pragma:\s*no\s+cover\b)",
    re.IGNORECASE,
)
_STATEMENT_LIST_ATTRS = ("body", "orelse", "finalbody", "handlers")


def _is_standalone_string_expr(stmt: ast.AST) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _iter_standalone_string_stmts(node: ast.AST) -> list[tuple[int, int, str]]:
    matches: list[tuple[int, int, str]] = []
    for attr in _STATEMENT_LIST_ATTRS:
        stmts = getattr(node, attr, None)
        if not isinstance(stmts, list):
            continue
        for stmt in stmts:
            if _is_standalone_string_expr(stmt):
                matches.append((stmt.lineno, stmt.col_offset, stmt.value.value))
    return matches


def _find_docstrings_and_string_statements(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    findings_by_pos: dict[tuple[int, int], str] = {}

    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None and node.body:
                first_stmt = node.body[0]
                findings_by_pos.setdefault((first_stmt.lineno, first_stmt.col_offset), doc)

        for lineno, col_offset, text in _iter_standalone_string_stmts(node):
            findings_by_pos.setdefault((lineno, col_offset), text)

    return [(lineno, text) for (lineno, _), text in sorted(findings_by_pos.items())]


def _is_allowed_comment(line_no: int, text: str) -> bool:
    if line_no == 1 and text.startswith("#!"):
        return True
    if line_no in (1, 2) and _PEP263_ENCODING_RE.match(text):
        return True
    return bool(_TOOL_PRAGMA_RE.match(text))


def _find_disallowed_comments(source: str) -> list[tuple[int, str]]:
    disallowed: list[tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        line_no = token.start[0]
        if not _is_allowed_comment(line_no, token.string):
            disallowed.append((line_no, token.string))
    return disallowed


@pytest.mark.parametrize("module_name", SCOPED_MODULES)
def test_scoped_module_has_no_docstrings_or_string_statements(module_name: str) -> None:
    module_path = COMMANDS_DIR / module_name
    source = module_path.read_text(encoding="utf-8")
    findings = _find_docstrings_and_string_statements(source)
    assert not findings, (
        f"{module_path} contains docstrings or standalone string statements: "
        + ", ".join(f"line {line_no}: {text!r}" for line_no, text in findings)
    )


@pytest.mark.parametrize("module_name", SCOPED_MODULES)
def test_scoped_module_has_no_disallowed_comments(module_name: str) -> None:
    module_path = COMMANDS_DIR / module_name
    source = module_path.read_text(encoding="utf-8")
    disallowed = _find_disallowed_comments(source)
    assert not disallowed, f"{module_path} contains non-allowlisted comments: " + ", ".join(
        f"line {line_no}: {text!r}" for line_no, text in disallowed
    )


def test_self_doc_checkers_enforce_allowlist_and_reject_prose() -> None:
    allowed_source = (
        "#!/usr/bin/env python3\n"
        "# coding: utf-8\n"
        "x = 1  # noqa: E501\n"
        "y = 2  # type: ignore[arg-type]\n"
        "z = 3  # pragma: no cover\n"
    )
    assert _find_docstrings_and_string_statements(allowed_source) == []
    assert _find_disallowed_comments(allowed_source) == []

    rejected_source = (
        '"""Module docstring."""\n'
        "CONST = 1\n"
        '"""Attribute docstring."""\n'
        "def fn():\n"
        '    """Function docstring."""\n'
        "    # Explanatory prose comment.\n"
        "    return CONST\n"
    )
    doc_findings = _find_docstrings_and_string_statements(rejected_source)
    assert [line_no for line_no, _ in doc_findings] == [1, 3, 5]

    comment_findings = _find_disallowed_comments(rejected_source)
    assert comment_findings == [(6, "# Explanatory prose comment.")]
