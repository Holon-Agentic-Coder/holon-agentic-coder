from __future__ import annotations

from pathlib import Path

import pytest

from tests.self_doc_scanner import (
    find_disallowed_comments,
    find_docstrings_and_string_statements,
    is_allowlisted_comment,
)

TESTS_DIR = Path(__file__).resolve().parent

GOVERNED_TEST_FILES: tuple[str, ...] = (
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


def read_governed_test_file(relative_path: str) -> str:
    return (TESTS_DIR / relative_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("relative_path", GOVERNED_TEST_FILES)
def test_governed_test_file_exists(relative_path: str) -> None:
    assert (TESTS_DIR / relative_path).is_file()


@pytest.mark.parametrize("relative_path", GOVERNED_TEST_FILES)
def test_governed_test_file_has_no_docstrings(relative_path: str) -> None:
    findings = find_docstrings_and_string_statements(read_governed_test_file(relative_path))
    assert findings == [], f"{relative_path}: {findings}"


@pytest.mark.parametrize("relative_path", GOVERNED_TEST_FILES)
def test_governed_test_file_has_only_permitted_comments(relative_path: str) -> None:
    findings = find_disallowed_comments(read_governed_test_file(relative_path), allow_why=True)
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
    assert is_allowlisted_comment(line_number, text, column, allow_why=True) is permitted
