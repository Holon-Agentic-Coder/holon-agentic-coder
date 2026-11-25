import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

SCOPED_GITHUB_MODULES = (
    "apps/sandbox-executor/src/sandbox_executor/flow/github/adapter.py",
    "apps/sandbox-executor/src/sandbox_executor/flow/github/adapter_runner.py",
    "apps/sandbox-executor/src/sandbox_executor/flow/github/approval_invalidation.py",
    "apps/sandbox-executor/src/sandbox_executor/flow/github/review_policy.py",
    "apps/sandbox-executor/src/sandbox_executor/flow/github/review_post.py",
    "apps/sandbox-executor/src/sandbox_executor/flow/github/token_scopes.py",
)

_ENCODING_COOKIE_RE = re.compile(r"^#.*?coding[:=]\s*[-\w.]+")
_NOQA_PRAGMA_RE = re.compile(r"^#\s*noqa(?::\s*[A-Za-z0-9_,\s-]+)?\s*$", re.IGNORECASE)
_TYPE_IGNORE_PRAGMA_RE = re.compile(r"^#\s*type:\s*ignore(?:\[[^\]]+\])?\s*$")
_COVERAGE_PRAGMA_RE = re.compile(r"^#\s*pragma:\s*no\s+cover\s*$", re.IGNORECASE)
_DOCSTRING_NODE_TYPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def find_docstrings_and_string_statements(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    findings: list[tuple[int, str]] = []
    seen_positions: set[tuple[int, int]] = set()

    for node in ast.walk(tree):
        if isinstance(node, _DOCSTRING_NODE_TYPES) and ast.get_docstring(node, clean=False) is not None:
            first_stmt = node.body[0]
            pos = (first_stmt.lineno, first_stmt.col_offset)
            seen_positions.add(pos)
            findings.append((first_stmt.lineno, "docstring"))

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and (node.lineno, node.col_offset) not in seen_positions
        ):
            findings.append((node.lineno, "standalone string literal"))

    findings.sort()
    return findings


def _is_allowed_comment(comment_text: str, line_no: int, col_offset: int) -> bool:
    if line_no == 1 and col_offset == 0 and comment_text.startswith("#!"):
        return True
    if line_no in (1, 2) and _ENCODING_COOKIE_RE.search(comment_text):
        return True
    return bool(
        _NOQA_PRAGMA_RE.match(comment_text)
        or _TYPE_IGNORE_PRAGMA_RE.match(comment_text)
        or _COVERAGE_PRAGMA_RE.match(comment_text)
    )


def find_disallowed_comments(source: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        line_no, col_offset = token.start
        if not _is_allowed_comment(token.string, line_no, col_offset):
            findings.append((line_no, token.string))
    return findings


@pytest.mark.parametrize("rel_path", SCOPED_GITHUB_MODULES)
def test_scoped_module_exists(rel_path: str) -> None:
    assert (REPO_ROOT / rel_path).is_file(), f"Missing scoped GitHub module: {rel_path}"


@pytest.mark.parametrize("rel_path", SCOPED_GITHUB_MODULES)
def test_scoped_module_has_no_docstrings_or_string_statements(rel_path: str) -> None:
    source = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    findings = find_docstrings_and_string_statements(source)
    assert findings == [], f"{rel_path} contains docstrings or standalone string literals: {findings}"


@pytest.mark.parametrize("rel_path", SCOPED_GITHUB_MODULES)
def test_scoped_module_has_no_disallowed_comments(rel_path: str) -> None:
    source = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    findings = find_disallowed_comments(source)
    assert findings == [], f"{rel_path} contains non-allowlisted comments: {findings}"


def test_inspector_allows_shebang_encoding_and_tool_pragmas() -> None:
    source = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "x = 1  # noqa\n"
        "y = 2  # noqa: PLR0913\n"
        "z = 3  # type: ignore\n"
        "w = 4  # type: ignore[arg-type]\n"
        "if w == 0:  # pragma: no cover\n"
        "    pass\n"
    )
    assert find_docstrings_and_string_statements(source) == []
    assert find_disallowed_comments(source) == []


def test_inspector_rejects_docstrings_and_attribute_string_literals() -> None:
    source = (
        '"""module doc"""\n'
        "class Example:\n"
        '    """class doc"""\n'
        "    field: int\n"
        '    """attribute doc"""\n'
        "    def method(self) -> None:\n"
        '        """method doc"""\n'
        "        pass\n"
        "async def async_fn() -> None:\n"
        '    """async doc"""\n'
        "    pass\n"
    )
    findings = find_docstrings_and_string_statements(source)
    assert findings == [
        (1, "docstring"),
        (3, "docstring"),
        (5, "standalone string literal"),
        (7, "docstring"),
        (10, "docstring"),
    ]


def test_inspector_rejects_explanatory_and_sphinx_comments() -> None:
    source = (
        "x = 1\n"
        "#!/usr/bin/env python3\n"
        "# coding: utf-8\n"
        "# plain explanatory comment\n"
        "#: sphinx attribute comment\n"
        "y = 2  # explain why noqa is needed\n"
        "z = 3  # mentions pragma: no cover in prose\n"
    )
    findings = find_disallowed_comments(source)
    assert findings == [
        (2, "#!/usr/bin/env python3"),
        (3, "# coding: utf-8"),
        (4, "# plain explanatory comment"),
        (5, "#: sphinx attribute comment"),
        (6, "# explain why noqa is needed"),
        (7, "# mentions pragma: no cover in prose"),
    ]
