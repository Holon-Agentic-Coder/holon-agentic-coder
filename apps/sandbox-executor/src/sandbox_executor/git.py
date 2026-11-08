"""Centralised Git repository abstraction and helper operations.

Provides typed, robust Git interaction utilities:
- GitRepository: Intention-revealing adapter bound to a repository directory with pluggable runner.
- run_git: Safe subprocess execution with credential redaction and configurable timeouts.
- get_diff_numstat: Parse git diff numstats into typed tuples (insertions, deletions, path).
- resolve_ref: Resolve any Git revision or symbolic ref to a full commit SHA.
- get_head_commit: Retrieve current HEAD commit SHA.
- branch_exists: Check existence of local and remote tracking branches.
- changed_paths / has_effective_diff: Shared definition of an "empty execution" (no changes outside
  holon-knowledge/ledger/, plans/ and executions/), used by the executor entrypoint, flow Stage 3 and the review loop.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from sandbox_executor.redaction import redact_agent_secrets, redact_command

logger = logging.getLogger(__name__)

GitRunnerCallable = Callable[..., subprocess.CompletedProcess[str]]


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
        cmd_str = redact_command(args)
        logger.error(f"Git command timed out after {timeout}s: git {cmd_str}")
        raise TimeoutError(f"Git command timed out after {timeout}s: git {cmd_str}") from exc

    if check and res.returncode != 0:
        err = redact_agent_secrets(res.stderr.strip() or res.stdout.strip())
        cmd_str = redact_command(args)
        raise RuntimeError(f"Git command failed with exit code {res.returncode}: git {cmd_str}\n{err}")

    return res


class GitRepository:
    """Intention-revealing adapter for Git repository operations.

    Encapsulates subprocess invocations for a target repository directory, supporting
    an injected runner callable (such as executor.run_cmd) for logging, credential
    scrubbing, and safe.directory handling.
    """

    def __init__(
        self,
        repo_dir: str | Path,
        runner: GitRunnerCallable | None = None,
    ) -> None:
        """Initialise the Git repository adapter.

        Args:
            repo_dir: Path to the target Git repository working tree or directory.
            runner: Optional custom runner callable matching the run_cmd / run_git signature.
        """
        self.repo_dir = str(repo_dir)
        self._runner = runner

    def run(
        self,
        args: list[str],
        check: bool = True,
        timeout: int | None = 60,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Execute arbitrary git arguments bound to this repository.

        Args:
            args: Git command arguments (excluding the leading 'git').
            check: Whether to raise on non-zero exit code.
            timeout: Command timeout threshold in seconds.
            env: Optional environment dictionary.

        Returns:
            subprocess.CompletedProcess[str]: Process execution outcome.
        """
        if self._runner is None or self._runner is run_git:
            clean_args = args[1:] if args and args[0] == "git" else args
            return run_git(clean_args, cwd=self.repo_dir, check=check, timeout=timeout, env=env)

        cmd = ["git", *args] if not (args and args[0] == "git") else list(args)
        try:
            return self._runner(cmd, cwd=self.repo_dir, check=check, timeout=timeout, env=env)
        except TypeError:
            return self._runner(cmd, cwd=self.repo_dir, check=check, env=env)

    def rev_parse(
        self,
        *args: str,
        verify: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Execute git rev-parse with specific revision or flag arguments."""
        cmd_args = ["rev-parse"]
        if verify:
            cmd_args.append("--verify")
        if args:
            cmd_args.extend(args)
        else:
            cmd_args.append("HEAD")
        return self.run(cmd_args, check=check)

    def current_branch(self) -> str | None:
        """Query the current branch name, or None if in detached HEAD state."""
        res = self.run(["symbolic-ref", "--short", "HEAD"], check=False)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
        return None

    def status_porcelain(self, untracked: str = "all") -> str:
        """Execute git status --porcelain and return stripped stdout."""
        args = ["status", "--porcelain"]
        if untracked:
            args.append(f"--untracked-files={untracked}")
        res = self.run(args, check=False)
        return res.stdout.strip()

    def is_clean(self) -> bool:
        """Return True when working tree and staging area have no modifications."""
        return not bool(self.status_porcelain())

    def add(
        self,
        paths: list[str] | str | None = None,
        all_files: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Stage specified paths or all changes."""
        args = ["add"]
        if all_files:
            args.append("-A")
        if paths:
            path_list = [paths] if isinstance(paths, str) else list(paths)
            args.extend(path_list)
        return self.run(args, check=check)

    def commit(
        self,
        message: str,
        allow_empty: bool = False,
        author: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Commit staged changes."""
        args = ["commit", "-m", message]
        if allow_empty:
            args.append("--allow-empty")
        if author:
            args.extend(["--author", author])
        return self.run(args, check=check)

    def push(
        self,
        remote: str = "origin",
        branch: str | None = None,
        set_upstream: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Push commits to a remote repository."""
        args = ["push"]
        if set_upstream:
            args.append("-u")
        args.append(remote)
        if branch:
            args.append(branch)
        return self.run(args, check=check)

    def fetch(
        self,
        remote: str = "origin",
        branch: str | None = None,
        *extra: str,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Fetch revisions from a remote repository."""
        args = ["fetch", *extra, remote]
        if branch:
            args.append(branch)
        return self.run(args, check=check)

    def checkout(
        self,
        target: str,
        create: bool = False,
        force: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Switch branches or restore working tree files."""
        args = ["checkout"]
        if force:
            args.append("-f")
        if create:
            args.extend(["-b", target])
        else:
            args.append(target)
        return self.run(args, check=check)

    def merge_base(
        self,
        ref1: str,
        ref2: str,
        is_ancestor: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Find common ancestors or check ancestry between revisions."""
        args = ["merge-base"]
        if is_ancestor:
            args.extend(["--is-ancestor", ref1, ref2])
        else:
            args.extend([ref1, ref2])
        return self.run(args, check=check)

    def merge(
        self,
        target: str,
        no_commit: bool = False,
        no_ff: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Merge a branch or revision into the current branch."""
        args = ["merge", target]
        if no_commit:
            args.append("--no-commit")
        if no_ff:
            args.append("--no-ff")
        return self.run(args, check=check)

    def merge_abort(self, check: bool = False) -> subprocess.CompletedProcess[str]:
        """Abort an in-progress merge operation."""
        return self.run(["merge", "--abort"], check=check)

    def rebase_onto(
        self,
        newbase: str,
        upstream: str,
        branch: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Rebase commits onto a new base revision."""
        args = ["rebase", "--onto", newbase, upstream]
        if branch:
            args.append(branch)
        return self.run(args, check=check)

    def config(
        self,
        key: str,
        value: str | None = None,
        local: bool = True,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Get or set repository configuration."""
        args = ["config"]
        if local:
            args.append("--local")
        args.append(key)
        if value is not None:
            args.append(value)
        return self.run(args, check=check)

    def reset(
        self,
        target: str | None = None,
        *paths: str,
        hard: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Reset current HEAD or unstage paths."""
        args = ["reset"]
        if hard:
            args.append("--hard")
        else:
            args.append("-q")
        if target:
            args.append(target)
        if paths:
            args.extend(paths)
        return self.run(args, check=check)

    def clean(
        self,
        force: bool = True,
        directories: bool = True,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Remove untracked files and directories from the working tree."""
        args = ["clean"]
        flags = ""
        if force:
            flags += "f"
        if directories:
            flags += "d"
        if flags:
            args.append(f"-{flags}")
        return self.run(args, check=check)

    def diff(
        self,
        *args: str,
        cached: bool = False,
        quiet: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Execute git diff with common options."""
        cmd_args = ["diff"]
        if cached:
            cmd_args.append("--cached")
        if quiet:
            cmd_args.append("--quiet")
        cmd_args.extend(args)
        return self.run(cmd_args, check=check)


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
    repo = GitRepository(repo_dir)
    res = repo.diff("--numstat", base_ref, head_ref, check=False)
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
    res = GitRepository(repo_dir).rev_parse(clean_ref, verify=True, check=False)
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


# Paths that every role writes as bookkeeping (ledgers, plan files, execution notes). A diff that only touches
# these carries no implementation work.
EMPTY_DIFF_EXCLUDES: tuple[str, ...] = ("holon-knowledge/ledger/", "plans/", "executions/")
# Ledger vocabulary for an execution rejected by the empty-diff guard.
EMPTY_DIFF_STATUS: str = "failed"
EMPTY_DIFF_FAILURE_REASON: str = "empty_diff"


def empty_diff_summary(base: str) -> str:
    """Return the ledger summary recorded when an execution changes nothing outside the excluded paths.

    Args:
        base: Plan branch (or revision) the execution was compared against.

    Returns:
        str: Human-readable summary for the executions.jsonl row.
    """
    *head, last = EMPTY_DIFF_EXCLUDES
    excluded = f"{', '.join(head)} and {last}" if head else last
    return f"Execution produced no changes outside {excluded} relative to {base}"


def _is_excluded(path: str, exclude: tuple[str, ...]) -> bool:
    """Return True when a repository-relative path falls under one of the excluded prefixes."""
    clean = path.removeprefix("./")
    return any(clean == prefix.rstrip("/") or clean.startswith(prefix) for prefix in exclude)


GitRunner = Callable[[list[str]], str]
_NAME_ONLY_ARGS: tuple[str, ...] = ("--name-only", "--no-renames", "-z")


def changed_paths(
    repo_dir: str,
    base: str,
    head: str | None = None,
    exclude: tuple[str, ...] = EMPTY_DIFF_EXCLUDES,
    git_runner: GitRunner | None = None,
) -> list[str]:
    """List paths changed between a base revision and a head revision (or the working tree).

    With a head revision a path counts only when the net ``base...head`` diff changes it and one of
    head's own first-parent, non-merge commits touched it. The first condition ignores later commits on
    the base branch and edits that were reverted; the second ignores content that arrived through a
    merge, such as the executor's pre-push sync of the target branch. Without a head revision the
    working tree is compared with base, which covers commits made on top of base, staged and unstaged
    edits, and untracked files that are not ignored. Rename detection is disabled so a move into an
    excluded directory still reports the removed source path.

    Args:
        repo_dir: Path to Git repository.
        base: Base revision (e.g. the plan branch or its tip SHA).
        head: Head revision to compare, or None to compare the working tree.
        exclude: Repository-relative path prefixes to ignore (default: ledgers, plans and execution notes).
        git_runner: Optional callable that runs a Git command (arguments excluding 'git') in repo_dir and
            returns its stdout, raising on failure. Defaults to run_git; the executor entrypoint passes
            its own runner so the sandbox's safe.directory and environment handling apply.

    Returns:
        list[str]: Sorted, de-duplicated repository-relative paths outside the excluded prefixes.

    Raises:
        RuntimeError: If any Git command fails (e.g. unknown revision), so callers fail closed.
    """

    def _default_runner(args: list[str]) -> str:
        return GitRepository(repo_dir).run(args).stdout

    git = git_runner or _default_runner
    if head:
        net = set(git(["diff", *_NAME_ONLY_ARGS, f"{base}...{head}"]).split("\0"))
        own_log = git(["log", "--first-parent", "--no-merges", *_NAME_ONLY_ARGS, "--format=", f"{base}..{head}"])
        own = {entry.strip("\n") for entry in own_log.split("\0")}
        raw = list(net & own)
    else:
        raw = git(["diff", *_NAME_ONLY_ARGS, base, "--"]).split("\0")
        raw += git(["ls-files", "--others", "--exclude-standard", "-z"]).split("\0")
    return sorted({p for p in raw if p and not _is_excluded(p, exclude)})


def has_effective_diff(
    repo_dir: str,
    base: str,
    head: str | None = None,
    exclude: tuple[str, ...] = EMPTY_DIFF_EXCLUDES,
    git_runner: GitRunner | None = None,
) -> bool:
    """Return True when head (or the working tree) changes anything outside the excluded prefixes.

    Args:
        repo_dir: Path to Git repository.
        base: Base revision (e.g. the plan branch or its tip SHA).
        head: Head revision to compare, or None to compare the working tree.
        exclude: Repository-relative path prefixes to ignore (default: ledgers, plans and execution notes).
        git_runner: Optional Git command runner (see changed_paths).

    Returns:
        bool: True if at least one effective path changed.

    Raises:
        RuntimeError: If the diff cannot be computed; callers must treat this as an empty execution.
    """
    return bool(changed_paths(repo_dir, base, head=head, exclude=exclude, git_runner=git_runner))
