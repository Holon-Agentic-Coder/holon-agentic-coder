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
    try:
        res = run_git(["remote", "get-url", "--push", "--", remote], cwd=repo_dir, check=False)
    except (OSError, RuntimeError, TimeoutError):
        return ""
    return (res.stdout or "").strip() if res.returncode == 0 else ""


def pushes_over_https_github(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in ("https", "http") and (parts.hostname or "").lower() == "github.com"


def token_scope_failure(
    context: FlowContext, start_time: str, report_fn: Callable[[], token_scopes.TokenScopeReport] | None = None
) -> StageResult | None:
    workflow_paths = token_scopes.touches_workflows(declared_paths(context))
    if not workflow_paths:
        return None
    url = push_url(context.target_worktree)
    # why: the OAuth workflow scope only governs HTTPS pushes to github.com; an SSH key authorises any other push.
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
