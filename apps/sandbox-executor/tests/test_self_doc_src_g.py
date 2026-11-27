from __future__ import annotations

from pathlib import Path

import pytest

from scripts.self_explaining_comments import find_comments_and_docstrings

FLOW_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "flow"

SCOPE_MODULES: tuple[str, ...] = (
    "admission.py",
    "admission_decision.py",
    "admission_lock.py",
    "admission_order.py",
    "admission_record.py",
    "admission_state.py",
    "base_files.py",
    "calibrate_fetch_retry.py",
    "calibrate_promotion.py",
    "calibrate_rebuild.py",
)

DOCUMENTED_SOURCE = '''"""Module doc."""


def documented():
    """Function doc."""
    return 1  # explain
'''

ALLOWED_COMMENTS_SOURCE = """#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os  # noqa: F401

# why: reason the code cannot show
value = os.environ.get("X")  # type: ignore[arg-type]
# fmt: off
if value:  # pragma: no cover
    value = None
# fmt: on
"""


def test_scope_modules_are_sorted_and_unique() -> None:
    assert list(SCOPE_MODULES) == sorted(set(SCOPE_MODULES))


@pytest.mark.parametrize("module", SCOPE_MODULES)
def test_scope_module_exists(module: str) -> None:
    assert (FLOW_DIR / module).is_file()


@pytest.mark.parametrize("module", SCOPE_MODULES)
def test_scope_module_has_no_docstring_or_disallowed_comment(module: str) -> None:
    path = FLOW_DIR / module
    findings = find_comments_and_docstrings(str(path), path.read_text(encoding="utf-8"))
    listed = [f"{path}:{finding.first_line}: {finding.kind}" for finding in findings]
    assert listed == []


def test_checker_reports_docstrings_and_plain_comments() -> None:
    findings = find_comments_and_docstrings("example.py", DOCUMENTED_SOURCE)
    assert [finding.kind for finding in findings] == ["module docstring", "function docstring", "comment"]


def test_checker_accepts_reasons_and_machine_directives() -> None:
    assert find_comments_and_docstrings("example.py", ALLOWED_COMMENTS_SOURCE) == []
