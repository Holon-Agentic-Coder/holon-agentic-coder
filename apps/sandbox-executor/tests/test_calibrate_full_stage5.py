"""``holon calibrate`` runs the same Stage 5 as the flow: hermetic git repos with a bare origin, no network.

The real ``run_calibrate``, ``reconcile_records`` and ``promote_pending_candidates`` run on both paths; only the
Prettier formatting of the report and the flow-only collaborators (stage-conflict resolution, final-head CI) are
stubbed. The ledger seeds an ``unknown`` action row for the execution, a ``success`` summary row and three failure rows
of one fingerprint, so promotion really writes one ``KB-failure`` entry.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.cli.commands import calibrate as calibrate_command
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.stage5 import _reconcile_execution
from sandbox_executor.flow.stage5_ledger import already_reconciled
from sandbox_executor.flow.stages.calibrate import run_calibrate_stage
from sandbox_executor.knowledge.index import ActionIndexDatabase
from sandbox_executor.ledger import read_merged_rows_at_ref, read_merged_text
from sandbox_executor.ref_names import calibration_branch

EXEC = "I-1-x/P-2-y/E-3-z/_"
CALIBRATED = calibration_branch(EXEC)
LEDGER_REL = "holon-knowledge/ledger/executions.jsonl"
DB_REL = "holon-knowledge/.index/action_index.db"
VOLATILE_KEYS = ("action_id", "created_at")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _seed_rows() -> list[dict]:
    rows: list[dict] = [
        {"action_id": EXEC, "event_type": "execution", "verdict": "unknown", "created_at": "2026-11-22T10:00:00+00:00"},
        {
            "entry_type": "summary",
            "execution_id": "E-3-z",
            "plan_branch": "I-1-x/P-2-y/_",
            "status": "success",
            "exit_code": 0,
            "created_at": "2026-11-22T10:05:00+00:00",
        },
    ]
    rows += [
        {
            "action_id": f"act-fail-{i}",
            "context_fingerprint": "stage5fp_fp",
            "action": "Run the flaky step",
            "target": "src/flaky.py",
            "verdict": "failure",
            "error_context": "RuntimeError: boom",
        }
        for i in range(3)
    ]
    return rows


def make_repo(root: Path) -> SimpleNamespace:
    """A bare origin and a host checkout (on ``operator-work``) whose execution branch holds a plan and the ledger."""
    origin, host = root / "origin.git", root / "host"
    root.mkdir(parents=True)
    git(root, "init", "-q", "--bare", "-b", "main", str(origin))
    git(root, "clone", "-q", str(origin), str(host))
    git(host, "checkout", "-qb", "main")
    (host / ".gitignore").write_text("holon-knowledge/.index/\n")
    git(host, "add", ".gitignore")
    git(host, "commit", "-qm", "base")
    git(host, "push", "-q", "origin", "main")
    git(host, "checkout", "-qb", EXEC)
    plan = host / "plans/I-1-x/P-2-y.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# Plan P-2-y\n\n| metric | value |\n| --- | --- |\n| p_success_pred | 0.8 |\n")
    ledger = host / LEDGER_REL
    ledger.parent.mkdir(parents=True)
    ledger.write_text("".join(json.dumps(row) + "\n" for row in _seed_rows()))
    git(host, "add", "plans", LEDGER_REL)
    git(host, "commit", "-qm", "execution")
    git(host, "push", "-q", "origin", EXEC)
    git(host, "checkout", "-qb", "operator-work", "main")
    git(host, "checkout", "-q", EXEC, "--", LEDGER_REL)  # the action index is built from the execution's ledger
    ActionIndexDatabase(db_path=str(host / DB_REL)).sync_ledger(str(ledger))
    git(host, "rm", "-q", "--cached", LEDGER_REL)
    ledger.unlink()
    return SimpleNamespace(host=host, exec_sha=git(host, "rev-parse", EXEC))


@pytest.fixture(autouse=True)
def no_prettier():
    with patch("sandbox_executor.calibration.run.format_calibration_report"):
        yield


@pytest.fixture
def repo(tmp_path: Path) -> SimpleNamespace:
    return make_repo(tmp_path / "cli")


def run_flow(repo: SimpleNamespace) -> StageResult:
    ctx = FlowContext(target_worktree=str(repo.host), execution_branch=EXEC, agent="a", model="m", skip_push=True)
    ctx.stage_results["execute"] = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, start_time="t")
    ctx.stage_results["review"] = StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.APPROVED,
        start_time="t",
        payload={
            "consensus": {"approved": True, "target_commit": repo.exec_sha, "pr_number": 7},
            "posting": {"dual_posted": True},
        },
    )
    with (
        patch("sandbox_executor.flow.stages.calibrate.sync_before_calibration", return_value=None),
        patch("sandbox_executor.flow.stages.calibrate.check_final_head_ci", return_value={"state": "success"}),
    ):
        return run_calibrate_stage(ctx)


def run_cli(repo: SimpleNamespace, *flags: str, capsys: pytest.CaptureFixture[str] | None = None) -> str:
    args = argparse.Namespace(
        execution_branch=EXEC,
        target_worktree=str(repo.host),
        json="--json" in flags,
        skip_commit="--no-commit" in flags,
        report_only="--report-only" in flags,
    )
    assert calibrate_command.run(args) == 0
    return capsys.readouterr().out if capsys else ""


def calibrated_subjects(repo: SimpleNamespace) -> list[str]:
    return git(repo.host, "log", "--reverse", "--format=%s", f"{repo.exec_sha}..refs/heads/{CALIBRATED}").splitlines()


def ledger_rows(repo: SimpleNamespace, ref: str = f"refs/heads/{CALIBRATED}") -> list[dict]:
    return read_merged_rows_at_ref(str(repo.host), ref, "executions.jsonl")


def reconciliation_rows(repo: SimpleNamespace) -> list[dict]:
    rows = [r for r in ledger_rows(repo) if r.get("event_type") == "reconciliation"]
    return [{k: v for k, v in r.items() if k not in VOLATILE_KEYS} for r in rows]


def knowledge_files(repo: SimpleNamespace) -> list[str]:
    tree = git(repo.host, "ls-tree", "-r", "--name-only", CALIBRATED, "holon-knowledge/kb", "holon-knowledge/wisdom")
    return tree.splitlines()


def test_cli_produces_the_same_commits_and_ledger_rows_as_the_flow_stage(tmp_path: Path, capsys) -> None:
    flow_repo, cli_repo = make_repo(tmp_path / "flow"), make_repo(tmp_path / "cli")

    assert run_flow(flow_repo).status == StageStatus.SUCCESS
    run_cli(cli_repo)

    expected = [
        "chore(calibration): add calibration report for P-2-y",
        "chore(calibration): record stage 5 reconciliation",
        "chore(calibration): record stage 5 promoted knowledge and wisdom",
    ]
    assert calibrated_subjects(flow_repo) == calibrated_subjects(cli_repo) == expected
    assert reconciliation_rows(flow_repo) == reconciliation_rows(cli_repo)
    assert reconciliation_rows(cli_repo) == [
        {
            "parent_action_id": EXEC,
            "event_type": "reconciliation",
            "verdict": "success",
            "outcome": "success",
            "reason": "Stage 5 calibration reconciled execution outcome to success",
        }
    ]
    assert [r.get("event_type") for r in ledger_rows(flow_repo)] == [r.get("event_type") for r in ledger_rows(cli_repo)]
    assert knowledge_files(flow_repo) == knowledge_files(cli_repo) != []


def test_report_only_skips_reconciliation_and_promotion_and_says_so(repo: SimpleNamespace, capsys) -> None:
    out = run_cli(repo, "--report-only", capsys=capsys)

    assert "Report only" in out and "--report-only" in out
    assert calibrated_subjects(repo) == ["chore(calibration): add calibration report for P-2-y"]
    assert reconciliation_rows(repo) == []
    assert knowledge_files(repo) == []
    assert not (repo.host / "holon-knowledge/kb").exists()


def test_report_only_json_names_the_mode(repo: SimpleNamespace, capsys) -> None:
    stage5 = json.loads(run_cli(repo, "--report-only", "--json", capsys=capsys))["stage5"]

    assert stage5["mode"] == "report_only"
    assert stage5["reconciliation"]["status"] == "skipped_report_only"
    assert stage5["newly_promoted_count"] == 0


def test_second_run_appends_no_duplicate_rows_and_promotes_nothing(repo: SimpleNamespace, capsys) -> None:
    """The second run adds no Stage 5 rows or commits.

    ``run_calibrate`` itself still appends its ACT-CAL row and commits the report again, exactly as a flow rerun of
    Stage 5 does today, so the only new commit is the report commit.
    """
    run_cli(repo, "--json", capsys=capsys)
    first_subjects = calibrated_subjects(repo)

    second = json.loads(run_cli(repo, "--json", capsys=capsys))["stage5"]

    assert second["reconciliation"]["status"] == "already_recorded"
    assert second["reconciliation"]["rows_appended"] == 0
    assert second["newly_promoted_count"] == 0
    assert len(reconciliation_rows(repo)) == 1
    assert calibrated_subjects(repo)[len(first_subjects) :] == ["chore(calibration): add calibration report for P-2-y"]
    assert knowledge_files(repo) == ["holon-knowledge/kb/failure_modes/KB-failure-stage5fp.json"]


def test_no_commit_leaves_head_refs_and_ledger_unchanged(repo: SimpleNamespace, capsys) -> None:
    git(repo.host, "checkout", "-q", EXEC)
    ledger = repo.host / LEDGER_REL
    before = (
        git(repo.host, "rev-parse", "HEAD"),
        git(repo.host, "for-each-ref"),
        ledger.read_bytes(),
        read_merged_text(str(ledger)),
    )

    stage5 = json.loads(run_cli(repo, "--no-commit", "--json", capsys=capsys))["stage5"]

    after = (
        git(repo.host, "rev-parse", "HEAD"),
        git(repo.host, "for-each-ref"),
        ledger.read_bytes(),
        read_merged_text(str(ledger)),
    )
    assert after == before
    assert stage5["reconciliation"]["status"] == "skipped_no_commit"
    assert (repo.host / "plans/I-1-x/P-2-y_calibration.md").exists()
    assert not (repo.host / "holon-knowledge/kb").exists()


def test_json_output_is_one_document_with_the_stage5_outcome(repo: SimpleNamespace, capsys) -> None:
    doc = json.loads(run_cli(repo, "--json", capsys=capsys))

    assert doc["plan_id"] == "P-2-y"
    assert doc["stage5"] == {
        "mode": "full",
        "reconciliation": {"status": "recorded", "verdict": "success", "rows_appended": 1},
        "newly_promoted_count": 1,
    }


def test_text_output_reports_the_stage5_outcome(repo: SimpleNamespace, capsys) -> None:
    out = run_cli(repo, capsys=capsys)

    assert "Stage 5 reconciliation: recorded (verdict: success)" in out
    assert "Promoted 1 new knowledge/wisdom candidate(s)." in out


def test_missing_summary_row_skips_reconciliation_without_guessing(repo: SimpleNamespace, capsys) -> None:
    with patch("sandbox_executor.cli.commands.calibrate.resolve_execution_verdict", return_value=None):
        stage5 = json.loads(run_cli(repo, "--json", capsys=capsys))["stage5"]

    assert stage5["reconciliation"]["status"] == "skipped_no_verdict"
    assert stage5["newly_promoted_count"] == 0
    assert reconciliation_rows(repo) == []
    assert knowledge_files(repo) == []


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], False),
        ([{"event_type": "reconciliation", "parent_action_id": EXEC}], True),
        ([{"event_type": "reconciliation", "parent_action_id": "I-9/P-9/E-9/_"}], False),
        ([{"event_type": "ttl_expiration", "parent_action_id": EXEC}], False),
        (["not json", [1, 2], {"event_type": "reconciliation", "parent_action_id": EXEC}], True),
    ],
)
def test_already_reconciled_guard(tmp_path: Path, rows: list, expected: bool) -> None:
    ledger = tmp_path / "executions.jsonl"
    ledger.write_text("".join((row if isinstance(row, str) else json.dumps(row)) + "\n" for row in rows))
    assert already_reconciled(str(ledger), EXEC) is expected


def _recorded_ledger_context(tmp_path: Path) -> FlowContext:
    ledger = tmp_path / LEDGER_REL
    ledger.parent.mkdir(parents=True)
    row = {"event_type": "reconciliation", "parent_action_id": EXEC, "verdict": "success"}
    ledger.write_text(json.dumps(row) + "\n")
    return FlowContext(target_worktree=str(tmp_path), execution_branch=EXEC, dry_run=False, token_reduce=False)


def test_already_recorded_commits_leftover_rows(tmp_path: Path) -> None:
    steps = MagicMock()
    context = _recorded_ledger_context(tmp_path)

    result = _reconcile_execution(context, steps, "success")

    assert result["status"] == "already_recorded"
    steps.reconcile.assert_not_called()
    steps.commit_reconciliation.assert_called_once()


def test_already_recorded_logs_a_verdict_mismatch(tmp_path: Path) -> None:
    context = _recorded_ledger_context(tmp_path)

    result = _reconcile_execution(context, MagicMock(), "failure")

    assert result == {"status": "already_recorded", "verdict": "failure", "rows_appended": 0}
    assert any("'failure' differs from the recorded reconciliation verdict 'success'" in line for line in context.logs)
