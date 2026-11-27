from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import pytest

from scripts.self_explaining_comments import is_allowed_comment

TESTS_DIR = Path(__file__).resolve().parent

GOVERNED_TEST_MODULES: tuple[str, ...] = (
    "flow/github/test_review_post.py",
    "test_ci_timing_compare.py",
    "test_calibration_commit.py",
    "flow/github/test_pr.py",
    "test_wisdom.py",
    "test_calibration_dual_ev.py",
    "test_flow_default_branch_worktree.py",
    "test_launcher_sidecar.py",
    "test_review_target.py",
    "test_promotion_idempotence.py",
    "executor_fixtures.py",
    "test_executor_outcome.py",
)

DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def docstring_lines(source: str) -> list[int]:
    return [
        getattr(node, "lineno", 1)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, DOCSTRING_OWNERS) and ast.get_docstring(node, clean=False) is not None
    ]


def disallowed_comment_lines(source: str) -> list[int]:
    return [
        token.start[0]
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not is_allowed_comment(token.string, token.start[0])
    ]


def test_guard_flags_docstrings_and_plain_comments_but_accepts_reasons_and_directives() -> None:
    flagged_source = 'def sample():\n    """Describe."""\n    return 1  # describe\n'
    accepted_source = 'def sample():\n    # why: keeps the fixture hermetic\n    return "#"  # noqa: E501\n'

    assert docstring_lines(flagged_source) == [1]
    assert disallowed_comment_lines(flagged_source) == [3]
    assert docstring_lines(accepted_source) == []
    assert disallowed_comment_lines(accepted_source) == []


@pytest.mark.parametrize("relative_path", GOVERNED_TEST_MODULES, ids=GOVERNED_TEST_MODULES)
def test_governed_test_module_has_no_docstrings(relative_path: str) -> None:
    assert docstring_lines((TESTS_DIR / relative_path).read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("relative_path", GOVERNED_TEST_MODULES, ids=GOVERNED_TEST_MODULES)
def test_governed_test_module_has_only_reason_or_directive_comments(relative_path: str) -> None:
    assert disallowed_comment_lines((TESTS_DIR / relative_path).read_text(encoding="utf-8")) == []
