"""Git repository health for the executor: the probe, the in-place repairs and the executor git identity.

The mounted worktree's git directory belongs to the host, so it is never rebuilt: only benign damage (dubious
ownership, abandoned locks, leaked git environment variables, a dangling HEAD) is repaired in place. Patch seams
(``_get_local_safe_directories``, ``workspace_mounted`` and the identity constants) are read through the package facade
at call time.
"""

import contextlib
import os
import sys

import sandbox_executor.entrypoint.executor as executor_facade
from sandbox_executor.entrypoint.executor.commands import _CLEAN_GIT_ENV_VARS, _FALLBACK_SAFE_DIRECTORIES, _get_repo
from sandbox_executor.entrypoint.git_locks import _remove_abandoned_locks, _resolve_local_ref
from sandbox_executor.redaction import redact_agent_secrets


def _probe_git_repo(repo_dir: str) -> tuple[bool, str]:
    """Probes the repository at repo_dir to verify if git is healthy and usable.

    Returns a tuple of (is_healthy, error_output).
    """
    repo = _get_repo(repo_dir)
    res = repo.rev_parse("--is-inside-work-tree")
    if res.returncode != 0:
        err = (res.stderr or res.stdout).strip()
        if "detected dubious ownership" in err or "safe.directory" in err:
            retry_res = repo.run(
                ["-c", f"safe.directory={repo_dir}", "rev-parse", "--is-inside-work-tree"],
                check=False,
            )
            norm_repo = os.path.realpath(repo_dir)
            configured_dirs = executor_facade._get_local_safe_directories(repo_dir)
            already_configured = norm_repo in _FALLBACK_SAFE_DIRECTORIES or any(
                sd == "*" or os.path.realpath(sd) == norm_repo for sd in configured_dirs
            )
            if retry_res.returncode == 0 and already_configured:
                pass
            else:
                return False, err
        else:
            return False, err

    # Check for stale index.lock
    index_lock = os.path.join(repo_dir, ".git", "index.lock")
    if os.path.exists(index_lock):
        return False, "stale .git/index.lock detected"

    # Check for dangling or missing HEAD symref
    res_head = repo.rev_parse("HEAD", verify=True)
    if res_head.returncode != 0:
        err = (res_head.stderr or res_head.stdout).strip()
        return False, f"dangling HEAD: {err}"

    return True, ""


def _repair_git_repo(
    repo_dir: str,
    probe_err: str,
    default_branch: str = "main",
    plan_branch: str | None = None,
) -> bool:
    """Attempts to repair benign git failures in-place without rebuilding the repository.

    Repairs handled:
    - (a) Dubious ownership: persists safe.directory to local git config.
    - (b) Abandoned lock files: removed only when old enough to be provably stale.
    - (c) Leaked git environment variables: stripped from os.environ.
    - (d) Dangling or missing HEAD symref: restores HEAD onto ``default_branch`` (the execution
      branch), never onto whatever branch happens to sort first.

    Args:
        repo_dir: Workspace path.
        probe_err: Error text produced by :func:`_probe_git_repo`.
        default_branch: Branch HEAD must end up on -- the execution branch.
        plan_branch: Branch the workspace was checked out from, used as the tip source when the
            execution branch does not exist yet.

    Returns True if the repository was successfully restored to a usable state, False otherwise.
    """
    # Case (a): Dubious ownership
    if "detected dubious ownership" in probe_err or "safe.directory" in probe_err:
        _persist_safe_directory(repo_dir)

    # Case (b): Stale index lock or locked ref
    if (
        "index.lock" in probe_err
        or "cannot lock ref" in probe_err
        or os.path.exists(os.path.join(repo_dir, ".git", "index.lock"))
    ):
        _clear_abandoned_locks(repo_dir)

    # Case (c): Leaked environment variables
    for var in _CLEAN_GIT_ENV_VARS:
        if var in os.environ:
            os.environ.pop(var, None)

    # Case (d): Dangling or missing HEAD symref
    if (
        "dangling HEAD" in probe_err
        or "Needed a single revision" in probe_err
        or "HEAD" in probe_err
        or not os.path.exists(os.path.join(repo_dir, ".git", "HEAD"))
    ) and os.path.isdir(os.path.join(repo_dir, ".git")):
        _restore_head(repo_dir, default_branch or "main", plan_branch)

    healthy, _ = _probe_git_repo(repo_dir)
    return healthy


def _persist_safe_directory(repo_dir: str) -> None:
    """Add ``repo_dir`` to the local safe.directory config, or fall back to per-invocation ``-c safe.directory``."""
    res = _get_repo(repo_dir).run(
        ["-c", f"safe.directory={repo_dir}", "config", "--local", "--add", "safe.directory", repo_dir],
        check=False,
    )
    if res.returncode != 0:
        err_msg = (res.stderr or res.stdout).strip()
        print(
            f"Warning: unable to write safe.directory to local git config ({redact_agent_secrets(err_msg)}); "
            f"falling back to per-invocation -c safe.directory",
            file=sys.stderr,
        )
        _FALLBACK_SAFE_DIRECTORIES.add(os.path.realpath(repo_dir))


def _clear_abandoned_locks(repo_dir: str) -> None:
    """Remove the provably abandoned git lock files and report the ones still held as warnings."""
    removed, still_held = _remove_abandoned_locks(repo_dir)
    for lock in removed:
        print(f"Warning: removed abandoned git lock file {lock}", file=sys.stderr)
    if still_held:
        print(
            "Warning: git lock file(s) too young to be declared abandoned are left in place: "
            f"{', '.join(still_held)}. They may belong to a running git process.",
            file=sys.stderr,
        )


def _restore_head(repo_dir: str, target_branch: str, plan_branch: str | None) -> None:
    """Point a dangling or missing HEAD at ``target_branch``, read from disk, or warn and leave HEAD untouched."""
    git_dir = os.path.join(repo_dir, ".git")
    head_path = os.path.join(git_dir, "HEAD")
    # HEAD damage makes every git command fail, so the tip has to be read from disk. Guessing
    # an arbitrary branch here would silently put the execution commit on the wrong ref,
    # so the only acceptable sources are the execution branch itself and the plan branch the
    # workspace was checked out from.
    tip_sha = _resolve_local_ref(git_dir, target_branch) or (
        _resolve_local_ref(git_dir, plan_branch) if plan_branch else None
    )
    if not tip_sha:
        print(
            f"Warning: cannot restore HEAD to '{target_branch}': no local ref for it or for "
            f"'{plan_branch or 'unknown'}'; leaving HEAD untouched.",
            file=sys.stderr,
        )
        return
    target_ref_dir = os.path.join(git_dir, "refs", "heads", *target_branch.split("/")[:-1])
    with contextlib.suppress(Exception):
        os.makedirs(target_ref_dir, exist_ok=True)
        with open(os.path.join(git_dir, "refs", "heads", *target_branch.split("/")), "w") as rf:
            rf.write(f"{tip_sha}\n")
    with contextlib.suppress(Exception), open(head_path, "w") as f:
        f.write(f"ref: refs/heads/{target_branch}\n")
    repo = _get_repo(repo_dir)
    verify = repo.run(["symbolic-ref", "HEAD"], check=False)
    if verify.returncode != 0 or verify.stdout.strip() != f"refs/heads/{target_branch}":
        repo.run(["symbolic-ref", "HEAD", f"refs/heads/{target_branch}"], check=False)


def _configure_executor_identity(repo_dir: str) -> None:
    """Set the executor git identity, so the agent's own commits and the executor's commits can land.

    Called straight after the execution branch is created (before the agent runs) and again before the executor
    commits, because a git recovery re-initialises the repository and discards the local config. A mounted host
    worktree's repository config is shared with the host and mounted read-only, so the identity then goes in the
    container's own global config.
    """
    scope = "--global" if executor_facade.workspace_mounted() else "--local"
    repo = _get_repo(repo_dir)
    repo.run(["config", scope, "user.email", executor_facade.EXECUTOR_GIT_EMAIL], check=False)
    repo.run(["config", scope, "user.name", executor_facade.EXECUTOR_GIT_NAME], check=False)
