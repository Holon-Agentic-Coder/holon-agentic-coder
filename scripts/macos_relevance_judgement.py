"""Judging the changed files for the macOS relevance gate: the decision rules, the verdicts and the log lines.

``scripts.macos_relevance`` applies the event rules first and then, like its offline replay, calls ``judge_commit``
here to judge every changed file with ``scripts.macos_relevance_rules``. Standard library only and Python 3.9 syntax.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from scripts.ci_bookkeeping_chain import Decision
from scripts.macos_relevance_git import GitRunner, blob_text, changed_files
from scripts.macos_relevance_rules import BY_PATH, CONTENT_CAP, Verdict, judge_change

# ``scripts.ci_bookkeeping_gate.RESULT_AFFECTING_LABELS`` carries the same literal; a test keeps the two equal.
FORCE_LABEL = "ci-macos"
SKIP_SENTENCE = (
    "The macOS unit leg is skipped: no changed file is platform-sensitive; "
    f"add the '{FORCE_LABEL}' label or re-run the job to force it."
)

RULE_EVENT_NOT_PULL_REQUEST = "event-not-pull-request"
RULE_RERUN_ATTEMPT = "rerun-attempt"
RULE_FORCE_LABEL = "force-label"
RULE_INCOMPLETE_EVENT = "incomplete-event"
RULE_CHANGED_FILES_UNREADABLE = "changed-files-unreadable"
RULE_NO_CHANGED_FILES = "no-changed-files"
RULE_SENSITIVE_PATH = "sensitive-path"
RULE_SENSITIVE_CONTENT = "sensitive-content"
RULE_INTERNAL_ERROR = "internal-error"
RULE_ALL_NEUTRAL = "all-neutral"
RUN_RULES = (
    RULE_EVENT_NOT_PULL_REQUEST,
    RULE_RERUN_ATTEMPT,
    RULE_FORCE_LABEL,
    RULE_INCOMPLETE_EVENT,
    RULE_CHANGED_FILES_UNREADABLE,
    RULE_NO_CHANGED_FILES,
    RULE_SENSITIVE_PATH,
    RULE_SENSITIVE_CONTENT,
    RULE_INTERNAL_ERROR,
)
SKIP_RULES = (RULE_ALL_NEUTRAL,)
GIT_ERRORS = (subprocess.SubprocessError, OSError, ValueError)


@dataclass(frozen=True)
class Judgement:
    """The gate's decision and the verdict on every changed file (none when an earlier rule decided)."""

    decision: Decision
    verdicts: tuple[Verdict, ...] = ()

    def lines(self) -> list[str]:
        """Render the decision line, one line per judged path and, on a skip, the sentence that says how to force."""
        decision = self.decision
        head = f"macos-relevance-gate: {'skip' if decision.skip else 'run'} ({decision.rule}): {decision.detail}"
        judged = [verdict_line(verdict) for verdict in self.verdicts]
        return [printable(head.rstrip()), *judged, *([SKIP_SENTENCE] if decision.skip else [])]


def verdict_line(verdict: Verdict) -> str:
    """Render ``  <sensitive|neutral> <status> <path> [<- old path]  <side>:<reason>``, printable only."""
    change = verdict.change
    moved = f" <- {change.old_path}" if change.old_path and change.old_path != change.path else ""
    why = f"  {verdict.side}:{verdict.reason}" if verdict.sensitive else ""
    kind = "sensitive" if verdict.sensitive else "neutral"
    return printable(f"  {kind:<9} {change.status} {change.path}{moved}{why}")


def printable(text: str) -> str:
    """Replace every character that is not printable, as ``Decision.line`` does, so no path injects a log command."""
    return "".join(char if char.isprintable() else "?" for char in text)


def verdicts_decision(verdicts: Sequence[Verdict]) -> Decision:
    """Decide on the judged files: the first sensitive one runs the leg; only the last statement returns "skip"."""
    if not verdicts:
        return Decision(False, RULE_NO_CHANGED_FILES, "")
    sensitive = next((verdict for verdict in verdicts if verdict.sensitive), None)
    if sensitive is not None:
        rule = RULE_SENSITIVE_PATH if sensitive.basis == BY_PATH else RULE_SENSITIVE_CONTENT
        return Decision(False, rule, f"{sensitive.change.path} ({sensitive.side}: {sensitive.reason})")
    return Decision(True, RULE_ALL_NEUTRAL, f"{len(verdicts)} changed files")


def judge_commit(runner: GitRunner, base: str, head: str) -> Judgement:
    """Judge every file that ``head`` changes against ``base``; a git failure listing them runs the leg."""
    try:
        changes = changed_files(runner, base, head)
    except GIT_ERRORS as error:
        return Judgement(Decision(False, RULE_CHANGED_FILES_UNREADABLE, type(error).__name__))
    revs = {"new": head, "old": base}
    verdicts = tuple(
        judge_change(change, lambda side, path: blob_text(runner, revs[side], path, CONTENT_CAP)) for change in changes
    )
    return Judgement(verdicts_decision(verdicts), verdicts)
