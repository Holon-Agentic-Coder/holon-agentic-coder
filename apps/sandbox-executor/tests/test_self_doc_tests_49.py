from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

CLEANED_TEST_FILES = (
    "test_review_workspace_integration.py",
    "test_runner_specs.py",
    "test_sandbox_hermetic_guard.py",
    "test_sidecar_scratch_location.py",
    "test_stress_selection.py",
    "test_stress_support.py",
    "test_taken_ids.py",
    "test_test_measurement.py",
    "test_touch_set.py",
    "test_touch_set_overlap.py",
    "test_turn_live_children.py",
    "test_turns.py",
)

DOCSTRING_OWNER_TYPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
REASON_COMMENT = re.compile(r"^#\s*why:\s*\S")
ENCODING_COOKIE = re.compile(r"^#.*?coding[:=]\s*[-\w.]+")
ENCODING_COOKIE_LINES = (1, 2)
CODE_LIST = r"[A-Za-z0-9_-]+(?:\s*,\s*[A-Za-z0-9_-]+)*"
TOOL_DIRECTIVES = (
    re.compile(rf"^#\s*noqa(?::\s*{CODE_LIST})?\s*$", re.IGNORECASE),
    re.compile(rf"^#\s*type:\s*ignore(?:\[\s*{CODE_LIST}\s*\])?\s*$"),
    re.compile(r"^#\s*pragma:\s*\S.*$"),
    re.compile(r"^#\s*fmt:\s*(?:off|on|skip)\s*$"),
)


def opens_with_string_constant(node: ast.AST) -> bool:
    body = getattr(node, "body", [])
    return (
        bool(body)
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    )


def docstring_owners(source: str) -> list[str]:
    return [
        getattr(node, "name", "<module>")
        for node in ast.walk(ast.parse(source))
        if isinstance(node, DOCSTRING_OWNER_TYPES) and opens_with_string_constant(node)
    ]


def is_allowed_comment(line: int, column: int, text: str) -> bool:
    if text.startswith("#!"):
        return line == 1 and column == 0
    if line in ENCODING_COOKIE_LINES and ENCODING_COOKIE.match(text):
        return True
    return bool(REASON_COMMENT.match(text)) or any(directive.match(text) for directive in TOOL_DIRECTIVES)


def disallowed_comments(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not is_allowed_comment(token.start[0], token.start[1], token.string)
    ]


def cleaned_test_file_params() -> list[object]:
    return [pytest.param(name, id=name) for name in CLEANED_TEST_FILES]


def read_cleaned_test_file(name: str) -> str:
    return (TESTS_DIR / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", cleaned_test_file_params())
def test_cleaned_test_file_exists(name: str) -> None:
    assert (TESTS_DIR / name).is_file(), f"{name} is missing from {TESTS_DIR}"


@pytest.mark.parametrize("name", cleaned_test_file_params())
def test_cleaned_test_file_has_no_docstrings(name: str) -> None:
    owners = docstring_owners(read_cleaned_test_file(name))
    assert owners == [], f"{name} still has docstrings in: {owners}"


@pytest.mark.parametrize("name", cleaned_test_file_params())
def test_cleaned_test_file_has_only_reason_or_directive_comments(name: str) -> None:
    comments = disallowed_comments(read_cleaned_test_file(name))
    assert comments == [], f"{name} still has disallowed comments: {comments}"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param('def helper():\n    """Explain."""\n', id="function-docstring"),
        pytest.param("x = 1  # note\n", id="plain-comment"),
        pytest.param("#: attribute note\nX = 1\n", id="attribute-comment"),
        pytest.param("x = 1  # type of x is int\n", id="prose-starting-with-type"),
        pytest.param("x = 1\n#!/usr/bin/env python3\n", id="shebang-after-line-one"),
    ],
)
def test_guard_flags_docstrings_and_explanatory_comments(source: str) -> None:
    assert docstring_owners(source) or disallowed_comments(source)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("# why: the runner needs it\nx = 1\n", id="reason-comment"),
        pytest.param("x = 1  # noqa: E501\n", id="noqa-directive"),
        pytest.param("x = f(1)  # type: ignore[call-overload]\n", id="type-ignore-directive"),
        pytest.param("x = 1  # pragma: no cover\n", id="pragma-directive"),
        pytest.param("# fmt: off\nx = 1\n", id="fmt-directive"),
        pytest.param("#!/usr/bin/env python3\nx = 1\n", id="line-one-shebang"),
        pytest.param("# -*- coding: utf-8 -*-\nx = 1\n", id="encoding-cookie"),
        pytest.param('PLAN = "# Plan heading"\n', id="hash-inside-string"),
    ],
)
def test_guard_accepts_reasons_directives_and_strings(source: str) -> None:
    assert docstring_owners(source) == []
    assert disallowed_comments(source) == []
