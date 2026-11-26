"""Tests for the single-flow failure-mode clean-up (scripts/prune_kb_single_flow_failures.py)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.prune_kb_single_flow_failures import EXECUTIONS_LEDGER, FAILURE_MODES_DIR, classify_tree, main

ROOT_A = "I-1795000001-alpha-flow"
ROOT_B = "I-1795000002-beta-flow"
TARGET_A = f"{ROOT_A}/P-1795000010-CLA-CO5.5/E-1795000020-CLA-CO5.5/_"
TARGET_B = f"{ROOT_B}/P-1795000011-CLA-CO5.5/E-1795000021-CLA-CO5.5/_"
PENDING_FRAGMENT = "holon-knowledge/ledger/pending/executions/I-1795000001.jsonl"

LEDGER_ROWS = [
    ("ACT-REV-1-a", TARGET_A),
    ("ACT-REV-2-a", TARGET_A),
    ("ACT-FIND-3-a", TARGET_A),
    ("ACT-FIND-4-b", TARGET_B),
    ("ACT-REV-5-legacy", "88"),
    ("ACT-1-a", TARGET_A),
    ("ACT-RES-1-a", TARGET_A),
]


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _rows_text(rows: list[tuple[str, str]]) -> str:
    return "".join(
        json.dumps({"action_id": action_id, "target": target, "created_at": f"2026-11-20T00:00:{i:02d}+00:00"}) + "\n"
        for i, (action_id, target) in enumerate(rows)
    )


def _failure(repo: Path, kb_id: str, refs: object, payload_target: str = TARGET_A) -> None:
    envelope = {
        "kb_id": f"KB-failure-{kb_id}",
        "kb_type": "failure_mode",
        "evidence": {"ledger_refs": refs, "failure_count": 2},
        "payload": {"name": f"Failure mode in {payload_target}", "description": "x"},
    }
    _write(repo, f"{FAILURE_MODES_DIR}/KB-failure-{kb_id}.json", json.dumps(envelope, indent=2))


def _build_repo(repo: Path, failures: dict[str, tuple[object, str]], pending: tuple[tuple[str, str], ...] = ()) -> Path:
    """Write a minimal tree: ledger, optional pending fragment, failure files, one pattern and one heuristic."""
    _write(repo, EXECUTIONS_LEDGER, _rows_text(LEDGER_ROWS))
    if pending:
        _write(repo, PENDING_FRAGMENT, _rows_text(list(pending)))
    for kb_id, (refs, payload_target) in failures.items():
        _failure(repo, kb_id, refs, payload_target)
    _write(repo, "holon-knowledge/kb/patterns/KB-pattern-x.json", '{"kb_id": "KB-pattern-x"}\n')
    _write(repo, "holon-knowledge/wisdom/heuristics/WB-heuristic-x.json", '{"wb_id": "WB-heuristic-x"}\n')
    return repo


def _tree(repo: Path) -> dict[str, str]:
    """Every file under ``holon-knowledge/`` with the digest of its bytes."""
    base = repo / "holon-knowledge"
    return {
        path.relative_to(repo).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(base.rglob("*"))
        if path.is_file()
    }


def _verdicts(repo: Path) -> dict[str, tuple[bool, str]]:
    return {
        v.path.name.removeprefix("KB-failure-").removesuffix(".json"): (v.prune, v.reason) for v in classify_tree(repo)
    }


MIXED = {
    "rev-one-root": (["ACT-REV-1-a", "ACT-REV-2-a"], TARGET_A),
    "find-two-roots": (["ACT-FIND-3-a", "ACT-FIND-4-b"], TARGET_A),
    "none-resolve": (["ACT-REV-9-gone", "ACT-FIND-9-gone"], TARGET_A),
    "partial-match": (["ACT-REV-1-a", "ACT-REV-9-gone"], TARGET_A),
    "partial-mismatch": (["ACT-REV-1-a", "ACT-REV-9-gone"], TARGET_B),
    "legacy-target": (["ACT-REV-5-legacy"], "88"),
    "empty-refs": ([], TARGET_A),
}


def test_classification_contract(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path, MIXED)
    _write(repo, f"{FAILURE_MODES_DIR}/KB-failure-broken.json", "{not json")

    assert _verdicts(repo) == {
        "rev-one-root": (True, "single-flow-chatter"),
        "find-two-roots": (False, "multiple-intent-roots"),
        "none-resolve": (False, "no-resolved-ref"),
        "partial-match": (True, "single-flow-chatter"),
        "partial-mismatch": (False, "multiple-intent-roots"),
        "legacy-target": (False, "no-intent-root"),
        "empty-refs": (False, "no-refs"),
        "broken": (False, "unreadable"),
    }


@pytest.mark.parametrize(
    "other_ref",
    ["ACT-1-a", "ACT-RES-1-a", "ACT-GATE-1-a", "ACT-WITHDRAW-1-a", "ACT-CAL-1-a", "ACT-BUDGET-1-a", "REV-1-a", "E-1-a"],
)
def test_any_other_ref_kind_is_kept(tmp_path: Path, other_ref: str) -> None:
    repo = _build_repo(tmp_path, {"other": (["ACT-REV-1-a", other_ref], TARGET_A)})

    assert _verdicts(repo) == {"other": (False, "non-review-ref")}


def test_ref_resolved_only_through_pending_fragment_is_pruned(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path, {"pending-only": (["ACT-REV-77-a"], TARGET_A)}, pending=(("ACT-REV-77-a", TARGET_A),))

    assert _verdicts(repo) == {"pending-only": (True, "single-flow-chatter")}


def test_dry_run_prints_count_and_deletes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _build_repo(tmp_path, MIXED)
    before = _tree(repo)

    assert main(["--repo", str(repo)]) == 0

    out = capsys.readouterr().out
    assert "single-flow review chatter: 2 of 7 KB-failure files (dry run)" in out
    assert "kept multiple-intent-roots: 2" in out
    assert _tree(repo) == before


def test_apply_deletes_exactly_the_chatter(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _build_repo(tmp_path, MIXED)
    before = _tree(repo)
    pruned = {f"{FAILURE_MODES_DIR}/KB-failure-{kb_id}.json" for kb_id in ("rev-one-root", "partial-match")}

    assert main(["--repo", str(repo), "--apply"]) == 0

    assert _tree(repo) == {rel: digest for rel, digest in before.items() if rel not in pruned}
    assert "single-flow review chatter: 2 of 7 KB-failure files (deleted)" in capsys.readouterr().out
    assert main(["--repo", str(repo)]) == 0
    assert "single-flow review chatter: 0 of 5 KB-failure files (dry run)" in capsys.readouterr().out


def test_list_prints_prunable_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _build_repo(tmp_path, MIXED)

    assert main(["--repo", str(repo), "--list"]) == 0

    listed = [line for line in capsys.readouterr().out.splitlines() if line.startswith(FAILURE_MODES_DIR)]
    assert listed == [
        f"{FAILURE_MODES_DIR}/KB-failure-partial-match.json",
        f"{FAILURE_MODES_DIR}/KB-failure-rev-one-root.json",
    ]


def test_malformed_ledger_line_refuses_apply(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path, MIXED)
    ledger = repo / EXECUTIONS_LEDGER
    ledger.write_text(ledger.read_text(encoding="utf-8") + "{truncated\n", encoding="utf-8")
    before = _tree(repo)

    assert main(["--repo", str(repo), "--apply"]) == 2
    assert _tree(repo) == before


def test_missing_failure_dir_reports_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--repo", str(tmp_path)]) == 0
    assert "single-flow review chatter: 0 of 0 KB-failure files (dry run)" in capsys.readouterr().out
