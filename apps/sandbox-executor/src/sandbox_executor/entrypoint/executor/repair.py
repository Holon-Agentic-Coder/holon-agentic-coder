import contextlib
import os
import sys

import sandbox_executor.entrypoint.executor as executor_facade
from sandbox_executor.entrypoint.executor.commands import _CLEAN_GIT_ENV_VARS, _FALLBACK_SAFE_DIRECTORIES, _get_repo
from sandbox_executor.entrypoint.git_locks import _remove_abandoned_locks, _resolve_local_ref
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.submodule_gitfile import (
    find_submodule_gitfile_issues,
    format_submodule_gitfile_error,
    repair_submodule_gitfiles,
)


def _is_dubious_ownership_error(err: str) -> bool:
    return "detected dubious ownership" in err or "safe.directory" in err


def _needs_lock_cleanup(repo_dir: str, probe_err: str) -> bool:
    return (
        "index.lock" in probe_err
        or "cannot lock ref" in probe_err
        or os.path.exists(os.path.join(repo_dir, ".git", "index.lock"))
    )


def _strip_leaked_git_env_vars() -> None:
    for var in _CLEAN_GIT_ENV_VARS:
        if var in os.environ:
            os.environ.pop(var, None)


def _needs_head_restore(repo_dir: str, probe_err: str) -> bool:
    return (
        "dangling HEAD" in probe_err
        or "Needed a single revision" in probe_err
        or "HEAD" in probe_err
        or not os.path.exists(os.path.join(repo_dir, ".git", "HEAD"))
    ) and os.path.isdir(os.path.join(repo_dir, ".git"))


def _resolve_allowed_tip_from_disk(git_dir: str, target_branch: str, plan_branch: str | None) -> str | None:
    return _resolve_local_ref(git_dir, target_branch) or (
        _resolve_local_ref(git_dir, plan_branch) if plan_branch else None
    )


def _probe_git_repo(repo_dir: str) -> tuple[bool, str]:
    repo = _get_repo(repo_dir)
    res = repo.rev_parse("--is-inside-work-tree")
    if res.returncode != 0:
        err = (res.stderr or res.stdout).strip()
        if _is_dubious_ownership_error(err):
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

    index_lock = os.path.join(repo_dir, ".git", "index.lock")
    if os.path.exists(index_lock):
        return False, "stale .git/index.lock detected"

    res_head = repo.rev_parse("HEAD", verify=True)
    if res_head.returncode != 0:
        err = (res_head.stderr or res_head.stdout).strip()
        return False, f"dangling HEAD: {err}"

    issues = find_submodule_gitfile_issues(repo_dir)
    if issues:
        return False, format_submodule_gitfile_error(issues)

    return True, ""


def _repair_git_repo(
    repo_dir: str,
    probe_err: str,
    default_branch: str = "main",
    plan_branch: str | None = None,
) -> bool:
    if _is_dubious_ownership_error(probe_err):
        _persist_safe_directory(repo_dir)

    if _needs_lock_cleanup(repo_dir, probe_err):
        _clear_abandoned_locks(repo_dir)

    _strip_leaked_git_env_vars()

    if _needs_head_restore(repo_dir, probe_err):
        _restore_head(repo_dir, default_branch or "main", plan_branch)

    if find_submodule_gitfile_issues(repo_dir):
        _repair_submodules(repo_dir)

    healthy, _ = _probe_git_repo(repo_dir)
    return healthy


def _repair_submodules(repo_dir: str) -> None:
    repaired, remaining = repair_submodule_gitfiles(
        repo_dir,
        run=lambda args, cwd=None, check=False: _get_repo(cwd or repo_dir).run(args, check=check),
    )
    if repaired:
        repaired_list = redact_agent_secrets(", ".join(repaired))
        print(f"Info: repaired submodule gitfile(s) to host-valid relative paths: {repaired_list}", file=sys.stderr)
    if remaining:
        err_msg = redact_agent_secrets(format_submodule_gitfile_error(remaining))
        print(f"Warning: could not repair submodule gitfile(s): {err_msg}", file=sys.stderr)


def _persist_safe_directory(repo_dir: str) -> None:
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
    git_dir = os.path.join(repo_dir, ".git")
    head_path = os.path.join(git_dir, "HEAD")
    tip_sha = _resolve_allowed_tip_from_disk(git_dir, target_branch, plan_branch)
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
    scope = "--global" if executor_facade.workspace_mounted() else "--local"
    repo = _get_repo(repo_dir)
    repo.run(["config", scope, "user.email", executor_facade.EXECUTOR_GIT_EMAIL], check=False)
    repo.run(["config", scope, "user.name", executor_facade.EXECUTOR_GIT_NAME], check=False)
