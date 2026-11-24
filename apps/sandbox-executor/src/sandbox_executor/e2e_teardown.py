"""Prefix-scoped git worktree and branch validation and unconditional teardown for E2E runs."""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sandbox_executor.e2e_types import PrefixScopingError, TeardownReport
from sandbox_executor.git import GitRunnerCallable, run_git
from sandbox_executor.git_flow import checked_out_branch

_INTENT_PREFIX_RE = re.compile(r"^I-(?P<epoch>\d+)-(?P<slug>[a-z0-9][a-z0-9-]*)$")
_PROTECTED_BRANCH_NAMES = frozenset({"main", "master", "HEAD", "origin", "origin/main", "origin/master", "origin/HEAD"})
_REF_PREFIXES = ("refs/heads/", "refs/remotes/origin/", "origin/")


@dataclass(frozen=True)
class _TeardownScope:
    """Internal parameter bundle for prefix-scoped worktree and branch teardown."""

    repo_dir: str
    intent_prefix: str
    disposable_worktree: str | None
    worktree_branch: str | None
    git_runner: GitRunnerCallable


def validate_e2e_intent_prefix(intent_prefix: str) -> str:
    """Validate and return a bare root intent prefix ``I-<timestamp>-<slug>``."""
    if not isinstance(intent_prefix, str):
        raise PrefixScopingError(f"Intent prefix must be a string, got {type(intent_prefix).__name__}.")
    cleaned = intent_prefix.strip()
    if not cleaned or cleaned in _PROTECTED_BRANCH_NAMES:
        raise PrefixScopingError(f"Invalid or protected E2E intent prefix: {intent_prefix!r}.")
    if "/" in cleaned or "*" in cleaned or "?" in cleaned or ".." in cleaned:
        raise PrefixScopingError(
            f"E2E intent prefix {intent_prefix!r} must be a bare root prefix without slashes or wildcards."
        )
    if not _INTENT_PREFIX_RE.fullmatch(cleaned):
        raise PrefixScopingError(
            f"E2E intent prefix {intent_prefix!r} does not match required 'I-<timestamp>-<slug>' format."
        )
    return cleaned


def build_e2e_intent_prefix(slug: str, epoch: int | None = None) -> str:
    """Construct and validate a unique disposable E2E intent prefix ``I-<epoch>-<slug>``."""
    clean_slug = re.sub(r"[^a-z0-9]+", "-", slug.strip().lower()).strip("-")
    if not clean_slug:
        raise PrefixScopingError(f"Cannot build E2E intent prefix from empty slug {slug!r}.")
    ts = int(time.time()) if epoch is None else int(epoch)
    if ts <= 0:
        raise PrefixScopingError(f"Intent epoch must be a positive integer, got {ts!r}.")
    return validate_e2e_intent_prefix(f"I-{ts}-{clean_slug}")


def normalise_ref_name(ref_name: str) -> str:
    """Strip leading ``refs/heads/``, ``refs/remotes/origin/``, or ``origin/`` from ``ref_name``."""
    cleaned = ref_name.strip()
    for prefix in _REF_PREFIXES:
        if cleaned.startswith(prefix):
            return cleaned[len(prefix) :]
    return cleaned


def _intent_slug_from_prefix(validated_prefix: str) -> str:
    match = _INTENT_PREFIX_RE.fullmatch(validated_prefix)
    return match.group("slug") if match else ""


def _is_valid_disposable_worktree_branch(branch: str, validated_prefix: str) -> bool:
    slug = _intent_slug_from_prefix(validated_prefix)
    if not branch or not slug or branch in _PROTECTED_BRANCH_NAMES:
        return False
    return branch == f"flow-{slug}" or branch == f"flow-{validated_prefix.lower()}"


def is_ref_within_intent_prefix(
    ref_name: str,
    intent_prefix: str,
    worktree_branch: str | None = None,
) -> bool:
    """Return True only when ``ref_name`` belongs strictly to ``intent_prefix`` (or its disposable worktree branch)."""
    validated_prefix = validate_e2e_intent_prefix(intent_prefix)
    if not isinstance(ref_name, str) or not ref_name.strip():
        return False
    normalised = normalise_ref_name(ref_name)
    if not normalised or normalised in _PROTECTED_BRANCH_NAMES:
        return False
    if normalised == validated_prefix or normalised.startswith(f"{validated_prefix}/"):
        return True
    if worktree_branch is not None:
        norm_wt = normalise_ref_name(worktree_branch)
        if _is_valid_disposable_worktree_branch(norm_wt, validated_prefix) and normalised == norm_wt:
            return True
    return False


def assert_refs_within_intent_prefix(
    refs: Iterable[str],
    intent_prefix: str,
    worktree_branch: str | None = None,
) -> tuple[str, ...]:
    """Verify every ref in ``refs`` is within ``intent_prefix``; raise :class:`PrefixScopingError` otherwise."""
    validated_prefix = validate_e2e_intent_prefix(intent_prefix)
    checked: list[str] = []
    out_of_scope: list[str] = []
    for ref in refs:
        if not is_ref_within_intent_prefix(ref, validated_prefix, worktree_branch=worktree_branch):
            out_of_scope.append(str(ref))
        else:
            checked.append(normalise_ref_name(ref))
    if out_of_scope:
        raise PrefixScopingError(f"Refusing to touch ref(s) outside intent prefix {validated_prefix!r}: {out_of_scope}")
    return tuple(checked)


def _invoke_git(git_runner: GitRunnerCallable, args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return git_runner(args, cwd=cwd, check=False)


def _candidate_worktree_paths(repo_dir: str, disposable_worktree: str | None) -> list[str]:
    if not disposable_worktree:
        return []
    abs_repo = os.path.abspath(repo_dir)
    abs_wt = os.path.abspath(disposable_worktree)
    if abs_wt == abs_repo:
        raise PrefixScopingError(
            f"Refusing to tear down primary repository directory {abs_repo!r} as a disposable worktree."
        )
    paths = [abs_wt]
    parent = Path(abs_wt).parent
    base_name = Path(abs_wt).name
    if parent.is_dir():
        paths.extend(str(sibling.resolve()) for sibling in sorted(parent.glob(f"{base_name}-rev*")))
    return paths


def _remove_disposable_worktrees(scope: _TeardownScope, errors: list[str]) -> list[str]:
    removed: list[str] = []
    for wt_path in _candidate_worktree_paths(scope.repo_dir, scope.disposable_worktree):
        if not os.path.exists(wt_path):
            continue
        current = checked_out_branch(wt_path)
        if current in ("main", "master"):
            raise PrefixScopingError(
                f"Refusing to remove worktree {wt_path!r} because it has protected branch {current!r} checked out."
            )
        if (
            current is not None
            and not _is_valid_disposable_worktree_branch(current, scope.intent_prefix)
            and not is_ref_within_intent_prefix(current, scope.intent_prefix, worktree_branch=scope.worktree_branch)
        ):
            raise PrefixScopingError(f"Refusing to remove worktree {wt_path!r} on out-of-prefix branch {current!r}.")
        res = _invoke_git(scope.git_runner, ["worktree", "remove", "--force", wt_path], scope.repo_dir)
        if res.returncode == 0 or not os.path.exists(wt_path):
            removed.append(wt_path)
        else:
            errors.append(f"git worktree remove {wt_path} failed: {(res.stderr or res.stdout).strip()}")
    _invoke_git(scope.git_runner, ["worktree", "prune"], scope.repo_dir)
    return removed


def _delete_scoped_local_branches(scope: _TeardownScope, errors: list[str]) -> list[str]:
    res = _invoke_git(scope.git_runner, ["for-each-ref", "--format=%(refname:short)", "refs/heads/"], scope.repo_dir)
    if res.returncode != 0:
        errors.append(f"git for-each-ref refs/heads/ failed: {(res.stderr or res.stdout).strip()}")
        return []
    matching = [
        line.strip()
        for line in res.stdout.splitlines()
        if line.strip()
        and is_ref_within_intent_prefix(line.strip(), scope.intent_prefix, worktree_branch=scope.worktree_branch)
    ]
    if not matching:
        return []
    current = checked_out_branch(scope.repo_dir)
    if current and current in matching:
        _invoke_git(scope.git_runner, ["checkout", "-q", "--detach", "HEAD"], scope.repo_dir)
    batch_res = _invoke_git(scope.git_runner, ["branch", "-D", *matching], scope.repo_dir)
    if batch_res.returncode == 0:
        return matching
    deleted: list[str] = []
    for branch in matching:
        del_res = _invoke_git(scope.git_runner, ["branch", "-D", branch], scope.repo_dir)
        if del_res.returncode == 0:
            deleted.append(branch)
        else:
            errors.append(f"git branch -D {branch} failed: {(del_res.stderr or del_res.stdout).strip()}")
    return deleted


def _collect_scoped_refs(lines: Iterable[str], scope: _TeardownScope, discovered: set[str]) -> None:
    for line in lines:
        raw = line.strip()
        if not raw or raw == "refs/remotes/origin/HEAD":
            continue
        short = normalise_ref_name(raw)
        if is_ref_within_intent_prefix(short, scope.intent_prefix, worktree_branch=scope.worktree_branch):
            discovered.add(short)


def _discover_scoped_remote_branches(scope: _TeardownScope) -> list[str]:
    if _invoke_git(scope.git_runner, ["remote", "get-url", "origin"], scope.repo_dir).returncode != 0:
        return []
    discovered: set[str] = set()
    tracking_res = _invoke_git(
        scope.git_runner, ["for-each-ref", "--format=%(refname)", "refs/remotes/origin/"], scope.repo_dir
    )
    if tracking_res.returncode == 0:
        _collect_scoped_refs(tracking_res.stdout.splitlines(), scope, discovered)
    ls_res = _invoke_git(
        scope.git_runner,
        ["ls-remote", "--heads", "origin", f"refs/heads/{scope.intent_prefix}", f"refs/heads/{scope.intent_prefix}/*"],
        scope.repo_dir,
    )
    if ls_res.returncode == 0:
        ref_cols = [parts[1] for line in ls_res.stdout.splitlines() if len(parts := line.strip().split()) >= 2]
        _collect_scoped_refs(ref_cols, scope, discovered)
    return sorted(discovered)


def _remote_branch_absent(scope: _TeardownScope, branch: str) -> bool:
    ls_res = _invoke_git(scope.git_runner, ["ls-remote", "--heads", "origin", f"refs/heads/{branch}"], scope.repo_dir)
    if ls_res.returncode != 0:
        return False
    return not ls_res.stdout.strip()


def _delete_scoped_remote_branches(scope: _TeardownScope, errors: list[str]) -> list[str]:
    branches = _discover_scoped_remote_branches(scope)
    if not branches:
        return []
    batch_push = _invoke_git(scope.git_runner, ["push", "origin", "--delete", *branches], scope.repo_dir)
    if batch_push.returncode == 0:
        return branches
    deleted: list[str] = []
    for branch in branches:
        push_res = _invoke_git(scope.git_runner, ["push", "origin", "--delete", branch], scope.repo_dir)
        if push_res.returncode == 0 or _remote_branch_absent(scope, branch):
            _invoke_git(scope.git_runner, ["update-ref", "-d", f"refs/remotes/origin/{branch}"], scope.repo_dir)
            deleted.append(branch)
        else:
            errors.append(f"git push origin --delete {branch} failed: {(push_res.stderr or push_res.stdout).strip()}")
    return deleted


def teardown_e2e_intent(
    repo_dir: str,
    intent_prefix: str,
    *,
    disposable_worktree: str | None = None,
    worktree_branch: str | None = None,
    **kwargs: Any,
) -> TeardownReport:
    """Unconditionally remove the disposable worktree and all local/remote branches under ``intent_prefix``."""
    candidate_refs: Iterable[str] | None = kwargs.pop("candidate_refs", None)
    git_runner: GitRunnerCallable = kwargs.pop("git_runner", run_git)
    if kwargs:
        raise TypeError(f"teardown_e2e_intent() got unexpected keyword argument(s): {', '.join(sorted(kwargs))}")

    validated_prefix = validate_e2e_intent_prefix(intent_prefix)
    if candidate_refs is not None:
        assert_refs_within_intent_prefix(candidate_refs, validated_prefix, worktree_branch=worktree_branch)
    if worktree_branch is not None and not _is_valid_disposable_worktree_branch(
        normalise_ref_name(worktree_branch), validated_prefix
    ):
        raise PrefixScopingError(
            f"Worktree branch {worktree_branch!r} does not match disposable intent prefix {validated_prefix!r}."
        )

    scope = _TeardownScope(repo_dir, validated_prefix, disposable_worktree, worktree_branch, git_runner)
    errors: list[str] = []
    removed_worktrees = _remove_disposable_worktrees(scope, errors)
    deleted_local = _delete_scoped_local_branches(scope, errors)
    deleted_remote = _delete_scoped_remote_branches(scope, errors)
    return TeardownReport(
        intent_prefix=validated_prefix,
        removed_worktrees=tuple(removed_worktrees),
        deleted_local_branches=tuple(deleted_local),
        deleted_remote_branches=tuple(deleted_remote),
        errors=tuple(errors),
    )
