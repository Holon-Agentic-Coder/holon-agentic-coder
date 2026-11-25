from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

_SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"
_SCOPED_MODULES = (
    "review_failure.py",
    "review_paths.py",
    "review_target.py",
)

_ENCODING_COOKIE_RE = re.compile(r"coding[:=]\s*[-\w.]+")
_ALLOWLISTED_PRAGMA_RE = re.compile(
    r"^#\s*(?:noqa(?:\s*:\s*[A-Za-z0-9_,\s-]+)?|type:\s*ignore(?:\[[^\]]+\])?|pragma:\s*no\s+cover)\s*$",
    re.IGNORECASE,
)


def _find_docstrings(source: str) -> list[str]:
    tree = ast.parse(source)
    findings: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and ast.get_docstring(node, clean=False) is not None
        ):
            name = getattr(node, "name", "<module>")
            line = getattr(node, "lineno", 1)
            findings.append(f"{type(node).__name__} {name} at line {line}")
    return findings


def _is_allowlisted_comment(line_number: int, text: str) -> bool:
    if line_number == 1 and text.startswith("#!"):
        return True
    if line_number in (1, 2) and _ENCODING_COOKIE_RE.search(text):
        return True
    return bool(_ALLOWLISTED_PRAGMA_RE.match(text))


def _find_disallowed_comments(source: str) -> list[str]:
    findings: list[str] = []
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        line_number = token.start[0]
        if not _is_allowlisted_comment(line_number, token.string):
            findings.append(f"line {line_number}: {token.string}")
    return findings


@pytest.mark.parametrize("module_name", _SCOPED_MODULES)
def test_scoped_module_has_no_docstrings_or_explanatory_comments(module_name: str) -> None:
    module_path = _SRC_DIR / module_name
    assert module_path.is_file(), f"Missing scoped module: {module_path}"
    source = module_path.read_text(encoding="utf-8")
    assert _find_docstrings(source) == []
    assert _find_disallowed_comments(source) == []


def test_docstring_detector_catches_module_class_function_and_async_function() -> None:
    sample = (
        '"""Module doc."""\n'
        "class Sample:\n"
        '    """Class doc."""\n'
        "    def method(self) -> None:\n"
        '        """Method doc."""\n'
        "async def run() -> None:\n"
        '    """Async doc."""\n'
    )
    findings = _find_docstrings(sample)
    assert len(findings) == 4
    assert any("Module <module>" in item for item in findings)
    assert any("ClassDef Sample" in item for item in findings)
    assert any("FunctionDef method" in item for item in findings)
    assert any("AsyncFunctionDef run" in item for item in findings)


def test_comment_detector_rejects_explanatory_comments_and_allows_pragmas() -> None:
    sample = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "# full-line explanation\n"
        "x = 1  # inline explanation\n"
        "y = 2  # noqa: E501\n"
        "z = 3  # type: ignore[arg-type]\n"
        "if False:  # pragma: no cover\n"
        "    pass\n"
        "w = 4  # mentions noqa in prose\n"
    )
    findings = _find_disallowed_comments(sample)
    assert findings == [
        "line 3: # full-line explanation",
        "line 4: # inline explanation",
        "line 9: # mentions noqa in prose",
    ]
