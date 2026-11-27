from __future__ import annotations

import dataclasses
import os
import posixpath
from pathlib import Path

from sandbox_executor.git import GitRunnerCallable, run_git
from sandbox_executor.redaction import redact_agent_secrets


@dataclasses.dataclass(frozen=True)
class SubmoduleRepairResult:
    repaired: tuple[str, ...] = ()
    error: str | None = None


def _to_posix_relpath(target: str, start: str) -> str:
    return Path(os.path.relpath(target, start)).as_posix()


def _gitfile_relpath(rel_path: str) -> str:
    return posixpath.join(rel_path.replace("\\", "/"), ".git")


def _list_submodules(repo_dir: str, runner: GitRunnerCallable) -> list[tuple[str, str]]:
    gitmodules = os.path.join(repo_dir, ".gitmodules")
    if not os.path.isfile(gitmodules):
        return []
    res = runner(
        ["config", "--file", gitmodules, "--get-regexp", r"^submodule\..*\.path$"],
        cwd=repo_dir,
        check=False,
    )
    if res.returncode != 0:
        return []
    submodules: list[tuple[str, str]] = []
    for raw_line in res.stdout.splitlines():
        key, _, rel_path = raw_line.strip().partition(" ")
        if not key.startswith("submodule.") or not key.endswith(".path") or not rel_path.strip():
            continue
        name = key.removeprefix("submodule.").removesuffix(".path")
        submodules.append((name, rel_path.strip()))
    return submodules


def _read_gitdir_pointer(gitfile_path: str) -> str | None:
    raw = Path(gitfile_path).read_text(encoding="utf-8").strip()
    if not raw.startswith("gitdir:"):
        return None
    target = raw.removeprefix("gitdir:").strip()
    return target or None


def _read_module_worktree(module_git_dir: str, runner: GitRunnerCallable) -> str | None:
    config_path = os.path.join(module_git_dir, "config")
    if not os.path.isfile(config_path):
        return None
    res = runner(
        ["config", "--file", config_path, "--get", "core.worktree"],
        cwd=module_git_dir,
        check=False,
    )
    if res.returncode != 0:
        return None
    value = res.stdout.strip()
    return value or None


def _is_absolute_git_path(path: str) -> bool:
    return os.path.isabs(path) or posixpath.isabs(path)


def _is_worktree_valid(
    module_git_dir: str,
    submodule_dir: str,
    worktree: str | None,
    require_relative: bool,
) -> bool:
    if worktree is None:
        return True
    if require_relative and _is_absolute_git_path(worktree):
        return False
    resolved = worktree if os.path.isabs(worktree) else os.path.join(module_git_dir, worktree)
    if not os.path.isdir(resolved):
        return False
    return os.path.realpath(resolved) == os.path.realpath(submodule_dir)


def _inspect_submodule(
    repo_dir: str,
    rel_path: str,
    require_relative: bool,
    runner: GitRunnerCallable,
) -> str | None:
    submodule_dir = os.path.join(repo_dir, rel_path)
    gitfile_path = os.path.join(submodule_dir, ".git")
    if not os.path.isfile(gitfile_path):
        return None
    try:
        gitdir_raw = _read_gitdir_pointer(gitfile_path)
    except OSError as exc:
        return f"unreadable gitfile: {redact_agent_secrets(str(exc))}"
    if gitdir_raw is None:
        return "malformed gitdir pointer"
    if require_relative and _is_absolute_git_path(gitdir_raw):
        return "non-relative gitdir path"
    resolved_gitdir = (
        gitdir_raw if os.path.isabs(gitdir_raw) else os.path.normpath(os.path.join(submodule_dir, gitdir_raw))
    )
    if not os.path.isdir(resolved_gitdir):
        return f"unresolvable gitdir '{gitdir_raw}'"
    worktree = _read_module_worktree(resolved_gitdir, runner)
    if not _is_worktree_valid(resolved_gitdir, submodule_dir, worktree, require_relative):
        return "invalid core.worktree"
    return None


def _resolve_module_gitdir(repo_dir: str, name: str, runner: GitRunnerCallable) -> str | None:
    res = runner(["rev-parse", "--git-path", f"modules/{name}"], cwd=repo_dir, check=False)
    if res.returncode == 0 and res.stdout.strip():
        candidate = res.stdout.strip()
        full = candidate if os.path.isabs(candidate) else os.path.join(repo_dir, candidate)
        if os.path.isdir(full):
            return os.path.realpath(full)
    common = runner(["rev-parse", "--git-common-dir"], cwd=repo_dir, check=False)
    if common.returncode == 0 and common.stdout.strip():
        common_dir = common.stdout.strip()
        common_full = common_dir if os.path.isabs(common_dir) else os.path.join(repo_dir, common_dir)
        fallback = os.path.join(common_full, "modules", name)
        if os.path.isdir(fallback):
            return os.path.realpath(fallback)
    return None


def _rewrite_submodule_gitfile(
    repo_dir: str,
    name: str,
    rel_path: str,
    runner: GitRunnerCallable,
) -> str | None:
    gitfile_rel = _gitfile_relpath(rel_path)
    module_git_dir = _resolve_module_gitdir(repo_dir, name, runner)
    if module_git_dir is None:
        return f"submodule gitfile '{gitfile_rel}' is broken and module gitdir 'modules/{name}' could not be resolved"
    submodule_dir = os.path.realpath(os.path.join(repo_dir, rel_path))
    gitfile_path = os.path.join(submodule_dir, ".git")
    rel_gitdir = _to_posix_relpath(module_git_dir, submodule_dir)
    rel_worktree = _to_posix_relpath(submodule_dir, module_git_dir)
    try:
        Path(gitfile_path).write_text(f"gitdir: {rel_gitdir}\n", encoding="utf-8")
    except OSError as exc:
        return f"failed to rewrite submodule gitfile '{gitfile_rel}': {redact_agent_secrets(str(exc))}"
    config_path = os.path.join(module_git_dir, "config")
    cfg_res = runner(
        ["config", "--file", config_path, "core.worktree", rel_worktree],
        cwd=repo_dir,
        check=False,
    )
    if cfg_res.returncode != 0:
        detail = redact_agent_secrets((cfg_res.stderr or cfg_res.stdout).strip()) or f"exit code {cfg_res.returncode}"
        return f"failed to update core.worktree for submodule gitfile '{gitfile_rel}': {detail}"
    return None


def find_broken_submodule_gitfiles(
    repo_dir: str,
    *,
    require_relative: bool = False,
    run: GitRunnerCallable | None = None,
) -> list[str]:
    runner = run or run_git
    broken: list[str] = []
    for _name, rel_path in _list_submodules(repo_dir, runner):
        if _inspect_submodule(repo_dir, rel_path, require_relative, runner) is not None:
            broken.append(_gitfile_relpath(rel_path))
    return broken


def repair_submodule_gitfiles(
    repo_dir: str,
    *,
    require_relative: bool = False,
    run: GitRunnerCallable | None = None,
) -> SubmoduleRepairResult:
    runner = run or run_git
    repaired: list[str] = []
    for name, rel_path in _list_submodules(repo_dir, runner):
        if _inspect_submodule(repo_dir, rel_path, require_relative, runner) is None:
            continue
        err = _rewrite_submodule_gitfile(repo_dir, name, rel_path, runner)
        if err is not None:
            return SubmoduleRepairResult(repaired=tuple(repaired), error=err)
        repaired.append(_gitfile_relpath(rel_path))
    return SubmoduleRepairResult(repaired=tuple(repaired), error=None)
