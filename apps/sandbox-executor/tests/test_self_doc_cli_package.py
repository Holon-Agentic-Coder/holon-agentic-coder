from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

from tests.self_doc_scanner import find_comments, find_docstrings_and_string_statements, is_allowlisted_comment

CLI_PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "cli"

CLI_PACKAGE_MODULES: tuple[str, ...] = (
    "__init__.py",
    "__main__.py",
    "antigravity_mounts.py",
    "doctor.py",
    "doctor_env.py",
    "logging.py",
    "main.py",
    "options.py",
    "pi_mounts.py",
    "preflight.py",
)

_WHY_REASON_RE = re.compile(r"^#\s*why:\s*\S", re.IGNORECASE)
_FMT_DIRECTIVE_RE = re.compile(r"^#\s*fmt:\s*(?:off|on|skip)\s*$")


def _is_allowed_comment(line_number: int, comment_text: str, column: int) -> bool:
    if is_allowlisted_comment(line_number, comment_text, column):
        return True
    return bool(_WHY_REASON_RE.match(comment_text) or _FMT_DIRECTIVE_RE.match(comment_text))


def _disallowed_comments(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not _is_allowed_comment(token.start[0], token.string, token.start[1])
    ]


def _read_module(module_name: str) -> str:
    return (CLI_PACKAGE_DIR / module_name).read_text(encoding="utf-8")


@pytest.mark.parametrize("module_name", CLI_PACKAGE_MODULES)
def test_cli_package_module_exists(module_name: str) -> None:
    assert (CLI_PACKAGE_DIR / module_name).is_file()


@pytest.mark.parametrize("module_name", CLI_PACKAGE_MODULES)
def test_cli_package_module_has_no_docstrings_or_string_statements(module_name: str) -> None:
    assert find_docstrings_and_string_statements(_read_module(module_name)) == []


@pytest.mark.parametrize("module_name", CLI_PACKAGE_MODULES)
def test_cli_package_module_has_no_disallowed_comments(module_name: str) -> None:
    assert _disallowed_comments(_read_module(module_name)) == []


def test_cli_package_keeps_shebang() -> None:
    assert _read_module("__init__.py").splitlines()[0] == "#!/usr/bin/env python3"


def test_logging_keeps_type_ignore_directive() -> None:
    comment_texts = [text for _, text in find_comments(_read_module("logging.py"))]
    assert "# type: ignore[override]" in comment_texts


@pytest.mark.parametrize(
    ("line_number", "comment_text", "column", "allowed"),
    [
        (5, "# why: reason", 0, True),
        (5, "# Why: Reason", 4, True),
        (5, "# why:", 0, False),
        (5, "# why:   ", 0, False),
        (5, "# fmt: off", 0, True),
        (5, "# fmt: skip", 8, True),
        (5, "# noqa: E501", 30, True),
        (5, "# type: ignore[override]", 30, True),
        (1, "# -*- coding: utf-8 -*-", 0, True),
        (5, "# explain x", 0, False),
        (1, "#!/usr/bin/env python3", 0, True),
        (2, "#!/usr/bin/env python3", 0, False),
    ],
)
def test_allow_rules(line_number: int, comment_text: str, column: int, allowed: bool) -> None:
    assert _is_allowed_comment(line_number, comment_text, column) is allowed


def test_inserted_docstring_and_comment_are_detected() -> None:
    tampered_source = '"""Inserted module docstring."""\n' + _read_module("main.py") + "# inserted prose comment\n"
    assert find_docstrings_and_string_statements(tampered_source)[0] == (1, "docstring:<module>")
    assert _disallowed_comments(tampered_source)[-1][1] == "# inserted prose comment"
