"""Stage 1 (Intent Creation): validate the intent payload, dispatch the intent creator, verify its branch."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
import time
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _dispatch_role, _stage_failure
from sandbox_executor.flow.git_refs import (
    _fetch_branch_prefix_error,
    _origin_has_branch,
    _read_ledger_rows,
    _track_local_branch,
)
from sandbox_executor.flow.intent_questions import validate_intent_questions
from sandbox_executor.flow.stage_conflicts import check_and_resolve_stage_conflicts
from sandbox_executor.flow_guard import SKIP_PUSH_DISPATCH_ERROR
from sandbox_executor.knowledge import ConsultEngine
from sandbox_executor.ref_names import RefTooLongError, resolve_root_intent_branch

logger = logging.getLogger(__name__)


def _reused_intent_conflict(repo_dir: str, intent_branch: str, branch: str, intent_data: dict[str, Any]) -> str | None:
    """Return why an existing origin intent branch cannot be reused for ``intent_data``, or None when it can.

    The branch is reused only when its ``intents.jsonl`` row for ``branch`` records the same goal and description, so a
    re-plan that changes a child at the same position and slug never silently inherits the earlier child's intent.
    """
    rows = [
        r for r in _read_ledger_rows(repo_dir, f"origin/{intent_branch}", "intents.jsonl") if r.get("branch") == branch
    ]
    if not rows:
        return (
            f"Intent stage failed: '{intent_branch}' already exists on origin but records no intent row for "
            f"'{branch}'; refusing to reuse it. Delete that branch or give this intent a different branch."
        )
    recorded = rows[-1]
    if recorded.get("goal") == intent_data.get("goal") and recorded.get("description") == intent_data.get(
        "description"
    ):
        return None
    return (
        f"Intent stage failed: '{intent_branch}' already exists on origin for a different intent (its recorded goal or "
        "description differs from this one); refusing to reuse it. Delete that branch or give this intent a different "
        "branch."
    )


def run_intent_stage(context: FlowContext) -> StageResult:  # noqa: C901, PLR0911, PLR0912, PLR0915
    """Execute Stage 1: validate the intent payload and dispatch the intent-creator agent.

    The intent-creator container (the same one 'holon intent' launches) creates the intent branch,
    appends intents.jsonl and pushes; the flow then fetches origin and verifies the branch exists.
    """
    start_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log("Starting Stage 1: Intent Creation")

    intent_data = dict(context.intent_data)

    # Pre-action wisdom consult
    consulted_ids: list[str] = []
    try:
        db_path = os.path.join(context.target_worktree, "holon-knowledge/.index/action_index.db")
        consult_engine = ConsultEngine(project_db_path=db_path)
        intent_labels = intent_data.get("labels", [])
        if isinstance(intent_labels, str):
            intent_labels = [lbl.strip() for lbl in intent_labels.split(",") if lbl.strip()]
        consult_res = consult_engine.consult_records(all_labels=intent_labels, top_k=6, max_tokens=1200)
        consulted_ids = consult_res.consulted_ids
        if consulted_ids:
            context.log(f"Consulted {len(consulted_ids)} knowledge records for intent: {consulted_ids}")
            intent_data["consulted_knowledge_ids"] = consulted_ids
    except Exception as e:
        logger.debug("Non-fatal consult failure during intent stage: %s", e)

    goal = intent_data.get("goal")
    raw_desc = intent_data.get("description")
    raw_slug = intent_data.get("slug")
    target_branch = intent_data.get("target_branch", "main")

    if not goal and not raw_desc:
        error_msg = "Validation failed: Intent must have at least 'goal' or 'description'."
        return _stage_failure(context, FlowStage.INTENT, start_time, error_msg)

    # Front-load clarifying questions validation
    valid_questions, unresolved = validate_intent_questions(intent_data)
    if not valid_questions:
        unresolved_list_str = "; ".join(unresolved)
        error_msg = (
            f"Validation failed: Unresolved questions detected in intent payload. "
            f"All clarifying questions must be resolved prior to completing Stage 1. Unresolved: {unresolved_list_str}"
        )
        return _stage_failure(context, FlowStage.INTENT, start_time, error_msg)

    description = raw_desc or goal or "No description provided"

    # Sanitise slug
    if not raw_slug:
        candidate = (goal or description or "task")[:40]
        raw_slug = re.sub(r"[^a-zA-Z0-9]+", "-", candidate).strip("-").lower()
    else:
        raw_slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw_slug).strip("-").lower()

    # The intent creator expects the bare branch prefix and appends '/_' itself.
    try:
        branch = resolve_root_intent_branch(intent_data, raw_slug, int(time.time()), context.agent, context.model)
    except RefTooLongError as e:
        return _stage_failure(context, FlowStage.INTENT, start_time, f"Intent stage failed: {e}")
    intent_data["branch"] = branch
    intent_data["target_branch"] = target_branch

    intent_branch = f"{branch}/_"
    context.intent_branch = intent_branch
    context.intent_data = intent_data

    dispatched = False
    if not context.dry_run:
        if context.skip_push:
            return _stage_failure(context, FlowStage.INTENT, start_time, SKIP_PUSH_DISPATCH_ERROR)
        repo_dir = context.target_worktree
        check_and_resolve_stage_conflicts(repo_dir, target_branch=target_branch, stage_name="intent", branch=None)
        fetch_error = _fetch_branch_prefix_error(repo_dir, branch)
        if fetch_error is not None:
            return _stage_failure(
                context,
                FlowStage.INTENT,
                start_time,
                f"Intent stage failed: could not fetch origin branches under {branch} before dispatching the "
                f"intent creator: {fetch_error}",
                intent_branch=intent_branch,
            )
        if _origin_has_branch(repo_dir, intent_branch):
            conflict = _reused_intent_conflict(repo_dir, intent_branch, branch, intent_data)
            if conflict is not None:
                return _stage_failure(context, FlowStage.INTENT, start_time, conflict, intent_branch=intent_branch)
            context.log(f"Intent branch '{intent_branch}' already exists on origin; reusing it without re-dispatching.")
        else:
            tmp_dir = os.path.expanduser("~/.holon/tmp")
            os.makedirs(tmp_dir, exist_ok=True)
            fd, intent_file = tempfile.mkstemp(prefix="holon-flow-intent-", suffix=".json", dir=tmp_dir)
            try:
                # mkstemp creates 0600, but the container's 'holon' user (uid 1000) reads the bind mount, as it
                # reads the 0644 file 'holon intent <file>' mounts. The payload is the intent JSON, not a secret.
                os.fchmod(fd, 0o644)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(intent_data, f)
                exit_code = _dispatch_role(context, "intent-creator", [], intent_file=intent_file)
            finally:
                with contextlib.suppress(OSError):
                    os.remove(intent_file)
            dispatched = True
            if exit_code != 0:
                return _stage_failure(
                    context,
                    FlowStage.INTENT,
                    start_time,
                    f"Intent stage failed: intent-creator agent exited with code {exit_code}.",
                    intent_branch=intent_branch,
                )
            fetch_error = _fetch_branch_prefix_error(repo_dir, branch)
            if fetch_error is not None:
                return _stage_failure(
                    context,
                    FlowStage.INTENT,
                    start_time,
                    f"Intent stage failed: the intent-creator agent exited 0 but origin branches under {branch} "
                    f"could not be fetched to verify '{intent_branch}': {fetch_error}",
                    intent_branch=intent_branch,
                )
            if not _origin_has_branch(repo_dir, intent_branch):
                return _stage_failure(
                    context,
                    FlowStage.INTENT,
                    start_time,
                    f"Intent stage failed: the intent-creator agent exited cleanly but '{intent_branch}' "
                    "was not found on origin.",
                    intent_branch=intent_branch,
                )
        _track_local_branch(context, intent_branch)

    end_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log(f"Stage 1 completed: Intent branch '{intent_branch}' established.")
    result = StageResult(
        stage=FlowStage.INTENT,
        status=StageStatus.SUCCESS,
        start_time=start_time,
        end_time=end_time,
        payload={
            "intent_branch": intent_branch,
            "branch": branch,
            "slug": raw_slug,
            "consulted_knowledge_ids": consulted_ids,
            "dispatched": dispatched,
            "intent": intent_data,
        },
    )
    context.stage_results["intent"] = result
    return result
