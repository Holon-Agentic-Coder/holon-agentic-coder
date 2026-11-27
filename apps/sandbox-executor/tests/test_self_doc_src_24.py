from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

STAGES_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "flow" / "stages"

SLICE_MODULES = (
    "calibrate.py",
    "children.py",
    "decomposition.py",
    "decomposition_summary.py",
    "empty_diff_outcome.py",
    "execute.py",
    "execute_veto.py",
    "integration.py",
    "intent.py",
    "plan.py",
)

DOCSTRING_OWNER_TYPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
ENCODING_COOKIE = re.compile(r"^#.*?coding[:=]\s*[-\w.]+")
ALLOWED_DIRECTIVES = (
    re.compile(r"^#\s*why:\s*\S"),
    re.compile(r"^#\s*noqa(?::\s*[A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*)?\s*$"),
    re.compile(r"^#\s*type:\s*\w+(?:\[[\w\s,-]+\])?\s*$"),
    re.compile(r"^#\s*pragma:\s*\S"),
    re.compile(r"^#\s*fmt:\s*(?:off|on|skip)\s*$"),
)


def is_allowed_comment(line_number: int, column: int, text: str) -> bool:
    if text.startswith("#!"):
        return line_number == 1 and column == 0
    if line_number in (1, 2) and ENCODING_COOKIE.match(text):
        return True
    return any(directive.match(text) for directive in ALLOWED_DIRECTIVES)


def docstring_owners(source: str) -> list[str]:
    return [
        getattr(node, "name", "<module>")
        for node in ast.walk(ast.parse(source))
        if isinstance(node, DOCSTRING_OWNER_TYPES) and ast.get_docstring(node, clean=False) is not None
    ]


def disallowed_comments(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not is_allowed_comment(*token.start, token.string)
    ]


@pytest.mark.parametrize("module", SLICE_MODULES)
def test_slice_module_exists(module: str) -> None:
    assert (STAGES_DIR / module).is_file()


@pytest.mark.parametrize("module", SLICE_MODULES)
def test_slice_module_has_no_docstrings(module: str) -> None:
    assert docstring_owners((STAGES_DIR / module).read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("module", SLICE_MODULES)
def test_slice_module_has_only_allowed_comments(module: str) -> None:
    assert disallowed_comments((STAGES_DIR / module).read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    ("line_number", "column", "text", "allowed"),
    [
        (5, 4, "# why: git commit -- with no pathspec commits the whole index", True),
        (5, 80, "# noqa: E501", True),
        (5, 80, "# type: ignore[arg-type]", True),
        (5, 80, "# pragma: no cover", True),
        (5, 0, "# fmt: off", True),
        (1, 0, "#!/usr/bin/env python3", True),
        (2, 0, "# -*- coding: utf-8 -*-", True),
        (3, 0, "#!/usr/bin/env python3", False),
        (5, 4, "# Refresh the local view of the plan branch", False),
        (5, 4, "# the type: ignore below is needed", False),
        (5, 4, "# why:", False),
    ],
)
def test_allowlist_accepts_only_reasons_and_directives(line_number: int, column: int, text: str, allowed: bool) -> None:
    assert is_allowed_comment(line_number, column, text) is allowed
