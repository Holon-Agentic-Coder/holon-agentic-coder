"""Stage 5 calibration commit path: ACT-CAL ledger provenance and calibration report formatting."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.calibration import (
    CalibrationCommitError,
    CalibrationFormatError,
    format_calibration_report,
    record_calibration_action,
    run_calibrate,
)
from sandbox_executor.calibration_format import LINT_DOCS_PRETTIER, PRETTIER_PASSES
from sandbox_executor.cli import main as cli_main
from sandbox_executor.ledger import merged_jsonl_text, read_merged_rows_at_ref, read_merged_text

# ---------------------------------------------------------------------------
# Stage 5 ACT-CAL ledger provenance regression tests (pytest style)
# ---------------------------------------------------------------------------

_REAL_EXECUTIONS_LEDGER = Path(__file__).resolve().parents[3] / "holon-knowledge" / "ledger" / "executions.jsonl"
_EXEC_BRANCH = "I-1-cal/P-2/E-3/_"
_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Calibration Test",
    "GIT_AUTHOR_EMAIL": "calibration-test@example.invalid",
    "GIT_COMMITTER_NAME": "Calibration Test",
    "GIT_COMMITTER_EMAIL": "calibration-test@example.invalid",
}


def _fingerprint(path: Path) -> tuple[int, str]:
    if not path.exists():
        return (-1, "")
    data = path.read_bytes()
    return (data.count(b"\n"), hashlib.sha256(data).hexdigest())


@pytest.fixture
def guard_real_ledger():
    """Fail if a test modifies the repository's own canonical execution ledger."""
    before = _fingerprint(_REAL_EXECUTIONS_LEDGER)
    yield
    assert _fingerprint(_REAL_EXECUTIONS_LEDGER) == before, "Real executions.jsonl was modified"


@pytest.fixture
def calibration_repo(tmp_path, monkeypatch, guard_real_ledger):
    """Create a throwaway git repository with plan and execution branches."""
    for key, value in _GIT_IDENTITY.items():
        monkeypatch.setenv(key, value)

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True).stdout

    git("init", "-q", "-b", "main")
    ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
    ledger.parent.mkdir(parents=True)
    summary = {
        "execution_id": "E-3",
        "plan_branch": "I-1-cal/P-2/_",
        "agent": "test-agent",
        "model": "test-model",
        "status": "success",
        "entry_type": "summary",
        "created_at": "2026-01-01T00:00:00.000Z",
        "duration": 12.0,
        "exit_code": 0,
    }
    ledger.write_text(json.dumps(summary) + "\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    git("checkout", "-q", "-b", "I-1-cal/P-2/_")
    git("checkout", "-q", "-b", _EXEC_BRANCH)
    (tmp_path / "feature.py").write_text("print('hello')\n", encoding="utf-8")
    git("add", "feature.py")
    git("commit", "-q", "-m", "feature")
    (tmp_path / "untracked_scratch.txt").write_text("not for commit\n", encoding="utf-8")
    return tmp_path, git


_FRAGMENT_REL = "holon-knowledge/ledger/pending/executions/I-1-cal.jsonl"


def _merged_ledger_text(repo: Path) -> str:
    """The logical executions ledger of the working tree: canonical rows plus pending fragments."""
    return read_merged_text(str(repo / "holon-knowledge" / "ledger" / "executions.jsonl")) or ""


def _ref_ledger_text(repo: Path, ref: str) -> str:
    """The logical executions ledger committed at ``ref``."""
    return merged_jsonl_text(read_merged_rows_at_ref(str(repo), ref, "executions.jsonl"))


def _cal_rows(ledger_text: str) -> list[dict]:
    rows = [json.loads(line) for line in ledger_text.splitlines() if line.strip()]
    return [r for r in rows if str(r.get("action_id", "")).startswith("ACT-CAL-")]


def test_run_calibrate_commit_failure_warning_redacts_credentials(calibration_repo, caplog):
    """A failed calibration commit warns without echoing credentials from the exception text."""
    repo, _ = calibration_repo
    caplog.set_level("WARNING", logger="sandbox_executor.calibration")
    sample_value = "ghp_" + "C" * 36
    with (
        patch("sandbox_executor.calibration.run.format_calibration_report"),
        patch(
            "sandbox_executor.calibration.run.calibration_commit_env",
            side_effect=RuntimeError(f"credential helper {sample_value}"),
        ),
        pytest.raises(CalibrationCommitError) as excinfo,
    ):
        run_calibrate(_EXEC_BRANCH, target_worktree=str(repo), skip_commit=False)

    messages = [rec.getMessage() for rec in caplog.records]
    assert any("Git commit of calibration report" in m and "*******" in m for m in messages)
    assert all(sample_value not in m for m in messages)
    assert "*******" in str(excinfo.value)
    assert sample_value not in str(excinfo.value)


_HOOK_SECRET = "ghp_" + "H" * 36


def _install_rejecting_pre_commit_hook(repo: Path, git) -> None:
    """Install a repository-local pre-commit hook that rejects every commit."""
    hooks_dir = repo / ".git" / "test-hooks"
    hooks_dir.mkdir()
    hook = hooks_dir / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho 'pre-commit rejected with {_HOOK_SECRET}' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    git("config", "core.hooksPath", str(hooks_dir))


def test_run_calibrate_rejected_report_commit_raises(calibration_repo, capsys):
    """A pre-commit hook that rejects the report commit makes run_calibrate fail, redacted."""
    repo, git = calibration_repo
    _install_rejecting_pre_commit_hook(repo, git)
    with (
        patch("sandbox_executor.calibration.run.format_calibration_report"),
        pytest.raises(CalibrationCommitError) as excinfo,
    ):
        run_calibrate(_EXEC_BRANCH, target_worktree=str(repo), skip_commit=False)

    message = str(excinfo.value)
    assert "plans/I-1-cal/P-2_calibration.md" in message
    assert "pre-commit rejected" in message
    assert _HOOK_SECRET not in message
    assert "committed" not in capsys.readouterr().out
    # Nothing reached git: the report is absent from HEAD and HEAD still carries no ACT-CAL row.
    head_has_report = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"HEAD:{_REPORT_REL}"], capture_output=True
    )
    assert head_has_report.returncode != 0
    assert _cal_rows(_ref_ledger_text(repo, "HEAD")) == []
    assert git("log", "--format=%s", "-1").strip() == "feature"


def test_cli_calibrate_rejected_report_commit_exits_non_zero(calibration_repo, capsys):
    """`holon calibrate` exits 1 with a redacted error when the report commit is rejected."""
    repo, git = calibration_repo
    _install_rejecting_pre_commit_hook(repo, git)
    test_args = ["holon", "calibrate", _EXEC_BRANCH, "--target-worktree", str(repo)]
    with (
        patch("sandbox_executor.calibration.run.format_calibration_report"),
        patch.object(sys, "argv", test_args),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli_main()

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert f"Error during calibration: Git commit of calibration report {_REPORT_REL} failed" in captured.err
    assert _HOOK_SECRET not in captured.err
    assert "committed" not in captured.out


def test_run_calibrate_appends_and_commits_act_cal(calibration_repo):
    repo, git = calibration_repo
    with patch("sandbox_executor.calibration.run.format_calibration_report"):
        report = run_calibrate(_EXEC_BRANCH, target_worktree=str(repo), skip_commit=False)

    ledger_rel = "holon-knowledge/ledger/executions.jsonl"
    rows = _cal_rows(_merged_ledger_text(repo))
    assert len(rows) == 1
    row = rows[0]
    assert list(row.keys()) == [
        "action_id",
        "execution_id",
        "action",
        "category",
        "target",
        "outcome",
        "entry_type",
        "action_origin",
        "labels",
        "error_context",
        "plan_branch",
        "execution_branch",
        "evaluated_commit_sha",
        "predicted_metrics",
        "actual_metrics",
        "calibration_deltas",
        "calibration_report_path",
        "created_at",
        "touch_set_accuracy",
    ]
    assert (row["entry_type"], row["action_origin"]) == ("action", "calibration")
    assert row["outcome"] == "success"
    assert row["action"] == "Calibration report generated for plan P-2"
    assert row["execution_id"].startswith("CAL-P-2-")
    assert row["plan_branch"] == "I-1-cal/P-2/_"
    assert row["execution_branch"] == _EXEC_BRANCH
    assert row["target"] == report.calibrated_branch
    assert row["predicted_metrics"] == report.predicted.to_dict()
    assert row["actual_metrics"] == report.actual.to_dict()
    assert row["calibration_deltas"] == report.deltas.to_dict()
    assert row["calibration_report_path"] == "plans/I-1-cal/P-2_calibration.md"

    # The ACT-CAL row is committed with the report on the calibrated branch, and nothing else is.
    assert _cal_rows(_ref_ledger_text(repo, "HEAD")) == rows
    committed = sorted(git("show", "--name-only", "--pretty=format:", "HEAD").split())
    assert committed == [_FRAGMENT_REL, "plans/I-1-cal/P-2_calibration.md"]
    assert "untracked_scratch.txt" in git("status", "--porcelain")
    # Exactly one summary row, unchanged: no duplicate summary was written.
    all_rows = read_merged_rows_at_ref(str(repo), "HEAD", "executions.jsonl")
    assert [r for r in all_rows if r.get("entry_type") == "summary"] == [
        json.loads((repo / ledger_rel).read_text(encoding="utf-8").splitlines()[0])
    ]


def test_run_calibrate_metrics_unaffected_by_act_cal_rows(calibration_repo):
    repo, _ = calibration_repo
    with patch("sandbox_executor.calibration.run.format_calibration_report"):
        first = run_calibrate(_EXEC_BRANCH, target_worktree=str(repo), skip_commit=True)
        second = run_calibrate(_EXEC_BRANCH, target_worktree=str(repo), skip_commit=False)
        third = run_calibrate(_EXEC_BRANCH, target_worktree=str(repo), skip_commit=True)
    assert first.actual.to_dict() == second.actual.to_dict() == third.actual.to_dict()
    assert first.predicted.to_dict() == third.predicted.to_dict()


def test_run_calibrate_skip_commit_appends_nothing(calibration_repo):
    repo, _ = calibration_repo
    ledger = repo / "holon-knowledge" / "ledger" / "executions.jsonl"
    before, merged_before = ledger.read_bytes(), _merged_ledger_text(repo)
    with patch("sandbox_executor.calibration.run.format_calibration_report"):
        run_calibrate(_EXEC_BRANCH, target_worktree=str(repo), skip_commit=True)
    assert ledger.read_bytes() == before
    assert _merged_ledger_text(repo) == merged_before


def test_run_calibrate_ledger_failure_warns_and_still_commits_report(calibration_repo, caplog):
    repo, git = calibration_repo
    caplog.set_level("WARNING")
    with (
        patch("sandbox_executor.calibration.run.format_calibration_report"),
        patch("sandbox_executor.ledger.LedgerLock.__enter__", side_effect=PermissionError("ledger denied")),
    ):
        run_calibrate(_EXEC_BRANCH, target_worktree=str(repo), skip_commit=False)

    ledger_path = os.path.join(str(repo), "holon-knowledge", "ledger", "executions.jsonl")
    warnings = [rec.getMessage() for rec in caplog.records if rec.levelname == "WARNING"]
    assert any("ACT-CAL" in msg and ledger_path in msg and "ledger denied" in msg for msg in warnings)
    committed = git("show", "--name-only", "--pretty=format:", "HEAD").split()
    assert committed == ["plans/I-1-cal/P-2_calibration.md"]


def test_run_calibrate_ledger_failure_reaches_operator_log_fn(calibration_repo):
    repo, git = calibration_repo
    logs: list[str] = []
    with (
        patch("sandbox_executor.calibration.run.format_calibration_report"),
        patch("sandbox_executor.ledger.LedgerLock.__enter__", side_effect=PermissionError("ledger denied")),
    ):
        run_calibrate(_EXEC_BRANCH, target_worktree=str(repo), skip_commit=False, log_fn=logs.append)

    ledger_path = os.path.join(str(repo), "holon-knowledge", "ledger", "executions.jsonl")
    assert any("ACT-CAL" in msg and ledger_path in msg and "ledger denied" in msg for msg in logs)
    committed = git("show", "--name-only", "--pretty=format:", "HEAD").split()
    assert committed == ["plans/I-1-cal/P-2_calibration.md"]


def test_record_calibration_action_accepts_failure_dict(tmp_path, guard_real_ledger):
    logs: list[str] = []
    row = record_calibration_action(
        {"plan_id": "P-9", "calibrated_branch": "I-1/P-9/E-1/calibrated", "execution_branch": "I-1/P-9/E-1/_"},
        str(tmp_path),
        "plans/P-9_calibration.md",
        outcome="failure",
        error_context="Calibration run failed: boom",
        log_fn=logs.append,
    )
    assert row is not None
    assert row["action_id"].startswith("ACT-CAL-")
    assert row["outcome"] == "failure"
    assert row["action"] == "Calibration failed for plan P-9; nothing committed, row left uncommitted"
    assert row["predicted_metrics"] is None
    written = _merged_ledger_text(tmp_path)
    assert json.loads(written) == row
    assert logs == []


def test_record_calibration_action_redacts_error_context(tmp_path, guard_real_ledger):
    sample_value = "ghp_" + "R" * 36
    row = record_calibration_action(
        {"plan_id": "P-9", "calibrated_branch": "I-1/P-9/E-1/calibrated", "execution_branch": "I-1/P-9/E-1/_"},
        str(tmp_path),
        "plans/P-9_calibration.md",
        outcome="failure",
        error_context=f"Calibration run failed: remote rejected {sample_value}",
    )
    assert row is not None
    assert row["error_context"] == "Calibration run failed: remote rejected [REDACTED_SECRET]"
    written = _merged_ledger_text(tmp_path)
    assert sample_value not in written
    assert json.loads(written) == row


def _stage5_context(repo: Path, *, dry_run: bool):
    from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus

    ctx = FlowContext(
        target_worktree=str(repo),
        execution_branch=_EXEC_BRANCH,
        plan_branch="I-1-cal/P-2/_",
        dry_run=dry_run,
        skip_push=True,
    )
    ctx.stage_results["execute"] = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS)
    return ctx


def test_stage5_dry_run_leaves_executions_ledger_unchanged(calibration_repo):
    """A dry-run Stage 5 appends nothing, including no reconciliation event."""
    from sandbox_executor.flow import StageStatus, run_calibrate_stage

    repo, _ = calibration_repo
    ledger = repo / "holon-knowledge" / "ledger" / "executions.jsonl"
    before, merged_before = ledger.read_bytes(), _merged_ledger_text(repo)
    with patch("sandbox_executor.calibration.run.format_calibration_report"):
        res = run_calibrate_stage(_stage5_context(repo, dry_run=True))
    assert res.status == StageStatus.SUCCESS
    assert ledger.read_bytes() == before
    assert _merged_ledger_text(repo) == merged_before


def test_stage5_rejected_report_commit_takes_failure_path(calibration_repo):
    """A rejected report commit fails Stage 5 closed: its rows stay uncommitted, no push / ff / reconciliation."""
    from sandbox_executor.flow import StageStatus, run_calibrate_stage
    from sandbox_executor.git import run_git as real_run_git

    repo, git = calibration_repo
    _install_rejecting_pre_commit_hook(repo, git)
    calibrated = "I-1-cal/P-2/E-3/calibrated"
    exec_head_before = git("rev-parse", _EXEC_BRANCH).strip()
    ctx = _stage5_context(repo, dry_run=False)
    ctx.skip_push = False
    git_calls: list[list[str]] = []

    def recording_run_git(args, *a, **kw):
        git_calls.append(list(args))
        return real_run_git(args, *a, **kw)

    with (
        patch("sandbox_executor.calibration.run.format_calibration_report"),
        patch("sandbox_executor.flow.stages.calibrate.sync_before_calibration", return_value=None),
        patch("sandbox_executor.flow.stages.review.run_git", side_effect=recording_run_git),
    ):
        res = run_calibrate_stage(ctx)

    assert res.status == StageStatus.FAILED
    assert "nothing was committed or pushed" in (res.error or "")
    assert not [c for c in git_calls if c[:1] in (["push"], ["merge"]) or c[:1] == ["commit"]]
    assert git("rev-parse", _EXEC_BRANCH).strip() == exec_head_before
    assert git("branch", "--list", calibrated).strip() == ""
    assert git("rev-parse", "--abbrev-ref", "HEAD").strip() == _EXEC_BRANCH
    assert _cal_rows(_ref_ledger_text(repo, _EXEC_BRANCH)) == []
    # The ACT-CAL rows stay in the working tree: the rejected success row, then the failure row.
    working = _merged_ledger_text(repo)
    assert [r["outcome"] for r in _cal_rows(working)] == ["success", "failure"]
    failure = _cal_rows(working)[-1]
    assert "Git commit of calibration report plans/I-1-cal/P-2_calibration.md failed" in failure["error_context"]
    assert failure["action"] == "Calibration failed for plan P-2; nothing committed, row left uncommitted"
    assert _HOOK_SECRET not in working
    recon = [json.loads(line) for line in working.splitlines() if line.strip()]
    assert not [r for r in recon if r.get("event_type") == "reconciliation"]
    assert "holon-knowledge/ledger/pending/" in git("status", "--porcelain")
    joined = "\n".join(ctx.logs)
    assert _HOOK_SECRET not in joined
    assert _HOOK_SECRET not in (res.error or "")
    assert "Calibration attempt 1/3 failed (permanent)" in joined


def test_stage5_commits_reconciliation_row_on_calibrated_branch(calibration_repo):
    """The Stage 5 reconciliation row is committed on /calibrated and reaches the execution branch."""
    from sandbox_executor.flow import StageStatus, run_calibrate_stage

    repo, git = calibration_repo
    ledger_rel = "holon-knowledge/ledger/executions.jsonl"
    ctx = _stage5_context(repo, dry_run=False)
    with (
        patch("sandbox_executor.calibration.run.format_calibration_report"),
        patch("sandbox_executor.flow.stages.calibrate.sync_before_calibration", return_value=None),
    ):
        res = run_calibrate_stage(ctx)
    assert res.status == StageStatus.SUCCESS

    calibrated = "I-1-cal/P-2/E-3/calibrated"
    assert ctx.calibrated_branch == calibrated
    # The ledger has no uncommitted rows left behind.
    status = git("status", "--porcelain")
    assert ledger_rel not in status and "holon-knowledge/ledger/pending/" not in status
    for ref in (calibrated, _EXEC_BRANCH):
        rows = read_merged_rows_at_ref(str(repo), ref, "executions.jsonl")
        recon = [r for r in rows if r.get("event_type") == "reconciliation"]
        assert len(recon) == 1
        assert recon[0]["parent_action_id"] == _EXEC_BRANCH
        assert recon[0]["verdict"] == "success"
        assert len(_cal_rows(_ref_ledger_text(repo, ref))) == 1
    subjects = git("log", "--format=%s", "-2", calibrated).splitlines()
    assert subjects == [
        "chore(calibration): record stage 5 reconciliation",
        "chore(calibration): add calibration report for P-2",
    ]
    assert git("show", "--name-only", "--pretty=format:", calibrated).split() == [_FRAGMENT_REL]


# A calibration paragraph that the old pinned prettier@3.8.4 accepts as formatted (its `--check` passes) but the
# repository `npx prettier --check` (lint-docs, CI hygiene) rejects, and the same paragraph in the repository style that
# check accepts at width 120. Both are verbatim Prettier output.
_REPORT_REL = "plans/I-1-cal/P-2_calibration.md"
_PINNED_WRAP = (
    "# Calibration Report\n\n"
    "Plan `P-1793275710-claude-agent-claude-opus-5-5` executed with minimal error ($p\\_success\\_error = 0.20$,\n"
    "$cost\\_error =\n"
    "1.88$). The calibration step successfully completed the execution lifecycle and delivered verified\n"
    "post-execution analysis.\n"
)
_REPOSITORY_STYLE = (
    "# Calibration Report\n\n"
    "Plan `P-1793275710-claude-agent-claude-opus-5-5` executed with minimal error ($p\\_success\\_error = 0.20$, "
    "$cost\\_error =\n"
    "1.88$). The calibration step successfully completed the execution lifecycle and delivered verified "
    "post-execution\n"
    "analysis.\n"
)


class _FakePrettier:
    """Stand-in for calibration_format._run_prettier: records argv and rewrites the report like repository Prettier."""

    def __init__(
        self,
        check_returncode: int = 0,
        raises: Exception | None = None,
        expect_input: str | None = None,
        write_returncode: int = 0,
        failing_checks: int = 0,
    ):
        self.check_returncode = check_returncode
        self.raises = raises
        self.expect_input = expect_input
        self.write_returncode = write_returncode
        self.failing_checks = failing_checks
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, mode: str, report_rel: str, repo_dir: str) -> subprocess.CompletedProcess[str]:
        argv = [*LINT_DOCS_PRETTIER, mode, report_rel]
        self.calls.append((argv, repo_dir))
        if self.raises is not None:
            raise self.raises
        if mode == "--write":
            return self._write(argv, Path(repo_dir, report_rel))
        returncode = self.check_returncode
        if self.failing_checks > 0:
            self.failing_checks -= 1
            returncode = 1
        stderr = "" if returncode == 0 else f"[warn] {report_rel}\n[warn] Code style issues found.\n"
        stdout = "Checking formatting...\n"
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    def _write(self, argv: list[str], report: Path) -> subprocess.CompletedProcess[str]:
        if self.write_returncode != 0:
            return subprocess.CompletedProcess(argv, self.write_returncode, stdout="", stderr="[error] SyntaxError\n")
        if self.expect_input is not None:
            assert report.read_text(encoding="utf-8") == self.expect_input
            self.expect_input = None
        report.write_text(_REPOSITORY_STYLE, encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout=f"{report.name} 5ms\n", stderr="")


@pytest.fixture
def no_real_npx(monkeypatch):
    """Fail the test if anything reaches the real subprocess.run with npx (no network, no Node)."""
    real_run = subprocess.run

    def guarded_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "npx":
            raise AssertionError(f"real npx invoked: {cmd}")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)
    monkeypatch.setattr("sandbox_executor.calibration_format.shutil.which", lambda name: f"/usr/bin/{name}")


@pytest.mark.usefixtures("no_real_npx")
class TestFormatCalibrationReport:
    """The calibration report is formatted with the lint-docs Prettier invocation and fails closed."""

    def _write_report(self, repo: Path) -> None:
        (repo / _REPORT_REL).parent.mkdir(parents=True, exist_ok=True)
        (repo / _REPORT_REL).write_text(_PINNED_WRAP, encoding="utf-8")

    def test_report_needing_rewrap_is_formatted_to_repository_style(self, tmp_path):
        self._write_report(tmp_path)
        fake = _FakePrettier(expect_input=_PINNED_WRAP)
        with patch("sandbox_executor.calibration_format._run_prettier", side_effect=fake):
            format_calibration_report(_REPORT_REL, str(tmp_path))

        assert fake.expect_input is None
        assert (tmp_path / _REPORT_REL).read_text(encoding="utf-8") == _REPOSITORY_STYLE
        assert [argv[-2] for argv, _ in fake.calls] == ["--write", "--check"]
        for argv, cwd in fake.calls:
            assert argv[:3] == ["npx", "--yes", "prettier"]
            assert argv[-1] == _REPORT_REL
            assert cwd == str(tmp_path)

    def test_run_prettier_uses_lint_docs_argv_and_repository_cwd(self, tmp_path):
        with patch("sandbox_executor.calibration_format.subprocess.run") as mock_run:
            from sandbox_executor.calibration_format import _run_prettier

            _run_prettier("--check", _REPORT_REL, str(tmp_path))

        assert mock_run.call_args.args[0] == ["npx", "--yes", "prettier", "--check", _REPORT_REL]
        assert mock_run.call_args.kwargs["cwd"] == str(tmp_path)
        assert mock_run.call_args.kwargs["timeout"] > 0

    def test_invocation_matches_lint_docs_task(self):
        """Drift guard: the calibration Prettier must be the one `uv run task lint-docs` and CI `lint-md` run."""
        repo_root = Path(__file__).resolve().parents[3]
        pyproject = repo_root / "pyproject.toml"
        lint_docs = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["taskipy"]["tasks"]["lint-docs"]
        lint_docs_spec = lint_docs.split()[:2]
        calibration_spec = [arg for arg in LINT_DOCS_PRETTIER if arg != "--yes"]
        assert calibration_spec == lint_docs_spec == ["npx", "prettier"]
        makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
        lint_md_specs = set(re.findall(r'(npx \S+) --check "\*\*/\*\.md"', makefile))
        assert lint_md_specs == {" ".join(calibration_spec)}

    @pytest.mark.parametrize(
        ("fake", "which", "reason"),
        [
            (lambda: _FakePrettier(check_returncode=1), "/usr/bin/npx", "check exited 1"),
            (lambda: _FakePrettier(write_returncode=2), "/usr/bin/npx", "write exited 2: [error] SyntaxError"),
            (lambda: _FakePrettier(raises=subprocess.TimeoutExpired(["npx"], 120)), "/usr/bin/npx", "could not run"),
            (lambda: _FakePrettier(raises=FileNotFoundError("npx")), "/usr/bin/npx", "could not run"),
            (_FakePrettier, None, "npx is not on PATH"),
        ],
        ids=["check-fails", "write-fails", "timeout", "npx-not-found", "npx-missing"],
    )
    def test_guard_fails_closed(self, tmp_path, fake, which, reason):
        self._write_report(tmp_path)
        with (
            patch("sandbox_executor.calibration_format.shutil.which", return_value=which),
            patch("sandbox_executor.calibration_format._run_prettier", side_effect=fake()),
            pytest.raises(CalibrationFormatError) as excinfo,
        ):
            format_calibration_report(_REPORT_REL, str(tmp_path))

        message = str(excinfo.value)
        assert reason in message
        assert f"Calibration report {_REPORT_REL} failed the repository Prettier check" in message
        assert f"npx prettier --check {_REPORT_REL}" in message
        assert isinstance(excinfo.value, CalibrationCommitError)

    def test_failed_write_skips_the_check(self, tmp_path):
        self._write_report(tmp_path)
        fake = _FakePrettier(write_returncode=2)
        with (
            patch("sandbox_executor.calibration_format._run_prettier", side_effect=fake),
            pytest.raises(CalibrationFormatError),
        ):
            format_calibration_report(_REPORT_REL, str(tmp_path))
        assert [argv[-2] for argv, _ in fake.calls] == ["--write"]

    def test_check_converges_within_bounded_passes(self, tmp_path):
        self._write_report(tmp_path)
        fake = _FakePrettier(failing_checks=PRETTIER_PASSES - 1)
        with patch("sandbox_executor.calibration_format._run_prettier", side_effect=fake):
            format_calibration_report(_REPORT_REL, str(tmp_path))
        assert [argv[-2] for argv, _ in fake.calls] == ["--write", "--check"] * PRETTIER_PASSES

    def test_check_still_failing_after_bounded_passes_fails_closed(self, tmp_path):
        self._write_report(tmp_path)
        fake = _FakePrettier(check_returncode=1)
        with (
            patch("sandbox_executor.calibration_format._run_prettier", side_effect=fake),
            pytest.raises(CalibrationFormatError, match="check exited 1"),
        ):
            format_calibration_report(_REPORT_REL, str(tmp_path))
        assert len(fake.calls) == 2 * PRETTIER_PASSES

    def test_guard_message_redacts_credentials(self, tmp_path):
        sample_value = "ghp_" + "F" * 36
        fake = _FakePrettier(raises=OSError(f"npx auth {sample_value}"))
        with (
            patch("sandbox_executor.calibration_format._run_prettier", side_effect=fake),
            pytest.raises(CalibrationFormatError) as excinfo,
        ):
            format_calibration_report(_REPORT_REL, str(tmp_path))
        assert sample_value not in str(excinfo.value)
        assert "*******" in str(excinfo.value)

    def test_run_calibrate_fails_closed_before_ledger_and_git(self, calibration_repo):
        repo, git = calibration_repo
        ledger_rel = "holon-knowledge/ledger/executions.jsonl"
        ledger_before = (repo / ledger_rel).read_bytes()
        head_before = git("rev-parse", "HEAD").strip()
        git_calls: list[list[str]] = []
        from sandbox_executor.git import run_git as real_run_git

        def recording_run_git(args, *a, **kw):
            git_calls.append(list(args))
            return real_run_git(args, *a, **kw)

        with (
            patch("sandbox_executor.calibration_format._run_prettier", side_effect=_FakePrettier(check_returncode=1)),
            patch("sandbox_executor.calibration.refs.run_git", side_effect=recording_run_git),
            patch("sandbox_executor.calibration.run.run_git", side_effect=recording_run_git),
            patch("sandbox_executor.calibration.run.record_calibration_action") as mock_record,
            pytest.raises(CalibrationFormatError),
        ):
            run_calibrate(_EXEC_BRANCH, target_worktree=str(repo), skip_commit=False)

        mock_record.assert_not_called()
        assert not [c for c in git_calls if c[:1] in (["add"], ["commit"], ["push"])]
        assert (repo / ledger_rel).read_bytes() == ledger_before
        assert not (repo / "holon-knowledge" / "ledger" / "pending").exists()
        assert git("rev-parse", "HEAD").strip() == head_before

    def test_run_calibrate_skip_commit_only_warns(self, calibration_repo, caplog):
        repo, _ = calibration_repo
        caplog.set_level("WARNING", logger="sandbox_executor.calibration")
        with patch("sandbox_executor.calibration_format._run_prettier", side_effect=_FakePrettier(check_returncode=1)):
            report = run_calibrate(_EXEC_BRANCH, target_worktree=str(repo), skip_commit=True)

        assert report.committed is False
        assert (repo / _REPORT_REL).exists()
        assert any("failed the repository Prettier check" in rec.getMessage() for rec in caplog.records)

    def test_run_calibrate_skip_commit_warning_reaches_log_fn(self, calibration_repo):
        repo, _ = calibration_repo
        messages: list[str] = []
        with patch("sandbox_executor.calibration_format._run_prettier", side_effect=_FakePrettier(check_returncode=1)):
            run_calibrate(_EXEC_BRANCH, target_worktree=str(repo), skip_commit=True, log_fn=messages.append)

        assert any("failed the repository Prettier check" in m for m in messages)

    def test_run_calibrate_commits_report_that_passes_check(self, calibration_repo):
        repo, git = calibration_repo
        with patch("sandbox_executor.calibration_format._run_prettier", side_effect=_FakePrettier()):
            run_calibrate(_EXEC_BRANCH, target_worktree=str(repo), skip_commit=False)

        assert git("show", f"HEAD:{_REPORT_REL}") == _REPOSITORY_STYLE
