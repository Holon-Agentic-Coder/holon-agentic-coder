from __future__ import annotations

import logging
import shutil
import subprocess

from sandbox_executor.redaction import redact_agent_secrets

logger = logging.getLogger(__name__)

# why: mirrors lint-docs and lint-md (unpinned npx prettier); --yes only suppresses the install prompt. Pinning
# why: prettier@3.8.4 wrapped inline maths differently from CI's Prettier, so every calibrated head went red.
LINT_DOCS_PRETTIER: tuple[str, ...] = ("npx", "--yes", "prettier")
PRETTIER_TIMEOUT_SECONDS = 120
PRETTIER_OUTPUT_LINES = 5
# why: Markdown output is not always a fixpoint after one Prettier write.
PRETTIER_PASSES = 3


# why: the commit errors live here because the format error subclasses them; ``calibration`` re-exports both.
class CalibrationCommitError(RuntimeError):
    pass


# why: subclassing keeps callers that treat a commit error as "nothing committed, nothing pushed" correct.
class CalibrationFormatError(CalibrationCommitError):
    pass


def _run_prettier(mode: str, report_rel: str, target_worktree: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*LINT_DOCS_PRETTIER, mode, report_rel],
        cwd=target_worktree,
        capture_output=True,
        text=True,
        timeout=PRETTIER_TIMEOUT_SECONDS,
        check=False,
    )


def _summarise(label: str, proc: subprocess.CompletedProcess[str]) -> str:
    output = (proc.stdout + proc.stderr).strip().splitlines()[:PRETTIER_OUTPUT_LINES]
    return f"{label} exited {proc.returncode}: " + " | ".join(output)


def _converge_report(report_rel: str, target_worktree: str) -> str | None:
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
    if shutil.which("npx") is None:
        return "npx is not on PATH"
    try:
        return _converge_report(report_rel, target_worktree)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"Prettier could not run: {e}"


def format_calibration_report(report_rel: str, target_worktree: str) -> None:
    reason = _prettier_failure(report_rel, target_worktree)
    if reason is None:
        logger.info("Calibration report %s passes the repository Prettier check", report_rel)
        return
    detail = redact_agent_secrets(reason)
    raise CalibrationFormatError(
        f"Calibration report {report_rel} failed the repository Prettier check ({detail}); "
        f"reproduce with: npx prettier --check {report_rel}"
    )
