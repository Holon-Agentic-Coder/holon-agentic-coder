from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

GOVERNED_TEST_MODULES: tuple[str, ...] = (
    "cli/test_patch_targets.py",
    "e2e/test_flow_e2e.py",
    "flow/decomposition_fakes.py",
    "flow/fake_gh.py",
    "flow/hermetic_repos.py",
    "flow/lookup_patch.py",
    "flow/sleep_patch.py",
    "flow/stage_templates.py",
    "flow/sync_boundary_repos.py",
    "flow/test_admission.py",
    "flow/test_admission_base_files.py",
    "flow/test_admission_lock.py",
)

ENCODING_DECLARATION = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*[-\w.]+")
ALLOWED_COMMENT_PREFIXES = ("why:", "noqa", "type:", "pragma", "fmt:")
DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _opens_with_string_statement(node: ast.AST) -> bool:
    body = getattr(node, "body", [])
    if not body or not isinstance(body[0], ast.Expr):
        return False
    value = body[0].value
    return isinstance(value, ast.JoinedStr) or (isinstance(value, ast.Constant) and isinstance(value.value, str))


def _docstring_lines(source: str) -> list[int]:
    return [
        node.body[0].lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, DOCSTRING_OWNERS) and _opens_with_string_statement(node)
    ]


def _is_allowed_comment(text: str, line: int) -> bool:
    if line == 1 and text.startswith("#!"):
        return True
    if line <= 2 and ENCODING_DECLARATION.match(text):
        return True
    return text[1:].lstrip().startswith(ALLOWED_COMMENT_PREFIXES)


def _disallowed_comment_lines(source: str) -> list[int]:
    return [
        token.start[0]
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not _is_allowed_comment(token.string, token.start[0])
    ]


def _source_of(relative_path: str) -> str:
    return (TESTS_DIR / relative_path).read_text(encoding="utf-8")


def test_governed_test_modules_exist() -> None:
    missing = [path for path in GOVERNED_TEST_MODULES if not (TESTS_DIR / path).is_file()]
    assert missing == [], f"governed test modules missing: {missing}"


@pytest.mark.parametrize("relative_path", GOVERNED_TEST_MODULES, ids=GOVERNED_TEST_MODULES)
def test_governed_test_module_has_no_docstring(relative_path: str) -> None:
    lines = _docstring_lines(_source_of(relative_path))
    assert lines == [], f"{relative_path} has docstrings at lines {lines}"


@pytest.mark.parametrize("relative_path", GOVERNED_TEST_MODULES, ids=GOVERNED_TEST_MODULES)
def test_governed_test_module_has_only_why_or_directive_comments(relative_path: str) -> None:
    lines = _disallowed_comment_lines(_source_of(relative_path))
    assert lines == [], f"{relative_path} has disallowed comments at lines {lines}"
