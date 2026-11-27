from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

from tests.self_doc_scanner import find_docstrings_and_string_statements, is_allowlisted_comment

TESTS_DIR = Path(__file__).resolve().parent

CLEANED_TEST_FILES: tuple[str, ...] = (
    "flow/helpers.py",
    "test_local_llm.py",
    "flow/stages/test_children.py",
    "flow/stages/test_decomposition.py",
    "test_executor_commands.py",
    "test_git.py",
    "flow/test_ledger_worktree_cleanup.py",
    "test_ci_timing_noise.py",
    "flow/test_dispatch.py",
    "test_ledger_query.py",
    "test_flow_e2e_harness.py",
    "test_planner.py",
    "test_self_doc_tests_b.py",
)

_CODE_LIST = r"[A-Za-z0-9_-]+(?:\s*,\s*[A-Za-z0-9_-]+)*"
PERMITTED_TEST_COMMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^#\s*why:\s*\S.*$"),
    re.compile(r"^#\s*fmt:\s*(?:off|on|skip)\s*$"),
    re.compile(rf"^#\s*type:\s*(?:ignore(?:\[\s*{_CODE_LIST}\s*\])?|[^\s#][^#]*)\s*$"),
    re.compile(r"^#\s*pragma:\s*no\s+(?:cover|branch)\s*$"),
)


def is_permitted_test_comment(line_number: int, column: int, text: str) -> bool:
    if is_allowlisted_comment(line_number, text, column):
        return True
    if text.startswith("#!"):
        return False
    return any(pattern.match(text) for pattern in PERMITTED_TEST_COMMENT_PATTERNS)


def unpermitted_comments(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not is_permitted_test_comment(*token.start, token.string)
    ]


def read_cleaned_test_file(relative_path: str) -> str:
    return (TESTS_DIR / relative_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("relative_path", CLEANED_TEST_FILES)
def test_cleaned_test_file_exists(relative_path: str) -> None:
    assert (TESTS_DIR / relative_path).is_file()


@pytest.mark.parametrize("relative_path", CLEANED_TEST_FILES)
def test_cleaned_test_file_has_no_docstrings(relative_path: str) -> None:
    findings = find_docstrings_and_string_statements(read_cleaned_test_file(relative_path))
    assert findings == [], f"{relative_path}: {findings}"


@pytest.mark.parametrize("relative_path", CLEANED_TEST_FILES)
def test_cleaned_test_file_has_only_permitted_comments(relative_path: str) -> None:
    findings = unpermitted_comments(read_cleaned_test_file(relative_path))
    assert findings == [], f"{relative_path}: {findings}"


@pytest.mark.parametrize(
    ("line_number", "column", "text", "permitted"),
    [
        pytest.param(5, 4, "# why: git refuses an empty commit", True, id="why-reason"),
        pytest.param(5, 4, "# why:", False, id="bare-why"),
        pytest.param(5, 4, "# noqa: F401", True, id="noqa-codes"),
        pytest.param(5, 4, "# noqa: F401  (autouse fixture)", False, id="noqa-with-prose"),
        pytest.param(5, 0, "# fmt: off", True, id="fmt-off"),
        pytest.param(5, 0, "# fmt: off because long", False, id="fmt-with-prose"),
        pytest.param(5, 4, "# type: ignore[arg-type]", True, id="type-ignore-code"),
        pytest.param(5, 4, "# pragma: no cover", True, id="pragma-no-cover"),
        pytest.param(5, 4, "# setup", False, id="narration"),
        pytest.param(1, 0, "#!/usr/bin/env python3", True, id="shebang-first-line"),
        pytest.param(3, 0, "#!/usr/bin/env python3", False, id="shebang-later-line"),
        pytest.param(1, 0, "# -*- coding: utf-8 -*-", True, id="encoding-line"),
    ],
)
def test_permitted_comment_rules(line_number: int, column: int, text: str, permitted: bool) -> None:
    assert is_permitted_test_comment(line_number, column, text) is permitted
