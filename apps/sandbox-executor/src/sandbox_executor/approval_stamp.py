"""The line of an approved consensus review that names the head it covers and the CI result at that commit.

A later head needs its own approval; :mod:`sandbox_executor.flow.github.approval_invalidation` says when one stops
holding. The CI result is the measured count at the named commit, never a blanket "all green". An approval bound to a
PR-own change fingerprint (:mod:`sandbox_executor.flow.review.approval_fingerprint`) names it, and an approval carried
over a target-branch merge names the head the reviewers saw as well.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SHORT_SHA_CHARS = 7
FINGERPRINT_SHOWN_CHARS = 12


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


def _fingerprint_clause(fingerprint: str | None) -> str:
    """``change fingerprint `pf1:0123456789ab`; `` for a known fingerprint, else empty."""
    if not fingerprint:
        return ""
    version, _, digest = fingerprint.partition(":")
    return f"change fingerprint `{version}:{digest[:FINGERPRINT_SHOWN_CHARS]}`; "


def approval_stamp(
    commit: str, ci_status: Mapping[str, Any], fingerprint: str | None = None, carried_from: str | None = None
) -> str:
    """``Covers head `abc1234`; CI at that commit: 11 of 11 checks passed.`` for an approved report.

    A known ``fingerprint`` is named after the head; ``carried_from`` names the head the reviewers saw when the
    approval was carried over a target-branch merge to ``commit``. Without either the line is unchanged.
    """
    carried = f"carried over from `{carried_from[:SHORT_SHA_CHARS]}` (same change); " if carried_from else ""
    return (
        f"**Covers head** `{commit[:SHORT_SHA_CHARS]}`; {_fingerprint_clause(fingerprint)}{carried}"
        f"CI at that commit: {ci_result_at_commit(ci_status)}."
    )
