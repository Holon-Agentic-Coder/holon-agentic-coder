from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

from tests.self_doc_scanner import find_docstrings_and_string_statements, is_allowlisted_comment

REVIEW_PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "flow" / "review"

SCOPED_MODULES: tuple[str, ...] = (
    "delta_review_request.py",
    "git_identity.py",
    "head_test_evidence.py",
    "ids.py",
    "ledger_commit.py",
    "local_tests.py",
    "merged_head_gate.py",
    "merged_head_resume.py",
    "merged_head_routing.py",
    "posting.py",
)

_COMMENT_MARKER = re.compile(r"^#+\s*")
_REASON_OR_DIRECTIVE = re.compile(r"^(?:why:|noqa\b|type:\s*\S|pragma:|fmt:\s*(?:off|on|skip)\b)", re.IGNORECASE)


def _is_permitted_comment(line_number: int, column: int, text: str) -> bool:
    if is_allowlisted_comment(line_number, text, column):
        return True
    body = _COMMENT_MARKER.sub("", text, count=1).strip()
    return _REASON_OR_DIRECTIVE.match(body) is not None


def _forbidden_comments(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not _is_permitted_comment(token.start[0], token.start[1], token.string)
    ]


def _read_scoped_module(filename: str) -> str:
    return (REVIEW_PACKAGE_DIR / filename).read_text(encoding="utf-8")


@pytest.mark.parametrize("filename", SCOPED_MODULES)
def test_scoped_module_exists(filename: str) -> None:
    assert (REVIEW_PACKAGE_DIR / filename).is_file()


@pytest.mark.parametrize("filename", SCOPED_MODULES)
def test_scoped_module_has_no_docstrings(filename: str) -> None:
    assert find_docstrings_and_string_statements(_read_scoped_module(filename)) == []


@pytest.mark.parametrize("filename", SCOPED_MODULES)
def test_scoped_module_has_only_permitted_comments(filename: str) -> None:
    assert _forbidden_comments(_read_scoped_module(filename)) == []


def test_an_inserted_docstring_is_detected() -> None:
    source = 'def plan_id() -> str:\n    """Return the plan id."""\n    return "P-1"\n'
    assert find_docstrings_and_string_statements(source) == [(2, "docstring:plan_id")]


def test_an_inserted_plain_comment_is_detected() -> None:
    source = "intent = 1  # the intent counter\n"
    assert _forbidden_comments(source) == [(1, "# the intent counter")]


def test_reason_comments_and_directives_are_permitted() -> None:
    source = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "# why: ledgers are append-only\n"
        "review = 1  # noqa: E501\n"
        "plan = review  # type: int\n"
        "# fmt: off\n"
        "execution = 2  # pragma: no cover\n"
    )
    assert _forbidden_comments(source) == []


def test_a_directive_word_inside_prose_is_not_permitted() -> None:
    assert not _is_permitted_comment(3, 0, "# the type: of review")
