#!/usr/bin/env python3
"""macOS relevance gate: decide whether the ``macos-latest`` unit leg may skip its expensive steps on a pull request.

The macOS leg is the slowest job of the pipeline, and most changes cannot behave differently on macOS than on Linux.
The leg runs when at least one changed file is platform-sensitive by path or by content, its new or its old version,
as ``scripts.macos_relevance_rules`` states; it skips only when every changed file is neutral. The changed files are
those of the checked-out merge commit against its first parent (``scripts.macos_relevance_git``), judged by
``scripts.macos_relevance_judgement``. The rules of ``RUN_RULES`` are checked in order and the first match runs the
leg; any exception is ``internal-error``; only ``all-neutral`` skips. ``--replay-commit`` and ``--replay-range`` judge
squash-merged commits offline with the same functions. Standard library only and Python 3.9 syntax.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from scripts.ci_bookkeeping_api import load_event
from scripts.ci_bookkeeping_chain import Decision
from scripts.ci_bookkeeping_gate import write_output
from scripts.ci_timing_gate import write_step_summary
from scripts.macos_relevance_git import GitRunner, commit_parents, default_runner, first_parent_commits
from scripts.macos_relevance_judgement import (
    FORCE_LABEL,
    GIT_ERRORS,
    RULE_CHANGED_FILES_UNREADABLE,
    RULE_EVENT_NOT_PULL_REQUEST,
    RULE_FORCE_LABEL,
    RULE_INCOMPLETE_EVENT,
    RULE_INTERNAL_ERROR,
    RULE_RERUN_ATTEMPT,
    RUN_RULES,
    SKIP_RULES,
    SKIP_SENTENCE,
    Judgement,
    judge_commit,
    printable,
    verdict_line,
    verdicts_decision,
)

__all__ = [
    "FORCE_LABEL",
    "RUN_RULES",
    "SKIP_RULES",
    "SKIP_SENTENCE",
    "Judgement",
    "RelevanceFacts",
    "decide",
    "facts_from",
    "judge_commit",
    "main",
    "replay_commit",
    "replay_range",
    "safe_decide",
    "verdict_line",
    "verdicts_decision",
]

PULL_REQUEST_NUMBER = re.compile(r"\(#(\d+)\)\s*$")


@dataclass(frozen=True)
class RelevanceFacts:
    """What the event and the environment say about this run; a missing value is empty text, 0 or no labels."""

    event_name: str
    run_attempt: int
    labels: tuple[str, ...]
    sha: str


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _child(mapping: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = mapping.get(key)
    return value if isinstance(value, dict) else {}


def facts_from(environ: Mapping[str, str], payload: Mapping[str, object]) -> RelevanceFacts:
    """Fill the facts from the ``GITHUB_*`` environment and the event payload; label names are lowered."""
    labels = _child(payload, "pull_request").get("labels")
    entries = labels if isinstance(labels, list) else []
    attempt = _text(environ.get("GITHUB_RUN_ATTEMPT"))
    return RelevanceFacts(
        event_name=_text(environ.get("GITHUB_EVENT_NAME")),
        run_attempt=int(attempt) if attempt.isascii() and attempt.isdigit() else 0,
        labels=tuple(_text(entry.get("name")).lower() for entry in entries if isinstance(entry, dict)),
        sha=_text(environ.get("GITHUB_SHA")),
    )


def event_rule(facts: RelevanceFacts) -> Decision | None:
    """Return the "run" decision of the first event rule that matches, or ``None`` when the changed files decide."""
    checks = (
        (facts.event_name != "pull_request", RULE_EVENT_NOT_PULL_REQUEST, facts.event_name),
        (facts.run_attempt > 1, RULE_RERUN_ATTEMPT, f"attempt {facts.run_attempt}"),
        (FORCE_LABEL in facts.labels, RULE_FORCE_LABEL, FORCE_LABEL),
        (not (facts.sha and facts.run_attempt), RULE_INCOMPLETE_EVENT, ""),
    )
    return next((Decision(False, rule, detail) for matched, rule, detail in checks if matched), None)


def decide(facts: RelevanceFacts, runner: GitRunner) -> Judgement:
    """Apply the event rules, then judge the checked-out merge commit against its first parent."""
    blocked = event_rule(facts)
    if blocked is not None:
        return Judgement(blocked)
    try:
        head, parents = commit_parents(runner, "HEAD")
    except GIT_ERRORS as error:
        return Judgement(Decision(False, RULE_CHANGED_FILES_UNREADABLE, type(error).__name__))
    if head != facts.sha:
        return Judgement(Decision(False, RULE_CHANGED_FILES_UNREADABLE, "HEAD is not GITHUB_SHA"))
    if len(parents) != 2:
        return Judgement(Decision(False, RULE_CHANGED_FILES_UNREADABLE, f"HEAD has {len(parents)} parents, not 2"))
    return judge_commit(runner, parents[0], head)


def safe_decide(facts: RelevanceFacts, runner: GitRunner) -> Judgement:
    """Call ``decide``; any exception is ``internal-error`` with its class name, never its text."""
    try:
        return decide(facts, runner)
    except Exception as error:
        return Judgement(Decision(False, RULE_INTERNAL_ERROR, type(error).__name__))


def replay_commit(runner: GitRunner, rev: str) -> Judgement:
    """Judge ``rev`` against its first parent as a pull request without a label; no ``GITHUB_*`` value is read."""
    sha, parents = commit_parents(runner, rev)
    if not parents:
        return Judgement(Decision(False, RULE_CHANGED_FILES_UNREADABLE, "a root commit has no parent"))
    return judge_commit(runner, parents[0], sha)


def replay_range(runner: GitRunner, rev_range: str) -> list[str]:
    """Return one markdown table row per squash-merged pull request (subject ending in ``(#N)``) of ``rev_range``."""
    rows = ["| pull request | macOS leg | rule | first sensitive file (side: reason) |", "| --- | --- | --- | --- |"]
    for sha, subject in first_parent_commits(runner, rev_range):
        number = PULL_REQUEST_NUMBER.search(subject)
        if number:
            decision = replay_commit(runner, sha).decision
            detail = "-" if decision.skip else printable(decision.detail).replace("|", "\\|")
            rows.append(f"| #{number.group(1)} | {'skip' if decision.skip else 'run'} | {decision.rule} | {detail} |")
    return rows


def run_replay(args: argparse.Namespace, runner: GitRunner) -> None:
    """Print the replay of one commit (with every judged path) or of a range (one row per pull request)."""
    if args.replay_commit:
        print("\n".join(replay_commit(runner, args.replay_commit).lines()))
    else:
        print("\n".join(replay_range(runner, args.replay_range)))


def main(
    argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None, runner: GitRunner = default_runner
) -> int:
    """Report the judgement and return 0 in every case; whatever goes wrong ends in ``skip=false``."""
    env = os.environ if environ is None else environ
    try:
        parser = argparse.ArgumentParser(description="Decide whether the macOS unit leg may skip its expensive steps.")
        parser.add_argument("--replay-commit", default="", help="replay: judge this commit against its first parent")
        parser.add_argument("--replay-range", default="", help="replay: one row per squash-merged pull request")
        args = parser.parse_args(argv)
        if args.replay_commit or args.replay_range:
            run_replay(args, runner)
            return 0
        facts = facts_from(env, load_event(env.get("GITHUB_EVENT_PATH", "")))
        judgement = safe_decide(facts, runner)
        print("\n".join(judgement.lines()))
        write_step_summary(judgement.lines())
        write_output(env, judgement.decision.skip)
    except (Exception, SystemExit) as error:
        print(Judgement(Decision(False, RULE_INTERNAL_ERROR, type(error).__name__)).lines()[0])
        with contextlib.suppress(OSError):
            write_output(env, False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
