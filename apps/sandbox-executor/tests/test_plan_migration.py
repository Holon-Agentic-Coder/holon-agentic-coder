"""The one-off plans/ migration: moves, the two ledger path rewrites, idempotence and the byte-level guard."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.ledger import LedgerRewriteError, assert_ledger_prefix, atomic_ledger_append
from sandbox_executor.plan_migration import (
    EXECUTIONS_LEDGER_REL,
    PLANS_LEDGER_REL,
    MigrationError,
    _assert_only_fields_changed,
    migrate_plans,
)

PR_BRANCHES = [
    "I-10-alpha/P-1-agent-model/E-5-agent-model/_",
    "I-20-beta/P-2-orphan-agent-model/E-6-agent-model/_",
]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout


def _seed_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "plans").mkdir(parents=True)
    (repo / "holon-knowledge" / "ledger").mkdir(parents=True)
    (repo / "plans" / "P-1-agent-model.md").write_text("# plan one\n", encoding="utf-8")
    (repo / "plans" / "P-1-agent-model_calibration.md").write_text("# report one\n", encoding="utf-8")
    (repo / "plans" / "P-2-orphan-agent-model.md").write_text("# orphan with a PR branch\n", encoding="utf-8")
    (repo / "plans" / "P-3-stray-agent-model.md").write_text("# stray\n", encoding="utf-8")
    plans_rows = [
        {
            "plan_id": "P-1-agent-model",
            "intent_branch": "I-10-alpha/_",
            "agent": "agent",
            "ev": 18.020000000000003,
            "plan_file": "plans/P-1-agent-model.md",
            "status": "proposed",
        },
    ]
    executions_rows = [
        {"execution_id": "E-5-agent-model", "plan_branch": "I-10-alpha/P-1-agent-model/_", "status": "success"},
        {
            "action_id": "ACT-CAL-1",
            "execution_id": "CAL-P-1-agent-model-1",
            "entry_type": "action",
            "calibration_report_path": "plans/P-1-agent-model_calibration.md",
        },
    ]
    (repo / PLANS_LEDGER_REL).write_text("".join(json.dumps(r) + "\n" for r in plans_rows), encoding="utf-8")
    (repo / EXECUTIONS_LEDGER_REL).write_text("".join(json.dumps(r) + "\n" for r in executions_rows), encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed")
    return repo


def test_dry_run_reports_every_action_and_touches_nothing(tmp_path):
    repo = _seed_repo(tmp_path)
    before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts}
    report = migrate_plans(repo, PR_BRANCHES, apply=False)
    assert dict(report.moves) == {
        "plans/P-1-agent-model.md": "plans/I-10-alpha/P-1-agent-model.md",
        "plans/P-1-agent-model_calibration.md": "plans/I-10-alpha/P-1-agent-model_calibration.md",
        "plans/P-2-orphan-agent-model.md": "plans/I-20-beta/P-2-orphan-agent-model.md",
        "plans/P-3-stray-agent-model.md": "plans/legacy/P-3-stray-agent-model.md",
    }
    assert report.legacy == ["plans/P-3-stray-agent-model.md"]
    assert [r[:1] + r[2:] for r in report.rewrites] == [
        (PLANS_LEDGER_REL, "plans/P-1-agent-model.md", "plans/I-10-alpha/P-1-agent-model.md"),
        (
            EXECUTIONS_LEDGER_REL,
            "plans/P-1-agent-model_calibration.md",
            "plans/I-10-alpha/P-1-agent-model_calibration.md",
        ),
    ]
    rendered = report.render(applied=False)
    assert "dry run" in rendered and "plans/legacy/P-3-stray-agent-model.md" in rendered
    after = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts}
    assert before == after


def test_apply_moves_files_rewrites_only_the_two_fields_and_is_idempotent(tmp_path):
    repo = _seed_repo(tmp_path)
    plans_before = (repo / PLANS_LEDGER_REL).read_bytes()
    executions_before = (repo / EXECUTIONS_LEDGER_REL).read_bytes()

    report = migrate_plans(repo, PR_BRANCHES, apply=True)
    assert report.action_count == 4 + 2
    assert not list((repo / "plans").glob("*.md")), "no plan file may remain directly in plans/"
    assert (repo / "plans/I-10-alpha/P-1-agent-model.md").exists()
    assert (repo / "plans/I-10-alpha/P-1-agent-model_calibration.md").exists()
    assert (repo / "plans/I-20-beta/P-2-orphan-agent-model.md").exists()
    assert (repo / "plans/legacy/P-3-stray-agent-model.md").exists()
    assert report.missing_targets == []

    # The ledgers differ from before only in the two path fields (asserted byte-level, field-masked).
    _assert_only_fields_changed(PLANS_LEDGER_REL, plans_before, (repo / PLANS_LEDGER_REL).read_bytes(), ("plan_file",))
    _assert_only_fields_changed(
        EXECUTIONS_LEDGER_REL,
        executions_before,
        (repo / EXECUTIONS_LEDGER_REL).read_bytes(),
        ("calibration_report_path",),
    )
    rows = [json.loads(line) for line in (repo / PLANS_LEDGER_REL).read_text().splitlines()]
    assert rows[0]["plan_file"] == "plans/I-10-alpha/P-1-agent-model.md"
    assert rows[0]["ev"] == 18.020000000000003
    # git mv kept the files tracked under their new paths.
    tracked = _git(repo, "status", "--porcelain")
    assert "R " in tracked or _git(repo, "ls-files", "plans/I-10-alpha/P-1-agent-model.md").strip()

    # Idempotence is a test, not a hope: a second run finds nothing to do.
    second = migrate_plans(repo, PR_BRANCHES, apply=True)
    assert second.action_count == 0
    assert second.moves == [] and second.rewrites == [] and second.missing_targets == []


def test_a_change_outside_the_two_fields_aborts(tmp_path):
    before = b'{"plan_id": "P-1", "plan_file": "plans/P-1.md", "status": "proposed"}\n'
    reordered = b'{"plan_id": "P-1", "status": "proposed", "plan_file": "plans/I-1/P-1.md"}\n'
    with pytest.raises(MigrationError, match="outside"):
        _assert_only_fields_changed(PLANS_LEDGER_REL, before, reordered, ("plan_file",))
    with pytest.raises(MigrationError, match="row count"):
        _assert_only_fields_changed(PLANS_LEDGER_REL, before, before + b"{}\n", ("plan_file",))
    # The allowed change passes.
    allowed = b'{"plan_id": "P-1", "plan_file": "plans/I-1/P-1.md", "status": "proposed"}\n'
    _assert_only_fields_changed(PLANS_LEDGER_REL, before, allowed, ("plan_file",))


def test_append_only_guard_still_rejects_a_non_exempt_rewrite_after_migration(tmp_path):
    repo = _seed_repo(tmp_path)
    migrate_plans(repo, PR_BRANCHES, apply=True)
    ledger = repo / PLANS_LEDGER_REL
    # Appending stays legal.
    with atomic_ledger_append(str(ledger)) as f:
        f.write(json.dumps({"plan_id": "P-9", "intent_branch": "I-9/_"}) + "\n")
    # Rewriting a historical row is still refused by the guard: the exemption covered this migration only.
    before = ledger.read_bytes()
    ledger.write_bytes(before.replace(b"proposed", b"approved"))
    with pytest.raises(LedgerRewriteError):
        assert_ledger_prefix(str(ledger), before)
