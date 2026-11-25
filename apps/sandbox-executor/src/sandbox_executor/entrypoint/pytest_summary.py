import re

_SUMMARY_HEADER = re.compile(r"^=+\s*short test summary info\s*=+\s*$", re.MULTILINE)
_COUNTS = re.compile(r"^\d+ (?:failed|passed|errors?|skipped|xfailed|xpassed|warnings?)\b.* in [\d.]+s\b")
_OMISSION = "[... earlier output omitted ...]\n"


def counts_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip().strip("= ")
        if _COUNTS.match(stripped):
            return line.strip()
    return ""


def summary_section(text: str) -> str:
    matches = list(_SUMMARY_HEADER.finditer(text))
    if not matches:
        return ""
    body = text[matches[-1].start() :].strip().splitlines()
    counts = counts_line(text)
    return "\n".join(line for line in body if line.strip() != counts).strip()


def _tail_excerpt_with_omission(text: str, budget: int) -> str:
    if budget <= len(_OMISSION):
        return text[-budget:]
    remaining_budget = max(budget - len(_OMISSION), 0)
    return _OMISSION + text[-remaining_budget:]


def summary_excerpt(text: str, budget: int) -> str:
    if budget <= 0:
        return ""
    if len(text) <= budget:
        return text
    counts = counts_line(text)
    section = summary_section(text)
    if not section:
        return _tail_excerpt_with_omission(text, budget)
    tail = f"\n{counts}" if counts else ""
    room = max(budget - len(tail), 0)
    return section[:room].rstrip() + tail
