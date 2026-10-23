"""Centralised Git repository abstraction and helper operations (Bean 0124).

Provides typed, robust Git interaction utilities:
- run_git: Safe subprocess execution with credential redaction and configurable timeouts.
- get_diff_numstat: Parse git diff numstats into typed tuples (insertions, deletions, path).
- resolve_ref: Resolve any Git revision or symbolic ref to a full commit SHA.
- get_head_commit: Retrieve current HEAD commit SHA.
- branch_exists: Check existence of local and remote tracking branches.
"""

from __future__ import annotations

import logging
import subprocess

from sandbox_executor.redaction import redact_text

logger = logging.getLogger(__name__)


def run_git(
    args: list[str],
    cwd: str | None = None,
    check: bool = True,
    timeout: int | None = 60,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute a Git command with credential scrubbing, timeout handling, and typed output.

    Args:
        args: Git command arguments (excluding the leading 'git').
        cwd: Working directory path for Git execution (defaults to current directory).
        check: Whether to raise RuntimeError on non-zero exit codes.
        timeout: Subprocess timeout threshold in seconds (default: 60s).
        env: Optional environment variable mapping for the subprocess.

    Returns:
        subprocess.CompletedProcess[str]: Captured process output.

    Raises:
        TimeoutError: If the Git subprocess exceeds the specified timeout.
        RuntimeError: If check is True and the command exits with a non-zero status.
    """
    working_dir = cwd or "."
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=working_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        cmd_str = redact_text(" ".join(args))
        logger.error(f"Git command timed out after {timeout}s: git {cmd_str}")
        raise TimeoutError(f"Git command timed out after {timeout}s: git {cmd_str}") from exc

    if check and res.returncode != 0:
        err = redact_text(res.stderr.strip() or res.stdout.strip())
        cmd_str = redact_text(" ".join(args))
        raise RuntimeError(f"Git command failed with exit code {res.returncode}: git {cmd_str}\n{err}")

    return res


def get_diff_numstat(
    repo_dir: str,
    base_ref: str,
    head_ref: str,
) -> list[tuple[int, int, str]]:
    """Parse git diff --numstat between two revisions into typed tuples.

    Args:
        repo_dir: Path to Git repository.
        base_ref: Base revision (e.g. merge-base SHA or branch name).
        head_ref: Head revision to compare against base.

    Returns:
        list[tuple[int, int, str]]: List of (insertions, deletions, path) tuples.
            Binary files return (-1, -1, path).
    """
    res = run_git(
        ["diff", "--numstat", base_ref, head_ref],
        cwd=repo_dir,
        check=False,
    )
    if res.returncode != 0:
        logger.debug(f"get_diff_numstat failed between {base_ref} and {head_ref}: {res.stderr}")
        return []

    entries: list[tuple[int, int, str]] = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        raw_ins, raw_del, file_path = parts
        try:
            ins = int(raw_ins)
            dels = int(raw_del)
        except ValueError:
            # Binary files display '-' for insertions and deletions
            ins = -1
            dels = -1
        entries.append((ins, dels, file_path.strip()))

    return entries


def resolve_ref(repo_dir: str, ref: str) -> str | None:
    """Resolve a Git revision or ref name to a full 40-character commit SHA.

    Args:
        repo_dir: Path to Git repository.
        ref: Symbolic ref or revision string (e.g. 'HEAD', 'main', tag, short SHA).

    Returns:
        str | None: Full commit SHA if valid, None otherwise.
    """
    if not ref or not ref.strip():
        return None

    clean_ref = ref.strip()
    res = run_git(["rev-parse", "--verify", clean_ref], cwd=repo_dir, check=False)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    return None


def get_head_commit(repo_dir: str) -> str | None:
    """Retrieve the full commit SHA of HEAD for the specified repository.

    Args:
        repo_dir: Path to Git repository.

    Returns:
        str | None: Full commit SHA of HEAD, or None if unavailable.
    """
    return resolve_ref(repo_dir, "HEAD")


def branch_exists(repo_dir: str, branch_name: str, check_remote: bool = True) -> bool:
    """Check whether a branch exists locally or as a remote tracking ref.

    Args:
        repo_dir: Path to Git repository.
        branch_name: Name of branch to check.
        check_remote: Also check remote tracking branch 'origin/<branch_name>'.

    Returns:
        bool: True if branch ref exists, False otherwise.
    """
    clean_name = branch_name.strip()
    if not clean_name:
        return False

    # Check local branch
    if resolve_ref(repo_dir, f"refs/heads/{clean_name}") is not None:
        return True

    # Check direct ref verification fallback
    if resolve_ref(repo_dir, clean_name) is not None:
        return True

    if check_remote:
        # Check origin remote tracking ref
        remote_ref = f"origin/{clean_name}" if not clean_name.startswith("origin/") else clean_name
        if resolve_ref(repo_dir, f"refs/remotes/{remote_ref}") is not None:
            return True
        if resolve_ref(repo_dir, remote_ref) is not None:
            return True

    return False
