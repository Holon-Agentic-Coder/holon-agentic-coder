from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

from tests.self_doc_scanner import find_docstrings_and_string_statements, is_allowlisted_comment

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"

SCOPED_MODULES: tuple[str, ...] = (
    "knowledge/fragment_identities.py",
    "knowledge/ledger_fragment_sync.py",
    "entrypoint/conflict_resolver.py",
    "entrypoint/conflict_resolver_verify.py",
    "entrypoint/planner.py",
    "entrypoint/review_loop.py",
    "entrypoint/review_loop_args.py",
    "entrypoint/executor/__init__.py",
    "entrypoint/executor/run.py",
    "flow/__init__.py",
)

_IN_SCOPE_DIRECTIVE = re.compile(r"^#\s*(?:why:|fmt:\s*(?:off|on|skip)\b|type:\s*\S|pragma:\s*\S)")


def is_allowed_in_scope(line_number: int, comment_text: str, column: int = 0) -> bool:
    return is_allowlisted_comment(line_number, comment_text, column) or bool(_IN_SCOPE_DIRECTIVE.match(comment_text))


def disallowed_comments(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not is_allowed_in_scope(token.start[0], token.string, token.start[1])
    ]


def scoped_source(relative_path: str) -> str:
    return (PACKAGE_DIR / relative_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("relative_path", SCOPED_MODULES)
def test_scoped_module_exists(relative_path: str) -> None:
    assert (PACKAGE_DIR / relative_path).is_file()


@pytest.mark.parametrize("relative_path", SCOPED_MODULES)
def test_scoped_module_has_no_docstrings(relative_path: str) -> None:
    findings = find_docstrings_and_string_statements(scoped_source(relative_path))
    assert findings == [], f"{relative_path}: {findings}"


@pytest.mark.parametrize("relative_path", SCOPED_MODULES)
def test_scoped_module_has_only_allowed_comments(relative_path: str) -> None:
    findings = disallowed_comments(scoped_source(relative_path))
    assert findings == [], f"{relative_path}: {findings}"


def test_scope_lists_the_ten_intent_modules() -> None:
    assert len(SCOPED_MODULES) == 10
    assert len(set(SCOPED_MODULES)) == len(SCOPED_MODULES)


@pytest.mark.parametrize(
    ("line_number", "comment_text", "column", "expected"),
    [
        (5, "# why: the host owns the ledger", 0, True),
        (5, "# why: the host owns the ledger", 8, True),
        (5, "#why: compact form", 40, True),
        (5, "# fmt: off", 0, True),
        (5, "# fmt: skip", 12, True),
        (5, "# noqa: E501", 30, True),
        (5, "# type: ignore[arg-type]", 30, True),
        (5, "# pragma: no cover", 30, True),
        (1, "#!/usr/bin/env python3", 0, True),
        (2, "#!/usr/bin/env python3", 0, False),
        (1, "#!/usr/bin/env python3", 4, False),
        (1, "# -*- coding: utf-8 -*-", 0, True),
        (5, "# Explain this", 0, False),
        (5, "#: attribute note", 4, False),
        (5, "#: why: attribute reason", 4, False),
        (5, "# whyever", 0, False),
        (5, "# fmt note", 0, False),
    ],
)
def test_allowed_comment_rules(line_number: int, comment_text: str, column: int, expected: bool) -> None:
    assert is_allowed_in_scope(line_number, comment_text, column) is expected


def test_mutations_of_a_scoped_module_are_detected() -> None:
    source = scoped_source("knowledge/ledger_fragment_sync.py")
    assert find_docstrings_and_string_statements('"""Injected module docstring."""\n' + source)
    assert disallowed_comments(source + "# Explain why this exists.\n") == [
        (len(source.splitlines()) + 1, "# Explain why this exists.")
    ]
    assert disallowed_comments(source + "# why: the host owns this ledger.\n") == disallowed_comments(source)
