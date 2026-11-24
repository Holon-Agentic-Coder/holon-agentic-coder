from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "knowledge"

SCOPED_MODULES: tuple[str, ...] = (
    "__init__.py",
    "consult.py",
    "consult_filters.py",
    "index.py",
    "ledger_sync.py",
    "promotion.py",
    "promotion_rules.py",
    "records.py",
    "stats.py",
    "wisdom.py",
)

_ENCODING_COOKIE_RE = re.compile(r"coding[:=]\s*[-\w.]+")
_PRAGMA_COMMENT_RE = re.compile(
    r"^#\s*(?:noqa(?::\s*[A-Z0-9,\s]+)?|type:\s*ignore(?:\[[^\]]+\])?|pragma:\s*no\s+cover)\s*$"
)


def _find_docstrings(source: str) -> list[tuple[str, int]]:
    tree = ast.parse(source)
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and ast.get_docstring(node, clean=False) is not None
        ):
            name = "<module>" if isinstance(node, ast.Module) else node.name
            lineno = 1 if isinstance(node, ast.Module) else node.lineno
            found.append((name, lineno))
    return found


def _is_allowlisted_comment(line_no: int, text: str) -> bool:
    if line_no == 1 and text.startswith("#!"):
        return True
    if line_no in (1, 2) and _ENCODING_COOKIE_RE.search(text):
        return True
    return _PRAGMA_COMMENT_RE.match(text) is not None


def _find_disallowed_comments(source: str) -> list[tuple[int, str]]:
    disallowed: list[tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        line_no = token.start[0]
        if not _is_allowlisted_comment(line_no, token.string):
            disallowed.append((line_no, token.string))
    return disallowed


@pytest.mark.parametrize("filename", SCOPED_MODULES)
def test_scoped_knowledge_module_exists(filename: str) -> None:
    module_path = KNOWLEDGE_DIR / filename
    assert module_path.is_file(), f"Missing scoped knowledge module: {module_path}"


@pytest.mark.parametrize("filename", SCOPED_MODULES)
def test_scoped_knowledge_module_has_no_docstrings(filename: str) -> None:
    source = (KNOWLEDGE_DIR / filename).read_text(encoding="utf-8")
    assert _find_docstrings(source) == []


@pytest.mark.parametrize("filename", SCOPED_MODULES)
def test_scoped_knowledge_module_has_no_disallowed_comments(filename: str) -> None:
    source = (KNOWLEDGE_DIR / filename).read_text(encoding="utf-8")
    assert _find_disallowed_comments(source) == []


def test_find_docstrings_detects_module_class_and_function_docstrings() -> None:
    source = (
        '"""Module doc."""\n'
        "class Sample:\n"
        '    """Class doc."""\n'
        "    def method(self) -> None:\n"
        '        """Method doc."""\n'
        "async def async_fn() -> None:\n"
        '    """Async doc."""\n'
    )
    found_names = {name for name, _ in _find_docstrings(source)}
    assert found_names == {"<module>", "Sample", "method", "async_fn"}


def test_find_disallowed_comments_allows_pragmas_shebang_and_encoding() -> None:
    source = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "x = 1  # noqa\n"
        "y = 2  # noqa: PLR0913\n"
        "z = 3  # noqa: C901, PLR0912\n"
        "a = 4  # type: ignore\n"
        "b = 5  # type: ignore[misc]\n"
        "c = 6  # pragma: no cover\n"
    )
    assert _find_disallowed_comments(source) == []


def test_find_disallowed_comments_rejects_explanatory_comments() -> None:
    source = (
        "x = 1  # explanatory comment\n"
        "y = 2  # mentions noqa in prose\n"
        "z = 3  # mentions pragma: no cover in prose\n"
    )
    disallowed = _find_disallowed_comments(source)
    assert [line_no for line_no, _ in disallowed] == [1, 2, 3]
