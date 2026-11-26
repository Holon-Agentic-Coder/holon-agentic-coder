from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sandbox_executor.ledger.fragments import iter_fragment_files, ledger_name_of
from sandbox_executor.ledger.lock import LedgerLock, _read_ledger_bytes, assert_ledger_prefix
from sandbox_executor.ledger.merged import parse_jsonl_rows, row_identity
from sandbox_executor.ledger.order import parse_created_at
from sandbox_executor.ledger.promote_threshold import (
    canonical_ledger_paths,
    pending_root_intents,
    promote_every_from_env,
)

_Row = dict[str, object]


class PromotionRaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class FragmentSnapshot:
    path: str
    data: bytes


@dataclass(frozen=True)
class LedgerPromotion:
    ledger_name: str
    canonical_path: str
    lines: tuple[str, ...]
    promoted: tuple[FragmentSnapshot, ...]
    held: tuple[str, ...]


@dataclass(frozen=True)
class PromotionPlan:
    ledgers: tuple[LedgerPromotion, ...]


@dataclass(frozen=True)
class PromotionResult:
    root_intents: int
    every: int
    forced: bool
    threshold_met: bool
    appended: dict[str, int] = field(default_factory=dict)
    deleted: tuple[str, ...] = ()
    held: tuple[str, ...] = ()


def _parse_fragment(data: bytes) -> list[tuple[str, _Row]] | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    parsed: list[tuple[str, _Row]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return None
        if not isinstance(row, dict):
            return None
        parsed.append((line, row))
    return parsed


def _ordered_lines(kept: list[tuple[datetime | None, str]]) -> tuple[str, ...]:
    timed = sorted((instant, index, line) for index, (instant, line) in enumerate(kept) if instant is not None)
    untimed = [line for instant, line in kept if instant is None]
    return tuple(line for _instant, _index, line in timed) + tuple(untimed)


def plan_ledger(canonical_path: str) -> LedgerPromotion:
    ledger_name = ledger_name_of(canonical_path) or ""
    canonical_text = _read_ledger_bytes(canonical_path).decode("utf-8", errors="replace")
    seen = {row_identity(ledger_name, row) for row in parse_jsonl_rows(canonical_text)}
    kept: list[tuple[datetime | None, str]] = []
    promoted: list[FragmentSnapshot] = []
    held: list[str] = []
    for path in iter_fragment_files(canonical_path):
        data = _read_ledger_bytes(path)
        parsed = _parse_fragment(data)
        if parsed is None:
            held.append(path)
            continue
        promoted.append(FragmentSnapshot(path, data))
        for line, row in parsed:
            identity = row_identity(ledger_name, row)
            if identity not in seen:
                seen.add(identity)
                kept.append((parse_created_at(row.get("created_at")), line))
    return LedgerPromotion(ledger_name, canonical_path, _ordered_lines(kept), tuple(promoted), tuple(held))


def plan_promotion(repo_root: str) -> PromotionPlan:
    return PromotionPlan(tuple(plan_ledger(path) for path in canonical_ledger_paths(repo_root)))


def _check_snapshots(ledger: LedgerPromotion) -> None:
    for snapshot in ledger.promoted:
        if _read_ledger_bytes(snapshot.path) != snapshot.data:
            raise PromotionRaceError(f"Pending fragment {snapshot.path} changed during promotion; nothing appended")


def _append_lines(canonical_path: str, lines: tuple[str, ...]) -> None:
    with LedgerLock(canonical_path) as f:
        before = _read_ledger_bytes(canonical_path)
        if before and not before.endswith(b"\n"):
            f.write("\n")
        f.write("".join(line + "\n" for line in lines))
        f.flush()
        assert_ledger_prefix(canonical_path, before)


def _remove_fragment(path: str) -> None:
    os.remove(path)
    stem_dir = os.path.dirname(path)
    for directory in (stem_dir, os.path.dirname(stem_dir)):
        with contextlib.suppress(OSError):
            os.rmdir(directory)


def apply_promotion(plan: PromotionPlan) -> tuple[dict[str, int], tuple[str, ...]]:
    appended: dict[str, int] = {}
    deleted: list[str] = []
    for ledger in plan.ledgers:
        if not ledger.promoted:
            continue
        _check_snapshots(ledger)
        if ledger.lines:
            _append_lines(ledger.canonical_path, ledger.lines)
        appended[ledger.ledger_name] = len(ledger.lines)
        for snapshot in ledger.promoted:
            _remove_fragment(snapshot.path)
            deleted.append(snapshot.path)
    return appended, tuple(deleted)


def _relative(paths: Sequence[str], repo_root: str) -> tuple[str, ...]:
    return tuple(os.path.relpath(path, repo_root).replace(os.sep, "/") for path in paths)


def promote(
    repo_root: str,
    *,
    force: bool = False,
    every: int | None = None,
    environ: Mapping[str, str] = os.environ,
) -> PromotionResult:
    threshold = every if every is not None else promote_every_from_env(environ)
    root_intents = len(pending_root_intents(repo_root))
    threshold_met = root_intents >= threshold
    if not (force or threshold_met):
        return PromotionResult(root_intents, threshold, force, threshold_met)
    plan = plan_promotion(repo_root)
    appended, deleted = apply_promotion(plan)
    held = _relative([path for ledger in plan.ledgers for path in ledger.held], repo_root)
    return PromotionResult(root_intents, threshold, force, threshold_met, appended, _relative(deleted, repo_root), held)
