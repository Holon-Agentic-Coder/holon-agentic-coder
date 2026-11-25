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
_PRIMARY_PROTECTED_BRANCHES = frozenset({"main", "master"})
_FORBIDDEN_PREFIX_TOKENS = ("/", "*", "?", "..")
_REF_PREFIXES = ("refs/heads/", "refs/remotes/origin/", "origin/")
_REVIEW_WORKTREE_SUFFIX_GLOB = "-rev*"
_ORIGIN_HEAD_REF = "refs/remotes/origin/HEAD"


@dataclass(frozen=True)
class _TeardownScope:
    repo_dir: str
    intent_prefix: str
    disposable_worktree: str | None
    worktree_branch: str | None
    git_runner: GitRunnerCallable


def validate_e2e_intent_prefix(intent_prefix: str) -> str:
    if not isinstance(intent_prefix, str):
        raise PrefixScopingError(f"Intent prefix must be a string, got {type(intent_prefix).__name__}.")
    cleaned = intent_prefix.strip()
    if not cleaned or cleaned in _PROTECTED_BRANCH_NAMES:
        raise PrefixScopingError(f"Invalid or protected E2E intent prefix: {intent_prefix!r}.")
    if any(token in cleaned for token in _FORBIDDEN_PREFIX_TOKENS):
        raise PrefixScopingError(
            f"E2E intent prefix {intent_prefix!r} must be a bare root prefix without slashes or wildcards."
        )
    if not _INTENT_PREFIX_RE.fullmatch(cleaned):
        raise PrefixScopingError(
            f"E2E intent prefix {intent_prefix!r} does not match required 'I-<timestamp>-<slug>' format."
        )
    return cleaned


def build_e2e_intent_prefix(slug: str, epoch: int | None = None) -> str:
    clean_slug = re.sub(r"[^a-z0-9]+", "-", slug.strip().lower()).strip("-")
    if not clean_slug:
        raise PrefixScopingError(f"Cannot build E2E intent prefix from empty slug {slug!r}.")
    epoch_seconds = int(time.time()) if epoch is None else int(epoch)
    if epoch_seconds <= 0:
        raise PrefixScopingError(f"Intent epoch must be a positive integer, got {epoch_seconds!r}.")
    return validate_e2e_intent_prefix(f"I-{epoch_seconds}-{clean_slug}")


def normalise_ref_name(ref_name: str) -> str:
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
    validated_prefix = validate_e2e_intent_prefix(intent_prefix)
    if not isinstance(ref_name, str) or not ref_name.strip():
        return False
    normalised = normalise_ref_name(ref_name)
    if not normalised or normalised in _PROTECTED_BRANCH_NAMES:
        return False
    if normalised == validated_prefix or normalised.startswith(f"{validated_prefix}/"):
        return True
    if worktree_branch is not None:
        normalised_worktree_branch = normalise_ref_name(worktree_branch)
        if (
            _is_valid_disposable_worktree_branch(normalised_worktree_branch, validated_prefix)
            and normalised == normalised_worktree_branch
        ):
            return True
    return False


def assert_refs_within_intent_prefix(
    refs: Iterable[str],
    intent_prefix: str,
    worktree_branch: str | None = None,
) -> tuple[str, ...]:
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
    abs_worktree = os.path.abspath(disposable_worktree)
    if abs_worktree == abs_repo:
        raise PrefixScopingError(
            f"Refusing to tear down primary repository directory {abs_repo!r} as a disposable worktree."
        )
    paths = [abs_worktree]
    parent = Path(abs_worktree).parent
    base_name = Path(abs_worktree).name
    if parent.is_dir():
        paths.extend(
            str(sibling.resolve()) for sibling in sorted(parent.glob(f"{base_name}{_REVIEW_WORKTREE_SUFFIX_GLOB}"))
        )
    return paths


def _remove_disposable_worktrees(scope: _TeardownScope, errors: list[str]) -> list[str]:
    removed: list[str] = []
    for worktree_path in _candidate_worktree_paths(scope.repo_dir, scope.disposable_worktree):
        if not os.path.exists(worktree_path):
            continue
        current = checked_out_branch(worktree_path)
        if current in _PRIMARY_PROTECTED_BRANCHES:
            raise PrefixScopingError(
                f"Refusing to remove worktree {worktree_path!r} because it has protected branch {current!r} "
                "checked out."
            )
        if (
            current is not None
            and not _is_valid_disposable_worktree_branch(current, scope.intent_prefix)
            and not is_ref_within_intent_prefix(current, scope.intent_prefix, worktree_branch=scope.worktree_branch)
        ):
            raise PrefixScopingError(
                f"Refusing to remove worktree {worktree_path!r} on out-of-prefix branch {current!r}."
            )
        remove_result = _invoke_git(scope.git_runner, ["worktree", "remove", "--force", worktree_path], scope.repo_dir)
        if remove_result.returncode == 0 or not os.path.exists(worktree_path):
            removed.append(worktree_path)
        else:
            errors.append(
                f"git worktree remove {worktree_path} failed: {(remove_result.stderr or remove_result.stdout).strip()}"
            )
    _invoke_git(scope.git_runner, ["worktree", "prune"], scope.repo_dir)
    return removed


def _delete_scoped_local_branches(scope: _TeardownScope, errors: list[str]) -> list[str]:
    refs_result = _invoke_git(
        scope.git_runner, ["for-each-ref", "--format=%(refname:short)", "refs/heads/"], scope.repo_dir
    )
    if refs_result.returncode != 0:
        errors.append(f"git for-each-ref refs/heads/ failed: {(refs_result.stderr or refs_result.stdout).strip()}")
        return []
    matching = [
        line.strip()
        for line in refs_result.stdout.splitlines()
        if line.strip()
        and is_ref_within_intent_prefix(line.strip(), scope.intent_prefix, worktree_branch=scope.worktree_branch)
    ]
    if not matching:
        return []
    current = checked_out_branch(scope.repo_dir)
    if current and current in matching:
        _invoke_git(scope.git_runner, ["checkout", "-q", "--detach", "HEAD"], scope.repo_dir)
    batch_result = _invoke_git(scope.git_runner, ["branch", "-D", *matching], scope.repo_dir)
    if batch_result.returncode == 0:
        return matching
    deleted: list[str] = []
    for branch in matching:
        delete_result = _invoke_git(scope.git_runner, ["branch", "-D", branch], scope.repo_dir)
        if delete_result.returncode == 0:
            deleted.append(branch)
        else:
            errors.append(f"git branch -D {branch} failed: {(delete_result.stderr or delete_result.stdout).strip()}")
    return deleted


def _collect_scoped_refs(lines: Iterable[str], scope: _TeardownScope, discovered: set[str]) -> None:
    for line in lines:
        raw_ref = line.strip()
        if not raw_ref or raw_ref == _ORIGIN_HEAD_REF:
            continue
        short_ref = normalise_ref_name(raw_ref)
        if is_ref_within_intent_prefix(short_ref, scope.intent_prefix, worktree_branch=scope.worktree_branch):
            discovered.add(short_ref)


def _discover_scoped_remote_branches(scope: _TeardownScope) -> list[str]:
    if _invoke_git(scope.git_runner, ["remote", "get-url", "origin"], scope.repo_dir).returncode != 0:
        return []
    discovered: set[str] = set()
    tracking_result = _invoke_git(
        scope.git_runner, ["for-each-ref", "--format=%(refname)", "refs/remotes/origin/"], scope.repo_dir
    )
    if tracking_result.returncode == 0:
        _collect_scoped_refs(tracking_result.stdout.splitlines(), scope, discovered)
    ls_remote_result = _invoke_git(
        scope.git_runner,
        ["ls-remote", "--heads", "origin", f"refs/heads/{scope.intent_prefix}", f"refs/heads/{scope.intent_prefix}/*"],
        scope.repo_dir,
    )
    if ls_remote_result.returncode == 0:
        ref_columns = [
            parts[1] for line in ls_remote_result.stdout.splitlines() if len(parts := line.strip().split()) >= 2
        ]
        _collect_scoped_refs(ref_columns, scope, discovered)
    return sorted(discovered)


def _remote_branch_absent(scope: _TeardownScope, branch: str) -> bool:
    ls_remote_result = _invoke_git(
        scope.git_runner, ["ls-remote", "--heads", "origin", f"refs/heads/{branch}"], scope.repo_dir
    )
    if ls_remote_result.returncode != 0:
        return False
    return not ls_remote_result.stdout.strip()


def _delete_scoped_remote_branches(scope: _TeardownScope, errors: list[str]) -> list[str]:
    branches = _discover_scoped_remote_branches(scope)
    if not branches:
        return []
    batch_push = _invoke_git(scope.git_runner, ["push", "origin", "--delete", *branches], scope.repo_dir)
    if batch_push.returncode == 0:
        return branches
    deleted: list[str] = []
    for branch in branches:
        push_result = _invoke_git(scope.git_runner, ["push", "origin", "--delete", branch], scope.repo_dir)
        if push_result.returncode == 0 or _remote_branch_absent(scope, branch):
            _invoke_git(scope.git_runner, ["update-ref", "-d", f"refs/remotes/origin/{branch}"], scope.repo_dir)
            deleted.append(branch)
        else:
            errors.append(
                f"git push origin --delete {branch} failed: {(push_result.stderr or push_result.stdout).strip()}"
            )
    return deleted


def teardown_e2e_intent(
    repo_dir: str,
    intent_prefix: str,
    *,
    disposable_worktree: str | None = None,
    worktree_branch: str | None = None,
    **kwargs: Any,
) -> TeardownReport:
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
