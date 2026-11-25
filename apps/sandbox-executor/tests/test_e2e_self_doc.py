from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

_E2E_PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"
_SCOPED_E2E_MODULES: tuple[Path, ...] = (
    _E2E_PACKAGE_DIR / "e2e_guards.py",
    _E2E_PACKAGE_DIR / "e2e_harness.py",
    _E2E_PACKAGE_DIR / "e2e_teardown.py",
    _E2E_PACKAGE_DIR / "e2e_types.py",
)

_ENCODING_COOKIE_RE = re.compile(r"^#.*coding[:=]\s*[-\w.]+")
_ALLOWED_PRAGMA_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^#\s*noqa(?::\s*[A-Za-z0-9_-]+(?:\s*,\s*[A-Za-z0-9_-]+)*)?\s*$"),
    re.compile(r"^#\s*type:\s*ignore(?:\[\s*[A-Za-z0-9_-]+(?:\s*,\s*[A-Za-z0-9_-]+)*\s*\])?\s*$"),
    re.compile(r"^#\s*pragma:\s*no\s+cover\s*$"),
)


def _find_docstrings(source: str) -> list[str]:
    tree = ast.parse(source)
    findings: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and ast.get_docstring(node, clean=False) is not None
        ):
            kind = type(node).__name__
            name = getattr(node, "name", "<module>")
            lineno = getattr(node, "lineno", 1)
            findings.append(f"{kind}:{name}:{lineno}")
    return findings


def _is_allowed_comment(lineno: int, text: str) -> bool:
    if lineno == 1 and text.startswith("#!"):
        return True
    if lineno in (1, 2) and _ENCODING_COOKIE_RE.match(text):
        return True
    return any(pattern.match(text) for pattern in _ALLOWED_PRAGMA_RES)


def _find_disallowed_comments(source: str) -> list[tuple[int, str]]:
    disallowed: list[tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        lineno = token.start[0]
        if not _is_allowed_comment(lineno, token.string):
            disallowed.append((lineno, token.string))
    return disallowed


@pytest.mark.parametrize("module_path", _SCOPED_E2E_MODULES, ids=lambda path: path.name)
def test_scoped_e2e_modules_have_no_docstrings(module_path: Path) -> None:
    assert module_path.is_file(), f"Missing scoped E2E module: {module_path}"
    source = module_path.read_text(encoding="utf-8")
    assert _find_docstrings(source) == []


@pytest.mark.parametrize("module_path", _SCOPED_E2E_MODULES, ids=lambda path: path.name)
def test_scoped_e2e_modules_have_no_disallowed_comments(module_path: Path) -> None:
    assert module_path.is_file(), f"Missing scoped E2E module: {module_path}"
    source = module_path.read_text(encoding="utf-8")
    assert _find_disallowed_comments(source) == []


def test_find_docstrings_flags_module_class_function_and_async_docstrings() -> None:
    sample = '''"""module doc"""

class Example:
    """class doc"""

    def method(self) -> None:
        """method doc"""

async def async_fn() -> None:
    """async doc"""
    def nested() -> None:
        """nested doc"""
'''
    found = _find_docstrings(sample)
    assert len(found) == 5
    assert any(entry.startswith("Module:<module>:") for entry in found)
    assert any(entry.startswith("ClassDef:Example:") for entry in found)
    assert any(entry.startswith("FunctionDef:method:") for entry in found)
    assert any(entry.startswith("AsyncFunctionDef:async_fn:") for entry in found)
    assert any(entry.startswith("FunctionDef:nested:") for entry in found)


def test_find_disallowed_comments_allows_pragmas_shebang_and_encoding() -> None:
    sample = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "x = 1  # noqa: E501\n"
        "y = 2  # type: ignore[arg-type]\n"
        "z = 3  # pragma: no cover\n"
    )
    assert _find_disallowed_comments(sample) == []


def test_find_disallowed_comments_flags_explanatory_comments_and_late_encoding() -> None:
    sample = "x = 1  # explanatory comment\ny = 2  # noqa: E501 extra prose\n# coding: utf-8\n"
    disallowed = _find_disallowed_comments(sample)
    assert [lineno for lineno, _ in disallowed] == [1, 2, 3]
