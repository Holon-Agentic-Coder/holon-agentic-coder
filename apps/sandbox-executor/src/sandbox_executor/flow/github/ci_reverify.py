from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from sandbox_executor.entrypoint.test_failure_classify import FailureClass
from sandbox_executor.entrypoint.test_failure_record import (
    LEDGER_RELPATH,
    STAGE_5,
    TestFailureEvidence,
    excerpts_by_test,
    failing_ids_from_output,
    record_test_failure,
)
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.approval_invalidation import invalidate_approval
from sandbox_executor.flow.github.ci_failure_detail import (
    STEP_LOOKUP_TIMEOUT_SECONDS,
    bare_detail,
    describe_failure,
    short_sha,
)
from sandbox_executor.git import run_git

GREEN = "green"
RED = "red"
UNVERIFIED = "unverified"
SKIPPED = "skipped"
_VERDICT_BY_STATE = {"success": GREEN, "failed": RED, "skipped": SKIPPED}
_SYMBOLIC_HEAD = "HEAD"
MAX_FAILED_RUNS = 3
_LOG_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\S*Z ?")

FailedCheckOutput = Callable[[str, str], str]


def _same_commit(first: str | None, second: str | None) -> bool:
    first_sha, second_sha = str(first or ""), str(second or "")
    return bool(first_sha and second_sha) and (first_sha.startswith(second_sha) or second_sha.startswith(first_sha))


def _consensus_of(context: FlowContext) -> Mapping[str, Any]:
    review = context.stage_results.get("review")
    return (review.payload.get("consensus") if review else None) or {}


def approved_head_of(context: FlowContext) -> str | None:
    consensus = _consensus_of(context)
    approved_commit = str(consensus.get("target_commit") or "").strip()
    return (
        approved_commit if consensus.get("approved") and approved_commit and approved_commit != _SYMBOLIC_HEAD else None
    )


def published_head_of(repo_dir: str) -> str | None:
    try:
        proc = run_git(["rev-parse", _SYMBOLIC_HEAD], cwd=repo_dir, check=False)
    except Exception:
        return None
    sha = str(getattr(proc, "stdout", "") or "").strip()
    return sha if proc.returncode == 0 and sha else None


def _failed_details(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(record.get("failed_details") or [bare_detail(n) for n in record.get("failed_checks") or []])


def _timing_rerun_clause(record: Mapping[str, Any]) -> str:
    timing_rerun_note = record.get("timing_rerun") or {}
    return " after one automatic rerun of the timing gate" if timing_rerun_note.get("attempted") else ""


def _moved_clause(moved: bool | None, carried: bool) -> str:
    if not moved:
        return "."
    carry_over_clause = ", which carried over by PR-own fingerprint" if carried else ""
    return f"; the head moved after approval{carry_over_clause}."


def _report(verdict: str, record: Mapping[str, Any], final_head: str | None, moved: bool | None, carried: bool) -> str:
    head = short_sha(final_head)
    rerun = _timing_rerun_clause(record)
    if verdict == RED:
        failure_summary = ", ".join(describe_failure(d) for d in _failed_details(record))
        return f"CI is red on final head {head}{rerun}: {failure_summary}. Not ready to merge."
    if verdict == UNVERIFIED:
        return f"CI on final head {head} is not verified ({record.get('reason') or 'no result'}). Not merge-ready."
    if verdict == GREEN:
        return f"CI is green on final head {head}{rerun}" + _moved_clause(moved, carried)
    return "CI was not re-verified: nothing was pushed."


def reverify_final_head(
    record: Mapping[str, Any], approved_head: str | None, final_head: str | None, carried: bool = False
) -> dict[str, Any]:
    verdict = _VERDICT_BY_STATE.get(str(record.get("state")), UNVERIFIED)
    head_moved = None if not (approved_head and final_head) else not _same_commit(approved_head, final_head)
    return {
        "verdict": verdict,
        "approved_head": approved_head,
        "final_head": final_head,
        "head_moved": head_moved,
        "failed_details": _failed_details(record) if verdict == RED else [],
        "timing_rerun": record.get("timing_rerun"),
        "approval_carried_over": bool(carried and head_moved),
        "report": _report(verdict, record, final_head, head_moved, carried),
    }


def rerun_note(reverified: Mapping[str, Any], refreshed_head: str | None) -> dict[str, Any]:
    note = f"The head moved after calibration; a Stage 5 rerun refreshed the report to {short_sha(refreshed_head)}."
    return {**reverified, "report": f"{note} {reverified.get('report') or ''}".strip()}


def blocked_error(reverified: Mapping[str, Any]) -> str:
    covered = reverified.get("approved_head")
    note = f" The approval covered {short_sha(covered)} and no longer stands." if covered else ""
    return f"Stage 5 blocked: {reverified['report']}{note}"


def _gh_stdout(args: list[str], repo_dir: str, run: Callable[..., Any]) -> str:
    proc = run(
        ["gh", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
        timeout=STEP_LOOKUP_TIMEOUT_SECONDS,
    )
    return str(proc.stdout or "") if proc.returncode == 0 else ""


def _gh_failed_check_output(repo_dir: str, final_head: str, run: Callable[..., Any] = subprocess.run) -> str:
    list_args = ["run", "list", "--commit", final_head, "--status", "failure", "--json", "databaseId"]
    try:
        runs = json.loads(_gh_stdout([*list_args, "--limit", str(MAX_FAILED_RUNS)], repo_dir, run) or "[]")
        run_ids = [str(entry["databaseId"]) for entry in runs[:MAX_FAILED_RUNS]]
        return "\n".join(_gh_stdout(["run", "view", run_id, "--log-failed"], repo_dir, run) for run_id in run_ids)
    except Exception:
        # why: the lookup only annotates a verdict that is already decided.
        return ""


def _check_log_text(output: str) -> str:
    return "\n".join(_LOG_TIMESTAMP.sub("", line.rsplit("\t", 1)[-1]) for line in output.splitlines())


def _execution_id_of(branch: str) -> str:
    return next((part for part in branch.split("/") if part.startswith("E-")), "")


def _record_stage5_test_failure(context: FlowContext, final_head: str, output: str) -> dict[str, Any] | None:
    text = _check_log_text(output)
    ids = failing_ids_from_output(text)
    if not ids:
        return None
    branch = context.execution_branch or context.intent_branch or ""
    execution_id = _execution_id_of(branch)
    evidence = TestFailureEvidence(
        run_id=f"{execution_id}:{final_head}",
        stage=STAGE_5,
        head_sha=final_head,
        failing_ids=ids,
        excerpts=excerpts_by_test(text, ids),
        # why: Stage 5 cannot re-run CI tests to diagnose, so the final head's own CI failure counts as introduced.
        classification=FailureClass.INTRODUCED.value,
    )
    ledger_path = os.path.join(context.target_worktree, *LEDGER_RELPATH)
    return record_test_failure(ledger_path, evidence, branch=branch, execution_id=execution_id)


def reverify_and_report(
    context: FlowContext,
    repo_dir: str,
    record: Mapping[str, Any],
    pushed: bool,
    *,
    failed_output: FailedCheckOutput = _gh_failed_check_output,
) -> dict[str, Any]:
    carried = bool(_consensus_of(context).get("carried_to"))
    final_head = published_head_of(repo_dir) if pushed else None
    reverified = reverify_final_head(record, approved_head_of(context), final_head, carried)
    context.log(f"Stage 5 re-verification: {reverified['report']}")
    if reverified["verdict"] == RED:
        reverified["invalidation"] = invalidate_approval(context, reverified)
        if final_head:
            output = failed_output(repo_dir, final_head)
            reverified["test_failure_record"] = _record_stage5_test_failure(context, final_head, output)
    return reverified
