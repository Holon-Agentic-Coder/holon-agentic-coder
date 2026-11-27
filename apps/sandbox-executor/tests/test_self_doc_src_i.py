from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import pytest

from scripts.self_explaining_comments import is_allowed_comment

FLOW_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "flow"

SCOPE_MODULES: tuple[str, ...] = (
    "combined_base.py",
    "conflict_classes.py",
    "conflict_classes_lists.py",
    "conflict_classes_resolve.py",
    "conflict_classes_tokens.py",
    "conflict_resolver_attempt.py",
    "conflict_resolver_role.py",
    "constants.py",
    "context.py",
    "dispatch.py",
)

_DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _is_string_statement(node: ast.AST) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _docstring_findings(source: str) -> list[tuple[int, str]]:
    labels_by_line: dict[int, str] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, _DOCSTRING_OWNERS) and ast.get_docstring(node, clean=False) is not None:
            labels_by_line.setdefault(node.body[0].lineno, "docstring")
        elif _is_string_statement(node):
            labels_by_line.setdefault(node.lineno, "string statement")
    return sorted(labels_by_line.items())


def _comment_findings(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not is_allowed_comment(token.string, token.start[0])
    ]


@pytest.mark.parametrize("module_name", SCOPE_MODULES)
def test_scope_module_exists(module_name: str) -> None:
    assert (FLOW_DIR / module_name).is_file()


@pytest.mark.parametrize("module_name", SCOPE_MODULES)
def test_scope_module_has_no_docstrings(module_name: str) -> None:
    path = FLOW_DIR / module_name
    assert _docstring_findings(path.read_text(encoding="utf-8")) == [], path


@pytest.mark.parametrize("module_name", SCOPE_MODULES)
def test_scope_module_has_only_allowed_comments(module_name: str) -> None:
    path = FLOW_DIR / module_name
    assert _comment_findings(path.read_text(encoding="utf-8")) == [], path


def test_detectors_report_docstrings_and_prose_comments_only() -> None:
    source = (
        "#!/usr/bin/env python3\n"
        "def answer() -> int:\n"
        '    """Return the answer."""\n'
        "    # why: callers expect a constant\n"
        "    # fmt: off\n"
        "    # prose that restates the code\n"
        "    return 42\n"
    )
    assert _docstring_findings(source) == [(3, "docstring")]
    assert _comment_findings(source) == [(6, "# prose that restates the code")]
