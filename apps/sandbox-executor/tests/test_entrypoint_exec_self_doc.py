from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

_ENTRYPOINT_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "entrypoint"

_SCOPED_MODULES: tuple[str, ...] = (
    "agent_output_block.py",
    "empty_diff_excerpt.py",
    "execution_args.py",
    "execution_workspace.py",
    "executor_test_run.py",
    "git_locks.py",
    "intent_creator.py",
    "partial_resume.py",
)

_PEP263_ENCODING_RE = re.compile(r"coding[:=]\s*[-\w.]+")
_TOOL_PRAGMA_RE = re.compile(r"^#\s*(?:noqa\b|type:\s*ignore\b|pragma:\s*no\s+cover\b)", re.IGNORECASE)
_DOCSTRING_NODE_TYPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _find_docstrings(source: str) -> list[str]:
    tree = ast.parse(source)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, _DOCSTRING_NODE_TYPES) and ast.get_docstring(node, clean=False) is not None:
            name = getattr(node, "name", "<module>")
            lineno = getattr(node, "lineno", 1)
            found.append(f"{name}:{lineno}")
    return sorted(found)


def _is_allowlisted_comment(line_no: int, comment_text: str) -> bool:
    if line_no == 1 and comment_text.startswith("#!"):
        return True
    if line_no in (1, 2) and _PEP263_ENCODING_RE.search(comment_text):
        return True
    return bool(_TOOL_PRAGMA_RE.match(comment_text))


def _find_disallowed_comments(source: str) -> list[str]:
    disallowed: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        line_no = token.start[0]
        if not _is_allowlisted_comment(line_no, token.string):
            disallowed.append(f"line {line_no}: {token.string}")
    return disallowed


@pytest.mark.parametrize("filename", _SCOPED_MODULES)
def test_scoped_entrypoint_module_exists(filename: str) -> None:
    module_path = _ENTRYPOINT_DIR / filename
    assert module_path.is_file(), f"Expected scoped entrypoint module at {module_path}"


@pytest.mark.parametrize("filename", _SCOPED_MODULES)
def test_scoped_entrypoint_module_has_no_docstrings(filename: str) -> None:
    source = (_ENTRYPOINT_DIR / filename).read_text(encoding="utf-8")
    assert _find_docstrings(source) == []


@pytest.mark.parametrize("filename", _SCOPED_MODULES)
def test_scoped_entrypoint_module_has_no_disallowed_comments(filename: str) -> None:
    source = (_ENTRYPOINT_DIR / filename).read_text(encoding="utf-8")
    assert _find_disallowed_comments(source) == []


def test_intent_creator_preserves_shebang() -> None:
    first_line = (_ENTRYPOINT_DIR / "intent_creator.py").read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "#!/usr/bin/env python3"
