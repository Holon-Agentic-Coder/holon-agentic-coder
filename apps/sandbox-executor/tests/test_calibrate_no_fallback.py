"""Stage 5 never writes a fallback calibration report: a failed ``run_calibrate`` fails the stage closed.

Transient failures (timeouts, provider API errors) are retried a bounded number of times and a named near-term usage
reset is waited out once; anything else, or an exhausted retry budget, returns FAILED with nothing committed or pushed,
an empty /calibrated branch discarded, the checkpoint at CALIBRATE and one appended failure ``ACT-CAL`` row.
"""

import dataclasses
import json
import subprocess
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import sandbox_executor.calibration
from sandbox_executor.budgets import BudgetBreach
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus, run_calibrate_stage
from sandbox_executor.flow.calibrate_rebuild import CalibrationSyncError
from sandbox_executor.flow.calibrate_retry import RetryKind, classify_calibrate_error
from sandbox_executor.flow.checkpoint import load_checkpoint, save_checkpoint
from sandbox_executor.flow.stage5 import Stage5Steps, run_stage5, standalone_context
from sandbox_executor.flow.stages.calibrate import stage5_steps
from sandbox_executor.ledger import read_merged_rows, read_merged_text

from tests.calibrate_scenarios import build_no_fallback_repo, clone_flow_pair, own_path_copy, run_guarded_template
from tests.flow.sleep_patch import scoped_sleep_patch
from tests.repo_templates import shared_template

EXECUTION_BRANCH = "I-100-intent/P-200-plan/E-300-exec/_"
CALIBRATED_BRANCH = "I-100-intent/P-200-plan/E-300-exec/calibrated"
REPORT_REL = "plans/I-100-intent/P-200-plan_calibration.md"
LEDGER_REL = "holon-knowledge/ledger/executions.jsonl"
SEED_ROW = '{"action_id": "ACT-SEED-1", "entry_type": "action", "outcome": "success"}\n'
TOKEN = "ghp_" + "Z" * 36
PR_249_ERROR = "fatal: gitfile does not point to a valid repository"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture(scope="session")
def no_fallback_repo_template(tmp_path_factory: pytest.TempPathFactory, hermetic_git_config: str) -> Path:
    root, _ = shared_template(
        tmp_path_factory,
        "calibrate-no-fallback-repo",
        lambda r: build_no_fallback_repo(r, EXECUTION_BRANCH, LEDGER_REL, SEED_ROW),
    )
    return root


@pytest.fixture
def repo(no_fallback_repo_template: Path, tmp_path: Path) -> Path:
    return own_path_copy(no_fallback_repo_template, tmp_path)


@pytest.fixture
def permanent_failure_template(
    no_fallback_repo_template: Path,
    tmp_path_factory: pytest.TempPathFactory,
    gh_write_guard: list[str],
    network_guard_fixture: list[str],
) -> SimpleNamespace:
    def build(root: Path) -> SimpleNamespace:
        own = own_path_copy(no_fallback_repo_template, root)
        tip = _git(own, "rev-parse", EXECUTION_BRANCH).strip()
        ctx = _context(own)
        with scoped_sleep_patch("sandbox_executor.flow.calibrate_retry") as mock_sleep:
            res, calibrate, reconcile, promote = _run(ctx, _raising(RuntimeError(f"{PR_249_ERROR} {TOKEN}")))
            sleep_calls = mock_sleep.call_count
        return SimpleNamespace(
            tip=tip,
            stage_run=(ctx, res),
            counts=(calibrate.call_count, reconcile.call_count, promote.call_count, sleep_calls),
        )

    root, ran = run_guarded_template(
        tmp_path_factory, "calibrate-no-fallback-permanent", build, (gh_write_guard, network_guard_fixture)
    )
    return SimpleNamespace(root=root, tip=ran.tip, stage_run=ran.stage_run, counts=ran.counts)


@pytest.fixture
def permanent_failure(permanent_failure_template: SimpleNamespace, tmp_path: Path) -> SimpleNamespace:
    own = own_path_copy(permanent_failure_template.root, tmp_path)
    ctx, res = clone_flow_pair(permanent_failure_template.root, permanent_failure_template.stage_run, own)
    return SimpleNamespace(
        repo=own, tip=permanent_failure_template.tip, ctx=ctx, res=res, counts=permanent_failure_template.counts
    )


@pytest.fixture
def sleep() -> Iterator[MagicMock]:
    with scoped_sleep_patch("sandbox_executor.flow.calibrate_retry") as mock:
        yield mock


def _context(repo: Path, *, dry_run: bool = False) -> FlowContext:
    return FlowContext(
        repo_dir=str(repo),
        execution_branch=EXECUTION_BRANCH,
        plan_branch="I-100-intent/P-200-plan/_",
        dry_run=dry_run,
        skip_push=True,
    )


def _successful_calibration(repo: Path) -> Any:
    """Do what a successful ``run_calibrate`` does: commit the report on /calibrated."""
    _git(repo, "checkout", "-q", "-B", CALIBRATED_BRANCH)
    report_path = repo / REPORT_REL
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# Calibration Report\n", encoding="utf-8")
    _git(repo, "add", "plans")
    _git(repo, "commit", "-q", "-m", "calibration report")
    report = MagicMock(calibrated_branch=CALIBRATED_BRANCH)
    report.to_dict.return_value = {"calibrated_branch": CALIBRATED_BRANCH}
    return report


def _run(ctx: FlowContext, calibrate: Callable[..., Any]) -> tuple[StageResult, MagicMock, MagicMock, MagicMock]:
    fake_calibrate = MagicMock(side_effect=calibrate)
    reconcile = MagicMock(return_value={})
    promote = MagicMock(return_value=[])
    with (
        patch("sandbox_executor.flow.stages.calibrate.run_calibrate", fake_calibrate),
        patch("sandbox_executor.flow.stages.calibrate.reconcile_records", reconcile),
        patch("sandbox_executor.flow.stages.calibrate.promote_pending_candidates", promote),
    ):
        res = run_calibrate_stage(ctx)
    return res, fake_calibrate, reconcile, promote


def _raising(*errors: BaseException, then: Callable[[], Any] | None = None) -> Callable[..., Any]:
    pending = list(errors)

    def calibrate(**_kwargs: Any) -> Any:
        if pending:
            raise pending.pop(0)
        assert then is not None
        return then()

    return calibrate


def _new_rows(repo: Path) -> list[dict[str, Any]]:
    text = (repo / LEDGER_REL).read_text(encoding="utf-8")
    assert text.startswith(SEED_ROW)
    seed = [json.loads(line) for line in SEED_ROW.splitlines() if line.strip()]
    rows = read_merged_rows(str(repo / LEDGER_REL))
    assert rows[: len(seed)] == seed
    return rows[len(seed) :]


def test_permanent_error_fails_closed(permanent_failure: SimpleNamespace):
    repo, tip, ctx, res = permanent_failure.repo, permanent_failure.tip, permanent_failure.ctx, permanent_failure.res
    calibrate_calls, reconcile_calls, promote_calls, sleep_calls = permanent_failure.counts

    assert calibrate_calls == 1
    assert res.status == StageStatus.FAILED
    assert res.summary_line().startswith("calibrate: failed")
    assert PR_249_ERROR in (res.error or "")
    assert "nothing was committed or pushed" in (res.error or "")
    assert TOKEN not in (res.error or "")
    assert all(TOKEN not in line for line in ctx.logs)
    assert not list(repo.rglob("*_calibration.md"))
    assert _git(repo, "rev-parse", EXECUTION_BRANCH).strip() == tip
    assert _git(repo, "branch", "--list", "*calibrated").strip() == ""
    assert ctx.calibrated_branch is None
    assert (reconcile_calls, promote_calls, sleep_calls) == (0, 0, 0)


def test_failure_row_is_a_pure_append(permanent_failure: SimpleNamespace):
    repo, res = permanent_failure.repo, permanent_failure.res

    assert res.status == StageStatus.FAILED
    rows = _new_rows(repo)
    assert len(rows) == 1
    row = rows[0]
    assert row["action_id"].startswith("ACT-CAL-")
    assert row["outcome"] == "failure"
    assert row["action"] == "Calibration failed for plan P-200-plan; nothing committed, row left uncommitted"
    assert row["error_context"].startswith(f"Calibration run failed: {PR_249_ERROR}")
    assert row["calibration_report_path"] == REPORT_REL
    assert TOKEN not in (read_merged_text(str(repo / LEDGER_REL)) or "")


def test_transient_error_retried_then_succeeds(repo, sleep):
    ctx = _context(repo)
    calibrate = _raising(
        subprocess.TimeoutExpired(["git"], 30),
        RuntimeError("API Error: 529 overloaded"),
        then=lambda: _successful_calibration(repo),
    )
    res, fake_calibrate, *_ = _run(ctx, calibrate)

    assert res.status == StageStatus.SUCCESS
    assert fake_calibrate.call_count == 3
    assert (repo / REPORT_REL).exists()
    assert [c.args[0] for c in sleep.call_args_list] == [10.0, 30.0]
    joined = "\n".join(ctx.logs)
    assert "Calibration attempt 1/3 failed (transient)" in joined
    assert "Calibration attempt 2/3 failed (transient): API Error: 529 overloaded" in joined
    assert "Fallback calibration generated" not in (repo / REPORT_REL).read_text(encoding="utf-8")


def test_transient_error_exhausted_fails(repo, sleep):
    ctx = _context(repo)
    timeouts = [subprocess.TimeoutExpired(["git", f"call-{i}"], 30) for i in range(3)]
    res, calibrate, *_ = _run(ctx, _raising(*timeouts))

    assert calibrate.call_count == 3
    assert res.status == StageStatus.FAILED
    assert "3 attempt(s)" in (res.error or "")
    assert len(res.payload["calibration_attempts"]) == 3
    for i in range(3):
        assert any(
            f"Calibration attempt {i + 1}/3 failed (transient)" in line and f"call-{i}" in line for line in ctx.logs
        )
    assert len(_new_rows(repo)) == 1
    assert not (repo / REPORT_REL).exists()


def test_quota_reset_is_waited_out_once(repo, sleep):
    ctx = _context(repo)
    calibrate = _raising(
        RuntimeError("usage limit reached, resets in 2 minutes"), then=lambda: _successful_calibration(repo)
    )
    res, fake_calibrate, *_ = _run(ctx, calibrate)

    assert res.status == StageStatus.SUCCESS
    assert fake_calibrate.call_count == 2
    assert sleep.call_count == 1
    assert sleep.call_args.args[0] == pytest.approx(125.0, abs=1.0)
    assert any("failed (quota)" in line for line in ctx.logs)


def test_quota_reset_beyond_the_ceiling_is_not_waited(repo, sleep, monkeypatch):
    monkeypatch.setenv("HOLON_REVIEW_QUOTA_WAIT_SECONDS", "60")
    calibrate = _raising(RuntimeError("usage limit reached, resets in 2 minutes"))
    res, fake_calibrate, *_ = _run(_context(repo), calibrate)

    assert res.status == StageStatus.FAILED
    assert fake_calibrate.call_count == 1
    sleep.assert_not_called()


def test_no_retry_when_state_is_not_clean(repo, sleep):
    def partial(**_kwargs: Any) -> Any:
        (repo / REPORT_REL).parent.mkdir(parents=True, exist_ok=True)
        (repo / REPORT_REL).write_text("# partial\n", encoding="utf-8")
        raise subprocess.TimeoutExpired(["git"], 30)

    ctx = _context(repo)
    res, calibrate, *_ = _run(ctx, partial)

    assert calibrate.call_count == 1
    assert res.status == StageStatus.FAILED
    sleep.assert_not_called()
    assert any("not safe to retry" in line for line in ctx.logs)
    # The partial file is left for the operator; nothing deletes or commits it.
    assert (repo / REPORT_REL).read_text(encoding="utf-8") == "# partial\n"
    assert REPORT_REL in _git(repo, "status", "--porcelain", "--untracked-files=all")


def test_dry_run_failure_appends_nothing(repo, sleep):
    before = (repo / LEDGER_REL).read_bytes()
    res, *_ = _run(_context(repo, dry_run=True), _raising(RuntimeError(PR_249_ERROR)))

    assert res.status == StageStatus.FAILED
    assert (repo / LEDGER_REL).read_bytes() == before


def test_checkpoint_stays_at_calibrate(repo, sleep, tmp_path_factory):
    ctx = _context(repo)
    for stage in (FlowStage.INTENT, FlowStage.PLAN, FlowStage.EXECUTE, FlowStage.REVIEW):
        ctx.stage_results[stage.value] = StageResult(stage=stage, status=StageStatus.SUCCESS)
    res, *_ = _run(ctx, _raising(RuntimeError(PR_249_ERROR)))
    assert ctx.stage_results[FlowStage.CALIBRATE.value] is res

    path = tmp_path_factory.mktemp("checkpoint") / "checkpoint.json"
    saved = load_checkpoint(save_checkpoint(ctx, str(path)))
    assert saved.current_stage == FlowStage.CALIBRATE
    assert FlowStage.CALIBRATE.value not in saved.completed_stages


_NOW = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (subprocess.TimeoutExpired(["git", "fetch"], 30), RetryKind.TRANSIENT),
        (TimeoutError("timed out"), RetryKind.TRANSIENT),
        (ConnectionResetError("connection reset by peer"), RetryKind.TRANSIENT),
        (BudgetBreach("calibrate", "turn", 300, 301), RetryKind.TRANSIENT),
        (BudgetBreach("calibrate", "stall", 90, 91), RetryKind.TRANSIENT),
        (BudgetBreach("calibrate", "stage", 900, 900), RetryKind.PERMANENT),
        (RuntimeError("API Error: 500 internal server error"), RetryKind.TRANSIENT),
        (RuntimeError("upstream overloaded, try again"), RetryKind.TRANSIENT),
        (RuntimeError("usage limit reached, resets in 2 minutes"), RetryKind.QUOTA),
        (RuntimeError("usage limit reached"), RetryKind.PERMANENT),
        (RuntimeError("Not logged in. Please run /login"), RetryKind.PERMANENT),
        (CalibrationSyncError("local branch holds commits origin lacks"), RetryKind.PERMANENT),
        (ValueError("bad metrics"), RetryKind.PERMANENT),
        (RuntimeError(PR_249_ERROR), RetryKind.PERMANENT),
    ],
)
def test_classifier_table(exc, kind):
    failure = classify_calibrate_error(exc, _NOW, 900.0)
    assert failure.kind is kind
    assert (failure.wait_seconds is not None) is (kind is RetryKind.QUOTA)


def test_classifier_redacts_the_cause():
    failure = classify_calibrate_error(RuntimeError(f"push failed {TOKEN}"), _NOW, 900.0)
    assert TOKEN not in failure.cause
    assert failure.cause.startswith("push failed")


def test_fallback_symbols_are_gone():
    src = Path(sandbox_executor.calibration.__file__).parent.parent
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for symbol in ("_fallback_report", "on_calibrate_error", "fallback_report_written"):
            assert symbol not in text, f"{symbol} still in {path}"
        if "Fallback calibration generated" in text:
            assert path.name == "calibration_self_check.py", path
    assert "on_calibrate_error" not in {f.name for f in dataclasses.fields(Stage5Steps)}


def test_cli_calibrate_still_raises(tmp_path):
    def calibrate(**_kwargs: Any) -> Any:
        raise RuntimeError("calibration broke")

    context = standalone_context(EXECUTION_BRANCH, str(tmp_path), no_commit=True)
    steps = dataclasses.replace(stage5_steps(), calibrate=calibrate)
    with pytest.raises(RuntimeError, match="calibration broke"):
        run_stage5(context, steps, None)
