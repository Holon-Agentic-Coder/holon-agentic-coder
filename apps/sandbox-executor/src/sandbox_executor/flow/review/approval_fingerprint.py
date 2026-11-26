"""The PR-own change fingerprint an approval is bound to, next to the SHA the reviewers saw.

The fingerprint covers the PR's reviewable own diff: ``merge-base(head, origin/<target>)`` to ``head``, without the
bookkeeping files. Those are the shared CI definition (``ci_final_head.BOOKKEEPING_PATH_PREFIXES`` and
``scripts/ci_bookkeeping_gate.py``): a ``.md``, ``.json`` or ``.jsonl`` file under ``plans/``, ``executions/`` or
``holon-knowledge/`` with a non-dot stem. Any other file under those roots (a ``plans/tool.py``, a ``plans/.json``)
still counts as reviewable code. It hashes two reductions of that diff together:

- ``git patch-id --stable`` over the full-index binary diff, which ignores line numbers and hunk order, so a merge of a
  target branch that only touched other files leaves it unchanged;
- the sorted ``status mode blob path`` lines of ``--raw``, so a mode-only change, an empty-file change or a binary
  change (none of which carries a text hunk) still changes the fingerprint.

The result is ``pf2:`` and 40 hex characters; the version prefix keeps a later reduction from ever matching an old one,
so a ``pf1`` approval (which excluded only the knowledge base and calibration reports) never matches a ``pf2`` one.
An empty PR-own change, an unknown ref or any git failure gives None, and None never matches anything, so an approval
without a fingerprint (a legacy checkpoint) keeps today's behaviour.

Known limitation (conservative): patch-id hashes the context lines of each hunk and the raw lines name the new blob,
so a target-branch edit to a file the PR also changes alters the fingerprint even when the edits do not overlap. That
costs a re-review, never a missed one.
"""

from __future__ import annotations

import hashlib
import logging
import posixpath
import subprocess
from collections.abc import Mapping
from typing import Any

from sandbox_executor.flow.github.ci_final_head import BOOKKEEPING_PATH_PREFIXES
from sandbox_executor.git import run_git

logger = logging.getLogger(__name__)

FINGERPRINT_VERSION = "pf2"
FINGERPRINT_HEX_CHARS = 40
# Mirrors ``scripts/ci_bookkeeping_gate.BOOKKEEPING_SUFFIXES``; a parity test keeps the two equal.
BOOKKEEPING_PATH_SUFFIXES = (".md", ".json", ".jsonl")
# ``*[!.]*`` needs a non-dot character before the suffix, like ``posixpath.splitext``: a ``plans/.json`` or
# ``plans/..json`` has no extension, so ``is_bookkeeping_path`` and the CI gate treat it as reviewable, as does this.
FINGERPRINT_EXCLUDED_PATHSPECS = tuple(
    f":(exclude,glob){prefix}**/*[!.]*{suffix}"
    for prefix in BOOKKEEPING_PATH_PREFIXES
    for suffix in BOOKKEEPING_PATH_SUFFIXES
)
# What an approval is bound to, carried from the loop's consensus data into the Stage 4 payload for Stage 5.
APPROVAL_RECORD_KEYS = ("target_commit", "approval_fingerprint", "base_branch", "carried_from", "carried_to")
_GIT_TIMEOUT_SECONDS = 60


def reviewable_pathspecs() -> tuple[str, ...]:
    """The ``git diff`` pathspecs of the reviewable diff: everything except the bookkeeping files."""
    return ("--", ".", *FINGERPRINT_EXCLUDED_PATHSPECS)


def is_bookkeeping_path(path: str) -> bool:
    """True only for a bookkeeping-suffixed file under a bookkeeping root, the same rule as the CI bookkeeping gate."""
    root, _, rest = path.partition("/")
    return f"{root}/" in BOOKKEEPING_PATH_PREFIXES and posixpath.splitext(rest)[1] in BOOKKEEPING_PATH_SUFFIXES


def _git_bytes(repo_dir: str, args: list[str], stdin: bytes | None = None) -> bytes | None:
    """The raw stdout of ``git <args>`` (fed ``stdin``), or None on a non-zero exit; no shell, binary-safe."""
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        input=stdin,
        capture_output=True,
        check=False,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    return proc.stdout if proc.returncode == 0 else None


def _raw_lines(raw: bytes) -> list[str]:
    """``status mode blob path`` per changed file, sorted, from ``git diff --raw --no-abbrev -z`` output."""
    fields = raw.split(b"\0")
    lines = []
    for index in range(0, len(fields) - 1, 2):
        meta, path = fields[index].decode("ascii", "replace"), fields[index + 1].decode("utf-8", "surrogateescape")
        parts = meta.lstrip(":").split()
        if len(parts) == 5:  # old mode, new mode, old blob, new blob, status
            lines.append(f"{parts[4]} {parts[1]} {parts[3]} {path}")
    return sorted(lines)


def pr_change_fingerprint(repo_dir: str, head: str, target_ref: str) -> str | None:
    """The fingerprint of ``head``'s own change against ``target_ref``, or None (empty change, unknown ref, failure)."""
    base = run_git(["merge-base", head, target_ref], cwd=repo_dir, check=False)
    base_sha = str(base.stdout or "").strip()
    if base.returncode != 0 or not base_sha:
        return None
    span = [base_sha, head]
    raw = _git_bytes(repo_dir, ["diff", "--raw", "--no-abbrev", "-z", "--no-renames", *span, *reviewable_pathspecs()])
    lines = _raw_lines(raw or b"")
    if not lines:
        return None
    diff = _git_bytes(repo_dir, ["diff", "--full-index", "--binary", "--no-renames", *span, *reviewable_pathspecs()])
    patch_id = _git_bytes(repo_dir, ["patch-id", "--stable"], stdin=diff) if diff is not None else None
    if patch_id is None:
        return None
    patch_ids = sorted(line.split()[0] for line in patch_id.decode("ascii", "replace").splitlines() if line.strip())
    material = "\n".join([FINGERPRINT_VERSION, *patch_ids, *lines]).encode("utf-8", "surrogateescape")
    return f"{FINGERPRINT_VERSION}:{hashlib.sha256(material).hexdigest()[:FINGERPRINT_HEX_CHARS]}"


def approval_fingerprint_of(repo_dir: str, head: str | None, base_branch: str | None) -> str | None:
    """The fingerprint of ``head`` against ``origin/<base_branch>``; None for an unresolved head, never raises."""
    if not head or head == "HEAD" or not base_branch:
        return None
    try:
        return pr_change_fingerprint(repo_dir, head, f"refs/remotes/origin/{base_branch}")
    except Exception as exc:
        logger.debug("Could not fingerprint %s against %s: %s", head, base_branch, exc)
        return None


def is_bookkeeping_only_move(repo_dir: str, approved: str, head: str) -> bool:
    """True only when ``approved..head`` changed at least one path and every changed path is bookkeeping.

    The span includes whatever a target merge brought in, so a merge with target code is never bookkeeping-only. An
    unknown commit, a missing repository or any git failure gives False.
    """
    try:
        names = run_git(["diff", "--name-only", "--no-renames", approved, head], cwd=repo_dir, check=False)
    except (OSError, TimeoutError) as exc:
        logger.debug("Could not diff %s..%s: %s", approved, head, exc)
        return False
    paths = [line for line in str(names.stdout or "").splitlines() if line.strip()]
    return names.returncode == 0 and bool(paths) and all(is_bookkeeping_path(path) for path in paths)


def approval_record(consensus_data: Mapping[str, Any]) -> dict[str, Any]:
    """The head the reviewers saw, the fingerprint and any carry-over, for the Stage 4 payload's consensus."""
    return {key: consensus_data.get(key) for key in APPROVAL_RECORD_KEYS}


def is_current_version(fingerprint: str | None) -> bool:
    """True only for a fingerprint of today's reduction; an older (``pf1``) one can never be compared, only unknown."""
    return bool(fingerprint) and str(fingerprint).startswith(f"{FINGERPRINT_VERSION}:")


def fingerprints_match(first: str | None, second: str | None) -> bool:
    """True only when both fingerprints are known and equal: None never matches None."""
    return bool(first and second) and first == second
