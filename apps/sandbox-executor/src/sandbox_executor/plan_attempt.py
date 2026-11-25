from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
from collections.abc import Callable

from sandbox_executor.plan_paths import PLAN_FILE_PATTERN, PLANS_DIR, bare_branch, is_calibration_report_path

PLAN_FAILURE_DETAIL_REL = ".holon/plan/failure.txt"
FAILURE_DETAIL_MAX_CHARS = 2000
_UNTRACKED_PORCELAIN_PREFIX = "?? "

Runner = Callable[..., subprocess.CompletedProcess]


def write_failure_detail(repo_dir: str, text: str) -> None:
    path = os.path.join(repo_dir, PLAN_FAILURE_DETAIL_REL)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.strip()[:FAILURE_DETAIL_MAX_CHARS])
    except OSError as exc:
        print(f"Warning: could not write the plan failure detail {PLAN_FAILURE_DETAIL_REL}: {exc}", file=sys.stderr)


def read_failure_detail(repo_dir: str) -> str | None:
    try:
        with open(os.path.join(repo_dir, PLAN_FAILURE_DETAIL_REL), encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return None
    return text[:FAILURE_DETAIL_MAX_CHARS] or None


def clear_failure_detail(repo_dir: str) -> None:
    with contextlib.suppress(OSError):
        os.remove(os.path.join(repo_dir, PLAN_FAILURE_DETAIL_REL))


def _untracked_paths(repo_dir: str, pathspec: str, run: Runner) -> list[str]:
    result = run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=all", "--", pathspec],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not isinstance(result.stdout, str):
        return []
    return [
        entry.removeprefix(_UNTRACKED_PORCELAIN_PREFIX)
        for entry in result.stdout.split("\0")
        if entry.startswith(_UNTRACKED_PORCELAIN_PREFIX)
    ]


def _remove(repo_dir: str, rel_path: str) -> bool:
    try:
        os.remove(os.path.join(repo_dir, rel_path))
    except OSError as exc:
        print(f"Warning: could not remove the failed attempt's plan file {rel_path}: {exc}", file=sys.stderr)
        return False
    print(f"Removed the failed attempt's untracked plan file {rel_path}.", file=sys.stderr)
    return True


def discard_untracked_plan_file(repo_dir: str, plan_rel: str, run: Runner = subprocess.run) -> bool:
    if plan_rel not in _untracked_paths(repo_dir, plan_rel, run):
        return False
    return _remove(repo_dir, plan_rel)


def intent_plans_dir(intent_branch: str) -> str:
    return f"{PLANS_DIR}/{bare_branch(intent_branch)}"


def untracked_plan_files(repo_dir: str, plans_dir_rel: str, run: Runner = subprocess.run) -> set[str]:
    return {
        path
        for path in _untracked_paths(repo_dir, plans_dir_rel, run)
        if re.fullmatch(PLAN_FILE_PATTERN, path) and not is_calibration_report_path(path)
    }


def snapshot_plan_attempt(repo_dir: str, intent_branch: str, run: Runner = subprocess.run) -> set[str]:
    clear_failure_detail(repo_dir)
    return untracked_plan_files(repo_dir, intent_plans_dir(intent_branch), run)


def settle_failed_plan_attempt(
    repo_dir: str, intent_branch: str, before: set[str], run: Runner = subprocess.run
) -> str | None:
    for path in sorted(untracked_plan_files(repo_dir, intent_plans_dir(intent_branch), run) - before):
        _remove(repo_dir, path)
    detail = read_failure_detail(repo_dir)
    clear_failure_detail(repo_dir)
    return detail
