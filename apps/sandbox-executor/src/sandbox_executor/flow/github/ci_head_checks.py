"""CI check runs of one exact commit SHA, read through the GitHub check-runs API and classified fail-closed.

``gh pr checks`` reports the latest run per check name on the pull request, whichever commit it ran on, and has no
notion of a cancelled or stale run. This module reads ``repos/{owner}/{repo}/commits/<sha>/check-runs`` instead, drops
every run registered against another SHA (a superseded run never stands in for the head) and keeps the newest run per
check name, so a re-triggered run replaces the cancelled one. Only ``success``, ``neutral`` and ``skipped`` pass.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sandbox_executor.flow.github.ci_failure_detail import STEP_LOOKUP_TIMEOUT_SECONDS, parse_run_id

PASSED = "passed"
PENDING = "pending"
CANCELLED = "cancelled"
FAILED = "failed"

PASSING_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
CANCELLED_CONCLUSIONS = frozenset({"cancelled", "stale"})
PENDING_STATUSES = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})
CHECK_RUNS_JQ = ".check_runs[] | {id, name, status, conclusion, head_sha, details_url, check_suite_id: .check_suite.id}"

HeadCheck = dict[str, Any]
HeadChecksProbe = Callable[[str], "list[HeadCheck] | None"]
RunRerunner = Callable[[str], bool]


def _gh_env(proxy_env: Mapping[str, str] | None) -> dict[str, str]:
    return {**os.environ, **(proxy_env or {}), "DOCKER_CLI_HINTS": "false"}


def _run_gh(argv: list[str], repo_dir: str, proxy_env: Mapping[str, str] | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
        timeout=STEP_LOOKUP_TIMEOUT_SECONDS,
        env=_gh_env(proxy_env),
    )


def _as_check(raw: Mapping[str, Any]) -> HeadCheck:
    """One check run as a plain record; ``run_id`` is None for a check that is not a GitHub Actions run."""
    return {
        "check_run_id": int(raw.get("id") or 0),
        "name": str(raw.get("name") or ""),
        "status": str(raw.get("status") or "").lower(),
        "conclusion": str(raw.get("conclusion") or "").lower(),
        "head_sha": str(raw.get("head_sha") or ""),
        "check_suite_id": int(raw.get("check_suite_id") or 0),
        "run_id": parse_run_id(raw.get("details_url")),
    }


def parse_check_runs(output: str) -> list[HeadCheck]:
    """Parse the one-object-per-line ``--jq`` output of the check-runs endpoint."""
    return [_as_check(json.loads(line)) for line in output.splitlines() if line.strip()]


def latest_checks_for_head(checks: list[HeadCheck], sha: str) -> list[HeadCheck]:
    """Only the runs on exactly ``sha``, keeping the newest run (highest check-run id) per check suite and name.

    Keying on the suite as well keeps two workflows that both define a job of the same name apart, so a passing run
    of one never hides a failed run of the other; a re-run stays in its suite and still replaces the cancelled run.
    """
    newest: dict[tuple[int, str], HeadCheck] = {}
    for check in checks:
        if check.get("head_sha") != sha:
            continue
        key = (int(check.get("check_suite_id") or 0), str(check.get("name") or ""))
        if key not in newest or int(check.get("check_run_id") or 0) > int(newest[key].get("check_run_id") or 0):
            newest[key] = check
    return sorted(newest.values(), key=lambda check: (str(check.get("name")), int(check.get("check_suite_id") or 0)))


def probe_head_checks(repo_dir: str, sha: str, proxy_env: Mapping[str, str] | None = None) -> list[HeadCheck] | None:
    """The newest check run per name on exactly ``sha``; None on any error, so an unreadable head is never passed."""
    argv = ["gh", "api", "--paginate", f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs", "--jq", CHECK_RUNS_JQ]
    try:
        proc = _run_gh(argv, repo_dir, proxy_env)
        if proc.returncode != 0:
            return None
        return latest_checks_for_head(parse_check_runs(proc.stdout), sha)
    except Exception:
        return None


def gh_head_checks_probe(repo_dir: str, proxy_env: Mapping[str, str] | None = None) -> HeadChecksProbe:
    """:func:`probe_head_checks` bound to a repository, for the gate's injectable probe seam."""
    return lambda sha: probe_head_checks(repo_dir, sha, proxy_env)


def is_pending(check: Mapping[str, Any]) -> bool:
    """True for a check run that has not completed (queued, in progress, waiting or requested)."""
    return check.get("status") != "completed"


def is_cancelled(check: Mapping[str, Any]) -> bool:
    """True for a completed run that was cancelled or went stale: never a pass."""
    return not is_pending(check) and check.get("conclusion") in CANCELLED_CONCLUSIONS


def is_failed(check: Mapping[str, Any]) -> bool:
    """True for a completed run whose conclusion is neither passing nor cancelled (unknown conclusions included)."""
    conclusion = check.get("conclusion")
    return not is_pending(check) and conclusion not in PASSING_CONCLUSIONS | CANCELLED_CONCLUSIONS


def classify_head_checks(checks: list[HeadCheck]) -> str:
    """``failed`` beats ``pending`` beats ``cancelled``; ``passed`` needs a non-empty list where every run passed."""
    if any(is_failed(check) for check in checks):
        return FAILED
    if not checks or any(is_pending(check) for check in checks):
        return PENDING
    if any(is_cancelled(check) for check in checks):
        return CANCELLED
    return PASSED


def gh_run_rerunner(repo_dir: str, proxy_env: Mapping[str, str] | None = None) -> RunRerunner:
    """A dispatcher of ``gh run rerun <run-id>`` (the whole run: a cancelled run has no failed jobs); False on error."""

    def rerun(run_id: str) -> bool:
        try:
            return _run_gh(["gh", "run", "rerun", run_id], repo_dir, proxy_env).returncode == 0
        except Exception:
            return False

    return rerun


@dataclass
class CancelledReruns:
    """Re-triggers each cancelled run on one head at most once, remembering the cancelled check runs it acted on."""

    dispatch: RunRerunner
    log: Callable[[str], None]
    run_ids: list[str] = field(default_factory=list)
    seen_check_runs: set[int] = field(default_factory=set)

    def retrigger(self, checks: list[HeadCheck]) -> str | None:
        """Re-trigger the newly cancelled runs; the reason to fail closed, or None (also when none is new)."""
        seen = self.seen_check_runs
        cancelled = [c for c in checks if is_cancelled(c) and int(c.get("check_run_id") or 0) not in seen]
        unrunnable = [str(c.get("name")) for c in cancelled if c.get("run_id") is None]
        if unrunnable:
            return f"cancelled check {', '.join(unrunnable)} has no Actions run to re-trigger"
        run_ids = list(dict.fromkeys(str(check["run_id"]) for check in cancelled))
        again = [run_id for run_id in run_ids if run_id in self.run_ids]
        if again:
            return f"run {', '.join(again)} was cancelled again after its re-trigger (cancelled twice)"
        self.seen_check_runs.update(int(check.get("check_run_id") or 0) for check in cancelled)
        for run_id in run_ids:
            self.log(f"Merged-head CI: re-triggering cancelled run {run_id}.")
            if not self.dispatch(run_id):
                return f"re-triggering cancelled run {run_id} failed"
            self.run_ids.append(run_id)
        return None
