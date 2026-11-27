from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

from tests.self_doc_scanner import find_docstrings_and_string_statements

TESTS_DIR = Path(__file__).resolve().parent

CLEANED_TEST_FILES: tuple[str, ...] = (
    "test_host_worktree_sandbox.py",
    "test_executor_agent_output_redaction.py",
    "test_url_credential_redaction.py",
    "test_makefile.py",
    "test_turn_supervisor.py",
    "test_knowledge.py",
    "test_calibrate_rerun.py",
    "test_executor_sub_intents.py",
    "hermetic_fixtures.py",
    "test_agent_runner.py",
    "flow/test_pipeline.py",
    "test_ledger_append_only.py",
)

ENCODING_COOKIE = re.compile(r"^#.*?coding[:=]\s*[-\w.]+")
WHY_REASON = re.compile(r"^why:")
MACHINE_DIRECTIVE = re.compile(r"^(?:noqa(?::|$)|type:|pragma:|fmt:)")

CLEANED_TEST_FILE_PARAMS = [pytest.param(path, id=path) for path in CLEANED_TEST_FILES]


def is_permitted_comment(line_number: int, column: int, text: str) -> bool:
    if text.startswith("#!"):
        return line_number == 1 and column == 0
    if line_number in (1, 2) and ENCODING_COOKIE.search(text):
        return True
    body = text.lstrip("#").strip()
    return bool(WHY_REASON.match(body) or MACHINE_DIRECTIVE.match(body))


def read_cleaned_test_file(relative_path: str) -> str:
    return (TESTS_DIR / relative_path).read_text(encoding="utf-8")


def forbidden_comments(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not is_permitted_comment(*token.start, token.string)
    ]


@pytest.mark.parametrize("relative_path", CLEANED_TEST_FILE_PARAMS)
def test_cleaned_test_file_exists(relative_path: str) -> None:
    assert (TESTS_DIR / relative_path).is_file()


@pytest.mark.parametrize("relative_path", CLEANED_TEST_FILE_PARAMS)
def test_cleaned_test_file_has_no_docstrings(relative_path: str) -> None:
    findings = find_docstrings_and_string_statements(read_cleaned_test_file(relative_path))
    assert findings == [], f"{relative_path}: {findings}"


@pytest.mark.parametrize("relative_path", CLEANED_TEST_FILE_PARAMS)
def test_cleaned_test_file_has_only_permitted_comments(relative_path: str) -> None:
    findings = forbidden_comments(read_cleaned_test_file(relative_path))
    assert findings == [], f"{relative_path}: {findings}"


@pytest.mark.parametrize(
    ("line_number", "column", "text", "permitted"),
    [
        pytest.param(10, 4, "# why: git refuses a shallow file here", True, id="why-reason"),
        pytest.param(10, 40, "# noqa: PLR0913", True, id="noqa"),
        pytest.param(10, 40, "# type: ignore[arg-type]", True, id="type-ignore"),
        pytest.param(10, 40, "# pragma: no cover", True, id="pragma"),
        pytest.param(10, 0, "# fmt: off", True, id="fmt"),
        pytest.param(1, 0, "#!/usr/bin/env python3", True, id="shebang-line-1"),
        pytest.param(5, 0, "#!/usr/bin/env python3", False, id="shebang-line-5"),
        pytest.param(2, 0, "# -*- coding: utf-8 -*-", True, id="encoding-cookie"),
        pytest.param(10, 0, "# explains the code", False, id="explanation"),
        pytest.param(10, 0, "# typed later", False, id="typed-prose"),
        pytest.param(10, 0, "# whyever not", False, id="why-prefix-prose"),
        pytest.param(10, 0, "# noqa-like prose", False, id="noqa-prefix-prose"),
    ],
)
def test_permitted_comment_rules(line_number: int, column: int, text: str, permitted: bool) -> None:
    assert is_permitted_comment(line_number, column, text) is permitted
