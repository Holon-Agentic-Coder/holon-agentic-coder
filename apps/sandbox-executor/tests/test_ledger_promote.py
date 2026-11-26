"""``ledger.promote``: additions-only, idempotent, threshold-gated promotion of pending fragments into canonical."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.ledger import PromotionRaceError, apply_promotion, plan_promotion, promote, read_merged_rows
from sandbox_executor.ledger.merged import row_identity
from sandbox_executor.ledger.promote_threshold import pending_root_intents, promote_every_from_env

LEDGER_REL = "holon-knowledge/ledger"
SEED_ROWS = {
    "intents.jsonl": {"branch": "I-0-seed/_", "intent_id": "I-0-seed", "created_at": "2026-01-01T00:00:00Z"},
    "plans.jsonl": {"plan_id": "P-0", "intent_branch": "I-0-seed/_", "created_at": "2026-01-01T00:00:00Z"},
    "executions.jsonl": {"execution_id": "E-0", "plan_branch": "I-0-seed/P-0/_", "created_at": "2026-01-01T00:00:00Z"},
}


def _seed_repo(root: Path) -> dict[str, bytes]:
    ledger_dir = root / LEDGER_REL
    ledger_dir.mkdir(parents=True)
    for name, row in SEED_ROWS.items():
        (ledger_dir / name).write_text(json.dumps(row) + "\n", encoding="utf-8")
    return {name: (ledger_dir / name).read_bytes() for name in SEED_ROWS}


def _plan_row(plan_id: str, root: str, created_at: str | None = "2026-02-01T00:00:00Z", **extra: object) -> dict:
    row: dict = {"plan_id": plan_id, "intent_branch": f"{root}/_", **extra}
    if created_at is not None:
        row["created_at"] = created_at
    return row


def _write_fragment(root: Path, stem: str, fragment_root: str, lines: list[str]) -> Path:
    path = root / LEDGER_REL / "pending" / stem / f"{fragment_root}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write("".join(line + "\n" for line in lines))
    return path


def _seed_roots(root: Path, count: int) -> None:
    for index in range(count):
        _write_fragment(root, "plans", f"I-{index}-r", [json.dumps(_plan_row(f"P-{index}", f"I-{index}-r"))])


def _canonical(root: Path, name: str = "plans.jsonl") -> bytes:
    return (root / LEDGER_REL / name).read_bytes()


def _pending_files(root: Path) -> list[Path]:
    return sorted((root / LEDGER_REL / "pending").rglob("*.jsonl")) if (root / LEDGER_REL / "pending").exists() else []


def test_below_threshold_changes_nothing(tmp_path: Path) -> None:
    seeded = _seed_repo(tmp_path)
    _seed_roots(tmp_path, 9)
    result = promote(str(tmp_path), environ={})
    assert (result.root_intents, result.every, result.threshold_met) == (9, 10, False)
    assert result.appended == {} and result.deleted == ()
    assert _canonical(tmp_path) == seeded["plans.jsonl"]
    assert len(_pending_files(tmp_path)) == 9


def test_threshold_met_promotes_everything(tmp_path: Path) -> None:
    seeded = _seed_repo(tmp_path)
    _seed_roots(tmp_path, 10)
    result = promote(str(tmp_path), environ={})
    assert result.threshold_met and result.appended == {"plans.jsonl": 10}
    assert len(result.deleted) == 10
    assert all(path.startswith(f"{LEDGER_REL}/pending/plans/") for path in result.deleted)
    assert _canonical(tmp_path).startswith(seeded["plans.jsonl"])
    assert _pending_files(tmp_path) == []
    assert not (tmp_path / LEDGER_REL / "pending").exists()


def test_unscoped_is_not_counted_but_promoted_once_met(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    _seed_roots(tmp_path, 9)
    _write_fragment(tmp_path, "executions", "_unscoped", [json.dumps({"action_id": "ACT-u", "entry_type": "action"})])
    assert len(pending_root_intents(str(tmp_path))) == 9
    assert not promote(str(tmp_path), environ={}).threshold_met
    _write_fragment(tmp_path, "intents", "I-9-r", [json.dumps({"branch": "I-9-r/_", "intent_id": "I-9-r"})])
    result = promote(str(tmp_path), environ={})
    assert result.threshold_met and result.appended["executions.jsonl"] == 1
    assert b"ACT-u" in _canonical(tmp_path, "executions.jsonl")


def test_force_promotes_a_single_root(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    _seed_roots(tmp_path, 1)
    result = promote(str(tmp_path), force=True, environ={})
    assert result.forced and not result.threshold_met and result.appended == {"plans.jsonl": 1}


@pytest.mark.parametrize(("value", "expected"), [("3", 3), ("", 10), ("  ", 10), (" 4 ", 4), (None, 10)])
def test_promote_every_from_env(value: str | None, expected: int) -> None:
    environ = {} if value is None else {"HOLON_LEDGER_PROMOTE_EVERY": value}
    assert promote_every_from_env(environ) == expected


@pytest.mark.parametrize("value", ["abc", "0", "-2", "1.5"])
def test_invalid_promote_every_from_env_raises(value: str) -> None:
    with pytest.raises(ValueError, match="HOLON_LEDGER_PROMOTE_EVERY"):
        promote_every_from_env({"HOLON_LEDGER_PROMOTE_EVERY": value})


def test_explicit_every_wins_over_environment(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    _seed_roots(tmp_path, 2)
    environ = {"HOLON_LEDGER_PROMOTE_EVERY": "1"}
    assert not promote(str(tmp_path), every=3, environ=environ).threshold_met
    result = promote(str(tmp_path), environ=environ)
    assert result.every == 1 and result.threshold_met


def test_appends_verbatim_lines_in_instant_order(tmp_path: Path) -> None:
    seeded = _seed_repo(tmp_path)
    late = json.dumps(_plan_row("P-late", "I-1-a", "2026-03-01T00:00:00Z", title="été"), ensure_ascii=False)
    early = '{"created_at": "2026-02-01T00:00:00+00:00",   "intent_branch": "I-2-b/_", "plan_id": "P-early"}'
    untimed = json.dumps(_plan_row("P-untimed", "I-1-a", None))
    _write_fragment(tmp_path, "plans", "I-1-a", [late, untimed])
    _write_fragment(tmp_path, "plans", "I-2-b", [early])
    promote(str(tmp_path), force=True, environ={})
    after = _canonical(tmp_path)
    assert after.startswith(seeded["plans.jsonl"])
    appended = after[len(seeded["plans.jsonl"]) :].decode("utf-8")
    assert appended == f"{early}\n{late}\n{untimed}\n"


def test_second_run_is_a_byte_identical_no_op(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    _seed_roots(tmp_path, 3)
    promote(str(tmp_path), force=True, environ={})
    before = {name: _canonical(tmp_path, name) for name in SEED_ROWS}
    result = promote(str(tmp_path), force=True, environ={})
    assert result.appended == {} and result.deleted == ()
    assert {name: _canonical(tmp_path, name) for name in SEED_ROWS} == before


def test_crash_recovery_skips_rows_already_in_canonical(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    line = json.dumps(_plan_row("P-1", "I-1-a"))
    with (tmp_path / LEDGER_REL / "plans.jsonl").open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    _write_fragment(tmp_path, "plans", "I-1-a", [line])
    before = _canonical(tmp_path)
    result = promote(str(tmp_path), force=True, environ={})
    assert result.appended == {"plans.jsonl": 0} and len(result.deleted) == 1
    assert _canonical(tmp_path) == before
    assert _pending_files(tmp_path) == []


def test_duplicates_across_fragments_are_appended_once(tmp_path: Path) -> None:
    seeded = _seed_repo(tmp_path)
    line = json.dumps(_plan_row("P-1", "I-1-a"))
    _write_fragment(tmp_path, "plans", "I-1-a", [line])
    _write_fragment(tmp_path, "plans", "I-2-b", [line])
    result = promote(str(tmp_path), force=True, environ={})
    assert result.appended == {"plans.jsonl": 1}
    assert _canonical(tmp_path) == seeded["plans.jsonl"] + (line + "\n").encode()


def test_canonical_without_trailing_newline_gains_one(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    canonical = tmp_path / LEDGER_REL / "plans.jsonl"
    seed_line = json.dumps(SEED_ROWS["plans.jsonl"])
    canonical.write_bytes(seed_line.encode())
    line = json.dumps(_plan_row("P-1", "I-1-a"))
    _write_fragment(tmp_path, "plans", "I-1-a", [line])
    promote(str(tmp_path), force=True, environ={})
    assert canonical.read_text(encoding="utf-8") == f"{seed_line}\n{line}\n"


def test_missing_canonical_file_is_created(tmp_path: Path) -> None:
    (tmp_path / LEDGER_REL).mkdir(parents=True)
    line = json.dumps(_plan_row("P-1", "I-1-a"))
    _write_fragment(tmp_path, "plans", "I-1-a", [line])
    promote(str(tmp_path), force=True, environ={})
    assert _canonical(tmp_path) == (line + "\n").encode()
    assert not (tmp_path / LEDGER_REL / "intents.jsonl").exists()


def test_malformed_fragment_is_held(tmp_path: Path) -> None:
    seeded = _seed_repo(tmp_path)
    held = _write_fragment(tmp_path, "plans", "I-1-a", [json.dumps(_plan_row("P-1", "I-1-a")), "{not json"])
    good_line = json.dumps(_plan_row("P-2", "I-2-b"))
    _write_fragment(tmp_path, "plans", "I-2-b", [good_line])
    held_bytes = held.read_bytes()
    result = promote(str(tmp_path), force=True, environ={})
    assert result.held == (f"{LEDGER_REL}/pending/plans/I-1-a.jsonl",)
    assert result.deleted == (f"{LEDGER_REL}/pending/plans/I-2-b.jsonl",)
    assert held.read_bytes() == held_bytes
    assert _canonical(tmp_path) == seeded["plans.jsonl"] + (good_line + "\n").encode()


def test_fragment_changed_between_plan_and_apply_raises(tmp_path: Path) -> None:
    seeded = _seed_repo(tmp_path)
    fragment = _write_fragment(tmp_path, "plans", "I-1-a", [json.dumps(_plan_row("P-1", "I-1-a"))])
    plan = plan_promotion(str(tmp_path))
    _write_fragment(tmp_path, "plans", "I-1-a", [json.dumps(_plan_row("P-2", "I-1-a"))])
    with pytest.raises(PromotionRaceError):
        apply_promotion(plan)
    assert _canonical(tmp_path) == seeded["plans.jsonl"]
    assert fragment.exists()


def test_race_on_a_later_ledger_appends_to_no_ledger(tmp_path: Path) -> None:
    seeded = _seed_repo(tmp_path)
    exec_row = {"execution_id": "E-1", "plan_branch": "I-1-a/P-1/_", "created_at": "2026-02-01T00:00:00Z"}
    intent_row = {"branch": "I-1-a/_", "intent_id": "I-1-a", "created_at": "2026-02-01T00:00:00Z"}
    _write_fragment(tmp_path, "executions", "I-1-a", [json.dumps(exec_row)])
    _write_fragment(tmp_path, "intents", "I-1-a", [json.dumps(intent_row)])
    _write_fragment(tmp_path, "plans", "I-1-a", [json.dumps(_plan_row("P-1", "I-1-a"))])
    plan = plan_promotion(str(tmp_path))
    last = plan.ledgers[-1]
    _write_fragment(tmp_path, last.ledger_name.removesuffix(".jsonl"), "I-1-a", ["{}"])
    with pytest.raises(PromotionRaceError, match=last.ledger_name):
        apply_promotion(plan)
    for name, data in seeded.items():
        assert _canonical(tmp_path, name) == data
    assert len(_pending_files(tmp_path)) == 3


def test_empty_pending_directories_are_removed_and_others_kept(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    _write_fragment(tmp_path, "plans", "I-1-a", [json.dumps(_plan_row("P-1", "I-1-a"))])
    _write_fragment(tmp_path, "intents", "I-1-a", ["[1]"])
    promote(str(tmp_path), force=True, environ={})
    pending = tmp_path / LEDGER_REL / "pending"
    assert not (pending / "plans").exists()
    assert (pending / "intents" / "I-1-a.jsonl").exists()


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def test_promotion_touches_only_ledger_paths(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    _seed_roots(tmp_path, 3)
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "seed")
    promote(str(tmp_path), force=True, environ={})
    changed = [line[3:] for line in _git(tmp_path, "status", "--porcelain", "-uall").splitlines()]
    assert changed and all(path.startswith(f"{LEDGER_REL}/") for path in changed)


def _identities(root: Path, name: str) -> list[str]:
    return sorted(row_identity(name, row) for row in read_merged_rows(str(root / LEDGER_REL / name)))


def test_reader_sees_the_same_rows_before_and_after(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    _write_fragment(tmp_path, "plans", "I-1-a", [json.dumps(_plan_row("P-1", "I-1-a", "2025-06-01T00:00:00Z"))])
    _write_fragment(tmp_path, "executions", "I-1-a", [json.dumps({"action_id": "ACT-1", "entry_type": "action"})])
    before = {name: _identities(tmp_path, name) for name in SEED_ROWS}
    promote(str(tmp_path), force=True, environ={})
    assert {name: _identities(tmp_path, name) for name in SEED_ROWS} == before


def test_reader_order_is_unchanged_when_fragments_are_not_older(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    _write_fragment(tmp_path, "plans", "I-2-b", [json.dumps(_plan_row("P-2", "I-2-b", "2026-03-01T00:00:00Z"))])
    _write_fragment(tmp_path, "plans", "I-1-a", [json.dumps(_plan_row("P-1", "I-1-a", "2026-01-01T00:00:00Z"))])
    canonical = str(tmp_path / LEDGER_REL / "plans.jsonl")
    before = read_merged_rows(canonical)
    promote(str(tmp_path), force=True, environ={})
    assert read_merged_rows(canonical) == before
