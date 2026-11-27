from __future__ import annotations

import contextlib
import logging
import re
import subprocess
from collections.abc import Callable

from sandbox_executor.git import run_git

logger = logging.getLogger(__name__)


def fetch_remote_ref_if_needed(ref_name: str, target_worktree: str = ".") -> bool:
    clean_ref = ref_name.strip().strip("/")
    if not clean_ref or clean_ref.startswith("-"):
        return False
    fetch_commands = [
        ["fetch", "origin", f"+{clean_ref}:{clean_ref}"],
        ["fetch", "origin", f"+{clean_ref}"],
    ]
    for cmd in fetch_commands:
        try:
            res = run_git(cmd, cwd=target_worktree, check=False, timeout=30)
            if res.returncode == 0:
                return True
        except Exception as e:
            logger.debug("git fetch failed for %s: %s", cmd, e)
    return False


fetch_ref_if_needed = fetch_remote_ref_if_needed


def resolve_commit_sha(ref: str, target_worktree: str = ".") -> str:
    clean_ref = ref.strip()
    if not clean_ref:
        raise RuntimeError("Empty reference provided for commit SHA resolution.")
    try:
        res = run_git(
            ["rev-parse", "--verify", f"{clean_ref}^{{commit}}"], cwd=target_worktree, check=False, timeout=15
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
        err = res.stderr.strip() or "commit object not found"
        raise RuntimeError(f"Cannot resolve commit SHA for '{ref}': {err}")
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Timeout resolving commit SHA for '{ref}'") from e
    except Exception as e:
        if isinstance(e, RuntimeError):
            raise
        raise RuntimeError(f"Error resolving commit SHA for '{ref}': {e}") from e


def _as_commit_sha(clean_ref: str, target_worktree: str) -> str | None:
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", clean_ref):
        return None
    try:
        res = run_git(
            ["rev-parse", "--verify", f"{clean_ref}^{{commit}}"], cwd=target_worktree, check=False, timeout=15
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception as e:
        logger.debug("rev-parse commit check failed: %s", e)
    return None


def _as_local_branch(clean_ref: str, target_worktree: str) -> str | None:
    try:
        res = run_git(
            ["rev-parse", "--verify", f"refs/heads/{clean_ref}"], cwd=target_worktree, check=False, timeout=30
        )
        if res.returncode == 0:
            with contextlib.suppress(Exception):
                fetch_remote_ref_if_needed(clean_ref, target_worktree=target_worktree)
            return clean_ref
    except Exception as e:
        logger.debug("rev-parse local branch check failed: %s", e)
    return None


def _as_qualified_ref(clean_ref: str, target_worktree: str) -> str | None:
    if not (clean_ref.startswith("origin/") or clean_ref.startswith("refs/")):
        return None
    try:
        res = run_git(["rev-parse", "--verify", clean_ref], cwd=target_worktree, check=False, timeout=30)
        if res.returncode == 0:
            return clean_ref
    except Exception as e:
        logger.debug("rev-parse direct ref check failed: %s", e)
    return None


def _as_remote_tracking(clean_ref: str, target_worktree: str) -> str | None:
    for remote_candidate in [f"origin/{clean_ref}", f"refs/remotes/origin/{clean_ref}"]:
        try:
            res = run_git(["rev-parse", "--verify", remote_candidate], cwd=target_worktree, check=False, timeout=30)
            if res.returncode == 0:
                return f"origin/{clean_ref}"
        except Exception as e:
            logger.debug("rev-parse remote tracking check failed: %s", e)
    return None


def _after_remote_fetch(clean_ref: str, target_worktree: str) -> str | None:
    if not fetch_remote_ref_if_needed(clean_ref, target_worktree=target_worktree):
        return None
    try:
        res = run_git(
            ["rev-parse", "--verify", f"refs/heads/{clean_ref}"], cwd=target_worktree, check=False, timeout=30
        )
        if res.returncode == 0:
            return clean_ref
        res_remote = run_git(
            ["rev-parse", "--verify", f"origin/{clean_ref}"], cwd=target_worktree, check=False, timeout=30
        )
        if res_remote.returncode == 0:
            return f"origin/{clean_ref}"
    except Exception as e:
        logger.debug("rev-parse after fetch failed: %s", e)
    return None


def _pull_request_head_via_gh(pr_num: str, target_worktree: str) -> str | None:
    try:
        res = subprocess.run(
            ["gh", "pr", "view", pr_num, "--json", "headRefOid", "-q", ".headRefOid"],
            cwd=target_worktree,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception as e:
        logger.debug("gh pr view failed: %s", e)
    return None


def _as_pull_request(clean_ref: str, target_worktree: str) -> str | None:
    pr_match = re.search(r"pull/(\d+)/head", clean_ref) or re.search(r"pr-(\d+)", clean_ref)
    if not pr_match:
        return None
    pr_num = pr_match.group(1)
    pr_target = f"pr-{pr_num}"
    try:
        res = run_git(
            ["fetch", "origin", f"+pull/{pr_num}/head:{pr_target}"], cwd=target_worktree, check=False, timeout=30
        )
        if res.returncode == 0:
            return pr_target
    except Exception as e:
        logger.debug("fetch PR ref failed: %s", e)
    return _pull_request_head_via_gh(pr_num, target_worktree)


def _as_direct_ref(clean_ref: str, target_worktree: str) -> str | None:
    try:
        res = run_git(["rev-parse", "--verify", clean_ref], cwd=target_worktree, check=False, timeout=15)
        if res.returncode == 0 and res.stdout.strip():
            return clean_ref
    except Exception as e:
        logger.debug("Final rev-parse failed: %s", e)
    return None


_REF_RESOLVERS_FIRST_MATCH_WINS: tuple[Callable[[str, str], str | None], ...] = (
    _as_commit_sha,
    _as_local_branch,
    _as_qualified_ref,
    _as_remote_tracking,
    _after_remote_fetch,
    _as_pull_request,
    _as_direct_ref,
)


def resolve_git_ref(ref_name: str, target_worktree: str = ".") -> str:
    clean_ref = ref_name.strip().strip("/")
    if not clean_ref:
        raise RuntimeError("Cannot resolve empty git ref.")
    for resolver in _REF_RESOLVERS_FIRST_MATCH_WINS:
        resolved = resolver(clean_ref, target_worktree)
        if resolved is not None:
            return resolved
    raise RuntimeError(f"Target git reference '{ref_name}' could not be resolved locally or remotely.")


def read_git_file(
    ref: str,
    rel_path: str,
    target_worktree: str = ".",
    repo_dir: str | None = None,
) -> str | None:
    if repo_dir is not None:
        target_worktree = repo_dir
    clean_ref = ref.strip()
    if not clean_ref:
        return None
    try:
        res = run_git(["show", f"{clean_ref}:{rel_path}"], cwd=target_worktree, check=False, timeout=15)
        if res.returncode == 0:
            return res.stdout
        logger.debug("git show %s:%s returned %d: %s", clean_ref, rel_path, res.returncode, res.stderr.strip())
        return None
    except Exception as e:
        logger.debug("Exception running git show %s:%s: %s", clean_ref, rel_path, e)
        return None
