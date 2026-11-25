from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

REVIEW_REPORT_PATH = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "review_report.py"

_DOCSTRING_NODE_TYPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
_ENCODING_COOKIE_RE = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*[-\w.]+")
_ALLOWED_PRAGMA_RE = re.compile(r"^#\s*(?:noqa\b|type:\s*ignore\b|pragma:\s*no\s+cover\b)")


def _has_leading_string_expr(node: ast.AST) -> bool:
    body = getattr(node, "body", None)
    if not body:
        return False
    first = body[0]
    return isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str)


def _find_docstrings(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, _DOCSTRING_NODE_TYPES):
            continue
        if ast.get_docstring(node, clean=False) is not None or _has_leading_string_expr(node):
            lineno = getattr(node, "lineno", 1)
            name = getattr(node, "name", "<module>")
            violations.append((lineno, name))
    return sorted(violations)


def _is_allowed_comment(lineno: int, comment: str) -> bool:
    if lineno == 1 and comment.startswith("#!"):
        return True
    if lineno in (1, 2) and _ENCODING_COOKIE_RE.match(comment):
        return True
    return bool(_ALLOWED_PRAGMA_RE.match(comment))


def _find_disallowed_comments(source: str) -> list[tuple[int, str]]:
    violations: list[tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        lineno = token.start[0]
        if not _is_allowed_comment(lineno, token.string):
            violations.append((lineno, token.string))
    return violations


def test_review_report_has_no_docstrings() -> None:
    source = REVIEW_REPORT_PATH.read_text(encoding="utf-8")
    assert _find_docstrings(source) == []


def test_review_report_has_no_non_pragma_comments() -> None:
    source = REVIEW_REPORT_PATH.read_text(encoding="utf-8")
    assert _find_disallowed_comments(source) == []


def test_docstring_detector_catches_module_class_function_and_async_function() -> None:
    sample = (
        '"""module doc"""\n'
        "\n"
        "class Example:\n"
        '    """class doc"""\n'
        "\n"
        "    def method(self) -> None:\n"
        '        """method doc"""\n'
        "\n"
        "async def coro() -> None:\n"
        '    """async doc"""\n'
    )
    assert [name for _, name in _find_docstrings(sample)] == ["<module>", "Example", "method", "coro"]


def test_comment_detector_rejects_explanatory_comments_and_section_banners() -> None:
    sample = (
        "# --- Section banner ---\n"
        "value = 1  # explain why noqa is mentioned mid-sentence\n"
        "# plain explanatory comment\n"
    )
    violations = _find_disallowed_comments(sample)
    assert len(violations) == 3
    assert [line for line, _ in violations] == [1, 2, 3]


def test_comment_detector_allows_pragmas_shebang_and_encoding_headers() -> None:
    sample = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "x = 1  # noqa: E501\n"
        "y = 2  # type: ignore[arg-type]\n"
        "if False:  # pragma: no cover\n"
        "    pass\n"
    )
    assert _find_disallowed_comments(sample) == []


def test_comment_detector_rejects_misplaced_shebang_and_encoding_headers() -> None:
    sample = "x = 1\n#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n"
    violations = _find_disallowed_comments(sample)
    assert [line for line, _ in violations] == [2, 3]
