from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

CLEANED_TEST_FILES: tuple[str, ...] = (
    "flow/test_admission_order.py",
    "flow/test_calibrate_nested_refs.py",
    "flow/test_calibrate_sync_refs.py",
    "flow/test_checkpoint.py",
    "flow/test_checkpoint_store.py",
    "flow/test_child_progress.py",
    "flow/test_conflict_classes.py",
    "flow/test_conflict_resolver_attempt.py",
    "flow/test_conflict_resolver_role.py",
    "flow/test_conflict_resolver_tiers.py",
    "flow/test_context.py",
    "flow/test_end_of_flow_sync.py",
)
CLEANED_TEST_FILE_PARAMS = [pytest.param(path, id=path) for path in CLEANED_TEST_FILES]

DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
ENCODING_COOKIE = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*[-\w.]+")
MACHINE_DIRECTIVE = re.compile(r"^(?:noqa\b|type:\s*\S|pragma:|fmt:\s*(?:off|on|skip)\b)")
REASON_PREFIX = "why:"


def cleaned_test_file_source(relative_path: str) -> str:
    return (TESTS_DIR / relative_path).read_text(encoding="utf-8")


def docstring_locations(source: str) -> list[tuple[int, str]]:
    return [
        (getattr(node, "lineno", 1), getattr(node, "name", "<module>"))
        for node in ast.walk(ast.parse(source))
        if isinstance(node, DOCSTRING_OWNERS) and ast.get_docstring(node, clean=False) is not None
    ]


def is_permitted_comment(line_number: int, column: int, text: str) -> bool:
    if text.startswith("#!"):
        return line_number == 1 and column == 0
    if line_number <= 2 and ENCODING_COOKIE.match(text):
        return True
    body = text.lstrip("#").lstrip()
    return body.lower().startswith(REASON_PREFIX) or MACHINE_DIRECTIVE.match(body) is not None


def forbidden_comments(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not is_permitted_comment(token.start[0], token.start[1], token.string)
    ]


@pytest.mark.parametrize("relative_path", CLEANED_TEST_FILE_PARAMS)
def test_cleaned_test_file_exists(relative_path: str) -> None:
    assert (TESTS_DIR / relative_path).is_file(), f"{relative_path} is missing"


@pytest.mark.parametrize("relative_path", CLEANED_TEST_FILE_PARAMS)
def test_cleaned_test_file_has_no_docstrings(relative_path: str) -> None:
    findings = docstring_locations(cleaned_test_file_source(relative_path))
    assert findings == [], f"{relative_path} has docstrings: {findings}"


@pytest.mark.parametrize("relative_path", CLEANED_TEST_FILE_PARAMS)
def test_cleaned_test_file_has_only_permitted_comments(relative_path: str) -> None:
    findings = forbidden_comments(cleaned_test_file_source(relative_path))
    assert findings == [], f"{relative_path} has forbidden comments: {findings}"


@pytest.mark.parametrize(
    ("line_number", "column", "text", "permitted"),
    [
        pytest.param(3, 4, "# why: git resolves a same-named tag before a branch", True, id="why-reason"),
        pytest.param(3, 40, "# noqa: PLR0913", True, id="noqa"),
        pytest.param(3, 40, "# type: ignore[arg-type]", True, id="type-ignore"),
        pytest.param(3, 40, "# pragma: no cover", True, id="pragma"),
        pytest.param(3, 0, "# fmt: off", True, id="fmt-off"),
        pytest.param(1, 0, "#!/usr/bin/env python3", True, id="shebang-line-1"),
        pytest.param(1, 0, "# -*- coding: utf-8 -*-", True, id="encoding-line-1"),
        pytest.param(3, 4, "# Assert INTENT and PLAN were skipped", False, id="describing-comment"),
        pytest.param(3, 4, "# the type of this value is a list", False, id="mentions-type"),
        pytest.param(5, 0, "#!/usr/bin/env python3", False, id="shebang-line-5"),
        pytest.param(3, 4, "# Why this is here", False, id="why-without-colon"),
    ],
)
def test_comment_allowlist_is_pinned(line_number: int, column: int, text: str, permitted: bool) -> None:
    assert is_permitted_comment(line_number, column, text) is permitted
