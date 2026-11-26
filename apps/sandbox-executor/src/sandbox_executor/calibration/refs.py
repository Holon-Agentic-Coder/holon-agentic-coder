"""Git reference resolution for calibration: fetches, commit SHAs, branch and PR refs, and file reads at a ref."""

from __future__ import annotations

import contextlib
import logging
import re
import subprocess

from sandbox_executor.git import run_git

logger = logging.getLogger(__name__)


def fetch_remote_ref_if_needed(ref_name: str, target_worktree: str = ".") -> bool:
    """Attempt to fetch a git reference from the remote repository.

    Args:
        ref_name: Name of branch or git reference to fetch.
        target_worktree: Path to the target git worktree directory.

    Returns:
        True if fetch succeeded, False otherwise.
    """
    clean_ref = ref_name.strip().strip("/")
    if not clean_ref or clean_ref.startswith("-"):
        return False
    fetch_commands = [
        ["fetch", "origin", f"+{clean_ref}:{clean_ref}"],
        ["fetch", "origin", f"+{clean_ref}"],
    ]
    for cmd in fetch_commands:
        try:
            res = run_git(
                cmd,
                cwd=target_worktree,
                check=False,
                timeout=30,
            )
            if res.returncode == 0:
                return True
        except Exception as e:
            logger.debug("git fetch failed for %s: %s", cmd, e)
    return False


fetch_ref_if_needed = fetch_remote_ref_if_needed


def resolve_commit_sha(ref: str, target_worktree: str = ".") -> str:
    """Resolve any git ref (branch, tag, SHA, HEAD) to a 40-character commit SHA.

    Args:
        ref: Git reference to resolve (branch name, commit SHA, HEAD, etc.)
        target_worktree: Path to the target git worktree directory.

    Returns:
        The 40-character hexadecimal commit SHA.

    Raises:
        RuntimeError: If ref cannot be resolved to a commit object.
    """
    clean_ref = ref.strip()
    if not clean_ref:
        raise RuntimeError("Empty reference provided for commit SHA resolution.")
    try:
        res = run_git(
            ["rev-parse", "--verify", f"{clean_ref}^{{commit}}"],
            cwd=target_worktree,
            check=False,
            timeout=15,
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


def resolve_git_ref(ref_name: str, target_worktree: str = ".") -> str:  # noqa: C901, PLR0911, PLR0912, PLR0915
    """Resolve execution or plan ref across commit SHAs, local branches, and remote refs.

    Lookup order:
    1. Commit SHA (40-char or short hex matching valid commit object).
    2. Local branch ref (`refs/heads/<ref_name>`).
    3. Remote tracking ref (`origin/<ref_name>` or `refs/remotes/origin/<ref_name>`).
    4. Remote fetch fallback (attempt fetch from origin).
    5. PR head ref (`pull/<n>/head` or `refs/pull/<n>/head` or `pr-<n>`).

    Args:
        ref_name: Target git reference string.
        target_worktree: Path to the target git worktree directory.

    Returns:
        The resolvable git ref string or canonical commit SHA.

    Raises:
        RuntimeError: If the reference cannot be resolved locally or remotely.
    """
    clean_ref = ref_name.strip().strip("/")
    if not clean_ref:
        raise RuntimeError("Cannot resolve empty git ref.")

    # 1. Check if clean_ref is a hex commit SHA (7 to 40 hex chars)
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", clean_ref):
        try:
            res = run_git(
                ["rev-parse", "--verify", f"{clean_ref}^{{commit}}"],
                cwd=target_worktree,
                check=False,
                timeout=15,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception as e:
            logger.debug("rev-parse commit check failed: %s", e)

    # 2. Check if clean_ref is a valid local branch
    try:
        res = run_git(
            ["rev-parse", "--verify", f"refs/heads/{clean_ref}"],
            cwd=target_worktree,
            check=False,
            timeout=30,
        )
        if res.returncode == 0:
            # Check if origin has an updated ref and fetch it if tracking exists
            with contextlib.suppress(Exception):
                fetch_remote_ref_if_needed(clean_ref, target_worktree=target_worktree)
            return clean_ref
    except Exception as e:
        logger.debug("rev-parse local branch check failed: %s", e)

    # Check if clean_ref already starts with origin/ or refs/
    if clean_ref.startswith("origin/") or clean_ref.startswith("refs/"):
        try:
            res = run_git(
                ["rev-parse", "--verify", clean_ref],
                cwd=target_worktree,
                check=False,
                timeout=30,
            )
            if res.returncode == 0:
                return clean_ref
        except Exception as e:
            logger.debug("rev-parse direct ref check failed: %s", e)

    # 3. Check if origin/<clean_ref> or refs/remotes/origin/<clean_ref> exists
    for remote_candidate in [f"origin/{clean_ref}", f"refs/remotes/origin/{clean_ref}"]:
        try:
            res = run_git(
                ["rev-parse", "--verify", remote_candidate],
                cwd=target_worktree,
                check=False,
                timeout=30,
            )
            if res.returncode == 0:
                return f"origin/{clean_ref}"
        except Exception as e:
            logger.debug("rev-parse remote tracking check failed: %s", e)

    # 4. Attempt remote fetch if not found locally
    if fetch_remote_ref_if_needed(clean_ref, target_worktree=target_worktree):
        try:
            res = run_git(
                ["rev-parse", "--verify", f"refs/heads/{clean_ref}"],
                cwd=target_worktree,
                check=False,
                timeout=30,
            )
            if res.returncode == 0:
                return clean_ref
            res_remote = run_git(
                ["rev-parse", "--verify", f"origin/{clean_ref}"],
                cwd=target_worktree,
                check=False,
                timeout=30,
            )
            if res_remote.returncode == 0:
                return f"origin/{clean_ref}"
        except Exception as e:
            logger.debug("rev-parse after fetch failed: %s", e)

    # 5. Check if clean_ref matches PR ref (e.g. pull/<n>/head or refs/pull/<n>/head or pr-<n>)
    pr_match = re.search(r"pull/(\d+)/head", clean_ref) or re.search(r"pr-(\d+)", clean_ref)
    if pr_match:
        pr_num = pr_match.group(1)
        pr_target = f"pr-{pr_num}"
        try:
            res = run_git(
                ["fetch", "origin", f"+pull/{pr_num}/head:{pr_target}"],
                cwd=target_worktree,
                check=False,
                timeout=30,
            )
            if res.returncode == 0:
                return pr_target
        except Exception as e:
            logger.debug("fetch PR ref failed: %s", e)

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

    # Final direct check with git rev-parse --verify clean_ref
    try:
        res = run_git(
            ["rev-parse", "--verify", clean_ref],
            cwd=target_worktree,
            check=False,
            timeout=15,
        )
        if res.returncode == 0 and res.stdout.strip():
            return clean_ref
    except Exception as e:
        logger.debug("Final rev-parse failed: %s", e)

    raise RuntimeError(f"Target git reference '{ref_name}' could not be resolved locally or remotely.")


def read_git_file(
    ref: str,
    rel_path: str,
    target_worktree: str = ".",
    repo_dir: str | None = None,
) -> str | None:
    """Read file content from a specific git ref using `git show <ref>:<path>`.

    Args:
        ref: Git reference (branch, tag, or commit SHA).
        rel_path: Relative path to the file within the repository.
        target_worktree: Path to the target git worktree directory.
        repo_dir: Backward-compatible alias for target_worktree.

    Returns:
        File contents as string if successful, None if git show fails.
    """
    if repo_dir is not None:
        target_worktree = repo_dir
    clean_ref = ref.strip()
    if not clean_ref:
        return None
    try:
        res = run_git(
            ["show", f"{clean_ref}:{rel_path}"],
            cwd=target_worktree,
            check=False,
            timeout=15,
        )
        if res.returncode == 0:
            return res.stdout
        logger.debug("git show %s:%s returned %d: %s", clean_ref, rel_path, res.returncode, res.stderr.strip())
        return None
    except Exception as e:
        logger.debug("Exception running git show %s:%s: %s", clean_ref, rel_path, e)
        return None
