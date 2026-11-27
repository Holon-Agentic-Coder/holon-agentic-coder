from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.self_doc_scanner import find_comments, find_docstrings_and_string_statements, is_allowlisted_comment

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"

SCOPED_MODULES: tuple[str, ...] = (
    "flow/calibrate_rerun.py",
    "flow/calibrate_retry.py",
    "flow/calibrate_stale_warning.py",
    "flow/calibrate_staleness.py",
    "flow/calibrate_sync.py",
    "flow/calibrate_target_sync.py",
    "flow/checkpoint.py",
    "flow/checkpoint_discovery.py",
    "flow/checkpoint_store.py",
    "flow/child_progress.py",
)

_WHY_COMMENT_RE = re.compile(r"^#\s*why:")
_FMT_DIRECTIVE_RE = re.compile(r"^#\s*fmt:\s*(off|on|skip)\s*$")


def _is_permitted_comment(line_number: int, text: str) -> bool:
    return (
        is_allowlisted_comment(line_number, text)
        or bool(_WHY_COMMENT_RE.match(text))
        or bool(_FMT_DIRECTIVE_RE.match(text))
    )


def _read_scoped_module(relative_path: str) -> str:
    return (PACKAGE_DIR / relative_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("relative_path", SCOPED_MODULES)
def test_scoped_module_exists(relative_path: str) -> None:
    assert (PACKAGE_DIR / relative_path).is_file()


@pytest.mark.parametrize("relative_path", SCOPED_MODULES)
def test_scoped_module_has_no_docstrings(relative_path: str) -> None:
    assert find_docstrings_and_string_statements(_read_scoped_module(relative_path)) == []


@pytest.mark.parametrize("relative_path", SCOPED_MODULES)
def test_scoped_module_has_only_permitted_comments(relative_path: str) -> None:
    comments = find_comments(_read_scoped_module(relative_path))
    assert [comment for comment in comments if not _is_permitted_comment(*comment)] == []


@pytest.mark.parametrize(
    ("line_number", "text", "expected"),
    [
        (5, "# why: x", True),
        (5, "# fmt: off", True),
        (5, "# noqa: E501", True),
        (1, "#!/usr/bin/env python3", True),
        (1, "# -*- coding: utf-8 -*-", True),
        (5, "# explain", False),
        (5, "# TODO", False),
        (5, "# why not", False),
    ],
)
def test_permitted_comment_predicate(line_number: int, text: str, expected: bool) -> None:
    assert _is_permitted_comment(line_number, text) is expected
