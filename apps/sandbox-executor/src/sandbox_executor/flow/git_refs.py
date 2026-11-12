"""Git ref helpers for the flow engine: branch fetches, child-branch discovery, ledger readers and the
execution refs and effective diff that Stage 4 reviews.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from typing import Any

from sandbox_executor.entrypoint.execution_workspace import workspace_mounted
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.git import changed_paths, empty_diff_summary, run_git
from sandbox_executor.ledger import LedgerRewriteError, atomic_ledger_append, validate_ledger_file_unique_ids
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.review_target import ReviewTargetError, is_pr_reference, resolve_review_target

logger = logging.getLogger(__name__)


EXECUTOR_GIT_ENV: dict[str, str] = {
    "GIT_AUTHOR_NAME": "Holon Executor Agent",
    "GIT_AUTHOR_EMAIL": "executor-agent@holon-agentic-coder.com",
    "GIT_COMMITTER_NAME": "Holon Executor Agent",
    "GIT_COMMITTER_EMAIL": "executor-agent@holon-agentic-coder.com",
}


def _fetch_branch_prefix_error(repo_dir: str, prefix: str) -> str | None:
    """Fetch every origin branch at or beneath '<prefix>/' into refs/remotes/origin/.

    Returns:
        str | None: None on success, otherwise a secret-redacted description of the fetch failure.
    """
    return _fetch_refspecs_error(repo_dir, [f"+refs/heads/{prefix}/*:refs/remotes/origin/{prefix}/*"])


def _fetch_refspecs_error(repo_dir: str, refspecs: list[str], remote: str = "origin") -> str | None:
    """Fetch the given refspecs from the worktree's remote (``origin`` by default) in one command.

    Returns:
        str | None: None on success, otherwise a secret-redacted description of the fetch failure.
    """
    res = run_git(["fetch", "--no-tags", remote, *refspecs], cwd=repo_dir, check=False, timeout=300)
    if res.returncode == 0:
        return None
    stderr = res.stderr.strip() if isinstance(res.stderr, str) else ""
    return redact_agent_secrets(stderr) if stderr else f"git fetch exited with code {res.returncode}"


def _fetch_branch_prefix(repo_dir: str, prefix: str) -> bool:
    """Fetch every origin branch at or beneath '<prefix>/' into refs/remotes/origin/; True on success."""
    return _fetch_branch_prefix_error(repo_dir, prefix) is None


def _resolve_branch_ref(repo_dir: str, branch: str, prefer_remote: bool = True) -> str | None:
    """Return a fully qualified ref for a branch name (origin tracking ref or local head), if it exists."""
    remote_ref = f"refs/remotes/origin/{branch}"
    local_ref = f"refs/heads/{branch}"
    candidates = (remote_ref, local_ref) if prefer_remote else (local_ref, remote_ref)
    for ref in candidates:
        res = run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo_dir, check=False)
        if res.returncode == 0:
            return ref
    return None


def _resolve_review_commit(repo_dir: str, review_target: str, fetch: bool = True) -> str:
    """Return the short SHA of the commit a review loop evaluates.

    The flow no longer leaves the operator's checkout on the execution branch, so the commit comes from
    the review target, never from an unrelated 'HEAD': a branch is fetched from origin and its tip used
    (the local branch only when origin cannot be read), and a PR number or URL resolves to the PR head
    through gh. Only a 'HEAD' target, or a PR whose head gh cannot report, reads the checkout. With
    fetch=False (dry runs, or a role container where the host fetched) the origin tracking ref is used as it is.

    Returns:
        str: The short commit SHA, or the target name itself when a branch target does not resolve.
    """
    clean = review_target.strip()
    rev = "HEAD"
    if clean and clean != "HEAD":
        if is_pr_reference(clean):
            try:
                rev = resolve_review_target(repo_dir, clean).head_sha
            except ReviewTargetError as exc:
                logger.debug("Could not resolve the PR head for %s: %s", clean, exc)
        else:
            if fetch:
                run_git(
                    ["fetch", "--no-tags", "origin", f"+refs/heads/{clean}:refs/remotes/origin/{clean}"],
                    cwd=repo_dir,
                    check=False,
                    timeout=300,
                )
            ref = _resolve_branch_ref(repo_dir, clean, prefer_remote=True)
            if ref is None:
                return clean
            rev = ref
    proc = run_git(["rev-parse", "--short", "--verify", f"{rev}^{{commit}}"], cwd=repo_dir, check=False)
    out = proc.stdout.strip() if proc.returncode == 0 and isinstance(proc.stdout, str) else ""
    return out or (clean if clean and clean != "HEAD" else "HEAD")


def _origin_has_branch(repo_dir: str, branch: str) -> bool:
    """Return True when the fetched origin tracking ref for a branch exists."""
    res = run_git(
        ["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}^{{commit}}"], cwd=repo_dir, check=False
    )
    return res.returncode == 0


def _fast_forward_local_branch(context: FlowContext, branch: str, target_sha: str) -> bool:
    """Create or fast-forward the local branch to target_sha; never move it off unpushed local commits.

    A local branch whose tip is not an ancestor of target_sha (ahead of or diverged from origin) is
    left untouched and reported. Returns True when the local branch now points at target_sha.
    """
    repo_dir = context.target_worktree
    local = run_git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo_dir, check=False)
    local_sha = local.stdout.strip() if local.returncode == 0 and isinstance(local.stdout, str) else ""
    if local_sha == target_sha:
        # A role committed on this branch in the worktree and the host pushed it; it may well be checked out.
        return True
    if local_sha:
        ancestor = run_git(["merge-base", "--is-ancestor", local_sha, target_sha], cwd=repo_dir, check=False)
        if ancestor.returncode != 0:
            context.log(
                f"Warning: local branch '{branch}' has diverged from origin; it was not fast-forwarded "
                f"to {target_sha[:12]}."
            )
            return False
    head = run_git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=repo_dir, check=False)
    if head.returncode == 0 and head.stdout.strip() == branch:
        # The role left the worktree on this branch: advance the checkout with it, as git refuses to move that ref.
        res = run_git(["merge", "--ff-only", "--quiet", target_sha], cwd=repo_dir, check=False)
    else:
        res = run_git(["branch", "-f", "--no-track", branch, target_sha], cwd=repo_dir, check=False)
    if res.returncode != 0:
        context.log(f"Warning: could not fast-forward local branch '{branch}' to {target_sha[:12]}.")
        return False
    return True


def _track_local_branch(context: FlowContext, branch: str) -> None:
    """Fast-forward the local branch to the fetched origin branch so later stages can name it directly.

    A local branch carrying commits origin does not have is left alone (with a warning) rather than reset.
    """
    remote = run_git(
        ["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}^{{commit}}"],
        cwd=context.target_worktree,
        check=False,
    )
    remote_sha = remote.stdout.strip() if remote.returncode == 0 and isinstance(remote.stdout, str) else ""
    if not remote_sha:
        context.log(f"Warning: could not update local branch '{branch}' from origin; using origin/{branch}.")
        return
    _fast_forward_local_branch(context, branch, remote_sha)


def _list_child_branches(repo_dir: str, parent_prefix: str, marker: str) -> set[str]:
    """Return fetched origin branches named '<parent_prefix>/<marker>-<timestamp>-.../_'."""
    pattern = re.compile(rf"^{re.escape(parent_prefix)}/{re.escape(marker)}-\d+-[^/]+/_$")
    res = run_git(
        ["for-each-ref", "--format=%(refname)", f"refs/remotes/origin/{parent_prefix}/"],
        cwd=repo_dir,
        check=False,
    )
    stdout = res.stdout if isinstance(res.stdout, str) else ""
    names = (line.strip().removeprefix("refs/remotes/origin/") for line in stdout.splitlines())
    return {name for name in names if pattern.match(name)}


def _snapshot_child_branches(repo_dir: str, parent_prefix: str, marker: str) -> set[str] | None:
    """Fetch origin beneath a parent prefix and return the child branches that exist before dispatch.

    Returns:
        set[str] | None: Branch names present on origin, or None when origin could not be fetched (the
        snapshot would then miss branches that already exist remotely, so callers must not dispatch).
    """
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
    """Find the branch a role container pushed beneath a parent prefix during dispatch.

    Only branches absent from the pre-dispatch snapshot ('before') are candidates, so the result does
    not depend on the container clock and never adopts a stale run or a sibling that already existed.
    A candidate must end with preferred_suffix (the runner and model that were dispatched). Exactly one
    candidate is adopted; anything else is reported instead of guessed.

    Returns:
        tuple[str | None, str | None]: (branch, None) on success, (None, None) when no new branch
        appeared, or (None, message) when the new branches cannot be attributed to this dispatch.
    """
    new = sorted(_list_child_branches(repo_dir, parent_prefix, marker) - before)
    if not new:
        return None, None
    matching = [b for b in new if b.removesuffix("/_").endswith(preferred_suffix)]
    if not matching:
        return None, (
            f"new {marker}-branches under {parent_prefix} do not match the dispatched runner/model "
            f"('{preferred_suffix.lstrip('-')}'): {', '.join(new)}; refusing to adopt another run's branch"
        )
    if len(matching) > 1:
        return None, (
            f"ambiguous {marker}-branches under {parent_prefix}: {len(matching)} appeared during dispatch "
            f"({', '.join(matching)}); refusing to guess which one this run produced"
        )
    return matching[0], None


def _git_show_text(repo_dir: str, ref: str, path: str) -> str | None:
    """Return a file's content at a ref, or None when it does not exist there."""
    res = run_git(["show", f"{ref}:{path}"], cwd=repo_dir, check=False)
    if res.returncode != 0 or not isinstance(res.stdout, str):
        return None
    return res.stdout


def _read_ledger_rows(repo_dir: str, ref: str, ledger_name: str) -> list[dict[str, Any]]:
    """Parse a holon-knowledge/ledger/*.jsonl file at a ref, skipping unparseable lines."""
    content = _git_show_text(repo_dir, ref, f"holon-knowledge/ledger/{ledger_name}") or ""
    rows: list[dict[str, Any]] = []
    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _latest_execution_summary(rows: list[dict[str, Any]], execution_id: str) -> dict[str, Any] | None:
    """Return the authoritative summary row for an execution: highest ledger_revision, then latest written."""
    best: tuple[int, int] | None = None
    chosen: dict[str, Any] | None = None
    for idx, row in enumerate(rows):
        if row.get("entry_type") != "summary" or row.get("execution_id") != execution_id:
            continue
        try:
            revision = int(row.get("ledger_revision") or 1)
        except (TypeError, ValueError):
            revision = 1
        key = (revision, idx)
        if best is None or key > best:
            best, chosen = key, row
    return chosen


def _append_superseding_execution_row(context: FlowContext, exec_branch: str, row: dict[str, Any]) -> str | None:
    """Append a superseding executions.jsonl row on the execution branch and push it (see ``_append_ledger_row``)."""
    message = f"execute: record {row.get('status') or 'superseding'} row for {row.get('execution_id')}"
    return _append_ledger_row(context, exec_branch, "executions.jsonl", row, message)


def _append_ledger_row(context: FlowContext, branch: str, ledger: str, row: dict[str, Any], msg: str) -> str | None:
    """Append ``row`` to holon-knowledge/ledger/<ledger> on ``branch``, commit it with ``msg`` and push that branch.

    Works in a temporary detached worktree on origin/<branch> (the operator's checkout is never disturbed); earlier rows
    are left untouched and the ledger's unique-id check runs before the commit. origin/<branch> and the local branch
    are then fast-forwarded. Returns None on success, otherwise a secret-redacted description of what failed.
    """
    repo_dir, ledger_rel = context.target_worktree, f"holon-knowledge/ledger/{ledger}"
    tmp_root = tempfile.mkdtemp(prefix="holon-flow-ledger-")
    worktree = os.path.join(tmp_root, "worktree")
    try:
        run_git(["worktree", "add", "--detach", worktree, f"refs/remotes/origin/{branch}"], cwd=repo_dir)
        ledger_path = os.path.join(worktree, ledger_rel)
        os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
        with atomic_ledger_append(ledger_path) as f:
            f.write(json.dumps(row) + "\n")
        ids = ("plan_id", "intent_branch") if ledger == "plans.jsonl" else ("execution_id", "plan_branch")
        validate_ledger_file_unique_ids(ledger_path, id_key=ids[0], parent_key=ids[1])
        run_git(["add", ledger_rel], cwd=worktree)
        commit_env = {**EXECUTOR_GIT_ENV, **os.environ, "DOCKER_CLI_HINTS": "false"}
        run_git(["commit", "-m", msg], cwd=worktree, env=commit_env)
        pushed_sha = run_git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()
        run_git(["push", "origin", f"HEAD:refs/heads/{branch}"], cwd=worktree, timeout=300)
    except LedgerRewriteError:
        raise
    except (RuntimeError, TimeoutError, OSError, ValueError) as exc:
        return redact_agent_secrets(str(exc))
    finally:
        run_git(["worktree", "remove", "--force", worktree], cwd=repo_dir, check=False)
        run_git(["worktree", "prune"], cwd=repo_dir, check=False)
        shutil.rmtree(tmp_root, ignore_errors=True)
    # The row is on origin now: aligning local refs is best-effort and never reported as a failed write.
    _sync_local_execution_refs(context, branch, pushed_sha)
    return None


def _sync_local_execution_refs(context: FlowContext, exec_branch: str, pushed_sha: str) -> None:
    """Fast-forward origin/<branch> and the local branch to a commit just pushed to origin.

    Keeps local readers (``holon calibrate``, ``git log``) on the same ledger state as origin. A local
    branch that has diverged from the pushed commit is left untouched and reported instead.
    """
    repo_dir = context.target_worktree
    remote_ref = f"refs/remotes/origin/{exec_branch}"
    if run_git(["update-ref", remote_ref, pushed_sha], cwd=repo_dir, check=False).returncode != 0:
        context.log(f"Warning: could not update '{remote_ref}' to the pushed commit {pushed_sha[:12]}.")
    _fast_forward_local_branch(context, exec_branch, pushed_sha)


def _execution_parent_plan_branch(execution_branch: str) -> str | None:
    """Derive '<intent>/P-.../_' from '<intent>/P-.../E-.../_', or None for non-execution targets."""
    clean = execution_branch.strip()
    if "/E-" not in clean:
        return None
    return clean.split("/E-", 1)[0] + "/_"


def _has_origin_remote(repo_dir: str) -> bool:
    """Return True when the repository has an 'origin' remote configured."""
    return run_git(["remote", "get-url", "origin"], cwd=repo_dir, check=False).returncode == 0


def _unfetched_branch_message(repo_dir: str, branches: list[str]) -> str | None:
    """Name the first branch whose origin tracking ref is missing (the host fetches before dispatch)."""
    for branch in branches:
        ref = f"refs/remotes/origin/{branch}"
        if run_git(["rev-parse", "--verify", "--quiet", ref], cwd=repo_dir, check=False).returncode != 0:
            return f"'{branch}' is not fetched from origin; the host must fetch it before dispatch"
    return None


def _execution_effective_diff(repo_dir: str, execution_branch: str) -> tuple[bool | None, str]:
    """Check whether an execution branch changes anything relative to its parent plan branch.

    The PR is opened from origin, so with an 'origin' remote both branches are fetched first and the
    freshly fetched origin tracking refs are compared (as ``_resolve_review_commit`` does): stale or
    unpushed local branches never decide the outcome, and a failed fetch (including a branch that was
    never pushed) is reported as unverifiable. Only a repository without an 'origin' remote falls back
    to its local branches, and the returned detail says so.

    Returns:
        tuple[bool | None, str]: (True, detail) when it does or the target is not an execution
        branch, (False, summary) for an empty execution, and (None, reason) when it cannot be
        verified (callers must then refuse to open a PR).
    """
    plan_branch = _execution_parent_plan_branch(execution_branch)
    if plan_branch is None:
        return True, "not an execution branch"
    if _has_origin_remote(repo_dir):
        branches = [plan_branch, execution_branch]
        if workspace_mounted():
            missing = _unfetched_branch_message(repo_dir, branches)
            if missing:
                return None, missing
        elif fetch_error := _fetch_refspecs_error(
            repo_dir, [f"+refs/heads/{branch}:refs/remotes/origin/{branch}" for branch in branches]
        ):
            return None, f"fetching '{plan_branch}' and '{execution_branch}' from origin failed: {fetch_error}"
        plan_ref = f"refs/remotes/origin/{plan_branch}"
        exec_ref = f"refs/remotes/origin/{execution_branch}"
        source = "origin"
    else:
        plan_ref = _resolve_branch_ref(repo_dir, plan_branch, prefer_remote=False)
        exec_ref = _resolve_branch_ref(repo_dir, execution_branch, prefer_remote=False)
        if not plan_ref or not exec_ref:
            return None, f"no 'origin' remote and cannot resolve '{plan_branch}' and '{execution_branch}' locally"
        source = "local branches (no 'origin' remote)"
    try:
        changed = changed_paths(repo_dir, plan_ref, exec_ref)
    except (RuntimeError, TimeoutError) as exc:
        return None, f"diff against {plan_branch} failed: {redact_agent_secrets(str(exc))}"
    if not changed:
        return False, f"{empty_diff_summary(plan_branch)} (compared on {source})"
    return True, f"{len(changed)} changed path(s) on {source}"
