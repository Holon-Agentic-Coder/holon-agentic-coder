from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"

SCOPED_CORE_HELPERS: tuple[str, ...] = (
    "agent_api_error.py",
    "agent_attribution.py",
    "approval_stamp.py",
    "artefacts.py",
    "attribution_check.py",
    "budget_limits.py",
    "budgets.py",
    "flow_guard.py",
    "formatting.py",
)

_DOCSTRING_NODE_TYPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
_PEP263_ENCODING_RE = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*([-_.a-zA-Z0-9]+)")
_ALLOWED_PRAGMA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^#\s*noqa(?::\s*[A-Za-z0-9]+(?:\s*,\s*[A-Za-z0-9]+)*)?\s*$"),
    re.compile(r"^#\s*type:\s*ignore(?:\[\s*[A-Za-z0-9_-]+(?:\s*,\s*[A-Za-z0-9_-]+)*\s*\])?\s*$"),
    re.compile(r"^#\s*pragma:\s*no\s+cover\s*$"),
)


def _has_leading_string_expr(body: list[ast.stmt]) -> bool:
    if not body:
        return False
    first_stmt = body[0]
    return (
        isinstance(first_stmt, ast.Expr)
        and isinstance(first_stmt.value, ast.Constant)
        and isinstance(first_stmt.value.value, str)
    )


def _find_docstrings(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, _DOCSTRING_NODE_TYPES):
            continue
        if ast.get_docstring(node, clean=False) is not None or _has_leading_string_expr(node.body):
            lineno = node.body[0].lineno if node.body else 1
            label = getattr(node, "name", "<module>")
            found.append((lineno, f"{type(node).__name__}:{label}"))
    return sorted(found)


def _is_allowlisted_comment(lineno: int, comment: str) -> bool:
    if lineno == 1 and comment.startswith("#!"):
        return True
    if lineno in (1, 2) and _PEP263_ENCODING_RE.match(comment):
        return True
    return any(pattern.match(comment) for pattern in _ALLOWED_PRAGMA_PATTERNS)


def _find_non_allowlisted_comments(source: str) -> list[tuple[int, str]]:
    violations: list[tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        lineno = token.start[0]
        if not _is_allowlisted_comment(lineno, token.string):
            violations.append((lineno, token.string))
    return violations


@pytest.mark.parametrize("filename", SCOPED_CORE_HELPERS)
def test_core_helper_has_no_docstrings_or_explanatory_comments(filename: str) -> None:
    module_path = PACKAGE_DIR / filename
    assert module_path.is_file(), f"Expected scoped helper module at {module_path}"
    source = module_path.read_text(encoding="utf-8")
    assert _find_docstrings(source) == []
    assert _find_non_allowlisted_comments(source) == []


def test_find_docstrings_detects_all_forms() -> None:
    source = (
        '"""module doc"""\n'
        "class Example:\n"
        '    """class doc"""\n'
        "    def method(self) -> None:\n"
        '        """"""\n'
        "        pass\n"
        "async def coro() -> None:\n"
        '    "async doc"\n'
        "    return None\n"
    )
    findings = _find_docstrings(source)
    labels = [label for _, label in findings]
    assert labels == [
        "Module:<module>",
        "ClassDef:Example",
        "FunctionDef:method",
        "AsyncFunctionDef:coro",
    ]


def test_find_non_allowlisted_comments_allows_pragmas_shebang_and_encoding() -> None:
    source = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "def fn(x: object) -> float:  # noqa: PLR0913\n"
        "    if x is None:  # pragma: no cover\n"
        "        return 0.0\n"
        "    return float(x)  # type: ignore[arg-type]\n"
    )
    assert _find_non_allowlisted_comments(source) == []


def test_find_non_allowlisted_comments_rejects_prose_and_misplaced_headers() -> None:
    source = (
        "x = 1\n"
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "# explanatory comment\n"
        "y = x + 1  # noqa: PLR0913 because reasons\n"
        "z = y + 1  # type: ignore[arg-type] extra text\n"
    )
    violations = _find_non_allowlisted_comments(source)
    assert [lineno for lineno, _ in violations] == [2, 3, 4, 5, 6]
