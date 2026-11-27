from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from sandbox_executor.entrypoint.execution_workspace import workspace_mounted
from sandbox_executor.flow.combined_base import _combined_diff_verdict, _integrated_plan_recorded
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.git_refs_branches import (
    BRANCH_TIP_SUFFIX,
    EXECUTOR_GIT_ENV,
    _execution_parent_plan_branch,
    _matches_dispatch_suffix,
)
from sandbox_executor.flow.git_refs_branches import EXECUTION_BRANCH_MARKER as EXECUTION_BRANCH_MARKER
from sandbox_executor.flow.git_refs_ledger import DEFAULT_LEDGER_REVISION as DEFAULT_LEDGER_REVISION
from sandbox_executor.flow.git_refs_ledger import _latest_execution_summary as _latest_execution_summary
from sandbox_executor.flow.git_refs_ledger import _ledger_unique_id_keys
from sandbox_executor.flow.git_refs_ledger import _parse_ledger_revision as _parse_ledger_revision
from sandbox_executor.flow.ledger_worktree import _ledger_worktree
from sandbox_executor.git import changed_paths, empty_diff_summary, run_git
from sandbox_executor.ledger import (
    LedgerRewriteError,
    atomic_ledger_append,
    read_merged_rows_at_ref,
    validate_ledger_file_unique_ids,
)
from sandbox_executor.ledger.fragments import ledger_commit_paths
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.review_target import ReviewTargetError, is_pr_reference, resolve_review_target

logger = logging.getLogger(__name__)

ORIGIN_REMOTE = "origin"
ORIGIN_REF_PREFIX = f"refs/remotes/{ORIGIN_REMOTE}/"
LOCAL_HEAD_REF_PREFIX = "refs/heads/"
GIT_REMOTE_TIMEOUT_SECONDS = 300
SHORT_SHA_LOG_LENGTH = 12
LEDGER_DIR_PREFIX = "holon-knowledge/ledger/"


def _fetch_branch_prefix_error(repo_dir: str, prefix: str) -> str | None:
    return _fetch_refspecs_error(repo_dir, [f"+{LOCAL_HEAD_REF_PREFIX}{prefix}/*:{ORIGIN_REF_PREFIX}{prefix}/*"])


def _fetch_refspecs_error(repo_dir: str, refspecs: list[str], remote: str = ORIGIN_REMOTE) -> str | None:
    res = run_git(
        ["fetch", "--no-tags", remote, *refspecs],
        cwd=repo_dir,
        check=False,
        timeout=GIT_REMOTE_TIMEOUT_SECONDS,
    )
    if res.returncode == 0:
        return None
    stderr = res.stderr.strip() if isinstance(res.stderr, str) else ""
    return redact_agent_secrets(stderr) if stderr else f"git fetch exited with code {res.returncode}"


def _fetch_branch_prefix(repo_dir: str, prefix: str) -> bool:
    return _fetch_branch_prefix_error(repo_dir, prefix) is None


def _resolve_branch_ref(repo_dir: str, branch: str, prefer_remote: bool = True) -> str | None:
    remote_ref = f"{ORIGIN_REF_PREFIX}{branch}"
    local_ref = f"{LOCAL_HEAD_REF_PREFIX}{branch}"
    candidates = (remote_ref, local_ref) if prefer_remote else (local_ref, remote_ref)
    for ref in candidates:
        res = run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo_dir, check=False)
        if res.returncode == 0:
            return ref
    return None


def _resolve_non_head_review_ref(repo_dir: str, target: str, fetch: bool) -> str | None:
    if is_pr_reference(target):
        try:
            return resolve_review_target(repo_dir, target).head_sha
        except ReviewTargetError as exc:
            logger.debug("Could not resolve the PR head for %s: %s", target, exc)
            return "HEAD"
    if fetch:
        run_git(
            [
                "fetch",
                "--no-tags",
                ORIGIN_REMOTE,
                f"+{LOCAL_HEAD_REF_PREFIX}{target}:{ORIGIN_REF_PREFIX}{target}",
            ],
            cwd=repo_dir,
            check=False,
            timeout=GIT_REMOTE_TIMEOUT_SECONDS,
        )
    return _resolve_branch_ref(repo_dir, target, prefer_remote=True)


def _short_commit_sha(repo_dir: str, rev: str) -> str:
    proc = run_git(["rev-parse", "--short", "--verify", f"{rev}^{{commit}}"], cwd=repo_dir, check=False)
    return proc.stdout.strip() if proc.returncode == 0 and isinstance(proc.stdout, str) else ""


def _resolve_review_commit(repo_dir: str, review_target: str, fetch: bool = True) -> str:
    clean_target = review_target.strip()
    is_explicit_target = bool(clean_target) and clean_target != "HEAD"
    rev = "HEAD"
    if is_explicit_target:
        resolved_ref = _resolve_non_head_review_ref(repo_dir, clean_target, fetch)
        if resolved_ref is None:
            return clean_target
        rev = resolved_ref
    short_sha = _short_commit_sha(repo_dir, rev)
    return short_sha or (clean_target if is_explicit_target else "HEAD")


def _origin_has_branch(repo_dir: str, branch: str) -> bool:
    res = run_git(
        ["rev-parse", "--verify", "--quiet", f"{ORIGIN_REF_PREFIX}{branch}^{{commit}}"],
        cwd=repo_dir,
        check=False,
    )
    return res.returncode == 0


def _checked_out_branch(repo_dir: str) -> str:
    head = run_git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=repo_dir, check=False)
    return head.stdout.strip() if head.returncode == 0 and isinstance(head.stdout, str) else ""


def _is_ancestor_commit(repo_dir: str, ancestor_sha: str, descendant_sha: str) -> bool:
    result = run_git(["merge-base", "--is-ancestor", ancestor_sha, descendant_sha], cwd=repo_dir, check=False)
    return result.returncode == 0


def fast_forward_local_branch(context: FlowContext, branch: str, target_sha: str) -> bool:
    repo_dir = context.target_worktree
    local = run_git(
        ["rev-parse", "--verify", "--quiet", f"{LOCAL_HEAD_REF_PREFIX}{branch}"],
        cwd=repo_dir,
        check=False,
    )
    local_sha = local.stdout.strip() if local.returncode == 0 and isinstance(local.stdout, str) else ""
    if local_sha == target_sha:
        return True
    if local_sha and not _is_ancestor_commit(repo_dir, local_sha, target_sha):
        context.log(
            f"Warning: local branch '{branch}' has diverged from origin; it was not fast-forwarded "
            f"to {target_sha[:SHORT_SHA_LOG_LENGTH]}."
        )
        return False
    if _checked_out_branch(repo_dir) == branch:
        update_result = run_git(["merge", "--ff-only", "--quiet", target_sha], cwd=repo_dir, check=False)
    else:
        update_result = run_git(["branch", "-f", "--no-track", branch, target_sha], cwd=repo_dir, check=False)
    if update_result.returncode != 0:
        context.log(f"Warning: could not fast-forward local branch '{branch}' to {target_sha[:SHORT_SHA_LOG_LENGTH]}.")
        return False
    return True


def _track_local_branch(context: FlowContext, branch: str) -> None:
    remote = run_git(
        ["rev-parse", "--verify", "--quiet", f"{ORIGIN_REF_PREFIX}{branch}^{{commit}}"],
        cwd=context.target_worktree,
        check=False,
    )
    remote_sha = remote.stdout.strip() if remote.returncode == 0 and isinstance(remote.stdout, str) else ""
    if not remote_sha:
        context.log(f"Warning: could not update local branch '{branch}' from origin; using origin/{branch}.")
        return
    fast_forward_local_branch(context, branch, remote_sha)


def _list_child_branches(repo_dir: str, parent_prefix: str, marker: str) -> set[str]:
    pattern = re.compile(rf"^{re.escape(parent_prefix)}/{re.escape(marker)}-\d+-[^/]+{re.escape(BRANCH_TIP_SUFFIX)}$")
    res = run_git(
        ["for-each-ref", "--format=%(refname)", f"{ORIGIN_REF_PREFIX}{parent_prefix}/"],
        cwd=repo_dir,
        check=False,
    )
    stdout = res.stdout if isinstance(res.stdout, str) else ""
    names = (line.strip().removeprefix(ORIGIN_REF_PREFIX) for line in stdout.splitlines())
    return {name for name in names if pattern.match(name)}


def _snapshot_child_branches(repo_dir: str, parent_prefix: str, marker: str) -> set[str] | None:
    if not _fetch_branch_prefix(repo_dir, parent_prefix):
        return None
    return _list_child_branches(repo_dir, parent_prefix, marker)


def _discover_child_branch(
    repo_dir: str,
    parent_prefix: str,
    marker: str,
    before: set[str],
    preferred_suffix: str,
) -> tuple[str | None, str | None]:
    new_branches = sorted(_list_child_branches(repo_dir, parent_prefix, marker) - before)
    if not new_branches:
        return None, None
    matching = [branch for branch in new_branches if _matches_dispatch_suffix(branch, preferred_suffix)]
    if not matching:
        return None, (
            f"new {marker}-branches under {parent_prefix} do not match the dispatched runner/model "
            f"('{preferred_suffix.lstrip('-')}'): {', '.join(new_branches)}; refusing to adopt another run's branch"
        )
    if len(matching) > 1:
        return None, (
            f"ambiguous {marker}-branches under {parent_prefix}: {len(matching)} appeared during dispatch "
            f"({', '.join(matching)}); refusing to guess which one this run produced"
        )
    return matching[0], None


def _git_show_text(repo_dir: str, ref: str, path: str) -> str | None:
    res = run_git(["show", f"{ref}:{path}"], cwd=repo_dir, check=False)
    if res.returncode != 0 or not isinstance(res.stdout, str):
        return None
    return res.stdout


def _read_ledger_rows(repo_dir: str, ref: str, ledger_name: str) -> list[dict[str, Any]]:
    return read_merged_rows_at_ref(repo_dir, ref, ledger_name, git=run_git)


def _append_superseding_execution_row(context: FlowContext, exec_branch: str, row: dict[str, Any]) -> str | None:
    message = f"execute: record {row.get('status') or 'superseding'} row for {row.get('execution_id')}"
    return _append_ledger_row(context, exec_branch, "executions.jsonl", row, message)


def _best_effort_sync_local_refs(context: FlowContext, branch: str, pushed_sha: str) -> None:
    _sync_local_execution_refs(context, branch, pushed_sha)


def _append_ledger_row(context: FlowContext, branch: str, ledger: str, row: dict[str, Any], msg: str) -> str | None:
    repo_dir = context.target_worktree
    ledger_rel = f"{LEDGER_DIR_PREFIX}{ledger}"
    try:
        with _ledger_worktree(repo_dir, f"{ORIGIN_REF_PREFIX}{branch}", run_git) as worktree:
            ledger_path = os.path.join(worktree, ledger_rel)
            os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
            with atomic_ledger_append(ledger_path) as handle:
                handle.write(json.dumps(row) + "\n")
            id_key, parent_key = _ledger_unique_id_keys(ledger)
            validate_ledger_file_unique_ids(ledger_path, id_key=id_key, parent_key=parent_key)
            run_git(["add", *ledger_commit_paths(worktree, ledger)], cwd=worktree)
            commit_env = {**EXECUTOR_GIT_ENV, **os.environ, "DOCKER_CLI_HINTS": "false"}
            run_git(["commit", "-m", msg], cwd=worktree, env=commit_env)
            pushed_sha = run_git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()
            run_git(
                ["push", ORIGIN_REMOTE, f"HEAD:{LOCAL_HEAD_REF_PREFIX}{branch}"],
                cwd=worktree,
                timeout=GIT_REMOTE_TIMEOUT_SECONDS,
            )
    except LedgerRewriteError:
        raise
    except (RuntimeError, TimeoutError, OSError, ValueError) as exc:
        return redact_agent_secrets(str(exc))
    _best_effort_sync_local_refs(context, branch, pushed_sha)
    return None


def _sync_local_execution_refs(context: FlowContext, exec_branch: str, pushed_sha: str) -> None:
    repo_dir = context.target_worktree
    remote_ref = f"{ORIGIN_REF_PREFIX}{exec_branch}"
    if run_git(["update-ref", remote_ref, pushed_sha], cwd=repo_dir, check=False).returncode != 0:
        context.log(
            f"Warning: could not update '{remote_ref}' to the pushed commit {pushed_sha[:SHORT_SHA_LOG_LENGTH]}."
        )
    fast_forward_local_branch(context, exec_branch, pushed_sha)


def _has_origin_remote(repo_dir: str) -> bool:
    return run_git(["remote", "get-url", ORIGIN_REMOTE], cwd=repo_dir, check=False).returncode == 0


def _unfetched_branch_message(repo_dir: str, branches: list[str], role: str = "") -> str | None:
    for branch in branches:
        ref = f"{ORIGIN_REF_PREFIX}{branch}"
        if run_git(["rev-parse", "--verify", "--quiet", ref], cwd=repo_dir, check=False).returncode != 0:
            label = f"{role} '{branch}'" if role else f"'{branch}'"
            return f"{label} is not fetched from origin; the host must fetch it before dispatch"
    return None


def _origin_refs_error(repo_dir: str, plan_branch: str, execution_branch: str) -> str | None:
    branches = [plan_branch, execution_branch]
    if workspace_mounted():
        return _unfetched_branch_message(repo_dir, branches)
    fetch_error = _fetch_refspecs_error(
        repo_dir,
        [f"+{LOCAL_HEAD_REF_PREFIX}{branch}:{ORIGIN_REF_PREFIX}{branch}" for branch in branches],
    )
    if fetch_error:
        return f"fetching '{plan_branch}' and '{execution_branch}' from origin failed: {fetch_error}"
    return None


def _resolve_effective_diff_refs(
    repo_dir: str, plan_branch: str, execution_branch: str
) -> tuple[str | None, str | None, str, str | None]:
    if _has_origin_remote(repo_dir):
        origin_error = _origin_refs_error(repo_dir, plan_branch, execution_branch)
        if origin_error:
            return None, None, ORIGIN_REMOTE, origin_error
        return (
            f"{ORIGIN_REF_PREFIX}{plan_branch}",
            f"{ORIGIN_REF_PREFIX}{execution_branch}",
            ORIGIN_REMOTE,
            None,
        )
    plan_ref = _resolve_branch_ref(repo_dir, plan_branch, prefer_remote=False)
    exec_ref = _resolve_branch_ref(repo_dir, execution_branch, prefer_remote=False)
    if not plan_ref or not exec_ref:
        return (
            None,
            None,
            "",
            f"no '{ORIGIN_REMOTE}' remote and cannot resolve '{plan_branch}' and '{execution_branch}' locally",
        )
    return plan_ref, exec_ref, f"local branches (no '{ORIGIN_REMOTE}' remote)", None


def _execution_effective_diff(
    repo_dir: str, execution_branch: str, target_branch: str = "main"
) -> tuple[bool | None, str]:
    plan_branch = _execution_parent_plan_branch(execution_branch)
    if plan_branch is None:
        return True, "not an execution branch"
    plan_ref, exec_ref, source, ref_error = _resolve_effective_diff_refs(repo_dir, plan_branch, execution_branch)
    if ref_error or not plan_ref or not exec_ref:
        return None, ref_error or f"cannot resolve '{plan_branch}' and '{execution_branch}'"
    if _integrated_plan_recorded(_read_ledger_rows(repo_dir, exec_ref, "executions.jsonl"), plan_branch):
        mounted = workspace_mounted() if source == ORIGIN_REMOTE else None
        return _combined_diff_verdict(repo_dir, (execution_branch, exec_ref), target_branch, mounted, source)
    try:
        changed = changed_paths(repo_dir, plan_ref, exec_ref)
    except (RuntimeError, TimeoutError) as exc:
        return None, f"diff against {plan_branch} failed: {redact_agent_secrets(str(exc))}"
    empty = f"{empty_diff_summary(plan_branch)} (compared on {source})"
    return bool(changed), f"{len(changed)} changed path(s) on {source}" if changed else empty
