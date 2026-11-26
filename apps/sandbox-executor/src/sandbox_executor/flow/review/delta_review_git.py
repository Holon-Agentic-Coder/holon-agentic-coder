"""Git reads behind scoped delta review: ancestry, merges, the reviewable delta and post-change file content.

Every read is over the reviewable pathspecs of ``approval_fingerprint`` (the bookkeeping files excluded) and uses the
two-dot range ``base..head``, which is only ever called with ``base`` an ancestor of ``head``. A git failure raises
``RuntimeError`` (or ``TimeoutError``), which the mode decision turns into a full review.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sandbox_executor.flow.review.approval_fingerprint import FINGERPRINT_EXCLUDED_PATHSPECS, reviewable_pathspecs
from sandbox_executor.git import run_git

_GIT_TIMEOUT_SECONDS = 60


def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _pathspecs(paths: Sequence[str] | None = None) -> list[str]:
    """The reviewable pathspecs, restricted to ``paths`` (taken literally) when given."""
    if not paths:
        return list(reviewable_pathspecs())
    return ["--", *(f":(literal){path}" for path in paths), *FINGERPRINT_EXCLUDED_PATHSPECS]


@dataclass(frozen=True)
class DeltaRepo:
    """The worktree whose delta ``base..head`` a scoped pass reviews."""

    repo_dir: str

    def _git(self, args: list[str]) -> str:
        """The stdout of ``git <args>``; a non-zero exit or undecodable output raises ``RuntimeError``."""
        try:
            return run_git(args, cwd=self.repo_dir, check=True, timeout=_GIT_TIMEOUT_SECONDS).stdout or ""
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"git {args[0]} printed undecodable output") from exc

    def is_ancestor(self, base: str, head: str) -> bool:
        """True when ``base`` is an ancestor of ``head``; an unknown commit raises ``RuntimeError``."""
        args = ["merge-base", "--is-ancestor", base, head]
        proc = run_git(args, cwd=self.repo_dir, check=False, timeout=_GIT_TIMEOUT_SECONDS)
        if proc.returncode not in (0, 1):
            raise RuntimeError(f"git merge-base --is-ancestor exited with code {proc.returncode}")
        return proc.returncode == 0

    def merge_commits(self, base: str, head: str) -> list[str]:
        """The merge commits in ``base..head``."""
        return _lines(self._git(["rev-list", "--merges", f"{base}..{head}"]))

    def changed_paths(self, base: str, head: str, paths: Sequence[str] | None = None) -> list[str]:
        """The reviewable paths ``base..head`` changed, restricted to ``paths`` when given."""
        return _lines(self._git(["diff", "--name-only", "--no-renames", base, head, *_pathspecs(paths)]))

    def own_commit_paths(self, base: str, head: str) -> list[str]:
        """The reviewable paths changed by the PR branch's own (first-parent, non-merge) commits of ``base..head``."""
        log = ["log", "--no-merges", "--first-parent", "--name-only", "--format="]
        return sorted(set(_lines(self._git([*log, f"{base}..{head}", *_pathspecs()]))))

    def changed_line_count(self, base: str, head: str, paths: Sequence[str]) -> int | None:
        """Added plus deleted lines of ``base..head`` over ``paths``; None when a binary change has no line count."""
        total = 0
        for line in _lines(self._git(["diff", "--numstat", "--no-renames", base, head, *_pathspecs(paths)])):
            added, deleted, _path = line.split("\t", 2)
            if not (added.isdigit() and deleted.isdigit()):
                return None
            total += int(added) + int(deleted)
        return total

    def diff(self, base: str, head: str, paths: Sequence[str]) -> str:
        """The unified diff ``base..head`` over ``paths``."""
        return self._git(["diff", "--no-renames", base, head, *_pathspecs(paths)])

    def file_at(self, head: str, path: str) -> str | None:
        """The text of ``path`` at ``head``; None when it no longer exists there or is binary."""
        try:
            proc = run_git(["show", f"{head}:{path}"], cwd=self.repo_dir, check=False, timeout=_GIT_TIMEOUT_SECONDS)
        except UnicodeDecodeError:
            return None
        text = proc.stdout or ""
        return None if proc.returncode != 0 or "\0" in text else text
