import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

_SCOPED_MODULES: tuple[str, ...] = (
    "apps/sandbox-executor/src/sandbox_executor/entrypoint/executor/__main__.py",
    "apps/sandbox-executor/src/sandbox_executor/entrypoint/executor/agent_output.py",
    "apps/sandbox-executor/src/sandbox_executor/entrypoint/executor/commands.py",
    "apps/sandbox-executor/src/sandbox_executor/entrypoint/executor/outcome.py",
    "apps/sandbox-executor/src/sandbox_executor/entrypoint/executor/pre_push.py",
    "apps/sandbox-executor/src/sandbox_executor/entrypoint/executor/repair.py",
    "apps/sandbox-executor/src/sandbox_executor/entrypoint/executor/sub_intents.py",
)

_ENCODING_RE = re.compile(r"coding[:=]\s*[-\w.]+")
_PRAGMA_RE = re.compile(r"^#\s*(?:noqa(?:\b|:)|type:\s*ignore\b|pragma:\s*no\s+cover\b)")


def _find_docstrings(source: str) -> list[tuple[str, str, int]]:
    tree = ast.parse(source)
    findings: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and ast.get_docstring(node, clean=False) is not None
        ):
            kind = type(node).__name__
            name = "<module>" if isinstance(node, ast.Module) else node.name
            lineno = 1 if isinstance(node, ast.Module) else node.lineno
            findings.append((kind, name, lineno))
    return findings


def _is_allowed_comment(lineno: int, text: str) -> bool:
    if lineno == 1 and text.startswith("#!"):
        return True
    if lineno in (1, 2) and _ENCODING_RE.search(text):
        return True
    return bool(_PRAGMA_RE.match(text))


def _find_disallowed_comments(source: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for token_type, token_string, (lineno, _col), _end, _line in tokens:
        if token_type == tokenize.COMMENT and not _is_allowed_comment(lineno, token_string):
            findings.append((lineno, token_string))
    return findings


@pytest.mark.parametrize("rel_path", _SCOPED_MODULES)
def test_scoped_executor_module_is_self_documenting(rel_path: str) -> None:
    module_path = _REPO_ROOT / rel_path
    assert module_path.is_file(), f"Scoped module missing: {rel_path}"

    source = module_path.read_text(encoding="utf-8")
    docstrings = _find_docstrings(source)
    comments = _find_disallowed_comments(source)

    assert docstrings == [], f"{rel_path} contains docstrings: {docstrings}"
    assert comments == [], f"{rel_path} contains non-allowlisted comments: {comments}"


def test_detector_catches_docstrings_and_explanatory_comments() -> None:
    source = (
        '"""Module doc."""\n'
        "\n"
        "# Explain why this exists.\n"
        "class Sample:\n"
        '    """Class doc."""\n'
        "\n"
        "    def method(self) -> int:\n"
        '        """Method doc."""\n'
        "        return 1  # inline prose mentioning noqa\n"
        "\n"
        "async def async_fn() -> None:\n"
        '    """Async doc."""\n'
        "    return None\n"
    )

    docstrings = _find_docstrings(source)
    comments = _find_disallowed_comments(source)

    assert len(docstrings) == 4
    assert {(kind, name) for kind, name, _line in docstrings} == {
        ("Module", "<module>"),
        ("ClassDef", "Sample"),
        ("FunctionDef", "method"),
        ("AsyncFunctionDef", "async_fn"),
    }
    assert comments == [
        (3, "# Explain why this exists."),
        (9, "# inline prose mentioning noqa"),
    ]


def test_detector_allows_shebang_encoding_and_tool_pragmas() -> None:
    source = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "import os  # noqa: F401\n"
        "value: int = 'x'  # type: ignore[assignment]\n"
        "if False:  # pragma: no cover\n"
        "    pass\n"
    )

    assert _find_docstrings(source) == []
    assert _find_disallowed_comments(source) == []
