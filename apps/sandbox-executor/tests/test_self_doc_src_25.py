from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

from tests.self_doc_scanner import find_docstrings_and_string_statements, is_allowlisted_comment

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"

SLICE_MODULES: tuple[str, ...] = (
    "flow/stages/plan_decision.py",
    "flow/stages/plan_resume.py",
    "flow/stages/plan_support.py",
    "flow/stages/review_outcome.py",
    "flow/stages/stage_support.py",
    "flow/stages/sync_conflict.py",
    "flow/stages/token_scope_guard.py",
    "flow/stages/touch_set_guard.py",
    "flow/review/__init__.py",
    "flow/review/adjudication.py",
)

_WHY_COMMENT_RE = re.compile(r"^#\s*why:\s*\S")
_FMT_DIRECTIVE_RE = re.compile(r"^#\s*fmt:\s*(off|on|skip)\s*$")


def _is_permitted_comment(line_number: int, comment_text: str, column: int = 0) -> bool:
    if is_allowlisted_comment(line_number, comment_text, column):
        return True
    return bool(_WHY_COMMENT_RE.match(comment_text) or _FMT_DIRECTIVE_RE.match(comment_text))


def _forbidden_comments(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not _is_permitted_comment(token.start[0], token.string, token.start[1])
    ]


def _slice_source(relative_path: str) -> str:
    return (PACKAGE_DIR / relative_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("relative_path", SLICE_MODULES)
def test_slice_module_exists(relative_path: str) -> None:
    assert (PACKAGE_DIR / relative_path).is_file(), relative_path


@pytest.mark.parametrize("relative_path", SLICE_MODULES)
def test_slice_module_has_no_docstrings(relative_path: str) -> None:
    findings = find_docstrings_and_string_statements(_slice_source(relative_path))
    assert findings == [], f"{relative_path}: {findings}"


@pytest.mark.parametrize("relative_path", SLICE_MODULES)
def test_slice_module_has_no_forbidden_comments(relative_path: str) -> None:
    findings = _forbidden_comments(_slice_source(relative_path))
    assert findings == [], f"{relative_path}: {findings}"


@pytest.mark.parametrize(
    ("line_number", "comment_text"),
    [
        (5, "# why: mkstemp creates the file with mode 0600"),
        (5, "# fmt: off"),
        (5, "# fmt: skip"),
        (5, "# noqa: E501"),
        (1, "#!/usr/bin/env python3"),
    ],
)
def test_permitted_comment_forms_pass(line_number: int, comment_text: str) -> None:
    assert _is_permitted_comment(line_number, comment_text)


@pytest.mark.parametrize("comment_text", ["# why:", "# why:   ", "# explain x", "# fmt: maybe"])
def test_descriptive_or_empty_comments_are_rejected(comment_text: str) -> None:
    assert not _is_permitted_comment(5, comment_text)


def test_forbidden_comment_scan_reports_descriptive_comment() -> None:
    source = "x = 1  # explain x\n# why: kept reason\ny = 2\n"
    assert _forbidden_comments(source) == [(1, "# explain x")]


def test_docstring_injected_into_slice_module_is_detected() -> None:
    source = '"""Injected module docstring."""\n' + _slice_source(SLICE_MODULES[0])
    assert find_docstrings_and_string_statements(source)
