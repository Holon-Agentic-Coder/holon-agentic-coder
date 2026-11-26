"""The route-on-close append funnel: canonical ledger rows land in ``pending/<stem>/<root-intent-id>.jsonl``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sandbox_executor.ledger import atomic_ledger_append, read_merged_rows, validate_ledger_file_unique_ids
from sandbox_executor.ledger.fragments import PENDING_EXCLUDE_PATHSPEC, is_ledger_commit_path, ledger_commit_paths
from sandbox_executor.ledger.order import to_utc_z, utc_now_z
from sandbox_executor.ledger.route import checked_out_root

LEDGER_REL = "holon-knowledge/ledger"
INTENT = "I-1-alpha"


def _canonical(root: Path, name: str = "executions.jsonl", rows: tuple[dict, ...] = ()) -> Path:
    path = root / LEDGER_REL / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _append(path: Path, *rows: object) -> None:
    with atomic_ledger_append(str(path)) as f:
        for row in rows:
            f.write((row if isinstance(row, str) else json.dumps(row)) + "\n")


def _fragment(root: Path, stem: str, fragment: str) -> Path:
    return root / LEDGER_REL / "pending" / stem / f"{fragment}.jsonl"


def test_row_lands_in_its_root_intent_fragment_and_reads_back_merged(tmp_path: Path) -> None:
    seeded = {"execution_id": "E-0", "plan_branch": "I-0-seed/P-0/_", "created_at": "2026-01-01T00:00:00Z"}
    canonical = _canonical(tmp_path, rows=(seeded,))
    before = canonical.read_bytes()
    row = {"execution_id": "E-1", "plan_branch": f"{INTENT}--2-child/P-1/_", "created_at": "2026-01-02T00:00:00Z"}

    _append(canonical, row)

    assert canonical.read_bytes() == before
    assert _fragment(tmp_path, "executions", INTENT).read_text(encoding="utf-8") == json.dumps(row) + "\n"
    assert read_merged_rows(str(canonical)) == [seeded, row]


def test_one_handle_splits_rows_of_several_root_intents_in_order(tmp_path: Path) -> None:
    canonical = _canonical(tmp_path, "plans.jsonl")
    rows = [
        {"plan_id": "P-1", "intent_branch": "I-1-alpha/_", "created_at": "2026-01-01T00:00:00Z"},
        {"plan_id": "P-2", "intent_branch": "I-2-beta/_", "created_at": "2026-01-01T00:00:01Z"},
        {"plan_id": "P-3", "intent_branch": "I-1-alpha/_", "created_at": "2026-01-01T00:00:02Z"},
    ]

    _append(canonical, *rows)

    alpha = _fragment(tmp_path, "plans", "I-1-alpha").read_text(encoding="utf-8").splitlines()
    beta = _fragment(tmp_path, "plans", "I-2-beta").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["plan_id"] for line in alpha] == ["P-1", "P-3"]
    assert [json.loads(line)["plan_id"] for line in beta] == ["P-2"]
    assert not canonical.read_bytes()


def test_unparseable_and_branchless_rows_go_to_the_unscoped_fragment(tmp_path: Path) -> None:
    canonical = _canonical(tmp_path)

    _append(canonical, "not json", {"action_id": "ACT-1", "created_at": "2026-01-01T00:00:00Z"}, "[1, 2]")

    lines = _fragment(tmp_path, "executions", "_unscoped").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "not json"
    assert json.loads(lines[1])["action_id"] == "ACT-1"
    assert lines[2] == "[1, 2]"


def test_branchless_row_follows_the_checked_out_intent_branch(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text(f"ref: refs/heads/{INTENT}/P-1/E-1/_\n", encoding="utf-8")
    canonical = _canonical(tmp_path)

    _append(canonical, {"action_id": "ACT-1", "execution_id": "REV-1", "created_at": "2026-01-01T00:00:00Z"})

    assert checked_out_root(str(canonical)) == INTENT
    assert json.loads(_fragment(tmp_path, "executions", INTENT).read_text(encoding="utf-8"))["action_id"] == "ACT-1"


@pytest.mark.parametrize(
    ("head", "expected"),
    [("ref: refs/heads/main\n", None), ("0123456789abcdef0123456789abcdef01234567\n", None)],
)
def test_main_or_detached_head_is_not_an_intent_root(tmp_path: Path, head: str, expected: str | None) -> None:
    worktree_git = tmp_path / "gitdirs" / "wt"
    worktree_git.mkdir(parents=True)
    (worktree_git / "HEAD").write_text(head, encoding="utf-8")
    (tmp_path / ".git").write_text("gitdir: gitdirs/wt\n", encoding="utf-8")

    assert checked_out_root(str(_canonical(tmp_path))) is expected


def test_exception_inside_the_handle_writes_nothing(tmp_path: Path) -> None:
    canonical = _canonical(tmp_path)

    with pytest.raises(RuntimeError), atomic_ledger_append(str(canonical)) as f:
        f.write(json.dumps({"plan_branch": f"{INTENT}/_"}) + "\n")
        raise RuntimeError("boom")

    assert not (tmp_path / LEDGER_REL / "pending").exists()


def test_non_canonical_path_receives_the_exact_bytes(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch" / "executions.jsonl"

    with atomic_ledger_append(str(scratch)) as f:
        f.write('{"created_at": "2026-01-01T00:00:00+00:00"}\n')
        f.write("partial")

    assert scratch.read_text(encoding="utf-8") == '{"created_at": "2026-01-01T00:00:00+00:00"}\npartial'
    assert not (tmp_path / "scratch" / "pending").exists()


def test_offset_timestamp_becomes_z_and_a_missing_one_is_stamped(tmp_path: Path) -> None:
    canonical = _canonical(tmp_path)

    _append(
        canonical,
        {"plan_branch": f"{INTENT}/_", "execution_id": "E-1", "created_at": "2026-01-01T02:00:00+02:00"},
        {"plan_branch": f"{INTENT}/_", "execution_id": "E-2"},
        {"plan_branch": f"{INTENT}/_", "execution_id": "E-3", "created_at": "yesterday"},
    )

    rows = [json.loads(line) for line in _fragment(tmp_path, "executions", INTENT).read_text().splitlines()]
    assert rows[0]["created_at"] == "2026-01-01T00:00:00.000000Z"
    assert rows[1]["created_at"].endswith("Z")
    assert rows[2]["created_at"] == "yesterday"


def test_z_row_is_written_byte_identical(tmp_path: Path) -> None:
    canonical = _canonical(tmp_path)
    line = '{"plan_branch":"I-1-alpha/_",  "created_at": "2026-01-01T00:00:00.5Z"}'

    _append(canonical, line)

    assert _fragment(tmp_path, "executions", INTENT).read_text(encoding="utf-8") == line + "\n"


def test_z_helpers_keep_the_instant() -> None:
    assert to_utc_z("2026-01-01T00:00:00+00:00") == "2026-01-01T00:00:00.000000Z"
    assert to_utc_z("2026-01-01T00:00:00") == "2026-01-01T00:00:00.000000Z"
    assert to_utc_z("nope") is None
    assert to_utc_z(None) is None
    assert utc_now_z().endswith("Z")


def test_duplicate_plan_id_across_canonical_and_fragment_is_rejected(tmp_path: Path) -> None:
    seeded = {"plan_id": "P-1", "intent_branch": f"{INTENT}/_", "created_at": "2026-01-01T00:00:00Z"}
    canonical = _canonical(tmp_path, "plans.jsonl", rows=(seeded,))
    _append(canonical, {**seeded, "intent_branch": INTENT, "created_at": "2026-01-02T00:00:00Z"})

    with pytest.raises(ValueError, match="P-1"):
        validate_ledger_file_unique_ids(str(canonical), id_key="plan_id", parent_key="intent_branch")


def test_commit_paths_name_only_existing_canonical_files_and_pending_dirs(tmp_path: Path) -> None:
    assert ledger_commit_paths(str(tmp_path)) == []
    (tmp_path / LEDGER_REL / "pending" / "plans").mkdir(parents=True)
    _canonical(tmp_path, "executions.jsonl")

    assert ledger_commit_paths(str(tmp_path), "plans.jsonl", "plans.jsonl") == [f"{LEDGER_REL}/pending/plans"]
    assert ledger_commit_paths(str(tmp_path)) == [f"{LEDGER_REL}/pending/plans", f"{LEDGER_REL}/executions.jsonl"]

    _append(_canonical(tmp_path, "executions.jsonl"), {"plan_branch": f"{INTENT}/_"})
    assert ledger_commit_paths(str(tmp_path), "executions.jsonl") == [
        f"{LEDGER_REL}/executions.jsonl",
        f"{LEDGER_REL}/pending/executions",
    ]


def test_ledger_commit_path_predicate_and_exclude_spec() -> None:
    assert is_ledger_commit_path(f"{LEDGER_REL}/plans.jsonl")
    assert is_ledger_commit_path(f"{LEDGER_REL}/pending/plans/{INTENT}.jsonl")
    assert not is_ledger_commit_path(f"{LEDGER_REL}/pending")
    assert not is_ledger_commit_path("plans/P-1.md")
    assert f":(exclude){LEDGER_REL}/pending" == PENDING_EXCLUDE_PATHSPEC
