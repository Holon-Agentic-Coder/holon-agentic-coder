from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import pytest

from scripts.self_explaining_comments import is_allowed_comment

FLOW_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "flow"

SCOPE_MODULES = (
    "end_of_flow_sync.py",
    "execution_publish.py",
    "execution_recovery.py",
    "host_remote.py",
    "in_flight.py",
    "intent_questions.py",
    "lazy_exports_sync.py",
    "ledger_interleave.py",
    "ledger_reconcile.py",
    "ledger_warning.py",
)

DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def is_string_statement(node: ast.AST) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def docstring_findings(source: str) -> list[tuple[int, str]]:
    nodes = list(ast.walk(ast.parse(source)))
    docstrings = {
        id(node.body[0]): f"docstring of {type(node).__name__}"
        for node in nodes
        if isinstance(node, DOCSTRING_OWNERS) and node.body and is_string_statement(node.body[0])
    }
    return sorted(
        (node.lineno, docstrings.get(id(node), "string statement")) for node in nodes if is_string_statement(node)
    )


def disallowed_comments(source: str) -> list[tuple[int, str]]:
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
    assert docstring_findings((FLOW_DIR / module_name).read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("module_name", SCOPE_MODULES)
def test_scope_module_has_only_allowed_comments(module_name: str) -> None:
    assert disallowed_comments((FLOW_DIR / module_name).read_text(encoding="utf-8")) == []


def test_detector_reports_docstrings_string_statements_and_comments() -> None:
    source = '"""Module."""\n\n\ndef f():\n    """Function."""\n    x = 1\n    "stray"\n    return x  # explain\n'

    assert [line for line, _ in docstring_findings(source)] == [1, 5, 7]
    assert disallowed_comments(source) == [(8, "# explain")]


def test_detector_accepts_reasons_and_machine_directives() -> None:
    source = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "# why: reason\n"
        "import os  # noqa: E501\n"
        "x = os.sep  # type: ignore[arg-type]\n"
        "y = 1  # pragma: no cover\n"
        "# fmt: off\n"
        "z = 2\n"
        "# fmt: on\n"
    )

    assert docstring_findings(source) == []
    assert disallowed_comments(source) == []
