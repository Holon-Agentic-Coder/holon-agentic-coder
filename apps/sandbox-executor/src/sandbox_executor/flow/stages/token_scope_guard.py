"""Stage 3 token scope pre-flight: refuse a workflow-touching plan whose GitHub token lacks the ``workflow`` scope.

GitHub refuses a push that changes ``.github/workflows/**`` from a token without the ``workflow`` scope, and the
host only pushes after the executor has run. When the plan's touch set or the intent's declared ``scope`` names a
workflow path, the token's scopes are read first (see :mod:`sandbox_executor.flow.github.token_scopes`), so a
missing scope fails the stage before admission, the plan refresh and the executor container. Plans that touch no
workflow path make no request. Unverifiable scopes (a fine-grained or app token, a failed request) pass with a note.
"""

from __future__ import annotations

from collections.abc import Callable

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.github import token_scopes
from sandbox_executor.flow.in_flight import read_plan_touch_set


def declared_paths(context: FlowContext) -> list[str]:
    """The plan touch set's entries and the intent's declared ``scope`` paths (an unreadable touch set adds none)."""
    paths: list[str] = []
    try:
        touch_set = read_plan_touch_set(context.target_worktree, str(context.plan_branch))
    except (OSError, RuntimeError, TimeoutError):
        touch_set = None
    if touch_set is not None:
        paths.extend(entry.path for entry in touch_set.entries)
    scope = context.intent_data.get("scope")
    if isinstance(scope, list):
        paths.extend(item for item in scope if isinstance(item, str))
    return paths


def token_scope_failure(
    context: FlowContext, start_time: str, report_fn: Callable[[], token_scopes.TokenScopeReport] | None = None
) -> StageResult | None:
    """A failed (undispatched) result when workflow paths are declared and the token positively lacks the scope."""
    workflow_paths = token_scopes.touches_workflows(declared_paths(context))
    if not workflow_paths:
        return None
    report = (report_fn or token_scopes.scope_report)()
    context.log(f"Stage 3 token scope pre-flight: {report.format_line()}")
    message = token_scopes.workflow_scope_error(report, workflow_paths)
    if message is not None:
        return _stage_failure(
            context,
            FlowStage.EXECUTE,
            start_time,
            f"Execute stage failed: {message}",
            dispatched=False,
            token_scope="missing",
            workflow_paths=workflow_paths,
        )
    if not report.verifiable:
        context.log(
            f"Stage 3 token scope pre-flight: the '{token_scopes.WORKFLOW_SCOPE}' scope could not be verified "
            f"({report.detail}); continuing."
        )
    return None
