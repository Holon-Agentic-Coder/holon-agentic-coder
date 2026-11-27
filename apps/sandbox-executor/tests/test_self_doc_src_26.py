from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

from tests.self_doc_scanner import find_docstrings_and_string_statements

REVIEW_PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "flow" / "review"

SCOPE_MODULES: tuple[str, ...] = (
    "approval_carry_over.py",
    "approval_coverage.py",
    "approval_fingerprint.py",
    "approval_gates.py",
    "carried_findings.py",
    "ci_evidence.py",
    "ci_failure_routing.py",
    "ci_inspection.py",
    "ci_noise_rerun.py",
    "ci_outcome.py",
)

_ENCODING_COOKIE = re.compile(r"coding[:=]")
_TOOL_DIRECTIVE = re.compile(r"^(noqa\b|type:|pragma:|fmt:)")


def _is_allowed_comment(line_number: int, column: int, text: str) -> bool:
    if text.startswith("#!"):
        return line_number == 1 and column == 0
    if line_number in (1, 2) and _ENCODING_COOKIE.search(text):
        return True
    body = text.lstrip("#").strip()
    return body.startswith("why:") or bool(_TOOL_DIRECTIVE.match(body))


def _disallowed_comments(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not _is_allowed_comment(*token.start, token.string)
    ]


def _read_scope_module(filename: str) -> str:
    return (REVIEW_PACKAGE_DIR / filename).read_text(encoding="utf-8")


@pytest.mark.parametrize("filename", SCOPE_MODULES)
def test_scope_module_exists(filename: str) -> None:
    assert (REVIEW_PACKAGE_DIR / filename).is_file(), f"Missing scope module: {filename}"


@pytest.mark.parametrize("filename", SCOPE_MODULES)
def test_scope_module_has_no_docstrings_or_string_statements(filename: str) -> None:
    findings = find_docstrings_and_string_statements(_read_scope_module(filename))
    assert findings == [], f"{filename} contains docstrings or string statements: {findings}"


@pytest.mark.parametrize("filename", SCOPE_MODULES)
def test_scope_module_has_only_reason_or_directive_comments(filename: str) -> None:
    findings = _disallowed_comments(_read_scope_module(filename))
    assert findings == [], f"{filename} contains comments that are not '# why:' or directives: {findings}"


@pytest.mark.parametrize(
    ("line_number", "column", "text", "expected"),
    [
        (10, 4, "# why: a stale base can only cost a re-review", True),
        (10, 0, "# Lazy: records imports review_report.", False),
        (10, 0, "# explains why the code does this", False),
        (10, 0, "# noqa: E501", True),
        (10, 0, "# type: ignore", True),
        (10, 0, "# pragma: no cover", True),
        (10, 0, "# fmt: off", True),
        (1, 0, "#!/usr/bin/env python3", True),
        (2, 0, "#!/usr/bin/env python3", False),
        (2, 0, "# -*- coding: utf-8 -*-", True),
        (3, 0, "# coding: utf-8", False),
    ],
)
def test_allowed_comment_predicate(line_number: int, column: int, text: str, expected: bool) -> None:
    assert _is_allowed_comment(line_number, column, text) is expected
