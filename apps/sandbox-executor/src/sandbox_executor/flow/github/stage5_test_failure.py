from __future__ import annotations

import os
import re
from typing import Any

from sandbox_executor.calibration.run import calibration_commit_env
from sandbox_executor.entrypoint.test_failure_record import (
    LEDGER_RELPATH,
    STAGE_5,
    TestFailureEvidence,
    excerpts_by_test,
    failing_ids_from_output,
    record_test_failure,
)
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.host_remote import DEFAULT_REMOTE
from sandbox_executor.git import run_git
from sandbox_executor.ledger.fragments import ledger_commit_paths
from sandbox_executor.redaction import redact_agent_secrets

# why: Stage 5 only sees the CI log, never a base-branch rerun, so it cannot tell introduced from pre-existing.
UNDIAGNOSED_CLASSIFICATION = "undiagnosed"
UNKNOWN_EXECUTION_ID = "E-unknown"
_LOG_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\S*Z ?")
_GH_LOG_PREFIX_COLUMNS = 2


def check_log_text(output: str) -> str:
    return "\n".join(
        _LOG_TIMESTAMP.sub("", line.split("\t", _GH_LOG_PREFIX_COLUMNS)[-1]) for line in output.splitlines()
    )


def _execution_id_of(branch: str) -> str:
    return next((part for part in branch.split("/") if part.startswith("E-")), UNKNOWN_EXECUTION_ID)


def _commit_and_push_ledger(context: FlowContext, branch: str) -> bool:
    repo_dir = context.target_worktree
    head = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir, check=False)
    ledger_paths = ledger_commit_paths(repo_dir, LEDGER_RELPATH[-1])
    if head.returncode != 0 or head.stdout.strip() != branch or not ledger_paths:
        context.log(f"Stage 5 test-failure row left uncommitted: HEAD is not '{branch}' or no ledger path exists.")
        return False
    run_git(["add", *ledger_paths], cwd=repo_dir, check=False)
    message = "chore(calibration): record stage 5 test failure"
    commit = run_git(
        ["commit", "-m", message, "--", *ledger_paths], cwd=repo_dir, env=calibration_commit_env(), check=False
    )
    if commit.returncode != 0:
        context.log(f"Warning: committing the Stage 5 test-failure row failed: {redact_agent_secrets(commit.stderr)}")
        return False
    if context.skip_push:
        return False
    pushed = run_git(["push", DEFAULT_REMOTE, branch], cwd=repo_dir, check=False)
    return pushed.returncode == 0


def record_stage5_test_failure(context: FlowContext, final_head: str, output: str) -> dict[str, Any] | None:
    text = check_log_text(output)
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
        classification=UNDIAGNOSED_CLASSIFICATION,
    )
    ledger_path = os.path.join(context.target_worktree, *LEDGER_RELPATH)
    row = record_test_failure(ledger_path, evidence, branch=branch, execution_id=execution_id)
    if row is None or context.dry_run:
        return row
    try:
        pushed = _commit_and_push_ledger(context, branch)
    except Exception as exc:
        context.log(f"Warning: committing the Stage 5 test-failure row failed: {redact_agent_secrets(str(exc))}")
        pushed = False
    return {**row, "pushed": pushed}
