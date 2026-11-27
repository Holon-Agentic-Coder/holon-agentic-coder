from __future__ import annotations

import logging
import os
import re

from sandbox_executor.calibration.refs import resolve_commit_sha
from sandbox_executor.calibration_evaluated_sha import evaluated_code_commit
from sandbox_executor.plan_paths import split_plan_branch

logger = logging.getLogger(__name__)


def parse_evaluated_commit_sha(report_content_or_path: str) -> str | None:
    content = report_content_or_path
    if (
        "\n" not in report_content_or_path
        and len(report_content_or_path) < 1024
        and not report_content_or_path.startswith("-")
        and os.path.exists(report_content_or_path)
    ):
        try:
            with open(report_content_or_path, encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.debug("Failed reading %s: %s", report_content_or_path, e)
            return None

    m = re.search(r"-\s+\*\*Evaluated Commit SHA:\*\*\s+`?([0-9a-fA-F]{7,40})`?", content)
    if m:
        return m.group(1).lower()
    return None


def is_calibration_stale(
    report_path: str,
    current_head_ref: str | None = None,
    target_worktree: str = ".",
) -> tuple[bool, str, str]:
    full_path = report_path
    if (
        "\n" not in report_path
        and len(report_path) < 1024
        and not os.path.isabs(report_path)
        and not os.path.exists(report_path)
    ):
        candidate = os.path.join(target_worktree, report_path)
        if os.path.exists(candidate):
            full_path = candidate
    evaluated_sha = parse_evaluated_commit_sha(full_path)
    if not evaluated_sha:
        return (True, "", "missing_evaluated_sha")

    target_head = current_head_ref or "HEAD"
    try:
        current_head_sha = resolve_commit_sha(target_head, target_worktree=target_worktree).lower()
        # why: Stage 5's own calibration commits on top of the evaluated head must not make the report stale
        current_head_sha = (evaluated_code_commit(current_head_sha, target_worktree) or current_head_sha).lower()
    except Exception as e:
        logger.debug("Failed to resolve current head SHA for %s: %s", target_head, e)
        current_head_sha = ""

    if not current_head_sha:
        return (True, evaluated_sha, "unresolvable_head")

    is_stale = (
        evaluated_sha != current_head_sha
        and not current_head_sha.startswith(evaluated_sha)
        and not evaluated_sha.startswith(current_head_sha)
    )
    return (is_stale, evaluated_sha, current_head_sha)


def verify_calibration_freshness(
    report_path: str,
    current_head_ref: str | None = None,
    target_worktree: str = ".",
) -> bool:
    is_stale, _, _ = is_calibration_stale(
        report_path, current_head_ref=current_head_ref, target_worktree=target_worktree
    )
    return not is_stale


def _is_bare_commit_sha(ref: str) -> bool:
    return re.fullmatch(r"[0-9a-fA-F]{7,40}", ref) is not None


def extract_branch_components(branch_str: str) -> dict[str, str]:
    clean = branch_str.strip().rstrip("/_").rstrip("/")
    parts = clean.split("/")

    components = {
        "intent_branch": "",
        "plan_branch": "",
        "execution_branch": branch_str,
        "intent_id": "",
        "plan_id": "",
        "plan_parent": "",
        "execution_id": "",
    }

    for part in parts:
        if part.startswith("I-"):
            components["intent_id"] = part
            components["intent_branch"] = f"{part}/_"
        elif part.startswith("E-"):
            components["execution_id"] = part

    if not components["execution_id"] and _is_bare_commit_sha(clean):
        components["execution_id"] = clean

    parent, plan_id = split_plan_branch(clean)
    components["plan_id"] = plan_id
    if parent and plan_id:
        components["plan_parent"] = parent
        components["plan_branch"] = f"{parent}/{plan_id}/_"

    return components
