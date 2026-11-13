"""The line of an approved consensus review that names the head it covers and the CI result at that commit.

A later head needs its own approval; :mod:`sandbox_executor.flow.github.approval_invalidation` says when one stops
holding. The CI result is the measured count at the named commit, never a blanket "all green".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SHORT_SHA_CHARS = 7


def ci_result_at_commit(ci_status: Mapping[str, Any]) -> str:
    """``11 of 11 checks passed``, ``2 of 11 checks failing``, ``pending`` or ``not measured`` for a CI probe."""
    total = int(ci_status.get("total") or 0)
    if ci_status.get("measured") is False or total <= 0:
        return "not measured"
    failed, pending = int(ci_status.get("failed") or 0), int(ci_status.get("pending") or 0)
    if failed:
        return f"{failed} of {total} checks failing"
    if pending:
        return f"{pending} of {total} checks still pending"
    passed = int(ci_status.get("passed") or 0)
    return f"{passed} of {total} checks passed" if passed > 0 else "not measured"


def approval_stamp(commit: str, ci_status: Mapping[str, Any]) -> str:
    """``Covers head `abc1234`; CI at that commit: 11 of 11 checks passed.`` for an approved report."""
    return f"**Covers head** `{commit[:SHORT_SHA_CHARS]}`; CI at that commit: {ci_result_at_commit(ci_status)}."
