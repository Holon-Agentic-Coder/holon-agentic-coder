from __future__ import annotations

import re
from dataclasses import dataclass

# why: every name is joined from fragments at import time so no line of this file matches its own patterns.
_DOT = "."
_TASK_DIRECTORY = "".join((_DOT, "be", "ans"))
_AGENT_DIRECTORY = "".join((_DOT, "cla", "ude"))
_SCRATCH_DIRECTORY = "".join((_DOT, "scr", "atch"))
_TRACKER_NOUN = "".join(("be", "an"))

_SEGMENT_START = r"(?<![^\s\"'`(/])"
# why: prose ends a name with sentence punctuation, so a run of .,;:!? before a boundary still ends the segment.
_SEGMENT_END = r"(?=/|[.,;:!?]*(?:[\s\"'`)]|$))"
_HOME_PREFIXES = r"(?<!~/)(?<!HOME/)"


@dataclass(frozen=True)
class HarnessPattern:
    label: str
    regex: re.Pattern[str]
    sample: str


def _directory(label: str, name: str) -> HarnessPattern:
    regex = re.compile(_SEGMENT_START + re.escape(name) + _SEGMENT_END)
    return HarnessPattern(label, regex, f"see {name}/notes.md")


HARNESS_PATTERNS: tuple[HarnessPattern, ...] = (
    _directory("task-tracking directory", _TASK_DIRECTORY),
    HarnessPattern(
        "agent-instruction directory",
        re.compile(_HOME_PREFIXES + _SEGMENT_START + re.escape(_AGENT_DIRECTORY) + "/"),
        f"read {_AGENT_DIRECTORY}/rules.md",
    ),
    _directory("scratch directory", _SCRATCH_DIRECTORY),
    HarnessPattern(
        "task id",
        re.compile(r"(?<![a-z])" + _TRACKER_NOUN + r"[ _-]?\d{4}(?!\d)", re.IGNORECASE),
        f"done in {_TRACKER_NOUN.title()} {40 + 2:04d}",
    ),
)


def find_references(text: str) -> list[str]:
    return [pattern.label for pattern in HARNESS_PATTERNS if pattern.regex.search(text)]


def sample_reference(label: str) -> str:
    return next(pattern.sample for pattern in HARNESS_PATTERNS if pattern.label == label)
