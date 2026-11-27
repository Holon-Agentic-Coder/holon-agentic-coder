"""Record and diagnose the Stage 4 resolver's failing pre-push test run through the test failure loop."""

from __future__ import annotations

import functools
import logging
import os
import subprocess
from collections.abc import Callable

from sandbox_executor.entrypoint.test_failure_classify import BaseSnapshot, run_failing_ids
from sandbox_executor.entrypoint.test_failure_loop import LoopPorts, TestFailureLoop, measure_selection
from sandbox_executor.entrypoint.test_failure_record import LEDGER_RELPATH, STAGE_4, record_test_failure
from sandbox_executor.entrypoint.test_measurement import measure_from_result
from sandbox_executor.git import run_git

logger = logging.getLogger(__name__)


def _git_output(repo_dir: str, *args: str) -> str:
    res = run_git(list(args), cwd=repo_dir, check=False)
    return res.stdout.strip() if res.returncode == 0 else ""


def _default_resolver_failure_loop(repo_dir: str) -> TestFailureLoop:
    branch = _git_output(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
    ports = LoopPorts(
        run_selection=functools.partial(measure_selection, repo_dir),
        rerun_ids=functools.partial(run_failing_ids, cwd=repo_dir),
        base_rerun=BaseSnapshot(repo_dir, os.environ.get("HOLON_TARGET_BRANCH") or "main"),
        record=functools.partial(
            record_test_failure,
            os.path.join(repo_dir, *LEDGER_RELPATH),
            branch=branch,
            execution_id=next((part for part in branch.split("/") if part.startswith("E-")), ""),
        ),
        stage=STAGE_4,
        head_sha=functools.partial(_git_output, repo_dir, "rev-parse", "HEAD"),
        # why: the resolver agent already had its turn; a failing verification goes back to the next iteration.
        fix_turn=None,
    )
    return TestFailureLoop(ports)


# why: a seam tests replace, so a scripted failing test run starts no diagnosis re-run.
resolver_failure_loop: Callable[[str], TestFailureLoop | None] = _default_resolver_failure_loop


def diagnose_resolver_failure(repo_dir: str, result: subprocess.CompletedProcess[str]) -> str:
    """Record the failing run as STAGE_4 rows and return a one-line classification note, or "" when none ran."""
    try:
        loop = resolver_failure_loop(repo_dir)
        if loop is None:
            return ""
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        outcome = loop.run(measure_from_result(result), output)
    except Exception:
        # why: a broken diagnosis must not hide the verification failure it was meant to explain.
        logger.exception("Resolver test failure loop failed")
        return ""
    if outcome.status == "passed":
        return ""
    rows = ", ".join(str(row.get("action_id")) for row in outcome.rows) or "none"
    return f"Test failure classification: {outcome.classification or 'unclassified'}; ledger rows: {rows}."
