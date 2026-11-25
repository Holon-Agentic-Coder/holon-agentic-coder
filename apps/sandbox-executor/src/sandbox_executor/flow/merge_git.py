"""Small git helpers shared by the target sync merge and the conflict resolver guard, so their rules cannot drift."""

from __future__ import annotations

from pathlib import Path

from sandbox_executor.git import run_git

_MARKER_PREFIXES = ("<<<<<<< ", ">>>>>>> ")


def git_out(worktree: str, *args: str) -> str:
    """The stdout of ``git args`` in ``worktree``, or ``""`` when git fails."""
    res = run_git(list(args), cwd=worktree, check=False)
    return res.stdout if res.returncode == 0 and isinstance(res.stdout, str) else ""


def has_conflict_markers(path: Path) -> bool:
    """Whether ``path`` still holds a conflict marker line (an unreadable file counts as clean)."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    return any(line.startswith(_MARKER_PREFIXES) for line in lines)
