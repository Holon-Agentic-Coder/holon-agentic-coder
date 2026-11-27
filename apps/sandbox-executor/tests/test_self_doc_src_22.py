from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

from tests.self_doc_scanner import find_docstrings_and_string_statements, is_allowlisted_comment

FLOW_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "flow"

SCOPE_MODULES: tuple[str, ...] = (
    "run_lock.py",
    "run_lock_record.py",
    "stage5.py",
    "stage5_ledger.py",
    "stage5_verdict.py",
    "stage_conflict_resolution.py",
    "stage_conflicts.py",
    "stage_status.py",
    "target_probe.py",
    "target_sync.py",
)

_REASON_OR_FORMATTER_COMMENT = re.compile(r"^#\s*(?:why:\s|fmt:\s*(?:off|on|skip)\s*$)")


def is_reason_or_directive_comment(line_number: int, comment_text: str, column: int = 0) -> bool:
    if is_allowlisted_comment(line_number, comment_text, column):
        return True
    return _REASON_OR_FORMATTER_COMMENT.match(comment_text) is not None


def find_comments_other_than_reasons_or_directives(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
        and not is_reason_or_directive_comment(token.start[0], token.string, token.start[1])
    ]


def test_every_scope_module_exists() -> None:
    missing = [name for name in SCOPE_MODULES if not (FLOW_DIR / name).is_file()]
    assert missing == []


@pytest.mark.parametrize("module_name", SCOPE_MODULES)
def test_scope_module_has_no_docstrings(module_name: str) -> None:
    source = (FLOW_DIR / module_name).read_text(encoding="utf-8")
    assert find_docstrings_and_string_statements(source) == []


@pytest.mark.parametrize("module_name", SCOPE_MODULES)
def test_scope_module_has_only_reason_or_directive_comments(module_name: str) -> None:
    source = (FLOW_DIR / module_name).read_text(encoding="utf-8")
    assert find_comments_other_than_reasons_or_directives(source) == []


@pytest.mark.parametrize(
    "comment_text",
    ["# why: the kernel releases the lock when the holder dies", "# fmt: off", "# fmt: skip", "# noqa: E501"],
)
def test_reason_and_directive_comments_are_accepted(comment_text: str) -> None:
    assert is_reason_or_directive_comment(5, comment_text)


@pytest.mark.parametrize(
    "comment_text",
    ["# Take the lock first.", "# TODO: tidy", "# Why this matters is obvious", "# whyever", "# why:", "# fmt: maybe"],
)
def test_prose_comments_are_rejected(comment_text: str) -> None:
    assert not is_reason_or_directive_comment(5, comment_text)
