"""Local git reads for the macOS relevance gate: the changed files of one commit against its first parent, and blobs.

On a ``pull_request`` run the checkout is the merge commit ``refs/pull/N/merge``; its first parent is the base branch,
so ``git diff HEAD^1 HEAD`` is exactly what the pull request changes. A squash-merged pull request on ``main`` is judged
the same way against its only parent, which is how the replay reuses this code. Every call goes through one
``GitRunner`` (arguments in, stdout bytes out, raising on a non-zero exit) so tests can use a temporary repository.
Standard library only and Python 3.9 syntax.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

GIT_TIMEOUT_SECONDS = 60
NEW, OLD = "new", "old"
# Status letters of ``git diff --name-status`` that carry a second (old) path.
TWO_PATH_STATUSES = ("R", "C")
GitRunner = Callable[[Sequence[str]], bytes]


def default_runner(args: Sequence[str]) -> bytes:
    """Run ``git`` with ``args`` in the current directory and return its stdout; a failure raises."""
    completed = subprocess.run(
        ["git", *args], check=True, capture_output=True, timeout=GIT_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL
    )
    return completed.stdout


@dataclass(frozen=True)
class ChangedFile:
    """One changed file: the status letter, its path and, for a rename or copy, the path it came from."""

    status: str
    path: str
    old_path: str = ""

    def sides(self) -> tuple[tuple[str, str], ...]:
        """Return the ``(side, path)`` pairs that exist: no old side for an addition, no new side for a deletion."""
        if self.status == "A":
            return ((NEW, self.path),)
        if self.status == "D":
            return ((OLD, self.path),)
        return ((NEW, self.path), (OLD, self.old_path or self.path))


def parse_name_status(output: bytes) -> list[ChangedFile]:
    """Parse ``git diff --name-status -z`` output field by field; a truncated record raises ``ValueError``."""
    fields = output.decode("utf-8", errors="surrogateescape").split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    changes: list[ChangedFile] = []
    index = 0
    while index < len(fields):
        status = fields[index][:1]
        if status in TWO_PATH_STATUSES:
            if index + 2 >= len(fields):
                raise ValueError("truncated rename or copy record")
            changes.append(ChangedFile(status, fields[index + 2], fields[index + 1]))
            index += 3
        else:
            if not status or index + 1 >= len(fields):
                raise ValueError("truncated status record")
            changes.append(ChangedFile(status, fields[index + 1]))
            index += 2
    return changes


def changed_files(runner: GitRunner, base: str, head: str) -> list[ChangedFile]:
    """Return the files that ``head`` changes against ``base``, renames detected."""
    return parse_name_status(runner(["diff", "--name-status", "-z", "-M", "--no-ext-diff", base, head, "--"]))


def blob_text(runner: GitRunner, rev: str, path: str, cap: int) -> str | None:
    """Return the UTF-8 text of ``rev:path``; ``None`` when it is larger than ``cap`` bytes, binary or unreadable."""
    spec = f"{rev}:{path}"
    try:
        if int(runner(["cat-file", "-s", spec]).strip()) > cap:
            return None
        return runner(["cat-file", "blob", spec]).decode("utf-8")
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def commit_parents(runner: GitRunner, rev: str) -> tuple[str, tuple[str, ...]]:
    """Return the full SHA of ``rev`` and the SHAs of its parents, in order."""
    shas = runner(["rev-list", "--parents", "-n", "1", rev, "--"]).decode("ascii").split()
    if not shas:
        raise ValueError(f"no commit {rev}")
    return shas[0], tuple(shas[1:])


def first_parent_commits(runner: GitRunner, rev_range: str) -> list[tuple[str, str]]:
    """Return ``(sha, subject)`` of the first-parent commits of ``rev_range``, oldest first."""
    output = runner(["log", "--first-parent", "--reverse", "--format=%H%x00%s%x00", rev_range, "--"])
    fields = output.decode("utf-8", errors="replace").split("\0")
    return [(fields[i].strip(), fields[i + 1]) for i in range(0, len(fields) - 1, 2)]
