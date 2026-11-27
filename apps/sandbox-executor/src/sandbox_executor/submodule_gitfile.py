from __future__ import annotations

import configparser
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from sandbox_executor.git import run_git

GitRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class SubmoduleGitfileIssue:
    name: str
    path: str
    gitfile_rel: str
    gitdir_raw: str
    reason: str


def _read_git_config(path: str) -> configparser.ConfigParser | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            # why: git config files indent keys with tabs, which configparser treats as continuations
            normalized = "\n".join(line.lstrip() for line in handle.read().splitlines())
    except OSError:
        return None
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(normalized)
    except configparser.Error:
        return None
    return parser


def parse_gitmodules(repo_dir: str) -> list[tuple[str, str]]:
    parser = _read_git_config(os.path.join(repo_dir, ".gitmodules"))
    if parser is None:
        return []
    entries: list[tuple[str, str]] = []
    prefix = 'submodule "'
    for section in parser.sections():
        cleaned = section.strip()
        if not (cleaned.startswith(prefix) and cleaned.endswith('"')):
            continue
        name = cleaned[len(prefix) : -1].strip()
        rel_path = parser.get(section, "path", fallback="").strip()
        if name and rel_path:
            entries.append((name, rel_path))
    return entries


def _read_gitdir_pointer(gitfile: str) -> str | None:
    try:
        with open(gitfile, encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.lower().startswith("gitdir:"):
                    value = stripped.split(":", 1)[1].strip()
                    return value or None
                return None
    except OSError:
        return None
    return None


def _resolve_gitdir_target(sub_dir: str, raw: str) -> str:
    if os.path.isabs(raw):
        return raw
    return os.path.normpath(os.path.join(sub_dir, raw))


def _find_invalid_worktree(target: str, sub_dir: str) -> str | None:
    parser = _read_git_config(os.path.join(target, "config"))
    if parser is None or not parser.has_option("core", "worktree"):
        return None
    worktree = parser.get("core", "worktree").strip()
    if not worktree:
        return None
    if os.path.isabs(worktree):
        return worktree
    resolved = os.path.realpath(os.path.join(target, worktree))
    if resolved != os.path.realpath(sub_dir):
        return worktree
    return None


def _inspect_submodule(repo_dir: str, name: str, rel_path: str) -> SubmoduleGitfileIssue | None:
    sub_dir = os.path.join(repo_dir, rel_path)
    gitfile = os.path.join(sub_dir, ".git")
    if not os.path.isfile(gitfile):
        return None
    gitfile_rel = f"{rel_path}/.git"
    raw = _read_gitdir_pointer(gitfile)
    if raw is None:
        return SubmoduleGitfileIssue(name, rel_path, gitfile_rel, "", "missing gitdir pointer")
    target = _resolve_gitdir_target(sub_dir, raw)
    if not os.path.isdir(target):
        return SubmoduleGitfileIssue(name, rel_path, gitfile_rel, raw, f"unresolvable gitdir '{raw}'")
    if os.path.isabs(raw):
        return SubmoduleGitfileIssue(name, rel_path, gitfile_rel, raw, f"absolute gitdir '{raw}'")
    invalid_worktree = _find_invalid_worktree(target, sub_dir)
    if invalid_worktree is not None:
        return SubmoduleGitfileIssue(name, rel_path, gitfile_rel, raw, f"invalid core.worktree '{invalid_worktree}'")
    return None


def find_submodule_gitfile_issues(repo_dir: str) -> list[SubmoduleGitfileIssue]:
    issues: list[SubmoduleGitfileIssue] = []
    for name, rel_path in parse_gitmodules(repo_dir):
        issue = _inspect_submodule(repo_dir, name, rel_path)
        if issue is not None:
            issues.append(issue)
    return issues


def _is_valid_module_gitdir(candidate: str) -> bool:
    if not os.path.isdir(candidate):
        return False
    return os.path.isfile(os.path.join(candidate, "HEAD")) or os.path.isfile(os.path.join(candidate, "config"))


def _resolve_repo_relative(repo_dir: str, raw_path: str) -> str:
    if os.path.isabs(raw_path):
        return os.path.normpath(raw_path)
    return os.path.normpath(os.path.join(repo_dir, raw_path))


def _find_candidate_module_gitdir(repo_dir: str, name: str, run: GitRunner) -> str | None:
    candidates: list[str] = []
    git_path_res = run(["rev-parse", "--git-path", f"modules/{name}"], cwd=repo_dir, check=False)
    if git_path_res.returncode == 0 and git_path_res.stdout.strip():
        candidates.append(_resolve_repo_relative(repo_dir, git_path_res.stdout.strip()))
    common_res = run(["rev-parse", "--git-common-dir"], cwd=repo_dir, check=False)
    if common_res.returncode == 0 and common_res.stdout.strip():
        common_dir = _resolve_repo_relative(repo_dir, common_res.stdout.strip())
        candidates.append(os.path.join(common_dir, "modules", name))
    for candidate in candidates:
        if _is_valid_module_gitdir(candidate):
            return candidate
    return None


def _rewrite_submodule_links(repo_dir: str, issue: SubmoduleGitfileIssue, candidate: str, run: GitRunner) -> None:
    sub_dir = os.path.join(repo_dir, issue.path)
    rel_gitdir = os.path.relpath(candidate, start=sub_dir).replace(os.sep, "/")
    with open(os.path.join(sub_dir, ".git"), "w", encoding="utf-8") as handle:
        handle.write(f"gitdir: {rel_gitdir}\n")
    rel_worktree = os.path.relpath(sub_dir, start=candidate).replace(os.sep, "/")
    config_path = os.path.join(candidate, "config")
    run(["config", "--file", config_path, "core.worktree", rel_worktree], cwd=repo_dir, check=False)


def repair_submodule_gitfiles(
    repo_dir: str,
    run: GitRunner | None = None,
) -> tuple[list[str], list[SubmoduleGitfileIssue]]:
    issues = find_submodule_gitfile_issues(repo_dir)
    if not issues:
        return [], []
    git_run = run or run_git
    for issue in issues:
        if not issue.gitdir_raw:
            continue
        target = _resolve_gitdir_target(os.path.join(repo_dir, issue.path), issue.gitdir_raw)
        if os.path.isdir(target):
            git_run(["submodule", "absorbgitdirs", "--", issue.path], cwd=repo_dir, check=False)
    repaired: list[str] = []
    for issue in find_submodule_gitfile_issues(repo_dir):
        candidate = _find_candidate_module_gitdir(repo_dir, issue.name, git_run)
        if candidate is not None:
            _rewrite_submodule_links(repo_dir, issue, candidate, git_run)
            repaired.append(issue.gitfile_rel)
    remaining = find_submodule_gitfile_issues(repo_dir)
    remaining_rels = {issue.gitfile_rel for issue in remaining}
    for issue in issues:
        if issue.gitfile_rel not in remaining_rels and issue.gitfile_rel not in repaired:
            repaired.append(issue.gitfile_rel)
    return repaired, remaining


def format_submodule_gitfile_error(issues: list[SubmoduleGitfileIssue]) -> str:
    details = "; ".join(f"{issue.gitfile_rel} ({issue.reason})" for issue in issues)
    return f"invalid submodule gitfile(s): {details}"
