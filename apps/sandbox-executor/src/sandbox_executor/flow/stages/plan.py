"""Stage 2 (Plan Generation): dispatch the planner and adopt its plan."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _dispatch_role, _stage_failure
from sandbox_executor.flow.git_refs import (
    _discover_child_branch,
    _fetch_branch_prefix_error,
    _git_show_text,
    _read_ledger_rows,
    _snapshot_child_branches,
    _track_local_branch,
)
from sandbox_executor.flow.host_remote import new_local_child_branches, publish_new_child_branches, remote_error
from sandbox_executor.flow.stage_conflicts import check_and_resolve_stage_conflicts
from sandbox_executor.flow.stages.plan_resume import AMBIGUOUS_PLAN_BRANCHES_HINT, adopt_existing_plan
from sandbox_executor.flow.stages.plan_support import (
    dry_run_plan_result,
    plan_metrics,
    plan_stage_payload,
    replan_feedback_env,
)
from sandbox_executor.flow.stages.stage_support import consult_knowledge, stage_success
from sandbox_executor.flow_guard import SKIP_PUSH_DISPATCH_ERROR
from sandbox_executor.ledger import build_plan_id
from sandbox_executor.naming import MissingTickerError, model_code, ticker_for
from sandbox_executor.plan_attempt import settle_failed_plan_attempt, snapshot_plan_attempt
from sandbox_executor.plan_paths import is_row_of_plan, recorded_plan_file

logger = logging.getLogger(__name__)


@dataclass
class PlanStage:
    """Stage 2 for one flow: the intent branch's prefix and the plan the planner produced, one method per step."""

    context: FlowContext
    start_time: str
    intent_prefix: str
    repo_dir: str
    preferred_suffix: str = ""
    existing_plans: set[str] = field(default_factory=set)
    plan_branch: str = ""
    plan_row: dict[str, Any] = field(default_factory=dict)
    plan_file: str = ""
    plan_content: str = ""

    @property
    def no_plan_error(self) -> str:
        """The error when no planner produced a plan file: the flow never writes a plan itself."""
        return (
            f"Plan stage failed: no planner agent produced plans/P-*.md on a plan branch under "
            f"{self.context.intent_branch}; refusing to fabricate a plan."
        )

    def fail(self, message: str, **payload: Any) -> StageResult:
        """Record and return the FAILED Stage 2 result."""
        return _stage_failure(self.context, FlowStage.PLAN, self.start_time, message, **payload)

    def resolve_suffix(self) -> StageResult | None:
        """Name the suffix the new plan branch ends in; a ticker-less runner fails before dispatch."""
        try:
            self.preferred_suffix = f"-{ticker_for(self.context.agent)}-{model_code(self.context.model)}"
        except MissingTickerError as exc:
            return self.fail(f"Plan stage failed: {exc}")
        return None

    def adopt_existing(self) -> StageResult | None:
        """Adopt the newest complete plan branch under this intent if one already exists."""
        branch, error = adopt_existing_plan(self.context, self.intent_prefix)
        if error is not None:
            return self.fail(f"Plan stage failed: {error}")
        self.plan_branch = branch
        return None

    def produce(self) -> StageResult | None:
        """Run the planner (or adopt an existing complete plan branch) and read back its plan."""
        for step in (self.prepare, self.adopt_existing):
            if (failure := step()) is not None:
                return failure
        steps = (self.read_plan,) if self.plan_branch else (self.dispatch_planner, self.adopt_plan_branch, self.read_plan)
        for step in steps:
            if (failure := step()) is not None:
                return failure
        return None

    def prepare(self) -> StageResult | None:
        """Refuse a skip-push run, resolve stage conflicts and snapshot the plan branches on origin and locally."""
        if self.context.skip_push:
            return self.fail(SKIP_PUSH_DISPATCH_ERROR)
        target_branch = self.context.intent_data.get("target_branch", "main")
        check_and_resolve_stage_conflicts(
            self.repo_dir, target_branch=target_branch, stage_name="plan", branch=self.context.intent_branch
        )
        existing_plans = _snapshot_child_branches(self.repo_dir, self.intent_prefix, "P")
        if existing_plans is None:
            return self.fail(
                f"Plan stage failed: could not fetch origin branches under {self.intent_prefix} before dispatching "
                "the planner."
            )
        local_plans = set(new_local_child_branches(self.repo_dir, self.intent_prefix, "P", set()))
        self.existing_plans = existing_plans | local_plans
        return None

    def dispatch_planner(self) -> StageResult | None:
        """Dispatch the planner container and publish every plan branch it committed."""
        context = self.context
        replan_feedback = str(context.intent_data.get("replan_feedback") or "").strip()
        with replan_feedback_env(replan_feedback):
            attempt_before = snapshot_plan_attempt(self.repo_dir, context.intent_branch)
            exit_code = _dispatch_role(context, "planner", [context.intent_branch, context.agent, context.model])
        # The host pushes every plan branch the container committed, a failed planner's salvage commit included.
        push_error = remote_error(
            publish_new_child_branches, self.repo_dir, self.intent_prefix, "P", self.existing_plans
        )
        if exit_code != 0:
            # A failed attempt leaves no untracked plan file, and its own failure detail (an API error) names the cause.
            detail = settle_failed_plan_attempt(self.repo_dir, context.intent_branch, attempt_before)
            cause = f"Plan stage failed: {detail}" if detail else self.no_plan_error
            return self.fail(f"{cause} (planner agent exited with code {exit_code})")
        if push_error is not None:
            return self.fail(f"Plan stage failed: {push_error}")
        return None

    def adopt_plan_branch(self) -> StageResult | None:
        """Fetch origin and adopt the one new plan branch the planner pushed."""
        fetch_error = _fetch_branch_prefix_error(self.repo_dir, self.intent_prefix)
        if fetch_error is not None:
            return self.fail(
                f"Plan stage failed: the planner agent exited 0 but origin branches under {self.intent_prefix} "
                f"could not be fetched to adopt its plan branch: {fetch_error}"
            )
        plan_branch, ambiguity = _discover_child_branch(
            self.repo_dir,
            self.intent_prefix,
            "P",
            before=self.existing_plans,
            preferred_suffix=self.preferred_suffix,
        )
        if ambiguity:
            return self.fail(f"Plan stage failed: {ambiguity}; {AMBIGUOUS_PLAN_BRANCHES_HINT}.")
        if not plan_branch:
            return self.fail(self.no_plan_error)
        self.plan_branch = plan_branch
        return None

    def read_plan(self) -> StageResult | None:
        """Read the plan branch's newest plan row and the plan file it records."""
        plan_ref = f"refs/remotes/origin/{self.plan_branch}"
        plan_rows = [
            r for r in _read_ledger_rows(self.repo_dir, plan_ref, "plans.jsonl") if is_row_of_plan(r, self.plan_branch)
        ]
        self.plan_row = plan_rows[-1] if plan_rows else {}
        self.plan_file = recorded_plan_file(self.plan_row.get("plan_file"), self.plan_branch)
        plan_content = _git_show_text(self.repo_dir, plan_ref, self.plan_file)
        if plan_content is None:
            return self.fail(self.no_plan_error, plan_branch=self.plan_branch)
        self.plan_content = plan_content
        return None


def run_plan_stage(context: FlowContext) -> StageResult:
    """Execute Stage 2: dispatch the planner agent and adopt the plan branch it committed.

    The host fetches the intent branch; the planner container (the same one 'holon plan' launches) authors
    plans/P-*.md and appends plans.jsonl on a plan branch in the mounted worktree; the host pushes that branch. The
    flow never writes a plan itself: if no planner produced a plan file,
    the stage fails. Downstream stages operate unattended and never stall on interactive questions.
    """
    start_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log("Starting Stage 2: Plan Generation")

    if not context.intent_branch:
        err = "Cannot run Plan stage without an active intent_branch in FlowContext."
        context.log(err)
        return StageResult(stage=FlowStage.PLAN, status=StageStatus.FAILED, start_time=start_time, error=err)

    intent_prefix = context.intent_branch.removesuffix("/_").rstrip("/")
    stage = PlanStage(context, start_time, intent_prefix, context.target_worktree)
    failure = stage.resolve_suffix()
    if failure is not None:
        return failure
    # Pre-planning wisdom consult
    consulted_ids = consult_knowledge(context, context.intent_data, match="any", purpose="plan", log=logger)
    if context.dry_run:
        # Validation only: name the branch the planner would create, without dispatching or writing anything.
        plan_id = build_plan_id(int(time.time()), intent_prefix, context.agent, context.model)
        return dry_run_plan_result(context, start_time, f"{intent_prefix}/{plan_id}/_", consulted_ids)
    failure = stage.produce()
    if failure is not None:
        return failure

    metrics = plan_metrics(stage.repo_dir, stage.plan_content, stage.plan_row)
    context.plan_branch = stage.plan_branch
    _track_local_branch(context, stage.plan_branch)
    payload = plan_stage_payload(context, stage.plan_branch, stage.plan_file, metrics, consulted_ids)
    return stage_success(context, FlowStage.PLAN, "plan", start_time, payload)


def _format_entropy(value: Any) -> str:
    """Render an entropy value for messages, tolerating missing or non-numeric values."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return f"{float(value):g}"
    return str(value) if value is not None else "unknown"
