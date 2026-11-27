"""Stage 1 (Intent Creation): validate the intent payload, dispatch the intent creator, verify its branch."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _dispatch_role, _stage_failure
from sandbox_executor.flow.git_refs import (
    _fetch_branch_prefix_error,
    _origin_has_branch,
    _read_ledger_rows,
    _track_local_branch,
)
from sandbox_executor.flow.host_remote import fetch_base, push_branch, remote_error
from sandbox_executor.flow.intent_questions import validate_intent_questions
from sandbox_executor.flow.stages.stage_support import consult_knowledge, intent_payload_file, stage_success
from sandbox_executor.flow.target_sync_hooks import at_stage_boundary
from sandbox_executor.flow.target_sync_result import SyncBoundary
from sandbox_executor.flow_guard import SKIP_PUSH_DISPATCH_ERROR
from sandbox_executor.naming import MissingTickerError
from sandbox_executor.ref_names import RefTooLongError, SlugTooLongError, resolve_root_intent_branch, root_intent_slug

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


@dataclass
class IntentStage:
    """Stage 1 for one flow: the working intent payload and the branch it resolves to, one method per step."""

    context: FlowContext
    start_time: str
    intent_data: dict[str, Any]
    branch: str = ""
    slug: str = ""
    target_branch: str = "main"
    dispatched: bool = False

    @property
    def intent_branch(self) -> str:
        """The intent branch: the bare branch prefix the intent creator appends ``/_`` to."""
        return f"{self.branch}/_"

    def fail(self, message: str, **payload: Any) -> StageResult:
        """Record and return the FAILED Stage 1 result."""
        return _stage_failure(self.context, FlowStage.INTENT, self.start_time, message, **payload)

    def validation_error(self) -> str | None:
        """Why the intent payload is not acceptable (no goal or description, unresolved questions), or None."""
        if not self.intent_data.get("goal") and not self.intent_data.get("description"):
            return "Validation failed: Intent must have at least 'goal' or 'description'."
        # Front-load clarifying questions validation
        valid_questions, unresolved = validate_intent_questions(self.intent_data)
        if valid_questions:
            return None
        unresolved_list_str = "; ".join(unresolved)
        return (
            f"Validation failed: Unresolved questions or intent rule violations detected in intent payload. "
            "All clarifying questions and intent rules must be resolved prior to completing Stage 1. "
            f"Unresolved: {unresolved_list_str}"
        )

    def resolve_branch(self) -> StageResult | None:
        """Name the intent branch and record it on the context; a failure when the ref cannot be named."""
        goal = self.intent_data.get("goal")
        description = self.intent_data.get("description") or goal or "No description provided"
        target_branch = self.intent_data.get("target_branch", "main")
        # The intent creator expects the bare branch prefix and appends '/_' itself; a slug over the limit fails first.
        try:
            self.slug = root_intent_slug(self.intent_data, goal or description or "task")
            self.branch = resolve_root_intent_branch(
                self.intent_data, self.slug, int(time.time()), self.context.agent, self.context.model
            )
        except (RefTooLongError, MissingTickerError, SlugTooLongError) as e:
            return self.fail(f"Intent stage failed: {e}")
        self.intent_data["branch"] = self.branch
        self.intent_data["target_branch"] = self.target_branch = target_branch
        self.context.intent_branch = self.intent_branch
        self.context.intent_data = self.intent_data
        return None

    def establish_branch(self) -> StageResult | None:
        """Reuse the origin intent branch or dispatch the intent creator to create it; a failure, or None."""
        if self.context.skip_push:
            return self.fail(SKIP_PUSH_DISPATCH_ERROR)
        repo_dir = self.context.target_worktree
        if (failure := at_stage_boundary(self.context, FlowStage.INTENT, SyncBoundary.STAGE_START)) is not None:
            return failure
        fetch_error = _fetch_branch_prefix_error(repo_dir, self.branch)
        if fetch_error is not None:
            return self.fail(
                f"Intent stage failed: could not fetch origin branches under {self.branch} before dispatching the "
                f"intent creator: {fetch_error}",
                intent_branch=self.intent_branch,
            )
        if not _origin_has_branch(repo_dir, self.intent_branch):
            failure = self.create_branch()
        elif (conflict := _reused_intent_conflict(repo_dir, self.intent_branch, self.branch, self.intent_data)) is None:
            self.context.log(
                f"Intent branch '{self.intent_branch}' already exists on origin; reusing it without re-dispatching."
            )
            failure = None
        else:
            failure = self.fail(conflict, intent_branch=self.intent_branch)
        if failure is None:
            _track_local_branch(self.context, self.intent_branch)
            failure = at_stage_boundary(self.context, FlowStage.INTENT, SyncBoundary.STAGE_END)
        return failure

    def create_branch(self) -> StageResult | None:
        """Dispatch the intent creator on the fetched target branch, then publish and verify its branch."""
        base_error = remote_error(fetch_base, self.context.target_worktree, self.target_branch)
        if base_error is not None:
            return self.fail(f"Intent stage failed: {base_error}", intent_branch=self.intent_branch)
        with intent_payload_file(self.intent_data) as intent_file:
            exit_code = _dispatch_role(self.context, "intent-creator", [], intent_file=intent_file)
        self.dispatched = True
        if exit_code != 0:
            return self.fail(
                f"Intent stage failed: intent-creator agent exited with code {exit_code}.",
                intent_branch=self.intent_branch,
            )
        return self.publish_branch()

    def publish_branch(self) -> StageResult | None:
        """Push the intent branch the container created, fetch origin and verify the branch is there."""
        repo_dir = self.context.target_worktree
        push_error = remote_error(push_branch, repo_dir, self.intent_branch)
        if push_error is not None:
            return self.fail(f"Intent stage failed: {push_error}", intent_branch=self.intent_branch)
        fetch_error = _fetch_branch_prefix_error(repo_dir, self.branch)
        if fetch_error is not None:
            return self.fail(
                f"Intent stage failed: the intent-creator agent exited 0 but origin branches under {self.branch} "
                f"could not be fetched to verify '{self.intent_branch}': {fetch_error}",
                intent_branch=self.intent_branch,
            )
        if not _origin_has_branch(repo_dir, self.intent_branch):
            return self.fail(
                f"Intent stage failed: the intent-creator agent exited cleanly but '{self.intent_branch}' "
                "was not found on origin.",
                intent_branch=self.intent_branch,
            )
        return None


def run_intent_stage(context: FlowContext) -> StageResult:
    """Execute Stage 1: validate the intent payload and dispatch the intent-creator agent.

    The host fetches the target branch; the intent-creator container (the same one 'holon intent' launches) creates
    the intent branch in the mounted worktree and appends intents.jsonl; the host then pushes the branch, fetches
    origin and verifies the branch exists.
    """
    start_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log("Starting Stage 1: Intent Creation")
    stage = IntentStage(context, start_time, dict(context.intent_data))

    # Pre-action wisdom consult
    consulted_ids = consult_knowledge(context, stage.intent_data, match="all", purpose="intent", log=logger)
    if consulted_ids:
        stage.intent_data["consulted_knowledge_ids"] = consulted_ids

    error = stage.validation_error()
    if error is not None:
        return stage.fail(error)
    failure = stage.resolve_branch()
    if failure is None and not context.dry_run:
        failure = stage.establish_branch()
    if failure is not None:
        return failure

    context.log(f"Stage 1 completed: Intent branch '{stage.intent_branch}' established.")
    payload = {
        "intent_branch": stage.intent_branch,
        "branch": stage.branch,
        "slug": stage.slug,
        "consulted_knowledge_ids": consulted_ids,
        "dispatched": stage.dispatched,
        "intent": stage.intent_data,
    }
    return stage_success(context, FlowStage.INTENT, "intent", start_time, payload)
