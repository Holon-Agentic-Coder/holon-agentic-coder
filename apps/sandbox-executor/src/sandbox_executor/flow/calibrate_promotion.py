from __future__ import annotations

import logging
import os

from sandbox_executor.calibration import calibration_commit_env
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.git import run_git
from sandbox_executor.redaction import redact_agent_secrets

logger = logging.getLogger(__name__)


def _commit_stage5_promoted_artifacts(context: FlowContext, repo_dir: str, promoted_count: int = 0) -> None:
    branch = context.calibrated_branch
    if not branch:
        return
    try:
        head = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir, check=False)
        if head.returncode != 0 or head.stdout.strip() != branch:
            context.log(
                f"Stage 5 promoted knowledge/wisdom artifacts left uncommitted: "
                f"HEAD is not the calibrated branch '{branch}'."
            )
            return
        candidate_dirs = ["holon-knowledge/kb", "holon-knowledge/wisdom"]
        existing_paths = [p for p in candidate_dirs if os.path.exists(os.path.join(repo_dir, p))]
        if not existing_paths:
            return
        run_git(["add", *existing_paths], cwd=repo_dir, check=False)
        status_res = run_git(["status", "--porcelain", "--", *existing_paths], cwd=repo_dir, check=False)
        if status_res.returncode != 0:
            return
        if not status_res.stdout.strip():
            # why: this also runs after calibrations that promoted nothing, to commit an earlier failed flow's files
            if promoted_count > 0:
                context.log("Stage 5 promoted knowledge/wisdom artifacts unchanged: zero modifications to commit.")
            return
        commit_res = run_git(
            [
                "commit",
                "-m",
                "chore(calibration): record stage 5 promoted knowledge and wisdom",
                "--",
                *existing_paths,
            ],
            cwd=repo_dir,
            env=calibration_commit_env(),
            check=False,
        )
        if commit_res.returncode != 0:
            combined_output = (commit_res.stdout + " " + commit_res.stderr).lower()
            if "nothing to commit" not in combined_output:
                detail = redact_agent_secrets(commit_res.stderr.strip())
                msg = f"Warning: committing Stage 5 promoted knowledge/wisdom artifacts failed: {detail}"
                logger.warning(msg)
                context.log(msg)
    except Exception as e:
        msg = f"Warning: committing Stage 5 promoted knowledge/wisdom artifacts failed: {redact_agent_secrets(str(e))}"
        logger.warning(msg)
        context.log(msg)
