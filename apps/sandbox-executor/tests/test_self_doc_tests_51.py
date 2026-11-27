from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

SCOPED_TEST_FILES = (
    "flow/test_package_exports.py",
    "flow/test_patch_targets.py",
    "flow/test_pipeline_approved.py",
    "flow/test_pipeline_touch_set.py",
    "flow/test_pipeline_unlimited.py",
    "flow/test_plan_refresh.py",
    "flow/test_process_reaper.py",
    "flow/test_resume_checkpoint.py",
    "flow/test_resume_guard.py",
    "flow/test_review_local_tests.py",
    "flow/test_run_lock.py",
    "flow/test_sleep_patch.py",
)

MACHINE_DIRECTIVE = re.compile(r"(?:noqa\b|type:\s*\S|pragma:|fmt:\s*(?:off|on|skip)\b)")
ENCODING_DECLARATION = re.compile(r"coding[:=]")
REASON_PREFIX = "why:"
DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def comment_body(comment: str) -> str:
    return comment.lstrip("#").strip()


def is_allowed_comment(comment: str, line_number: int) -> bool:
    if line_number == 1 and comment.startswith("#!"):
        return True
    if line_number <= 2 and ENCODING_DECLARATION.search(comment):
        return True
    body = comment_body(comment)
    return body.lower().startswith(REASON_PREFIX) or MACHINE_DIRECTIVE.match(body) is not None


def disallowed_comments(source: str) -> list[str]:
    return [
        f"{token.start[0]}: {token.string}"
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not is_allowed_comment(token.string, token.start[0])
    ]


def docstrings(source: str) -> list[str]:
    return [
        f"{node.body[0].lineno}: {type(node).__name__} docstring"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, DOCSTRING_OWNERS) and ast.get_docstring(node, clean=False) is not None
    ]


@pytest.mark.parametrize("relative_path", SCOPED_TEST_FILES, ids=lambda path: Path(path).stem)
def test_scoped_test_file_is_self_explaining(relative_path: str) -> None:
    source = (TESTS_DIR / relative_path).read_text(encoding="utf-8")
    findings = docstrings(source) + disallowed_comments(source)
    assert findings == [], f"{relative_path}:\n" + "\n".join(findings)


def test_guard_reports_only_docstrings_and_plain_comments() -> None:
    source = (
        "#!/usr/bin/env python\n"
        "def sample():\n"
        '    """Says what it does."""\n'
        "    # plain narration\n"
        "    # why: a reason the code cannot show\n"
        '    marker = "# not a comment"  # noqa: F841\n'
    )
    assert docstrings(source) == ["3: FunctionDef docstring"]
    assert disallowed_comments(source) == ["4: # plain narration"]


def test_every_scoped_test_file_exists() -> None:
    missing = [path for path in SCOPED_TEST_FILES if not (TESTS_DIR / path).is_file()]
    assert missing == []
