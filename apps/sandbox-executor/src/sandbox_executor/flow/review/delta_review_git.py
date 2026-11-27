from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sandbox_executor.flow.review.approval_fingerprint import FINGERPRINT_EXCLUDED_PATHSPECS, reviewable_pathspecs
from sandbox_executor.git import parse_numstat, run_git

_GIT_TIMEOUT_SECONDS = 60


def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _pathspecs(paths: Sequence[str] | None = None) -> list[str]:
    if not paths:
        return list(reviewable_pathspecs())
    return ["--", *(f":(literal){path}" for path in paths), *FINGERPRINT_EXCLUDED_PATHSPECS]


@dataclass(frozen=True)
class DeltaRepo:
    repo_dir: str

    def _git(self, args: list[str]) -> str:
        try:
            return run_git(args, cwd=self.repo_dir, check=True, timeout=_GIT_TIMEOUT_SECONDS).stdout or ""
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"git {args[0]} printed undecodable output") from exc

    def is_ancestor(self, base: str, head: str) -> bool:
        args = ["merge-base", "--is-ancestor", base, head]
        proc = run_git(args, cwd=self.repo_dir, check=False, timeout=_GIT_TIMEOUT_SECONDS)
        if proc.returncode not in (0, 1):
            raise RuntimeError(f"git merge-base --is-ancestor exited with code {proc.returncode}")
        return proc.returncode == 0

    def merge_commits(self, base: str, head: str) -> list[str]:
        return _lines(self._git(["rev-list", "--merges", f"{base}..{head}"]))

    def changed_paths(self, base: str, head: str, paths: Sequence[str] | None = None) -> list[str]:
        return _lines(self._git(["diff", "--name-only", "--no-renames", base, head, *_pathspecs(paths)]))

    def own_commit_paths(self, base: str, head: str) -> list[str]:
        log = ["log", "--no-merges", "--first-parent", "--name-only", "--format="]
        return sorted(set(_lines(self._git([*log, f"{base}..{head}", *_pathspecs()]))))

    def changed_line_count(self, base: str, head: str, paths: Sequence[str]) -> int | None:
        total = 0
        raw = self._git(["diff", "--numstat", "--no-renames", base, head, *_pathspecs(paths)])
        for added, deleted, _path in parse_numstat(raw):
            if added < 0 or deleted < 0:
                return None
            total += added + deleted
        return total

    def diff(self, base: str, head: str, paths: Sequence[str]) -> str:
        return self._git(["diff", "--no-renames", base, head, *_pathspecs(paths)])

    def file_at(self, head: str, path: str) -> str | None:
        try:
            proc = run_git(["show", f"{head}:{path}"], cwd=self.repo_dir, check=False, timeout=_GIT_TIMEOUT_SECONDS)
        except UnicodeDecodeError:
            return None
        text = proc.stdout or ""
        return None if proc.returncode != 0 or "\0" in text else text
