#!/usr/bin/env python3
"""Bookkeeping-only push gate: decide whether a CI job may skip its expensive steps.

The flow pushes plan files, execution reports and ledger rows (``plans/``, ``executions/``, ``holon-knowledge/``) to a
pull request several times, and such a push cannot change a test result. Every job calls this gate as its first step
after the checkout. Only positive evidence skips; everything else, including every error, runs the full job. The rules
are checked in this order, and the first one that fails decides for a full run:

1. ``event-not-pull-request``, ``rerun-attempt``, ``action-not-eligible``: the run is the first attempt of a
   ``pull_request`` run with the action ``synchronize``, ``labeled`` or ``unlabeled``.
2. ``result-affecting-label`` (label events), ``missing-previous-head`` (``synchronize``), ``incomplete-event``.
3. ``force-push-or-diverged``, ``diff-too-large``, ``non-bookkeeping-change`` (``synchronize``): the new head is
   strictly ahead of the previous head and every changed path, both names of a rename, is a bookkeeping path.
4. The rules of ``scripts.ci_bookkeeping_chain``: the same job really succeeded on the previous head (for a label event:
   on the current head), reached through a chain of runs that this gate skipped.
5. When that real success is on an older commit, the checks of point 3 again, from that commit to the new head.

A ``synchronize`` push that point 3 blocks with ``non-bookkeeping-change`` or ``diff-too-large`` and whose new head is a
two-parent merge of the base branch into the previous head is decided by ``scripts.ci_merge_push_gate`` (rule
``merge-push-clean-main-merge`` skips); a push whose new head is not a merge commit keeps the decision of point 3,
except that the ``hygiene`` job (or ``test-hygiene.yml``) always reports ``merge-push-hygiene-never-skipped``.

A push to ``main`` in ``test-unit.yml`` or ``test-stress.yml`` is decided by ``scripts.ci_main_push_gate`` instead
(rules ``main-push-superseded`` and ``main-push-tested-tree`` skip). Any exception is ``api-error``. The two rules that
skip are ``bookkeeping-only`` and ``label-not-result-affecting``. The gate prints one line with the rule that decided
and writes ``skip=true`` or ``skip=false`` to the step output. Standard library only and Python 3.9 syntax, so it runs
on the runner's own ``python3``.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import posixpath
import sys
import urllib.error
from collections.abc import Mapping, Sequence

from scripts.ci_bookkeeping_api import GateClient, GateContext, Opener, compare, context_from, load_event
from scripts.ci_bookkeeping_chain import (
    CHAIN_RULES,
    RULE_PREVIOUS_RUN_NOT_SUCCESSFUL,
    RULE_PREVIOUS_RUN_SUCCEEDED,
    Decision,
    JobQuery,
    find_real_success,
)
from scripts.ci_main_push_gate import decide_main_push, is_main_push
from scripts.ci_merge_push_gate import GateRules, decide_merge_push
from scripts.ci_timing_gate import OVERRIDE_LABEL, default_opener, write_step_summary

BOOKKEEPING_ROOTS = ("plans", "executions", "holon-knowledge")
# The file types a skipped hygiene job still checks (``make lint-md``, ``make lint-json``); any other type runs in full.
BOOKKEEPING_SUFFIXES = (".md", ".json", ".jsonl")
# Every label that a workflow reads belongs here: adding or removing one can change a job's result. ``ci-macos`` is read
# by ``scripts/macos_relevance.py`` (its ``FORCE_LABEL``) and forces the macOS unit leg to run.
RESULT_AFFECTING_LABELS = frozenset({OVERRIDE_LABEL, "module-budget-change", "ci-macos"})
ELIGIBLE_ACTIONS = ("synchronize", "labeled", "unlabeled")
LABEL_ACTIONS = ("labeled", "unlabeled")

RULE_EVENT_NOT_PULL_REQUEST = "event-not-pull-request"
RULE_RERUN_ATTEMPT = "rerun-attempt"
RULE_ACTION_NOT_ELIGIBLE = "action-not-eligible"
RULE_RESULT_AFFECTING_LABEL = "result-affecting-label"
RULE_MISSING_PREVIOUS_HEAD = "missing-previous-head"
RULE_INCOMPLETE_EVENT = "incomplete-event"
RULE_FORCE_PUSH_OR_DIVERGED = "force-push-or-diverged"
RULE_DIFF_TOO_LARGE = "diff-too-large"
RULE_NON_BOOKKEEPING_CHANGE = "non-bookkeeping-change"
RULE_API_ERROR = "api-error"
RULE_BOOKKEEPING_ONLY = "bookkeeping-only"
RULE_LABEL_NOT_RESULT_AFFECTING = "label-not-result-affecting"
RUN_RULES = (
    RULE_EVENT_NOT_PULL_REQUEST,
    RULE_RERUN_ATTEMPT,
    RULE_ACTION_NOT_ELIGIBLE,
    RULE_RESULT_AFFECTING_LABEL,
    RULE_MISSING_PREVIOUS_HEAD,
    RULE_INCOMPLETE_EVENT,
    RULE_FORCE_PUSH_OR_DIVERGED,
    RULE_DIFF_TOO_LARGE,
    RULE_NON_BOOKKEEPING_CHANGE,
    *(rule for rule in CHAIN_RULES if rule != RULE_PREVIOUS_RUN_SUCCEEDED),
    RULE_API_ERROR,
)
SKIP_RULES = (RULE_BOOKKEEPING_ONLY, RULE_LABEL_NOT_RESULT_AFFECTING)


def is_bookkeeping_path(path: str) -> bool:
    """Return true only for a markdown, JSON or JSONL file whose first whole path component is a bookkeeping root."""
    root, _, rest = path.partition("/")
    return root in BOOKKEEPING_ROOTS and posixpath.splitext(rest)[1] in BOOKKEEPING_SUFFIXES


def first_outside_path(paths: Sequence[str]) -> str | None:
    """Return the first path that is not bookkeeping, or ``None`` when every path is."""
    return next((path for path in paths if not is_bookkeeping_path(path)), None)


def event_rule(context: GateContext) -> Decision | None:
    """Return the "run" decision of the first event rule that fails, or ``None`` when the event is eligible."""
    label_event = context.action in LABEL_ACTIONS
    named = (
        context.head_sha,
        context.branch,
        context.workflow_file,
        context.job_name,
        context.label or not label_event,
    )
    complete = all(named) and context.run_number > 0 and context.run_attempt > 0
    # GitHub compares label names without case, so the label is lowered before the look-up.
    result_affecting = label_event and context.label.lower() in RESULT_AFFECTING_LABELS
    checks = (
        (context.event_name != "pull_request", RULE_EVENT_NOT_PULL_REQUEST, context.event_name),
        (context.run_attempt > 1, RULE_RERUN_ATTEMPT, f"attempt {context.run_attempt}"),
        (context.action not in ELIGIBLE_ACTIONS, RULE_ACTION_NOT_ELIGIBLE, context.action),
        (result_affecting, RULE_RESULT_AFFECTING_LABEL, context.label),
        (not label_event and not context.before.strip("0"), RULE_MISSING_PREVIOUS_HEAD, context.before),
        (not complete, RULE_INCOMPLETE_EVENT, ""),
    )
    return next((Decision(False, rule, detail) for failed, rule, detail in checks if failed), None)


def diff_rule(client: GateClient, base: str, head: str) -> tuple[Decision | None, int]:
    """Compare ``base...head``; return the "run" decision (``None`` when all is bookkeeping) and the path count."""
    comparison = compare(client, base, head)
    if comparison.status != "ahead":
        return Decision(False, RULE_FORCE_PUSH_OR_DIVERGED, f"compare status {comparison.status}"), 0
    if comparison.truncated:
        return Decision(False, RULE_DIFF_TOO_LARGE, f"{len(comparison.paths)} paths or more"), 0
    outside = first_outside_path(comparison.paths)
    if outside is not None:
        return Decision(False, RULE_NON_BOOKKEEPING_CHANGE, outside), 0
    return None, len(comparison.paths)


def _is_cancelled_block(blocked: Decision) -> bool:
    return blocked.rule == RULE_PREVIOUS_RUN_NOT_SUCCESSFUL and blocked.detail.startswith("cancelled ")


def merge_push_or(blocked: Decision, context: GateContext, client: GateClient) -> Decision:
    """Hand a push blocked by a changed or too large diff to the merge-push route; keep ``blocked`` when it declines."""
    if blocked.rule not in (RULE_NON_BOOKKEEPING_CHANGE, RULE_DIFF_TOO_LARGE) and not _is_cancelled_block(blocked):
        return blocked
    merged = decide_merge_push(context, client, GateRules(diff_rule, is_bookkeeping_path))
    return blocked if merged is None else merged


def decide(context: GateContext, client: GateClient) -> Decision:
    """Apply the rules in order; only the last statement returns "skip". Exceptions are left to ``safe_decide``."""
    if is_main_push(context):
        return decide_main_push(context, client, GateRules(diff_rule, is_bookkeeping_path))
    blocked = event_rule(context)
    if blocked is not None:
        return blocked
    synchronize = context.action == "synchronize"
    target = context.before if synchronize else context.head_sha
    changed = 0
    if synchronize:
        blocked, changed = diff_rule(client, target, context.head_sha)
        if blocked is not None:
            return merge_push_or(blocked, context, client)
    query = JobQuery(
        context.workflow_file, context.branch, context.job_name, context.os_name, context.run_number, target
    )
    chain = find_real_success(client, query)
    if chain.real_sha is None:
        blocked = chain.run_decision()
        return merge_push_or(blocked, context, client) if synchronize else blocked
    if chain.real_sha not in (target, context.head_sha):
        blocked, changed = diff_rule(client, chain.real_sha, context.head_sha)
        if blocked is not None:
            return merge_push_or(blocked, context, client) if synchronize else blocked
    rule = RULE_BOOKKEEPING_ONLY if synchronize else RULE_LABEL_NOT_RESULT_AFFECTING
    return Decision(True, rule, f"{changed} changed paths since the real success on {chain.real_sha[:12]}")


def safe_decide(context: GateContext, client: GateClient) -> Decision:
    """Call ``decide``; any exception is ``api-error`` with its class name and HTTP status, never its text."""
    try:
        return decide(context, client)
    except Exception as error:
        status = f" {error.code}" if isinstance(error, urllib.error.HTTPError) else ""
        return Decision(False, RULE_API_ERROR, f"{type(error).__name__}{status}")


def write_output(environ: Mapping[str, str], skip: bool) -> None:
    """Append the ``skip`` step output to the file named by ``GITHUB_OUTPUT``, when it is set."""
    path = environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"skip={'true' if skip else 'false'}\n")


def main(
    argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None, opener: Opener = default_opener
) -> int:
    """Report the decision and return 0 in every case; whatever goes wrong ends in ``api-error`` and ``skip=false``."""
    env = os.environ if environ is None else environ
    try:
        parser = argparse.ArgumentParser(description="Decide whether this CI job may skip its expensive steps.")
        parser.add_argument("--job-name", required=True, help="the job's base name, without the matrix part")
        parser.add_argument("--os", dest="os_name", default="", help="the runner label of a matrix job")
        args = parser.parse_args(argv)
        context = context_from(env, load_event(env.get("GITHUB_EVENT_PATH", "")), args.job_name, args.os_name)
        decision = safe_decide(
            context, GateClient(env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_TOKEN", ""), opener)
        )
        print(decision.line())
        write_step_summary([decision.line()])
        write_output(env, decision.skip)
    except (Exception, SystemExit) as error:
        print(Decision(False, RULE_API_ERROR, type(error).__name__).line())
        with contextlib.suppress(OSError):
            write_output(env, False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
