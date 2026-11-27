from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

from tests.self_doc_scanner import find_docstrings_and_string_statements, is_allowlisted_comment

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"

SCOPE_MODULES: tuple[str, ...] = (
    "agent_runner.py",
    "calibration_diff.py",
    "calibration_ev.py",
    "calibration_evaluated_sha.py",
    "calibration_execution_owner.py",
    "calibration_format.py",
    "calibration_outcome.py",
    "calibration_predicted.py",
    "calibration_record.py",
    "calibration_review_churn.py",
    "calibration_rows.py",
    "calibration_self_check.py",
    "calibration_touch_set.py",
    "config_env_report.py",
)

REASON_COMMENT_RE = re.compile(r"^#\s*why:\s*\S")
FORMATTER_DIRECTIVE_RE = re.compile(r"^#\s*fmt:\s*(?:off|on|skip)\s*$")


def is_allowed_in_scope(line_number: int, comment_text: str, column: int = 0) -> bool:
    return (
        is_allowlisted_comment(line_number, comment_text, column)
        or bool(REASON_COMMENT_RE.match(comment_text))
        or bool(FORMATTER_DIRECTIVE_RE.match(comment_text))
    )


def disallowed_comments(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not is_allowed_in_scope(token.start[0], token.string, token.start[1])
    ]


def read_scope_module(file_name: str) -> str:
    return (PACKAGE_DIR / file_name).read_text(encoding="utf-8")


def test_scope_lists_fourteen_existing_modules() -> None:
    assert len(SCOPE_MODULES) == 14
    assert len(set(SCOPE_MODULES)) == 14
    assert list(SCOPE_MODULES) == sorted(SCOPE_MODULES)
    missing = [name for name in SCOPE_MODULES if not (PACKAGE_DIR / name).is_file()]
    assert missing == []


@pytest.mark.parametrize("file_name", SCOPE_MODULES)
def test_scope_module_has_no_docstrings_or_string_statements(file_name: str) -> None:
    findings = find_docstrings_and_string_statements(read_scope_module(file_name))
    assert findings == [], f"{file_name}: {findings}"


@pytest.mark.parametrize("file_name", SCOPE_MODULES)
def test_scope_module_has_no_disallowed_comments(file_name: str) -> None:
    findings = disallowed_comments(read_scope_module(file_name))
    assert findings == [], f"{file_name}: {findings}"


@pytest.mark.parametrize(
    ("line_number", "comment_text", "expected"),
    [
        (5, "# why: reason", True),
        (5, "#   why: x", True),
        (5, "# why:", False),
        (5, "# fmt: off", True),
        (5, "# fmt: skip", True),
        (5, "# noqa: E501", True),
        (5, "# type: ignore[arg-type]", True),
        (5, "# pragma: no cover", True),
        (1, "#!/usr/bin/env python3", True),
        (1, "# -*- coding: utf-8 -*-", True),
        (5, "# Explain this", False),
        (5, "#: attribute doc", False),
        (5, "#", False),
        (5, "# whyever", False),
    ],
)
def test_scope_allowlist_rules(line_number: int, comment_text: str, expected: bool) -> None:
    assert is_allowed_in_scope(line_number, comment_text) is expected


def test_inserted_docstring_and_comment_are_detected() -> None:
    source = read_scope_module("calibration_record.py")
    mutated = '"""Inserted module docstring."""\n' + source + "# explain\n"
    assert (1, "docstring:<module>") in find_docstrings_and_string_statements(mutated)
    assert disallowed_comments(mutated)[-1][1] == "# explain"
