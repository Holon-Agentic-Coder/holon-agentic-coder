"""The evidence rule for resolver rejections: an important or critical finding stays open unless proven otherwise.

A resolver may reject a nit with any reason from the vocabulary. A rejection of an ``important`` or ``critical`` finding
counts only with a specific reason (never a bare ``out of scope`` or ``unspecified``) and an ``evidence`` string naming
the code it checked: a ``path:line`` token or a test (``tests/...::test_name`` or a ``test_*.py`` file). Anything else
leaves the finding ``open``, keeping the resolver's own text as evidence for the audit trail.
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


def default_disposition(severity: str) -> str:
    """The disposition of a finding the resolver did not adjudicate: open for important and higher, else rejected."""
    return OPEN if severity in EVIDENCED_SEVERITIES else REJECTED


def settle_rejection(severity: str, reason: str, evidence: str | None) -> tuple[str, str | None, str | None]:
    """The ``(disposition, rejection_reason, evidence)`` a rejection stands as under the evidence rule."""
    if severity not in EVIDENCED_SEVERITIES or (reason not in UNEVIDENCED_REASONS and names_checked_code(evidence)):
        return REJECTED, reason, evidence
    audit = f"unevidenced rejection ({reason})" + (f": {evidence}" if evidence else "")
    return OPEN, None, audit
