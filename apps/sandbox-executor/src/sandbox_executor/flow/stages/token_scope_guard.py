"""Stage 3 token scope pre-flight: refuse a workflow-touching plan whose GitHub token lacks the ``workflow`` scope.

GitHub refuses a push that changes ``.github/workflows/**`` from a token without the ``workflow`` scope, and the
host only pushes after the executor has run. When the plan's touch set or the intent's declared ``scope`` names a
workflow path, the token's scopes are read first (see :mod:`sandbox_executor.flow.github.token_scopes`), so a
missing scope fails the stage before admission, the plan refresh and the executor container. Plans that touch no
workflow path make no request. Unverifiable scopes (a fine-grained or app token, a failed request) pass with a note.
The OAuth ``workflow`` scope only governs HTTPS pushes, so a worktree whose remote pushes over SSH (or to a host other
than github.com) skips the check with a note: the host's SSH key, not the token, authorises that push.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.github import token_scopes
from sandbox_executor.flow.host_remote import DEFAULT_REMOTE
from sandbox_executor.flow.in_flight import read_plan_touch_set
from sandbox_executor.git import run_git
from sandbox_executor.redaction import redact_agent_secrets


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


def push_url(repo_dir: str, remote: str = DEFAULT_REMOTE) -> str:
    """The URL ``git push`` uses for ``remote`` (``pushurl``/``insteadOf`` applied), or ``""`` when unknown."""
    try:
        res = run_git(["remote", "get-url", "--push", "--", remote], cwd=repo_dir, check=False)
    except (OSError, RuntimeError, TimeoutError):
        return ""
    return (res.stdout or "").strip() if res.returncode == 0 else ""


def pushes_over_https_github(url: str) -> bool:
    """True when ``url`` is an HTTP(S) github.com URL, the only push the token's OAuth scopes govern."""
    parts = urlsplit(url)
    return parts.scheme in ("https", "http") and (parts.hostname or "").lower() == "github.com"


def token_scope_failure(
    context: FlowContext, start_time: str, report_fn: Callable[[], token_scopes.TokenScopeReport] | None = None
) -> StageResult | None:
    """A failed (undispatched) result when workflow paths are declared and the token positively lacks the scope."""
    workflow_paths = token_scopes.touches_workflows(declared_paths(context))
    if not workflow_paths:
        return None
    url = push_url(context.target_worktree)
    if not pushes_over_https_github(url):
        context.log(
            "Stage 3 token scope pre-flight: skipped, the remote does not push over HTTPS to github.com "
            f"({redact_agent_secrets(url) or 'no push URL'}); the '{token_scopes.WORKFLOW_SCOPE}' scope does not apply."
        )
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
