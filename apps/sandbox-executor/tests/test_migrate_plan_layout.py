"""Tests for the one-off plans/ layout migration (scripts/migrate_plan_layout.py)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.migrate_plan_layout import EXECUTIONS_LEDGER, PLANS_LEDGER, main

ROOT = "I-1794391245-worktree"
LEGACY_PLAN = "P-1794391254-8ec0e2e8-claude-agent-claude-sonnet-5-5"
CHILD = "I-1794617704-touchset/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5--1-add-the-planner"
CHILD_PLAN = "P-1794617800-aa11bb22-claude-agent-claude-opus-5-5"
NEW_PLAN = "P-1795000000-CLA-CS5.5"
ROWLESS = "P-1794000100-claude-agent-claude-fable-5"
ROWLESS_HEAD = "I-1794000000-rowless/P-1794000100-0badc0de-claude-agent-claude-fable-5/E-1794000200-1234abcd-a-m/_"


def _write(repo: Path, rel: str, text: str = "# file\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tree(repo: Path) -> dict[str, str]:
    """Every file under ``repo`` with the digest of its bytes."""
    return {
        str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest() for p in repo.rglob("*") if p.is_file()
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    plan_rows = [
        # Legacy hex plan id whose plan file was named without the hex; superseded once (same plan_file).
        {
            "plan_id": LEGACY_PLAN,
            "intent_branch": f"{ROOT}/_",
            "plan_file": "plans/P-1794391254-claude-agent-claude-sonnet-5-5.md",
        },
        {
            "plan_id": LEGACY_PLAN,
            "intent_branch": f"{ROOT}/_",
            "plan_file": "plans/P-1794391254-claude-agent-claude-sonnet-5-5.md",
            "ledger_revision": 2,
        },
        # A child intent nested below a plan segment.
        {"plan_id": CHILD_PLAN, "intent_branch": f"{CHILD}/_", "plan_file": f"plans/{CHILD_PLAN}.md"},
        # A new-generation plan already in the new layout.
        {
            "plan_id": NEW_PLAN,
            "intent_branch": "I-1794999999-done/_",
            "plan_file": f"plans/I-1794999999-done/{NEW_PLAN}.md",
        },
    ]
    ledger = "".join(json.dumps(row) + "\n" for row in plan_rows)
    # One compact-separator row proves the rewrite keeps a row's own serialisation.
    ledger += (
        json.dumps(
            {"plan_id": "P-1-x", "intent_branch": "I-1-x/_", "plan_file": "plans/P-1-x.md"}, separators=(",", ":")
        )
        + "\n"
    )
    _write(tmp_path, PLANS_LEDGER, ledger)
    execution_rows = [
        {"action_id": "ACT-1", "entry_type": "action", "execution_id": "E-1"},
        {
            "action_id": "ACT-CAL-1",
            "calibration_report_path": f"plans/{LEGACY_PLAN}_calibration.md",
            "execution_branch": "x",
        },
        {"entry_type": "summary", "execution_id": "E-2", "plan_branch": f"{ROOT}/{LEGACY_PLAN}/_"},
    ]
    _write(tmp_path, EXECUTIONS_LEDGER, "".join(json.dumps(row) + "\n" for row in execution_rows))
    for rel in (
        "plans/P-1794391254-claude-agent-claude-sonnet-5-5.md",
        f"plans/{LEGACY_PLAN}_calibration.md",
        f"plans/{CHILD_PLAN}.md",
        f"plans/I-1794999999-done/{NEW_PLAN}.md",
        "plans/P-1-x.md",
        f"plans/{ROWLESS}.md",
        "plans/P-1794000300-orphan-agent-model.md",
    ):
        _write(tmp_path, rel, f"# {rel}\n")
    (tmp_path / "heads.txt").write_text(f"{ROWLESS_HEAD}\n{ROOT}/{LEGACY_PLAN}/E-1794391300-ab12cd34-a-m/_\n")
    return tmp_path


def _run(repo: Path, *extra: str) -> int:
    return main(["--repo", str(repo), "--pr-heads", str(repo / "heads.txt"), *extra])


def test_migration_moves_every_flat_file_to_its_branch_path_and_rewrites_only_the_two_fields(repo, capsys) -> None:
    plans_before = (repo / PLANS_LEDGER).read_text(encoding="utf-8").splitlines()
    executions_before = (repo / EXECUTIONS_LEDGER).read_text(encoding="utf-8").splitlines()

    assert _run(repo) == 0
    report = capsys.readouterr().out

    expected = {
        f"plans/{ROOT}/{LEGACY_PLAN}.md",
        f"plans/{ROOT}/{LEGACY_PLAN}_calibration.md",
        f"plans/{CHILD}/{CHILD_PLAN}.md",
        f"plans/I-1794999999-done/{NEW_PLAN}.md",
        "plans/I-1-x/P-1-x.md",
        "plans/I-1794000000-rowless/P-1794000100-0badc0de-claude-agent-claude-fable-5.md",
        "plans/legacy/P-1794000300-orphan-agent-model.md",
    }
    assert {str(p.relative_to(repo)) for p in (repo / "plans").rglob("*.md")} == expected
    assert not [p for p in (repo / "plans").iterdir() if p.is_file()], "a plan file is left directly in plans/"

    plans_after = (repo / PLANS_LEDGER).read_text(encoding="utf-8").splitlines()
    executions_after = (repo / EXECUTIONS_LEDGER).read_text(encoding="utf-8").splitlines()
    for before, after, path_field in (
        (plans_before, plans_after, "plan_file"),
        (executions_before, executions_after, "calibration_report_path"),
    ):
        assert len(before) == len(after)
        for old_line, new_line in zip(before, after, strict=True):
            old, new = json.loads(old_line), json.loads(new_line)
            assert list(old) == list(new), "keys keep their order"
            assert {k: v for k, v in old.items() if k != path_field} == {
                k: v for k, v in new.items() if k != path_field
            }
            if path_field in new:
                assert (repo / new[path_field]).exists()
    assert plans_after[3] == plans_before[3], "an already migrated row is byte-identical"
    assert plans_after[4] == '{"plan_id":"P-1-x","intent_branch":"I-1-x/_","plan_file":"plans/I-1-x/P-1-x.md"}'
    assert executions_after[0] == executions_before[0] and executions_after[2] == executions_before[2]

    assert f"plans/{CHILD_PLAN}.md -> plans/{CHILD}/{CHILD_PLAN}.md" in report
    assert f"{PLANS_LEDGER}:3 plan_file: plans/{CHILD_PLAN}.md -> plans/{CHILD}/{CHILD_PLAN}.md" in report
    unmatched = report.split("match no supplied pull request head branch:", 1)[1]
    assert "plans/legacy/P-1794000300-orphan-agent-model.md" in unmatched
    assert f"plans/{CHILD}/{CHILD_PLAN}.md" in unmatched
    assert f"plans/{ROOT}/{LEGACY_PLAN}.md" not in unmatched


def test_a_second_run_changes_nothing(repo, capsys) -> None:
    assert _run(repo) == 0
    migrated = _tree(repo)
    capsys.readouterr()
    assert _run(repo) == 0
    assert "Nothing to migrate" in capsys.readouterr().out
    assert _tree(repo) == migrated


def test_a_dry_run_reports_without_changing_anything(repo, capsys) -> None:
    before = _tree(repo)
    assert _run(repo, "--dry-run") == 0
    assert "Would move 6 file(s):" in capsys.readouterr().out
    assert _tree(repo) == before


def test_an_occupied_target_aborts_before_anything_changes(repo, capsys) -> None:
    _write(repo, f"plans/{CHILD}/{CHILD_PLAN}.md", "# someone else's file\n")
    before = _tree(repo)
    assert _run(repo) == 1
    assert "the target already exists" in capsys.readouterr().err
    assert _tree(repo) == before


def test_two_files_for_one_target_abort_before_anything_changes(repo, capsys) -> None:
    rows = (repo / PLANS_LEDGER).read_text(encoding="utf-8")
    twin = {"plan_id": CHILD_PLAN, "intent_branch": f"{CHILD}/_", "plan_file": "plans/P-1794617800-twin.md"}
    (repo / PLANS_LEDGER).write_text(rows + json.dumps(twin) + "\n", encoding="utf-8")
    _write(repo, "plans/P-1794617800-twin.md")
    before = _tree(repo)
    assert _run(repo) == 1
    assert "would both move to" in capsys.readouterr().err
    assert _tree(repo) == before


def test_a_row_whose_path_cannot_be_rewritten_alone_aborts(repo, capsys) -> None:
    # The stored path appears twice under the same key shape, so a field-restricted rewrite is impossible.
    path = {"plan_file": "plans/P-9-y.md"}
    odd = json.dumps({"plan_id": "P-9-y", "intent_branch": "I-9-y/_", **path, "note": path}) + "\n"
    (repo / PLANS_LEDGER).write_text((repo / PLANS_LEDGER).read_text(encoding="utf-8") + odd, encoding="utf-8")
    _write(repo, "plans/P-9-y.md")
    before = _tree(repo)
    assert _run(repo) == 1
    assert "cannot locate exactly one 'plan_file'" in capsys.readouterr().err
    assert _tree(repo) == before
