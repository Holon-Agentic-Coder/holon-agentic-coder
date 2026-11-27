from __future__ import annotations

import io
import tokenize
from pathlib import Path

import pytest

from scripts.self_explaining_comments import is_allowed_comment
from tests.self_doc_scanner import find_docstrings_and_string_statements

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"

SCOPED_MODULES: tuple[str, ...] = (
    "env_registry.py",
    "env_registry_model.py",
    "intent_scope_check.py",
    "intent_scope_graph.py",
    "module_budget_guard.py",
    "preflight_outage.py",
    "redaction.py",
    "resolver_evidence.py",
    "resolver_result.py",
    "review_accounting.py",
    "review_ensemble.py",
    "review_request.py",
    "scaffold.py",
    "turns.py",
)


def disallowed_comments(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not is_allowed_comment(token.string, token.start[0])
    ]


def test_scoped_modules_are_fourteen_unique_existing_files() -> None:
    assert len(SCOPED_MODULES) == 14
    assert len(set(SCOPED_MODULES)) == 14
    assert [name for name in SCOPED_MODULES if not (PACKAGE_DIR / name).is_file()] == []


@pytest.mark.parametrize("module_name", SCOPED_MODULES)
def test_scoped_module_has_no_docstrings(module_name: str) -> None:
    source = (PACKAGE_DIR / module_name).read_text(encoding="utf-8")
    assert find_docstrings_and_string_statements(source) == []


@pytest.mark.parametrize("module_name", SCOPED_MODULES)
def test_scoped_module_has_only_reason_comments_and_directives(module_name: str) -> None:
    source = (PACKAGE_DIR / module_name).read_text(encoding="utf-8")
    assert disallowed_comments(source) == []


def test_an_inserted_module_docstring_is_reported() -> None:
    source = '"""Explain the module."""\n\nVALUE = 1\n'
    assert find_docstrings_and_string_statements(source) != []


def test_an_inserted_prose_comment_is_reported() -> None:
    source = "VALUE = 1\n# explain this\n"
    assert disallowed_comments(source) == [(2, "# explain this")]


def test_reasons_and_tool_directives_are_not_reported() -> None:
    source = "# why: reason\nVALUE = 1  # noqa: E501\n# fmt: off\nOTHER = 2\n# fmt: on\n"
    assert disallowed_comments(source) == []
