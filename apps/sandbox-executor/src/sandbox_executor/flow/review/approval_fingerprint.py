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

# why: the version prefix keeps a later reduction from matching an older one: a pf1 approval never matches pf2
FINGERPRINT_VERSION = "pf2"
FINGERPRINT_HEX_CHARS = 40
# why: mirrors scripts/ci_bookkeeping_gate.BOOKKEEPING_SUFFIXES; a parity test keeps the two equal
BOOKKEEPING_PATH_SUFFIXES = (".md", ".json", ".jsonl")
# why: *[!.]* needs a non-dot before the suffix, like splitext, so plans/.json stays reviewable
FINGERPRINT_EXCLUDED_PATHSPECS = tuple(
    f":(exclude,glob){prefix}**/*[!.]*{suffix}"
    for prefix in BOOKKEEPING_PATH_PREFIXES
    for suffix in BOOKKEEPING_PATH_SUFFIXES
)
APPROVAL_RECORD_KEYS = ("target_commit", "approval_fingerprint", "base_branch", "carried_from", "carried_to")
_GIT_TIMEOUT_SECONDS = 60
_RAW_DIFF_FIELD_COUNT = 5


def reviewable_pathspecs() -> tuple[str, ...]:
    return ("--", ".", *FINGERPRINT_EXCLUDED_PATHSPECS)


def is_bookkeeping_path(path: str) -> bool:
    root, _, rest = path.partition("/")
    return f"{root}/" in BOOKKEEPING_PATH_PREFIXES and posixpath.splitext(rest)[1] in BOOKKEEPING_PATH_SUFFIXES


def _git_bytes(repo_dir: str, args: list[str], stdin: bytes | None = None) -> bytes | None:
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
    fields = raw.split(b"\0")
    lines = []
    for index in range(0, len(fields) - 1, 2):
        meta, path = fields[index].decode("ascii", "replace"), fields[index + 1].decode("utf-8", "surrogateescape")
        parts = meta.lstrip(":").split()
        if len(parts) == _RAW_DIFF_FIELD_COUNT:
            _old_mode, new_mode, _old_blob, new_blob, status = parts
            lines.append(f"{status} {new_mode} {new_blob} {path}")
    return sorted(lines)


# why: patch-id context lines make non-overlapping edits to a shared file change it: a re-review, never a missed one
def pr_change_fingerprint(repo_dir: str, head: str, target_ref: str) -> str | None:
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
    # why: mode-only, empty-file and binary changes carry no text hunk for patch-id, so the raw lines are hashed too
    material = "\n".join([FINGERPRINT_VERSION, *patch_ids, *lines]).encode("utf-8", "surrogateescape")
    return f"{FINGERPRINT_VERSION}:{hashlib.sha256(material).hexdigest()[:FINGERPRINT_HEX_CHARS]}"


def approval_fingerprint_of(repo_dir: str, head: str | None, base_branch: str | None) -> str | None:
    if not head or head == "HEAD" or not base_branch:
        return None
    try:
        return pr_change_fingerprint(repo_dir, head, f"refs/remotes/origin/{base_branch}")
    except Exception as exc:
        logger.debug("Could not fingerprint %s against %s: %s", head, base_branch, exc)
        return None


def is_bookkeeping_only_move(repo_dir: str, approved: str, head: str) -> bool:
    try:
        names = run_git(["diff", "--name-only", "--no-renames", approved, head], cwd=repo_dir, check=False)
    except (OSError, TimeoutError) as exc:
        logger.debug("Could not diff %s..%s: %s", approved, head, exc)
        return False
    paths = [line for line in str(names.stdout or "").splitlines() if line.strip()]
    return names.returncode == 0 and bool(paths) and all(is_bookkeeping_path(path) for path in paths)


def approval_record(consensus_data: Mapping[str, Any]) -> dict[str, Any]:
    return {key: consensus_data.get(key) for key in APPROVAL_RECORD_KEYS}


def is_current_version(fingerprint: str | None) -> bool:
    return bool(fingerprint) and str(fingerprint).startswith(f"{FINGERPRINT_VERSION}:")


def fingerprints_match(first: str | None, second: str | None) -> bool:
    return bool(first and second) and first == second
