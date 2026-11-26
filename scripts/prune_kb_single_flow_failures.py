#!/usr/bin/env python3
"""One-off, repeatable clean-up of single-flow review chatter in ``holon-knowledge/kb/failure_modes/``.

Intent ``I-1795696249-prune-single-flow-kb-failures``. A ``KB-failure-*.json`` file is single-flow chatter, and is
pruned, only when all of these hold:

1. ``evidence.ledger_refs`` is a non-empty list and every ref starts with ``ACT-REV-`` or ``ACT-FIND-`` (the
   ``REVIEW_ACTION_ID_PREFIXES`` the promotion guard uses);
2. at least one ref resolves to a row of the merged execution ledger (canonical ``executions.jsonl`` plus the
   ``pending/executions/*.jsonl`` fragments);
3. every ref has an intent root: the ``I-<id>-<slug>`` of the resolved row's ``target``, or, for a ref with no ledger
   row, the one in ``payload.name``;
4. those roots are one and the same.

Every other file is kept: unreadable files, files backed by any other row kind, by no resolvable row, by no intent root
or by two or more intent roots. The ledgers are only read, never written. Nothing outside ``failure_modes/`` is touched.
A malformed ledger line makes ``--apply`` refuse (exit code 2), because the unreadable row could carry a second root.

Usage:
    uv run python scripts/prune_kb_single_flow_failures.py [--repo DIR] [--apply] [--list]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sandbox_executor.knowledge.promotion_rules import REVIEW_ACTION_ID_PREFIXES
from sandbox_executor.ledger.merged import read_merged_rows

FAILURE_MODES_DIR = "holon-knowledge/kb/failure_modes"
EXECUTIONS_LEDGER = "holon-knowledge/ledger/executions.jsonl"
FAILURE_GLOB = "KB-failure-*.json"
PRUNE_REASON = "single-flow-chatter"
BAD_LEDGER_EXIT = 2

_INTENT_ROOT = re.compile(r"(?<![\w-])I-\d+(?:-[^/\s]*)?")


@dataclass(frozen=True)
class Verdict:
    """The classification of one failure-mode file."""

    path: Path
    prune: bool
    reason: str


def intent_root(text: object) -> str | None:
    """Return the first ``I-<id>-<slug>`` intent root in ``text``, or ``None``."""
    if not isinstance(text, str):
        return None
    match = _INTENT_ROOT.search(text)
    return match.group(0) if match else None


def load_review_roots(repo: Path, bad_lines: list[int] | None = None) -> dict[str, str | None]:
    """Map each review row's ``action_id`` in the merged execution ledger to its target's intent root.

    Malformed ledger lines are appended to ``bad_lines``. An action id seen with two different roots maps to ``None``.
    """
    sink = bad_lines if bad_lines is not None else []
    rows = read_merged_rows(str(repo / EXECUTIONS_LEDGER), lambda line_no, _exc: sink.append(line_no))
    roots: dict[str, str | None] = {}
    for row in rows:
        action_id = row.get("action_id")
        if not isinstance(action_id, str) or not action_id.startswith(REVIEW_ACTION_ID_PREFIXES):
            continue
        root = intent_root(row.get("target"))
        roots[action_id] = root if roots.get(action_id, root) == root else None
    return roots


def _review_refs(envelope: dict[str, Any]) -> tuple[list[str] | None, str]:
    """Return the review refs of ``envelope``, or ``None`` with the reason the file is kept."""
    evidence = envelope.get("evidence")
    refs = evidence.get("ledger_refs") if isinstance(evidence, dict) else None
    if not isinstance(refs, list) or not refs:
        return None, "no-refs"
    if not all(isinstance(ref, str) and ref.startswith(REVIEW_ACTION_ID_PREFIXES) for ref in refs):
        return None, "non-review-ref"
    return refs, ""


def _payload_root(envelope: dict[str, Any]) -> str | None:
    """Return the intent root named in ``payload.name``, or ``None``."""
    payload = envelope.get("payload")
    return intent_root(payload.get("name")) if isinstance(payload, dict) else None


def _ref_roots(
    refs: list[str], review_roots: dict[str, str | None], payload_root: str | None
) -> tuple[set[str | None], int]:
    """Return the set of intent roots behind ``refs`` and how many refs resolved to a ledger row."""
    roots: set[str | None] = set()
    resolved = 0
    for ref in refs:
        if ref in review_roots:
            resolved += 1
            roots.add(review_roots[ref])
        else:
            roots.add(payload_root)
    return roots, resolved


def _keep_reason(envelope: object, review_roots: dict[str, str | None]) -> str:
    """Return why the envelope is kept, or ``PRUNE_REASON`` when it is single-flow chatter."""
    if not isinstance(envelope, dict):
        return "unreadable"
    refs, reason = _review_refs(envelope)
    if refs is None:
        return reason
    roots, resolved = _ref_roots(refs, review_roots, _payload_root(envelope))
    if not resolved:
        return "no-resolved-ref"
    if None in roots:
        return "no-intent-root"
    return PRUNE_REASON if len(roots) == 1 else "multiple-intent-roots"


def classify_failure_file(path: Path, review_roots: dict[str, str | None]) -> Verdict:
    """Classify one ``KB-failure-*.json`` file; anything unreadable or unexpected is kept."""
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Verdict(path, False, "unreadable")
    reason = _keep_reason(envelope, review_roots)
    return Verdict(path, reason == PRUNE_REASON, reason)


def classify_tree(repo: Path, bad_lines: list[int] | None = None) -> list[Verdict]:
    """Classify every failure-mode file under ``repo``, sorted by name."""
    failure_dir = repo / FAILURE_MODES_DIR
    if not failure_dir.is_dir():
        return []
    review_roots = load_review_roots(repo, bad_lines)
    return [classify_failure_file(path, review_roots) for path in sorted(failure_dir.glob(FAILURE_GLOB))]


def apply_prunes(verdicts: Sequence[Verdict], failure_dir: Path) -> int:
    """Delete every pruned file that is still a regular file directly inside ``failure_dir``; return the count."""
    target_dir = failure_dir.resolve()
    deleted = 0
    for verdict in verdicts:
        path = verdict.path
        if not verdict.prune or path.is_symlink() or not path.is_file():
            continue
        if path.parent.resolve() != target_dir or not path.match(FAILURE_GLOB):
            continue
        path.unlink()
        deleted += 1
    return deleted


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1], help="repository root")
    parser.add_argument("--apply", action="store_true", help="delete the single-flow chatter files")
    parser.add_argument("--list", action="store_true", help="print each prunable path")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Classify the failure-mode files, print the summary, and delete the chatter with ``--apply``."""
    args = _parse_args(argv)
    repo = Path(args.repo)
    bad_lines: list[int] = []
    verdicts = classify_tree(repo, bad_lines)
    prunable = [verdict for verdict in verdicts if verdict.prune]
    if args.list:
        for verdict in prunable:
            print(verdict.path.relative_to(repo).as_posix())
    if bad_lines:
        print(f"malformed execution ledger lines: {len(bad_lines)}")
    if args.apply and bad_lines:
        print("refusing --apply: a malformed ledger line could hide a second intent root")
        return BAD_LEDGER_EXIT
    count = apply_prunes(prunable, repo / FAILURE_MODES_DIR) if args.apply else len(prunable)
    mode = "deleted" if args.apply else "dry run"
    print(f"single-flow review chatter: {count} of {len(verdicts)} KB-failure files ({mode})")
    for reason, n in sorted(Counter(v.reason for v in verdicts if not v.prune).items()):
        print(f"kept {reason}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
