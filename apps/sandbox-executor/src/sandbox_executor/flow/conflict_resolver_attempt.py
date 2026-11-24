"""The host-side guard around one conflict resolver attempt on an in-progress merge.

Before an attempt the guard snapshots the index (every stage entry), the untracked files, ``HEAD`` and ``MERGE_HEAD``.
After it, any change to the index, ``HEAD`` or ``MERGE_HEAD`` (or a merge no longer in progress) is tampering: the
agent ran a git write, and the caller fails closed at once, first moving a ``HEAD`` the agent advanced (an agent
commit) back to the pre-attempt head so no unverified commit stays on the flow branch. Worktree edits to existing test
files outside the conflict set are allowed so the resolver can repair tests whose setup the merge invalidated; edits to
any other file outside the conflict set and new untracked files are out of set, and conflict markers left behind fail
the attempt too. A failed attempt is reset: out-of-set and extra test edits are reverted, only the listed new files are
deleted, and the conflict paths get their merge state back (``git checkout -m``, or the surviving side's stage blob for
a modify/delete conflict, which ``checkout -m`` cannot recreate). Paths are compared exactly as git reports them, so a
rename counts as out of set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sandbox_executor.flow.merge_git import git_out, has_conflict_markers
from sandbox_executor.flow.target_sync_merge import merge_in_progress, merge_verification_error, unmerged_paths
from sandbox_executor.git import run_git

# Where failed attempts' diffs are kept for the human, under the flow's git-ignored ``tmp/``.
ARTEFACT_DIR = "tmp/conflict-resolver"


def _nul_paths(worktree: str, *args: str) -> list[str]:
    return list(dict.fromkeys(path for path in git_out(worktree, *args).split("\0") if path))


def _is_test_path(path: str) -> bool:
    """Whether ``path`` is a test file (under a ``tests/`` directory or named ``test_*.py`` / ``*_test.py``)."""
    pure = PurePosixPath(path.replace("\\", "/"))
    is_py_test = pure.suffix == ".py" and (pure.name.startswith("test_") or pure.name.endswith("_test.py"))
    return "tests" in pure.parts[:-1] or is_py_test


@dataclass(frozen=True)
class MergeSnapshot:
    """The merge state an attempt must leave untouched: index stage entries, untracked files, the two heads."""

    index: str
    untracked: frozenset[str]
    head: str
    merge_head: str


def snapshot_merge(worktree: str) -> MergeSnapshot:
    return MergeSnapshot(
        index=git_out(worktree, "ls-files", "-s", "-z"),
        untracked=frozenset(_nul_paths(worktree, "ls-files", "-o", "--exclude-standard", "-z")),
        head=git_out(worktree, "rev-parse", "-q", "--verify", "HEAD").strip(),
        merge_head=git_out(worktree, "rev-parse", "-q", "--verify", "MERGE_HEAD").strip(),
    )


@dataclass(frozen=True)
class AttemptCheck:
    """What an attempt changed: tampering, out-of-set edits, allowed test edits, new files or markers left."""

    tampered: bool = False
    out_of_set: tuple[str, ...] = ()
    test_edits: tuple[str, ...] = ()
    new_untracked: tuple[str, ...] = ()
    markers: tuple[str, ...] = ()
    reason: str = ""

    @property
    def ok(self) -> bool:
        """Whether the attempt may be staged."""
        return not (self.tampered or self.out_of_set or self.new_untracked or self.markers)


def _tamper_reason(worktree: str, before: MergeSnapshot) -> str:
    if not merge_in_progress(worktree):
        return "the merge is no longer in progress"
    after = snapshot_merge(worktree)
    changed = [name for name in ("head", "merge_head", "index") if getattr(after, name) != getattr(before, name)]
    return f"the attempt changed {', '.join(changed)}" if changed else ""


def check_attempt(worktree: str, paths: list[str], before: MergeSnapshot) -> AttemptCheck:
    """Check one attempt against the snapshot taken before it."""
    tampered = _tamper_reason(worktree, before)
    if tampered:
        return AttemptCheck(tampered=True, reason=tampered)
    in_set = set(paths)
    outside = [p for p in _nul_paths(worktree, "diff", "--name-only", "-z") if p not in in_set]
    test_edits = tuple(p for p in outside if _is_test_path(p))
    out_of_set = tuple(p for p in outside if not _is_test_path(p))
    untracked = _nul_paths(worktree, "ls-files", "-o", "--exclude-standard", "-z")
    new = tuple(p for p in untracked if p not in before.untracked and not p.startswith(f"{ARTEFACT_DIR}/"))
    markers = tuple(p for p in (*paths, *test_edits) if has_conflict_markers(Path(worktree) / p))
    reasons = [
        f"{label}: {', '.join(found)}"
        for label, found in (("edits outside the conflict set", out_of_set), ("new files", new), ("markers", markers))
        if found
    ]
    return AttemptCheck(
        out_of_set=out_of_set, test_edits=test_edits, new_untracked=new, markers=markers, reason="; ".join(reasons)
    )


def stage_resolution(worktree: str, paths: list[str], extra_paths: tuple[str, ...] | list[str] = ()) -> str:
    """Stage the resolved paths (and any allowed test edits); return why the staged merge must not commit, or ``""``."""
    to_stage = list(dict.fromkeys((*paths, *extra_paths)))
    res = run_git(["add", "-A", "--", *to_stage], cwd=worktree, check=False)
    if res.returncode != 0:
        return f"git add failed: {(res.stderr or '').strip()}"
    return merge_verification_error(worktree, to_stage)


def _unmerged_stages(worktree: str, path: str) -> set[str]:
    entries = git_out(worktree, "ls-files", "-u", "-z", "--", path).split("\0")
    return {entry.split("\t", 1)[0].split()[2] for entry in entries if "\t" in entry}


def _restore_conflict(worktree: str, path: str) -> bool:
    """Give ``path`` its merge state back: markers for a two-sided conflict, the surviving side otherwise."""
    stages = _unmerged_stages(worktree, path)
    if {"2", "3"} <= stages:
        args = ["checkout", "-m", "--", path]
    elif stages & {"2", "3"}:
        args = ["checkout-index", "-f", f"--stage={min(stages & {'2', '3'})}", "--", path]
    else:
        return False
    return run_git(args, cwd=worktree, check=False).returncode == 0


def reset_attempt(worktree: str, paths: list[str], check: AttemptCheck) -> str:
    """Undo a failed attempt; return why the merge could not be restored, or ``""``."""
    for path in (*check.out_of_set, *check.test_edits):
        if run_git(["checkout", "--", path], cwd=worktree, check=False).returncode != 0:
            return f"could not revert {path}"
    for path in check.new_untracked:
        try:
            os.remove(os.path.join(worktree, path))
        except OSError as exc:
            return f"could not delete {path}: {exc}"
    failed = [path for path in paths if not _restore_conflict(worktree, path)]
    missing = sorted(set(paths) - set(unmerged_paths(worktree)))
    if failed or missing:
        return f"could not restore the merge state of {', '.join(sorted(set(failed) | set(missing)))}"
    return ""


def discard_tampered(worktree: str, before: MergeSnapshot) -> str:
    """Move ``HEAD`` back to the pre-attempt head when the agent moved it; return why it could not, or ``""``.

    The hard reset also drops the merge state, which the fail-closed caller would abort anyway.
    """
    head = git_out(worktree, "rev-parse", "-q", "--verify", "HEAD").strip()
    if not before.head or head == before.head:
        return ""
    res = run_git(["reset", "-q", "--hard", before.head], cwd=worktree, check=False)
    return "" if res.returncode == 0 else f"could not restore HEAD to {before.head[:12]}"


def save_attempt_diff(worktree: str, paths: list[str], label: str) -> str:
    """Write the attempt's ``git diff`` of the conflict paths under ``tmp/conflict-resolver/``; return its path."""
    target = Path(worktree) / ARTEFACT_DIR / f"{label}.diff"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Self-ignoring, so an artefact is never swept into a commit even where ``tmp/`` is not git-ignored.
        (target.parent / ".gitignore").write_text("*\n", encoding="utf-8")
        target.write_text(git_out(worktree, "diff", "--", *paths), encoding="utf-8")
    except OSError:
        return ""
    return str(target)
