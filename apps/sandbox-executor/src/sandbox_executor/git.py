from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from sandbox_executor.redaction import redact_agent_secrets, redact_command

logger = logging.getLogger(__name__)

GitRunnerCallable = Callable[..., subprocess.CompletedProcess[str]]

_BINARY_DIFF_COUNT = -1


def run_git(
    args: list[str],
    cwd: str | None = None,
    check: bool = True,
    timeout: int | None = 60,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
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
    def __init__(
        self,
        repo_dir: str | Path,
        runner: GitRunnerCallable | None = None,
    ) -> None:
        self.repo_dir = str(repo_dir)
        self._runner = runner

    def run(
        self,
        args: list[str],
        check: bool = True,
        timeout: int | None = 60,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
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
        cmd_args = ["rev-parse"]
        if verify:
            cmd_args.append("--verify")
        if args:
            cmd_args.extend(args)
        else:
            cmd_args.append("HEAD")
        return self.run(cmd_args, check=check)

    def current_branch(self) -> str | None:
        res = self.run(["symbolic-ref", "--short", "HEAD"], check=False)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
        return None

    def status_porcelain(self, untracked: str = "all") -> str:
        args = ["status", "--porcelain"]
        if untracked:
            args.append(f"--untracked-files={untracked}")
        res = self.run(args, check=False)
        return res.stdout.strip()

    def is_clean(self) -> bool:
        return not bool(self.status_porcelain())

    def add(
        self,
        paths: list[str] | str | None = None,
        all_files: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
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
        args = ["merge", target]
        if no_commit:
            args.append("--no-commit")
        if no_ff:
            args.append("--no-ff")
        return self.run(args, check=check)

    def merge_abort(self, check: bool = False) -> subprocess.CompletedProcess[str]:
        return self.run(["merge", "--abort"], check=check)

    def rebase_onto(
        self,
        newbase: str,
        upstream: str,
        branch: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
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
        cmd_args = ["diff"]
        if cached:
            cmd_args.append("--cached")
        if quiet:
            cmd_args.append("--quiet")
        cmd_args.extend(args)
        return self.run(cmd_args, check=check)


def _parse_numstat_counts(raw_insertions: str, raw_deletions: str) -> tuple[int, int]:
    try:
        return int(raw_insertions), int(raw_deletions)
    except ValueError:
        return _BINARY_DIFF_COUNT, _BINARY_DIFF_COUNT


def parse_numstat(text: str) -> list[tuple[int, int, str]]:
    entries: list[tuple[int, int, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        raw_ins, raw_del, file_path = parts
        ins, dels = _parse_numstat_counts(raw_ins, raw_del)
        entries.append((ins, dels, file_path.strip()))
    return entries


def get_diff_numstat(repo_dir: str, base_ref: str, head_ref: str) -> list[tuple[int, int, str]]:
    res = GitRepository(repo_dir).diff("--numstat", base_ref, head_ref, check=False)
    if res.returncode != 0:
        logger.debug(f"get_diff_numstat failed between {base_ref} and {head_ref}: {res.stderr}")
        return []
    return parse_numstat(res.stdout)


def resolve_ref(repo_dir: str, ref: str) -> str | None:
    if not ref or not ref.strip():
        return None

    clean_ref = ref.strip()
    res = GitRepository(repo_dir).rev_parse(clean_ref, verify=True, check=False)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    return None


def get_head_commit(repo_dir: str) -> str | None:
    return resolve_ref(repo_dir, "HEAD")


def branch_exists(repo_dir: str, branch_name: str, check_remote: bool = True) -> bool:
    clean_name = branch_name.strip()
    if not clean_name:
        return False

    if resolve_ref(repo_dir, f"refs/heads/{clean_name}") is not None:
        return True

    if resolve_ref(repo_dir, clean_name) is not None:
        return True

    if check_remote:
        remote_ref = f"origin/{clean_name}" if not clean_name.startswith("origin/") else clean_name
        if resolve_ref(repo_dir, f"refs/remotes/{remote_ref}") is not None:
            return True
        if resolve_ref(repo_dir, remote_ref) is not None:
            return True

    return False


EMPTY_DIFF_EXCLUDES: tuple[str, ...] = ("holon-knowledge/ledger/", "plans/", "executions/")
EMPTY_DIFF_STATUS: str = "failed"
EMPTY_DIFF_FAILURE_REASON: str = "empty_diff"

METADATA_PATH_PREFIXES: tuple[str, ...] = (
    *EMPTY_DIFF_EXCLUDES,
    "holon-knowledge/",
    ".holon/",
)


def is_metadata_path(path: str) -> bool:
    clean = path.strip()
    if clean.startswith("./"):
        clean = clean[2:]
    clean = clean.lstrip("/")
    if " => " in clean:
        clean = clean.split(" => ")[-1].strip().rstrip("}").strip()
    return any(clean.startswith(prefix) for prefix in METADATA_PATH_PREFIXES)


def empty_diff_summary(base: str) -> str:
    *head, last = EMPTY_DIFF_EXCLUDES
    excluded = f"{', '.join(head)} and {last}" if head else last
    return f"Execution produced no changes outside {excluded} relative to {base}"


def _is_excluded(path: str, exclude: tuple[str, ...]) -> bool:
    clean = path.removeprefix("./")
    return any(clean == prefix.rstrip("/") or clean.startswith(prefix) for prefix in exclude)


GitRunner = Callable[[list[str]], str]
_NAME_ONLY_ARGS: tuple[str, ...] = ("--name-only", "--no-renames", "-z")


def _repo_stdout_runner(repo_dir: str) -> GitRunner:
    def _run(args: list[str]) -> str:
        return GitRepository(repo_dir).run(args).stdout

    return _run


def changed_paths(
    repo_dir: str,
    base: str,
    head: str | None = None,
    exclude: tuple[str, ...] = EMPTY_DIFF_EXCLUDES,
    git_runner: GitRunner | None = None,
) -> list[str]:
    git = git_runner or _repo_stdout_runner(repo_dir)
    if head:
        net_changed = set(git(["diff", *_NAME_ONLY_ARGS, f"{base}...{head}"]).split("\0"))
        own_log = git(["log", "--first-parent", "--no-merges", *_NAME_ONLY_ARGS, "--format=", f"{base}..{head}"])
        first_parent_own_paths = {entry.strip("\n") for entry in own_log.split("\0")}
        raw = list(net_changed & first_parent_own_paths)
    else:
        working_tree_changed = git(["diff", *_NAME_ONLY_ARGS, base, "--"]).split("\0")
        untracked_paths = git(["ls-files", "--others", "--exclude-standard", "-z"]).split("\0")
        raw = working_tree_changed + untracked_paths
    return sorted({p for p in raw if p and not _is_excluded(p, exclude)})


def net_changed_paths(
    repo_dir: str,
    base: str,
    head: str,
    exclude: tuple[str, ...] = EMPTY_DIFF_EXCLUDES,
    git_runner: GitRunner | None = None,
) -> list[str]:
    git = git_runner or _repo_stdout_runner(repo_dir)
    raw = git(["diff", *_NAME_ONLY_ARGS, base, head]).split("\0")
    return sorted({p for p in raw if p and not _is_excluded(p, exclude)})


def has_effective_diff(
    repo_dir: str,
    base: str,
    head: str | None = None,
    exclude: tuple[str, ...] = EMPTY_DIFF_EXCLUDES,
    git_runner: GitRunner | None = None,
) -> bool:
    return bool(changed_paths(repo_dir, base, head=head, exclude=exclude, git_runner=git_runner))
