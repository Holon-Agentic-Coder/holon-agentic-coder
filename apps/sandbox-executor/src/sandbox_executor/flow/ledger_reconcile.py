"""Ledger reconciliation: resolve git conflicts in the three append-only JSONL ledgers.

A conflicted ledger is resolved by keeping one side as an untouched prefix and appending the other
side's new valid rows, under the ledger lock, so the append-only invariant holds.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from typing import Any, Literal, TextIO

from sandbox_executor.ledger import LedgerLock, LedgerRewriteError, assert_ledger_prefix

logger = logging.getLogger(__name__)


class ConflictResolutionError(RuntimeError):
    """Raised when an unresolvable merge conflict is encountered during a pipeline stage."""


#: Git conflict markers. A real marker always starts a line; a JSON row starts with ``{``, so a marker that only
#: appears inside a row's text (for example a description mentioning ``|||||||``) is never a conflict.
_CONFLICT_MARKERS = ("<<<<<<<", "|||||||", "=======", ">>>>>>>")


#: The conflict section that each marker opens: ours, the diff3 base, theirs, then back to lines both sides share.
_SECTION_AFTER_MARKER = {"<<<<<<<": "ours", "|||||||": "base", "=======": "theirs", ">>>>>>>": "both"}


#: The side of a conflicted ledger whose bytes stay an untouched prefix of the resolution.
PrefixSide = Literal["ours", "theirs"]


LEDGER_REL_PATHS = (
    os.path.join("holon-knowledge", "ledger", "intents.jsonl"),
    os.path.join("holon-knowledge", "ledger", "plans.jsonl"),
    os.path.join("holon-knowledge", "ledger", "executions.jsonl"),
)


def _has_conflict_marker_lines(content: str) -> bool:
    """Return True when any line of ``content`` starts with a git conflict marker."""
    return any(line.startswith(_CONFLICT_MARKERS) for line in content.splitlines())


def extract_valid_ledger_rows(content: str) -> list[dict[str, Any]]:
    """Extract valid JSON dictionary objects from ledger content, skipping git conflict markers.

    Args:
        content: String content of a JSONL ledger, potentially containing git conflict markers.

    Returns:
        list[dict[str, Any]]: Parsed valid JSON dictionary rows.

    Raises:
        None: Malformed lines and git conflict markers are ignored.
    """
    rows: list[dict[str, Any]] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(_CONFLICT_MARKERS):
            continue
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                rows.append(parsed)
            else:
                logger.warning(f"Skipping non-dictionary JSON ledger line: {stripped[:100]}")
        except Exception:
            logger.debug(f"Skipping unparseable ledger line: {stripped[:100]}")
    return rows


def _split_conflict_sides(content: str) -> tuple[list[str], list[str]]:
    """Split conflicted content into the ours and theirs files, each keeping the shared lines in place.

    Lines keep their exact text and line endings. The diff3 base section is dropped: on an append-only ledger each
    side already holds every base row.
    """
    ours: list[str] = []
    theirs: list[str] = []
    section = "both"
    for line in content.splitlines(keepends=True):
        marker = line[:7]
        if marker in _SECTION_AFTER_MARKER:
            section = _SECTION_AFTER_MARKER[marker]
            continue
        if section in ("both", "ours"):
            ours.append(line)
        if section in ("both", "theirs"):
            theirs.append(line)
    return ours, theirs


def _append_only_union(prefix_lines: list[str], other_lines: list[str]) -> str:
    """Return the prefix side unchanged, followed by the other side's lines it lacks, in their original order.

    Lines are compared as exact text (ignoring only the line ending), so an identical line is not repeated; rows are
    never parsed, deduplicated by key, sorted or re-serialised.
    """
    prefix = "".join(prefix_lines)
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    seen = {line.rstrip("\r\n") for line in prefix_lines}
    missing = (line.rstrip("\r\n") for line in other_lines if line.strip())
    extra = [line + "\n" for line in dict.fromkeys(missing) if line not in seen]
    return prefix + "".join(extra)


def resolve_append_only_conflict(content: str, prefix_side: PrefixSide = "ours") -> tuple[str, str]:
    """Resolve conflicted ledger content by appending, keeping one side's bytes as an untouched prefix.

    Args:
        content: Ledger content containing line-anchored git conflict markers.
        prefix_side: ``"ours"`` or ``"theirs"``: the side whose file must stay a byte prefix of the result.

    Returns:
        tuple[str, str]: The prefix side's file and the resolved content, which starts with it.

    Raises:
        ValueError: When ``prefix_side`` is neither ``"ours"`` nor ``"theirs"``.
    """
    if prefix_side not in ("ours", "theirs"):
        raise ValueError(f"prefix_side must be 'ours' or 'theirs', not {prefix_side!r}")
    ours, theirs = _split_conflict_sides(content)
    prefix_lines, other_lines = (theirs, ours) if prefix_side == "theirs" else (ours, theirs)
    return "".join(prefix_lines), _append_only_union(prefix_lines, other_lines)


def reconcile_jsonl_ledger_conflict(content: str, prefix_side: PrefixSide = "ours") -> str:
    """Resolve JSONL ledger content containing standard or diff3 conflict markers by pure append.

    Content without line-anchored conflict markers is returned unchanged. Otherwise the ``prefix_side`` file is kept
    byte for byte and the other side's missing lines follow in their original order; rows are never deduplicated by
    key, sorted by timestamp or re-serialised.

    Args:
        content: Raw JSONL ledger content.
        prefix_side: ``"ours"`` or ``"theirs"``: the side whose file stays a byte prefix.

    Returns:
        str: The resolved ledger content.
    """
    if not _has_conflict_marker_lines(content):
        return content
    return resolve_append_only_conflict(content, prefix_side)[1]


def _write_resolved_ledger(locked: TextIO, file_path: str, prefix: str, resolved: str) -> None:
    """Rewrite a conflicted ledger in place through its held ``LedgerLock``, then check the prefix side survived.

    The file holds conflict markers, so it cannot be resolved by appending. Writing through the locked descriptor keeps
    the inode, so an appender waiting on the lock appends after the resolution instead of to a replaced file; git still
    holds both sides in the index if the write is interrupted. The prefix is checked before the file is touched and
    again afterwards; either check fails closed with ``LedgerRewriteError`` if any byte of the kept side moved.
    """
    if not resolved.startswith(prefix):
        raise LedgerRewriteError(f"Resolution of {file_path} does not keep the prefix side byte for byte")
    locked.flush()
    locked.buffer.truncate(0)
    locked.buffer.write(resolved.encode("utf-8"))
    locked.flush()
    assert_ledger_prefix(file_path, prefix.encode("utf-8"))


def _resolve_ledger_under_lock(file_path: str, prefix_side: PrefixSide) -> None:
    """Under ``LedgerLock``, read the ledger and resolve any line-anchored conflict markers by pure append."""
    with LedgerLock(file_path) as locked:
        with open(file_path, encoding="utf-8", newline="") as f:
            content = f.read()
        if _has_conflict_marker_lines(content):
            _write_resolved_ledger(locked, file_path, *resolve_append_only_conflict(content, prefix_side))


def reconcile_ledger_file(file_path: str, prefix_side: PrefixSide = "ours") -> bool:
    """Resolve git conflict markers in a ledger file by pure append; a file without markers is left untouched.

    Args:
        file_path: Absolute or relative path to the ledger file.
        prefix_side: ``"ours"`` or ``"theirs"``: the side whose file stays a byte prefix.

    Returns:
        bool: True if the file has no conflict or was resolved, False when it is missing or cannot be resolved.

    Raises:
        LedgerRewriteError: When the resolution would move or moved a byte of the kept side; never turned into False.
    """
    if not os.path.exists(file_path):
        logger.error(f"Failed to reconcile ledger file '{file_path}': it does not exist")
        return False
    try:
        _resolve_ledger_under_lock(file_path, prefix_side)
        return True
    except LedgerRewriteError:
        raise
    except Exception as exc:
        logger.error(f"Failed to reconcile ledger file '{file_path}': {exc}")
        return False


def _ledgers_with_conflict_markers(repo_dir: str) -> list[str]:
    """Return the ledgers in the working tree that hold line-anchored git conflict markers."""
    found: list[str] = []
    for rel_path in LEDGER_REL_PATHS:
        full_path = os.path.join(repo_dir, rel_path)
        with contextlib.suppress(OSError, UnicodeDecodeError), open(full_path, encoding="utf-8") as f:
            if _has_conflict_marker_lines(f.read()):
                found.append(rel_path)
    return found


def _resolve_conflicting_ledger(repo_dir: str, rel_path: str) -> bool:
    """Resolve one conflicted working-tree ledger by pure append, without staging it.

    Staging belongs to the commit step that owns the ledger append, so a failed or dry run never leaves a ledger
    staged in the operator's checkout. ``ours`` (the checked-out HEAD's file) is kept as the prefix because this
    pre-check cannot tell which side is published: the checkout already holds those bytes, and the pre-push sync,
    which knows the target branch, keeps ``theirs`` instead. Either way every row of both sides is kept.
    """
    full_path = os.path.join(repo_dir, rel_path)
    return os.path.exists(full_path) and reconcile_ledger_file(full_path)
