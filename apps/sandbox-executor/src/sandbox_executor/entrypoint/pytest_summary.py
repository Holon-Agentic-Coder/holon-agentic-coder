"""Summary-first excerpt of pytest output: the ``short test summary info`` section and the final counts line.

The progress dots that open a long run carry no information, so the excerpt is taken from the end of the output (or
from the summary section when there is one) and never from its head.
"""

import re

_SUMMARY_HEADER = re.compile(r"^=+\s*short test summary info\s*=+\s*$", re.MULTILINE)
_COUNTS = re.compile(r"^\d+ (?:failed|passed|errors?|skipped|xfailed|xpassed|warnings?)\b.* in [\d.]+s\b")
_OMISSION = "[... earlier output omitted ...]\n"


def counts_line(text: str) -> str:
    """Return the last ``N failed, M passed in Xs`` line of ``text``, or an empty string."""
    for line in reversed(text.splitlines()):
        stripped = line.strip().strip("= ")
        if _COUNTS.match(stripped):
            return line.strip()
    return ""


def summary_section(text: str) -> str:
    """Return the ``short test summary info`` section (header included, counts line excluded), or an empty string."""
    matches = list(_SUMMARY_HEADER.finditer(text))
    if not matches:
        return ""
    body = text[matches[-1].start() :].strip().splitlines()
    counts = counts_line(text)
    return "\n".join(line for line in body if line.strip() != counts).strip()


def summary_excerpt(text: str, budget: int) -> str:
    """Return ``text`` when it fits ``budget``, else the summary section plus counts line, else the pure tail.

    When the summary section alone overflows the budget its head is kept (the first failed tests) and the counts line
    is still appended, so the final tally is never lost.
    """
    if len(text) <= budget:
        return text
    counts = counts_line(text)
    section = summary_section(text)
    if not section:
        return _OMISSION + text[-max(budget - len(_OMISSION), 0) :] if budget > len(_OMISSION) else text[-budget:]
    tail = f"\n{counts}" if counts else ""
    room = max(budget - len(tail), 0)
    return section[:room].rstrip() + tail
