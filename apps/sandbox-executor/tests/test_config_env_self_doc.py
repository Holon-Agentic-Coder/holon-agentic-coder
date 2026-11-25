from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

_SANDBOX_EXECUTOR_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"
SCOPED_MODULES: tuple[Path, ...] = (
    _SANDBOX_EXECUTOR_DIR / "config_env.py",
    _SANDBOX_EXECUTOR_DIR / "config_env_keys.py",
    _SANDBOX_EXECUTOR_DIR / "config_env_model.py",
    _SANDBOX_EXECUTOR_DIR / "config_env_parse.py",
    _SANDBOX_EXECUTOR_DIR / "config_env_secrets.py",
)

_ENCODING_RE = re.compile(r"coding[:=]\s*[-\w.]+")
_PRAGMA_RE = re.compile(r"^(?:noqa(?=$|:|\s)|type: ignore(?=$|\[|\s)|pragma: no cover(?=$|\s))")


def _is_allowlisted_comment(comment_text: str, line_number: int) -> bool:
    if line_number == 1 and comment_text.startswith("#!"):
        return True
    if line_number in (1, 2) and _ENCODING_RE.search(comment_text):
        return True
    body = comment_text.removeprefix("#").strip()
    return bool(_PRAGMA_RE.match(body))


def _find_docstrings(source: str, label: str) -> list[str]:
    tree = ast.parse(source, filename=label)
    findings: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and ast.get_docstring(node, clean=False) is not None
        ):
            name = getattr(node, "name", "<module>")
            line = getattr(node, "lineno", 1)
            findings.append(f"{label}:{line}: docstring on {name}")
    return findings


def _find_disallowed_comments(source: str, label: str) -> list[str]:
    findings: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            line_number = token.start[0]
            if not _is_allowlisted_comment(token.string, line_number):
                findings.append(f"{label}:{line_number}: disallowed comment {token.string!r}")
    return findings


@pytest.mark.parametrize("module_path", SCOPED_MODULES, ids=lambda path: path.name)
def test_scoped_module_has_no_docstrings(module_path: Path) -> None:
    assert module_path.is_file()
    source = module_path.read_text(encoding="utf-8")
    assert _find_docstrings(source, module_path.name) == []


@pytest.mark.parametrize("module_path", SCOPED_MODULES, ids=lambda path: path.name)
def test_scoped_module_has_no_disallowed_comments(module_path: Path) -> None:
    assert module_path.is_file()
    source = module_path.read_text(encoding="utf-8")
    assert _find_disallowed_comments(source, module_path.name) == []


def test_find_docstrings_catches_module_class_function_and_async_function() -> None:
    sample = (
        '"""module doc."""\n'
        "\n"
        "class Example:\n"
        '    """class doc."""\n'
        "\n"
        "    def sync_method(self) -> None:\n"
        '        """method doc."""\n'
        "\n"
        "async def async_helper() -> None:\n"
        '    """async doc."""\n'
    )
    findings = _find_docstrings(sample, "sample.py")
    assert len(findings) == 4
    assert any("<module>" in item for item in findings)
    assert any("Example" in item for item in findings)
    assert any("sync_method" in item for item in findings)
    assert any("async_helper" in item for item in findings)


@pytest.mark.parametrize(
    ("comment_text", "line_number"),
    [
        ("#!/usr/bin/env python3", 1),
        ("# -*- coding: utf-8 -*-", 1),
        ("# coding=utf-8", 2),
        ("# noqa", 5),
        ("# noqa: E501", 5),
        ("# type: ignore", 5),
        ("# type: ignore[assignment]", 5),
        ("# pragma: no cover", 5),
    ],
)
def test_allowlisted_comments_are_permitted(comment_text: str, line_number: int) -> None:
    assert _is_allowlisted_comment(comment_text, line_number)


@pytest.mark.parametrize(
    ("comment_text", "line_number"),
    [
        ("#!/usr/bin/env python3", 2),
        ("# -*- coding: utf-8 -*-", 3),
        ("# explanatory comment", 1),
        ("# do not use noqa here", 4),
        ("# mention pragma: no cover mid-sentence", 4),
        ("# mention type: ignore mid-sentence", 4),
    ],
)
def test_non_allowlisted_comments_are_rejected(comment_text: str, line_number: int) -> None:
    assert not _is_allowlisted_comment(comment_text, line_number)


def test_find_disallowed_comments_reports_only_explanatory_comments() -> None:
    sample = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "x = 1  # noqa: E501\n"
        "y = 2  # type: ignore[assignment]\n"
        "if x:  # pragma: no cover\n"
        "    z = 3  # explain z\n"
    )
    findings = _find_disallowed_comments(sample, "sample.py")
    assert len(findings) == 1
    assert "sample.py:6:" in findings[0]
