"""Property tests pinning the contract between the merged ledger reader and ``holon ledger promote``."""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from sandbox_executor.ledger import promote, read_merged_rows
from sandbox_executor.ledger.merged import parse_jsonl_rows, row_identity

LEDGER = "plans.jsonl"
LEDGER_REL = "holon-knowledge/ledger"
ROOTS = ("I-1-a", "I-2-b", "I-3-c")
HELD_ROOT = "I-9-held"
INSTANTS = (
    None,
    "2026-01-01T00:00:00Z",
    "2026-01-01T00:00:00+00:00",
    "2026-01-02T12:00:00Z",
    "2026-01-02T13:00:00+01:00",
    "2025-12-31T23:59:59Z",
    "not a time",
)

_rows = st.fixed_dictionaries(
    {
        "intent_branch": st.sampled_from([f"{root}/_" for root in ROOTS]),
        "ledger_revision": st.sampled_from([None, 1, 2]),
        "note": st.sampled_from(["", "x", "été"]),
    },
    optional={"plan_id": st.sampled_from(["P-1", "P-2", "P-3"]), "created_at": st.sampled_from(INSTANTS)},
)
_round = st.lists(st.tuples(st.sampled_from((*ROOTS, HELD_ROOT)), _rows), max_size=6)


def _line(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False)


def _identities(rows: list[dict]) -> Counter[str]:
    return Counter(row_identity(LEDGER, row) for row in rows)


def _write_round(ledger_dir: Path, entries: list[tuple[str, dict]]) -> None:
    for root, row in entries:
        path = ledger_dir / "pending" / "plans" / f"{root}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = "{held\n" if root == HELD_ROOT and not path.exists() else ""
        with path.open("a", encoding="utf-8") as f:
            f.write(prefix + _line(row) + "\n")


def _promote_round(repo: Path, ledger_dir: Path) -> None:
    canonical = ledger_dir / LEDGER
    before_bytes = canonical.read_bytes()
    merged_before = _identities(read_merged_rows(str(canonical)))
    promote(str(repo), force=True, environ={})
    after_bytes = canonical.read_bytes()
    assert after_bytes.startswith(before_bytes)
    assert _identities(read_merged_rows(str(canonical))) == merged_before
    promote(str(repo), force=True, environ={})
    assert canonical.read_bytes() == after_bytes
    remaining = sorted(path.name for path in (ledger_dir / "pending").rglob("*.jsonl"))
    assert remaining in ([], [f"{HELD_ROOT}.jsonl"])


@settings(max_examples=100, deadline=None)
@given(canonical_rows=st.lists(_rows, max_size=5), rounds=st.lists(_round, min_size=1, max_size=3))
def test_promotion_is_additions_only_and_matches_the_reader(canonical_rows: list[dict], rounds: list) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        ledger_dir = repo / LEDGER_REL
        ledger_dir.mkdir(parents=True)
        original = "".join(_line(row) + "\n" for row in canonical_rows).encode("utf-8")
        (ledger_dir / LEDGER).write_bytes(original)
        promoted_identities: set[str] = set()
        for entries in rounds:
            _write_round(ledger_dir, entries)
            promoted_identities |= {row_identity(LEDGER, row) for root, row in entries if root != HELD_ROOT}
            _promote_round(repo, ledger_dir)
        final = (ledger_dir / LEDGER).read_bytes()
        assert final.startswith(original)
        original_identities = _identities(canonical_rows)
        appended = _identities(parse_jsonl_rows(final[len(original) :].decode("utf-8")))
        assert set(appended) == promoted_identities - set(original_identities)
        assert all(count == 1 for count in appended.values())
