from __future__ import annotations

from pathlib import Path

import pytest

from scripts.self_explaining_comments import Finding, find_comments_and_docstrings

FLOW_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "flow"

SCOPE_MODULES = (
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

GUARD_SOURCE = """#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''Module docstring.'''
import os  # noqa: E501
value = os.sep  # type: ignore[arg-type]
# why: reason
# fmt: off
# pragma: no cover
# explain


def documented() -> None:
    '''Function docstring.'''
"""


def describe(findings: list[Finding]) -> str:
    return "\n".join(f"  line {finding.first_line}: {finding.kind} {finding.text!r}" for finding in findings)


@pytest.mark.parametrize("module", SCOPE_MODULES)
def test_scope_module_exists(module: str) -> None:
    assert (FLOW_DIR / module).is_file()


@pytest.mark.parametrize("module", SCOPE_MODULES)
def test_scope_module_has_no_docstring_or_disallowed_comment(module: str) -> None:
    path = FLOW_DIR / module
    findings = find_comments_and_docstrings(str(path), path.read_text(encoding="utf-8"))
    assert findings == [], f"{path}:\n{describe(findings)}"


def test_checker_reports_docstrings_and_plain_comments_but_allows_reasons_and_directives() -> None:
    findings = find_comments_and_docstrings("guard.py", GUARD_SOURCE)
    assert sorted((finding.first_line, finding.kind) for finding in findings) == [
        (3, "module docstring"),
        (9, "comment"),
        (13, "function docstring"),
    ]
