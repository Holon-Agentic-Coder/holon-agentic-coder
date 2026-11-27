from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.self_doc_scanner import (
    find_comments,
    find_docstrings_and_string_statements,
    is_allowlisted_comment,
)

CALIBRATION_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "calibration"

SLICE_MODULES: tuple[str, ...] = (
    "__init__.py",
    "actual.py",
    "actual_evidence.py",
    "branches.py",
    "checkout.py",
    "deltas.py",
    "physics.py",
    "predicted.py",
    "refs.py",
    "run.py",
)

_SLICE_DIRECTIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^#\s*why:\s*\S"),
    re.compile(r"^#\s*fmt:\s*(off|on|skip)\b"),
    re.compile(r"^#\s*type:\s*\S"),
)


def _is_allowed_slice_comment(line_number: int, text: str) -> bool:
    if is_allowlisted_comment(line_number, text):
        return True
    return any(pattern.match(text) for pattern in _SLICE_DIRECTIVE_PATTERNS)


def _disallowed_slice_comments(source: str) -> list[tuple[int, str]]:
    return [(line, text) for line, text in find_comments(source) if not _is_allowed_slice_comment(line, text)]


def _read_slice_module(filename: str) -> str:
    return (CALIBRATION_DIR / filename).read_text(encoding="utf-8")


def test_slice_registry_lists_ten_unique_modules() -> None:
    assert len(SLICE_MODULES) == 10
    assert len(set(SLICE_MODULES)) == len(SLICE_MODULES)


@pytest.mark.parametrize("filename", SLICE_MODULES)
def test_slice_module_exists(filename: str) -> None:
    assert (CALIBRATION_DIR / filename).is_file(), f"Missing slice module: {filename}"


@pytest.mark.parametrize("filename", SLICE_MODULES)
def test_slice_module_has_no_docstrings_or_string_statements(filename: str) -> None:
    findings = find_docstrings_and_string_statements(_read_slice_module(filename))
    assert findings == [], f"{filename} contains docstrings or string statements: {findings}"


@pytest.mark.parametrize("filename", SLICE_MODULES)
def test_slice_module_has_only_allowed_comments(filename: str) -> None:
    findings = _disallowed_slice_comments(_read_slice_module(filename))
    assert findings == [], f"{filename} contains disallowed comments: {findings}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# why: git rejects a ref that is checked out elsewhere", True),
        ("# fmt: off", True),
        ("# fmt: skip", True),
        ("# noqa: E501", True),
        ("# type: ignore[assignment]", True),
        ("# why:", False),
        ("# why:   ", False),
        ("# explain", False),
        ("# 1. Switch branch", False),
        ("# fmt: maybe", False),
    ],
)
def test_slice_comment_allowlist_rules(text: str, expected: bool) -> None:
    assert _is_allowed_slice_comment(10, text) is expected


def test_inserted_docstring_and_comment_fail_slice_checks() -> None:
    source = _read_slice_module("deltas.py")
    mutated = '"""Inserted module docstring."""\n' + source + "# Explain the rating rule.\n"
    assert find_docstrings_and_string_statements(mutated) == [(1, "docstring:<module>")]
    assert (len(mutated.splitlines()), "# Explain the rating rule.") in _disallowed_slice_comments(mutated)
