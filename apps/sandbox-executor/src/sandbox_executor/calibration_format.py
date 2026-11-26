"""Format the calibration report exactly as the repository's lint-docs Prettier check does, failing closed.

Also home to the commit errors ``calibration`` raises (re-exported there), since the format error subclasses them.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from sandbox_executor.redaction import redact_agent_secrets

logger = logging.getLogger(__name__)


# Prettier invocation for the calibration report. It mirrors ``lint-docs`` in the root pyproject.toml and the
# ``lint-md`` Makefile target (``npx prettier``, unpinned, repository .prettierrc.json found from cwd). ``--yes`` only
# suppresses npx's install prompt and does not change version resolution. The earlier pinned prettier@3.8.4 wrapped
# prose around inline maths differently from the Prettier that CI resolves, so every calibrated head went red.
LINT_DOCS_PRETTIER: tuple[str, ...] = ("npx", "--yes", "prettier")
PRETTIER_TIMEOUT_SECONDS = 120
PRETTIER_OUTPUT_LINES = 5
# Bounded write/check passes: Markdown output is not always a fixpoint after a single Prettier write.
PRETTIER_PASSES = 3


class CalibrationCommitError(RuntimeError):
    """Raised when git rejects the calibration report commit (for example a hook or signing failure).

    The message is credential-redacted. Callers treat it as a calibration failure: the report
    and the ACT-CAL row stay uncommitted and the ``/calibrated`` branch is neither pushed nor
    fast-forwarded.
    """


class CalibrationFormatError(CalibrationCommitError):
    """Raised when the calibration report does not pass the repository Prettier check.

    Subclassing CalibrationCommitError keeps callers that treat that error as "nothing committed,
    nothing pushed" correct: the guard runs before the ACT-CAL ledger append, ``git add`` and
    ``git commit``. The message is credential-redacted.
    """


def _run_prettier(mode: str, report_rel: str, target_worktree: str) -> subprocess.CompletedProcess[str]:
    """Run the lint-docs Prettier invocation on one report (the single seam tests patch).

    Args:
        mode: Prettier mode flag, ``--write`` or ``--check``.
        report_rel: Report path relative to ``target_worktree``.
        target_worktree: Worktree root; Prettier finds .prettierrc.json from here.

    Returns:
        subprocess.CompletedProcess[str]: The finished Prettier process with captured output.

    Raises:
        OSError: If npx cannot be started.
        subprocess.TimeoutExpired: If Prettier runs longer than PRETTIER_TIMEOUT_SECONDS.

    Side Effects:
        ``--write`` rewrites the report in place.
    """
    return subprocess.run(
        [*LINT_DOCS_PRETTIER, mode, report_rel],
        cwd=target_worktree,
        capture_output=True,
        text=True,
        timeout=PRETTIER_TIMEOUT_SECONDS,
        check=False,
    )


def _summarise(label: str, proc: subprocess.CompletedProcess[str]) -> str:
    """Summarise a failed Prettier pass as its exit code and the first lines of its output.

    Args:
        label: The pass that failed (``write`` or ``check``).
        proc: The completed Prettier process.

    Returns:
        str: For example ``check exited 1: [warn] plans/P-1_calibration.md``.
    """
    output = (proc.stdout + proc.stderr).strip().splitlines()[:PRETTIER_OUTPUT_LINES]
    return f"{label} exited {proc.returncode}: " + " | ".join(output)


def _converge_report(report_rel: str, target_worktree: str) -> str | None:
    """Run up to PRETTIER_PASSES write/check passes, stopping at the first passing check or failed write.

    Args:
        report_rel: Report path relative to ``target_worktree``.
        target_worktree: Worktree root used as Prettier's working directory.

    Returns:
        str | None: Why the last pass failed, or None once the check passes.

    Raises:
        OSError: If npx cannot be started.
        subprocess.TimeoutExpired: If a Prettier pass times out.

    Side Effects:
        Rewrites the report in place through Prettier's write passes.
    """
    reason = None
    for _ in range(PRETTIER_PASSES):
        write = _run_prettier("--write", report_rel, target_worktree)
        if write.returncode != 0:
            return _summarise("write", write)
        check = _run_prettier("--check", report_rel, target_worktree)
        if check.returncode == 0:
            return None
        reason = _summarise("check", check)
    return reason


def _prettier_failure(report_rel: str, target_worktree: str) -> str | None:
    """Format the report then check it, returning why the check did not pass (None when it passed).

    Args:
        report_rel: Report path relative to ``target_worktree``.
        target_worktree: Worktree root used as Prettier's working directory.

    Returns:
        str | None: A short reason when npx is missing, Prettier cannot run, a write fails or the
            check still fails after PRETTIER_PASSES passes; None when the report passes the check.

    Side Effects:
        Rewrites the report in place through Prettier's write passes.
    """
    if shutil.which("npx") is None:
        return "npx is not on PATH"
    try:
        return _converge_report(report_rel, target_worktree)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"Prettier could not run: {e}"


def format_calibration_report(report_rel: str, target_worktree: str) -> None:
    """Format the calibration report exactly as ``uv run task lint-docs`` checks it, failing closed.

    Runs up to PRETTIER_PASSES rounds of ``npx --yes prettier --write`` then ``--check`` with ``cwd=target_worktree``,
    so the repository .prettierrc.json applies and Prettier resolves exactly as it does for lint-docs and the CI
    lint-md check (unpinned npx; a host with a stale npx cache can still resolve an older version than CI).

    Args:
        report_rel: Report path relative to ``target_worktree`` (for example ``plans/P-1_calibration.md``).
        target_worktree: Worktree root.

    Raises:
        CalibrationFormatError: If npx is missing, Prettier cannot run or times out, or the check
            does not pass. The redacted message names the report and the command to reproduce.

    Side Effects:
        Rewrites the report in place; logs the outcome.
    """
    reason = _prettier_failure(report_rel, target_worktree)
    if reason is None:
        logger.info("Calibration report %s passes the repository Prettier check", report_rel)
        return
    detail = redact_agent_secrets(reason)
    raise CalibrationFormatError(
        f"Calibration report {report_rel} failed the repository Prettier check ({detail}); "
        f"reproduce with: npx prettier --check {report_rel}"
    )
