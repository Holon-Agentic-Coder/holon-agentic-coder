from __future__ import annotations

import re

OPEN = "open"
REJECTED = "rejected"
UNSPECIFIED_REASON = "unspecified"
EVIDENCED_SEVERITIES = frozenset({"critical", "important"})
UNEVIDENCED_REASONS = frozenset({"out of scope", UNSPECIFIED_REASON})

_FILE_LINE = re.compile(r"[\w./-]+\.\w+:\d+")
_TEST_REFERENCE = re.compile(r"[\w./-]*tests?/[\w./-]+\.py::\w+|\btest_\w+\.py\b")


def names_checked_code(evidence: str | None) -> bool:
    text = evidence or ""
    return bool(_FILE_LINE.search(text) or _TEST_REFERENCE.search(text))


def has_stated_reason(reason: str | None) -> bool:
    clean = (reason or "").strip().lower()
    return bool(clean) and clean != UNSPECIFIED_REASON


def require_rejection_reason(reason: str | None) -> str:
    if not has_stated_reason(reason):
        raise ValueError(f"a rejection needs a stated reason, got {reason!r}")
    return str(reason)


def default_disposition(severity: str) -> str:
    del severity
    return OPEN


def settle_rejection(severity: str, reason: str, evidence: str | None) -> tuple[str, str | None, str | None]:
    if not has_stated_reason(reason):
        return OPEN, None, "refused rejection (no reason)" + (f": {evidence}" if evidence else "")
    if severity not in EVIDENCED_SEVERITIES or (reason not in UNEVIDENCED_REASONS and names_checked_code(evidence)):
        return REJECTED, reason, evidence
    audit = f"unevidenced rejection ({reason})" + (f": {evidence}" if evidence else "")
    return OPEN, None, audit
