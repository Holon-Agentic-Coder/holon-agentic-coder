#!/usr/bin/env python3
"""macOS relevance gate: decide whether the ``macos-latest`` unit leg may skip its expensive steps on a pull request.

The macOS leg is the slowest job of the pipeline, and most changes cannot behave differently on macOS than on Linux.
The rule is an allow-list with a default of "run": the leg skips only when every file the pull request changes is
platform-neutral: bookkeeping files exactly as ``scripts.ci_bookkeeping_gate.is_bookkeeping_path`` defines them, any
markdown file (lower-case ``.md``) outside ``.github/``, and ``LICENSE``. A list of platform-sensitive paths could never
be complete (a new module that calls ``subprocess`` would be missing from it); here an unknown file runs the leg.
Everything else runs the leg, among it every workflow and action under ``.github/``, this script, ``pyproject.toml``,
the lock files, shell scripts and all Python. The rules of ``RUN_RULES`` are checked in order (the first match runs the
leg; any exception is ``api-error``) and only ``all-platform-neutral`` skips. The changed files are those of
``base...head``, the pull request's own changes since the merge base. Standard library only and Python 3.9 syntax.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import posixpath
import sys
import urllib.error
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from scripts.ci_bookkeeping_api import GateClient, Opener, compare, load_event
from scripts.ci_bookkeeping_chain import Decision
from scripts.ci_bookkeeping_gate import is_bookkeeping_path, write_output
from scripts.ci_timing_gate import default_opener, write_step_summary

# ``scripts.ci_bookkeeping_gate.RESULT_AFFECTING_LABELS`` carries the same literal; a test keeps the two equal.
FORCE_LABEL = "ci-macos"
NEUTRAL_FILE_NAMES = ("LICENSE",)
MARKDOWN_SUFFIX = ".md"
SENSITIVE_ROOT = ".github"
NEUTRAL, SENSITIVE = "neutral", "sensitive"
SKIP_SENTENCE = (
    "The macOS unit leg is skipped: every changed file is platform-neutral; "
    f"add the '{FORCE_LABEL}' label or re-run the job to force it."
)

RULE_EVENT_NOT_PULL_REQUEST = "event-not-pull-request"
RULE_RERUN_ATTEMPT = "rerun-attempt"
RULE_FORCE_LABEL = "force-label"
RULE_INCOMPLETE_EVENT = "incomplete-event"
RULE_DIFF_TOO_LARGE = "diff-too-large"
RULE_NO_CHANGED_FILES = "no-changed-files"
RULE_PLATFORM_SENSITIVE_CHANGE = "platform-sensitive-change"
RULE_API_ERROR = "api-error"
RULE_ALL_PLATFORM_NEUTRAL = "all-platform-neutral"
RUN_RULES = (
    RULE_EVENT_NOT_PULL_REQUEST,
    RULE_RERUN_ATTEMPT,
    RULE_FORCE_LABEL,
    RULE_INCOMPLETE_EVENT,
    RULE_DIFF_TOO_LARGE,
    RULE_NO_CHANGED_FILES,
    RULE_PLATFORM_SENSITIVE_CHANGE,
    RULE_API_ERROR,
)
SKIP_RULES = (RULE_ALL_PLATFORM_NEUTRAL,)


def is_platform_neutral(path: str) -> bool:
    """Return true only for a bookkeeping file, a markdown file outside ``.github/`` or ``LICENSE``."""
    if not path or path.partition("/")[0] == SENSITIVE_ROOT:
        return False
    return is_bookkeeping_path(path) or posixpath.basename(path).endswith(MARKDOWN_SUFFIX) or path in NEUTRAL_FILE_NAMES


def judge_paths(paths: Sequence[str]) -> list[tuple[str, str]]:
    """Return every path with its class, ``neutral`` or ``sensitive``, in input order."""
    return [(path, NEUTRAL if is_platform_neutral(path) else SENSITIVE) for path in paths]


@dataclass(frozen=True)
class RelevanceFacts:
    """What the event and the environment say about this run; a missing value is empty text, 0 or no labels."""

    event_name: str
    run_attempt: int
    labels: tuple[str, ...]
    base_sha: str
    head_sha: str


@dataclass(frozen=True)
class Judgement:
    """The gate's decision and the changed paths it judged (none when an earlier rule decided)."""

    decision: Decision
    paths: tuple[str, ...] = ()

    def lines(self) -> list[str]:
        """Render the decision line, one line per judged path and, on a skip, the sentence that says how to force."""
        head = "macos-relevance-gate:" + self.decision.line().partition(":")[2]
        judged = [f"  {kind:<9} {path}" for path, kind in judge_paths(self.paths)]
        return [head, *judged, *([SKIP_SENTENCE] if self.decision.skip else [])]


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _child(mapping: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = mapping.get(key)
    return value if isinstance(value, dict) else {}


def facts_from(environ: Mapping[str, str], payload: Mapping[str, object]) -> RelevanceFacts:
    """Fill the facts from the ``GITHUB_*`` environment and the event payload; label names are lowered."""
    pull_request = _child(payload, "pull_request")
    labels = pull_request.get("labels")
    entries = labels if isinstance(labels, list) else []
    attempt = _text(environ.get("GITHUB_RUN_ATTEMPT"))
    return RelevanceFacts(
        event_name=_text(environ.get("GITHUB_EVENT_NAME")),
        run_attempt=int(attempt) if attempt.isascii() and attempt.isdigit() else 0,
        labels=tuple(_text(entry.get("name")).lower() for entry in entries if isinstance(entry, dict)),
        base_sha=_text(_child(pull_request, "base").get("sha")),
        head_sha=_text(_child(pull_request, "head").get("sha")),
    )


def event_rule(facts: RelevanceFacts) -> Decision | None:
    """Return the "run" decision of the first event rule that matches, or ``None`` when the paths decide."""
    checks = (
        (facts.event_name != "pull_request", RULE_EVENT_NOT_PULL_REQUEST, facts.event_name),
        (facts.run_attempt > 1, RULE_RERUN_ATTEMPT, f"attempt {facts.run_attempt}"),
        (FORCE_LABEL in facts.labels, RULE_FORCE_LABEL, FORCE_LABEL),
        (not (facts.base_sha and facts.head_sha and facts.run_attempt), RULE_INCOMPLETE_EVENT, ""),
    )
    return next((Decision(False, rule, detail) for matched, rule, detail in checks if matched), None)


def paths_decision(paths: Sequence[str], truncated: bool = False) -> Decision:
    """Judge the changed paths; only the last statement returns "skip"."""
    if truncated:
        return Decision(False, RULE_DIFF_TOO_LARGE, f"{len(paths)} paths or more")
    if not paths:
        return Decision(False, RULE_NO_CHANGED_FILES, "")
    sensitive = next((path for path in paths if not is_platform_neutral(path)), None)
    if sensitive is not None:
        return Decision(False, RULE_PLATFORM_SENSITIVE_CHANGE, sensitive)
    return Decision(True, RULE_ALL_PLATFORM_NEUTRAL, f"{len(paths)} changed files")


def decide(facts: RelevanceFacts, client: GateClient) -> Judgement:
    """Apply the event rules, then judge the paths of ``base...head``. Exceptions are left to ``safe_decide``."""
    blocked = event_rule(facts)
    if blocked is not None:
        return Judgement(blocked)
    comparison = compare(client, facts.base_sha, facts.head_sha)
    return Judgement(paths_decision(comparison.paths, comparison.truncated), comparison.paths)


def safe_decide(facts: RelevanceFacts, client: GateClient) -> Judgement:
    """Call ``decide``; any exception is ``api-error`` with its class name and HTTP status, never its text."""
    try:
        return decide(facts, client)
    except Exception as error:
        status = f" {error.code}" if isinstance(error, urllib.error.HTTPError) else ""
        return Judgement(Decision(False, RULE_API_ERROR, f"{type(error).__name__}{status}"))


def replay(paths_file: str) -> Judgement:
    """Judge the newline-separated paths of ``paths_file`` as a pull request without a label; no network."""
    with open(paths_file, encoding="utf-8") as handle:
        paths = tuple(line.strip() for line in handle if line.strip())
    return Judgement(paths_decision(paths), paths)


def main(
    argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None, opener: Opener = default_opener
) -> int:
    """Report the judgement and return 0 in every case; whatever goes wrong ends in ``api-error`` and ``skip=false``."""
    env = os.environ if environ is None else environ
    try:
        parser = argparse.ArgumentParser(description="Decide whether the macOS unit leg may skip its expensive steps.")
        parser.add_argument("--paths-file", default="", help="replay: judge these paths, one per line, offline")
        args = parser.parse_args(argv)
        if args.paths_file:
            print("\n".join(replay(args.paths_file).lines()))
            return 0
        facts = facts_from(env, load_event(env.get("GITHUB_EVENT_PATH", "")))
        client = GateClient(env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_TOKEN", ""), opener)
        judgement = safe_decide(facts, client)
        print("\n".join(judgement.lines()))
        write_step_summary(judgement.lines())
        write_output(env, judgement.decision.skip)
    except (Exception, SystemExit) as error:
        print(Judgement(Decision(False, RULE_API_ERROR, type(error).__name__)).lines()[0])
        with contextlib.suppress(OSError):
            write_output(env, False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
