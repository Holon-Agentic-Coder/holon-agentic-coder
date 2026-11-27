from __future__ import annotations

from pathlib import Path

from sandbox_executor.git import run_git

_MARKER_PREFIXES = ("<<<<<<< ", ">>>>>>> ")


def git_out(worktree: str, *args: str) -> str:
    res = run_git(list(args), cwd=worktree, check=False)
    return res.stdout if res.returncode == 0 and isinstance(res.stdout, str) else ""


def has_conflict_markers(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    return any(line.startswith(_MARKER_PREFIXES) for line in lines)
