"""The evidence rule for resolver rejections: a finding leaves review only through a reasoned disposition.

A finding the resolver did not adjudicate stays ``open`` at every severity. A rejection counts only with a stated reason
from the vocabulary: an empty or ``unspecified`` reason is refused and leaves the finding ``open``, whatever its
severity. A rejection of an ``important`` or ``critical`` finding further needs a specific reason (never a bare
``out of scope``) and an ``evidence`` string naming the code it checked: a ``path:line`` token or a test
(``tests/...::test_name`` or a ``test_*.py`` file). Anything else leaves the finding ``open``, keeping the resolver's
own text as evidence for the audit trail.
"""

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
    """True when ``evidence`` names a file and line or a test."""
    text = evidence or ""
    return bool(_FILE_LINE.search(text) or _TEST_REFERENCE.search(text))


def has_stated_reason(reason: str | None) -> bool:
    """True when ``reason`` is a stated rejection reason (neither empty nor ``unspecified``)."""
    clean = (reason or "").strip().lower()
    return bool(clean) and clean != UNSPECIFIED_REASON


def require_rejection_reason(reason: str | None) -> str:
    """Return ``reason`` when it is a stated rejection reason; raise ``ValueError`` for an empty or unspecified one."""
    if not has_stated_reason(reason):
        raise ValueError(f"a rejection needs a stated reason, got {reason!r}")
    return str(reason)


def default_disposition(severity: str) -> str:
    """The disposition of a finding the resolver did not adjudicate: it stays open at every severity."""
    del severity
    return OPEN


def settle_rejection(severity: str, reason: str, evidence: str | None) -> tuple[str, str | None, str | None]:
    """The ``(disposition, rejection_reason, evidence)`` a rejection stands as under the evidence rule."""
    if not has_stated_reason(reason):
        return OPEN, None, "refused rejection (no reason)" + (f": {evidence}" if evidence else "")
    if severity not in EVIDENCED_SEVERITIES or (reason not in UNEVIDENCED_REASONS and names_checked_code(evidence)):
        return REJECTED, reason, evidence
    audit = f"unevidenced rejection ({reason})" + (f": {evidence}" if evidence else "")
    return OPEN, None, audit
