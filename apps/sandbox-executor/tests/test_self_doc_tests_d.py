from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.self_doc_scanner import find_comments, find_docstrings_and_string_statements, is_allowlisted_comment

TESTS_DIR = Path(__file__).resolve().parent

SCOPED_TEST_MODULES: tuple[str, ...] = (
    "test_ledger_lock.py",
    "ci_bookkeeping_fakes.py",
    "test_doctor_replay.py",
    "test_init.py",
    "test_complexity_gate.py",
    "test_malformed_config_never_crashes.py",
    "test_preflight.py",
    "test_review_ensemble.py",
    "test_review_resolver_failure.py",
    "test_unique_stage_ids.py",
    "cli/test_main.py",
    "flow/test_worktree_all_stages.py",
    "test_self_doc_tests_d.py",
)

REASON_COMMENT = re.compile(r"^#+\s*why:", re.IGNORECASE)
TOOL_DIRECTIVE_COMMENT = re.compile(r"^#+\s*(?:noqa\b|type:\s*\S|pragma:|fmt:\s*(?:off|on|skip)\b)")


def is_allowed_test_comment(line_number: int, comment_text: str) -> bool:
    if is_allowlisted_comment(line_number, comment_text):
        return True
    if comment_text.startswith("#!"):
        return False
    return bool(REASON_COMMENT.match(comment_text) or TOOL_DIRECTIVE_COMMENT.match(comment_text))


@pytest.mark.parametrize("relative_path", SCOPED_TEST_MODULES, ids=lambda path: path.replace("/", "-"))
def test_scoped_test_module_has_no_docstring_or_explanatory_comment(relative_path: str) -> None:
    module_path = TESTS_DIR / relative_path
    assert module_path.is_file(), f"missing scoped test module: {relative_path}"
    source = module_path.read_text(encoding="utf-8")

    docstrings = find_docstrings_and_string_statements(source)
    explanatory_comments = [
        (line_number, text)
        for line_number, text in find_comments(source)
        if not is_allowed_test_comment(line_number, text)
    ]

    assert docstrings == [], "\n".join(f"{line}: {label}" for line, label in docstrings)
    assert explanatory_comments == [], "\n".join(f"{line}: {text}" for line, text in explanatory_comments)


@pytest.mark.parametrize(
    ("comment_text", "line_number", "expected"),
    [
        ("# why: x", 5, True),
        ("# fmt: off", 5, True),
        ("# noqa: E501", 5, True),
        ("# type: ignore[arg-type]", 5, True),
        ("#!/usr/bin/env python3", 1, True),
        ("#!/usr/bin/env python3", 3, False),
        ("# note", 5, False),
        ("#: doc", 5, False),
        ("# --- banner ---", 5, False),
    ],
)
def test_allowed_test_comment_accepts_reasons_and_directives_only(
    comment_text: str, line_number: int, expected: bool
) -> None:
    assert is_allowed_test_comment(line_number, comment_text) is expected
