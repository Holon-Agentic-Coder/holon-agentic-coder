import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
CLEANED_TEST_FILES = (
    "test_uv_lock.py",
    "test_workflow_triggers.py",
    "test_workspace_survival_stress.py",
    "test_worktree_head.py",
    "url_redaction_matrix.py",
    "worktree_fixtures.py",
    "worktree_repo_templates.py",
    "cli/_helpers.py",
    "cli/test_doctor.py",
    "cli/test_env_files_and_redaction.py",
    "cli/test_flow_cli.py",
    "cli/test_intent_check_cli.py",
)
DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
ENCODING_COOKIE = re.compile(r"coding[:=]")
TOOL_DIRECTIVE = re.compile(r"#\s*(?:noqa\b|type:\s*\S|pragma:|fmt:\s*(?:off|on|skip)\b)")


def is_why_comment(text: str) -> bool:
    return text.startswith("# why:")


def is_shebang(line_number: int, text: str) -> bool:
    return line_number == 1 and text.startswith("#!")


def is_encoding_line(line_number: int, text: str) -> bool:
    return line_number <= 2 and ENCODING_COOKIE.search(text) is not None


def is_tool_directive(text: str) -> bool:
    return TOOL_DIRECTIVE.match(text) is not None


def is_permitted_comment(line_number: int, text: str) -> bool:
    return (
        is_why_comment(text)
        or is_shebang(line_number, text)
        or is_encoding_line(line_number, text)
        or is_tool_directive(text)
    )


def find_docstrings(source: str) -> list[tuple[int, str]]:
    return [
        (getattr(node, "lineno", 1), docstring.splitlines()[0] if docstring.strip() else docstring)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, DOCSTRING_OWNERS) and (docstring := ast.get_docstring(node, clean=False)) is not None
    ]


def find_disallowed_comments(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not is_permitted_comment(token.start[0], token.string)
    ]


def cleaned_file_params() -> list:
    return [pytest.param(path, id=path) for path in CLEANED_TEST_FILES]


@pytest.mark.parametrize("relative_path", cleaned_file_params())
def test_cleaned_test_file_exists(relative_path: str) -> None:
    assert (TESTS_DIR / relative_path).is_file()


@pytest.mark.parametrize("relative_path", cleaned_file_params())
def test_cleaned_test_file_has_no_docstrings(relative_path: str) -> None:
    docstrings = find_docstrings((TESTS_DIR / relative_path).read_text(encoding="utf-8"))
    assert docstrings == [], f"{relative_path} still holds docstrings: {docstrings}"


@pytest.mark.parametrize("relative_path", cleaned_file_params())
def test_cleaned_test_file_has_only_permitted_comments(relative_path: str) -> None:
    comments = find_disallowed_comments((TESTS_DIR / relative_path).read_text(encoding="utf-8"))
    assert comments == [], f"{relative_path} still holds disallowed comments: {comments}"


def test_scanner_reports_docstrings_and_plain_comments() -> None:
    source = '"""Module."""\n\n\ndef helper():\n    """Helper."""\n    return 1  # plain\n'
    assert len(find_docstrings(source)) == 2
    assert find_disallowed_comments(source) == [(6, "# plain")]


def test_scanner_permits_why_shebang_encoding_and_directives() -> None:
    source = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "# why: a reason the code cannot show\n"
        "LONG = 1  # noqa: E501\n"
        "TYPED = 2  # type: ignore\n"
        "COVERED = 3  # pragma: no cover\n"
        "# fmt: off\n"
        'HASH = "#96 is not a comment"\n'
    )
    assert find_docstrings(source) == []
    assert find_disallowed_comments(source) == []


def test_scanner_rejects_lookalike_directives() -> None:
    assert find_disallowed_comments("VALUE = 1  # typed by hand\n") == [(1, "# typed by hand")]
    assert find_disallowed_comments("#: attribute comment\nVALUE = 1\n") == [(1, "#: attribute comment")]
