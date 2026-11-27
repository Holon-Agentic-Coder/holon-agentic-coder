from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.entrypoint.test_failure_record import LEDGER_RELPATH, STAGE_5, failing_ids_from_output
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.github import ci_reverify
from sandbox_executor.flow.github.ci_reverify import _gh_failed_check_output, reverify_and_report
from sandbox_executor.flow.github.stage5_test_failure import UNDIAGNOSED_CLASSIFICATION
from sandbox_executor.flow.github.stage5_test_failure import check_log_text as _check_log_text
from sandbox_executor.ledger.merged import read_merged_rows

APPROVED = "a" * 40
FINAL = "b" * 40
BRANCH = "I-1-demo/P-2-CLA-CO5.5/E-3-CLA-CO5.5/_"
ALPHA = "tests/test_demo.py::test_alpha"
BETA = "tests/test_demo.py::test_beta"
RED_RECORD = {"state": "failed", "failed_checks": ["unit-tests"]}
GREEN_RECORD = {"state": "success"}
PYTEST_LINES = (
    "============================= FAILURES =============================",
    "____________________________ test_alpha ____________________________",
    "    def test_alpha():",
    ">       assert compute() == 3",
    "E       AssertionError: assert 2 == 3",
    "tests/test_demo.py:12: AssertionError",
    "____________________________ test_beta _____________________________",
    "    def test_beta():",
    ">       parse('x')",
    "E       ValueError: invalid literal for int() with base 10: 'x'",
    "tests/test_demo.py:20: ValueError",
    "=========================== short test summary info ============================",
    "FAILED tests/test_demo.py::test_alpha - AssertionError: assert 2 == 3",
    "FAILED tests/test_demo.py::test_beta - ValueError: invalid literal for int() with base 10: 'x'",
    "========================= 2 failed, 40 passed in 3.21s =========================",
)
LINT_LINES = (
    "Found 1 error.",
    "src/app.py:3:1: F401 `os` imported but unused",
    "Error: Process completed with exit code 1.",
)


def _gh_log(lines: tuple[str, ...], step: str = "Run tests") -> str:
    return "\n".join(f"unit-tests\t{step}\t2026-11-27T01:02:03.1234567Z {line}" for line in lines) + "\n"


CAPTURED_LOG = _gh_log(PYTEST_LINES)
LINT_LOG = _gh_log(LINT_LINES, step="Lint")


@pytest.fixture
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FlowContext:
    monkeypatch.setattr(ci_reverify, "invalidate_approval", lambda context, reverified: {"posted": False})
    monkeypatch.setattr(ci_reverify, "published_head_of", lambda repo_dir: FINAL)
    flow_context = FlowContext(
        target_worktree=str(tmp_path), execution_branch=BRANCH, token_reduce=False, agent="a", model="m"
    )
    flow_context.stage_results["review"] = StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.HALTED_FOR_HUMAN,
        payload={"consensus": {"approved": True, "target_commit": APPROVED}},
    )
    return flow_context


def _test_failure_rows(tmp_path: Path) -> list[dict[str, Any]]:
    rows = read_merged_rows(os.path.join(tmp_path, *LEDGER_RELPATH))
    return [row for row in rows if row.get("action") == "test_failure"]


def test_failed_final_head_check_appends_one_stage_5_row_with_ids_excerpts_and_classification(
    context: FlowContext, tmp_path: Path
) -> None:
    reverified = reverify_and_report(context, str(tmp_path), RED_RECORD, True, failed_output=lambda r, h: CAPTURED_LOG)

    rows = _test_failure_rows(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["stage"] == STAGE_5
    assert row["failing_test_ids"] == [ALPHA, BETA]
    assert set(row["test_excerpts"]) == {ALPHA, BETA}
    assert "E       AssertionError: assert 2 == 3" in row["test_excerpts"][ALPHA]
    assert "E       ValueError: invalid literal" in row["test_excerpts"][BETA]
    assert row["classification"] == UNDIAGNOSED_CLASSIFICATION
    assert row["action_origin"] == "calibration"
    assert re.fullmatch(r"[0-9a-f]{16}", row["failure_signature"])
    assert row["head_sha"] == FINAL
    assert row["run_id"] == f"E-3-CLA-CO5.5:{FINAL}"
    assert reverified["verdict"] == "red"
    assert reverified["test_failure_record"]["failure_signature"] == row["failure_signature"]


def test_green_final_head_check_appends_no_row_and_never_looks_up_the_log(context: FlowContext, tmp_path: Path) -> None:
    lookups: list[str] = []

    def lookup(repo_dir: str, head: str) -> str:
        lookups.append(head)
        return CAPTURED_LOG

    reverified = reverify_and_report(context, str(tmp_path), GREEN_RECORD, True, failed_output=lookup)

    assert lookups == []
    assert _test_failure_rows(tmp_path) == []
    assert "test_failure_record" not in reverified


@pytest.mark.parametrize("output", [LINT_LOG, ""])
def test_red_final_head_check_without_failing_tests_appends_no_row(
    context: FlowContext, tmp_path: Path, output: str
) -> None:
    reverified = reverify_and_report(context, str(tmp_path), RED_RECORD, True, failed_output=lambda r, h: output)

    assert _test_failure_rows(tmp_path) == []
    assert reverified["test_failure_record"] is None


def test_gh_lookup_runs_gh_in_the_repo_with_the_inherited_environment() -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def recorder(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        stdout = json.dumps([{"databaseId": 42}]) if argv[1:3] == ["run", "list"] else CAPTURED_LOG
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    output = _gh_failed_check_output("/repo", FINAL, run=recorder)

    assert output == CAPTURED_LOG
    assert [argv for argv, _ in calls] == [
        ["gh", "run", "list", "--commit", FINAL, "--status", "failure", "--json", "databaseId", "--limit", "3"],
        ["gh", "run", "view", "42", "--log-failed"],
    ]
    assert all(kwargs["cwd"] == "/repo" and "env" not in kwargs for _, kwargs in calls)


def test_gh_lookup_error_gives_empty_output() -> None:
    def failing(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise OSError("gh missing")

    assert _gh_failed_check_output("/repo", FINAL, run=failing) == ""


def test_log_prefix_is_stripped_before_parsing() -> None:
    cleaned = _check_log_text(CAPTURED_LOG)

    assert cleaned.splitlines()[0] == PYTEST_LINES[0]
    assert failing_ids_from_output(cleaned) == (ALPHA, BETA)
    assert _check_log_text("\n".join(PYTEST_LINES)) == "\n".join(PYTEST_LINES)


def test_log_content_tabs_survive_prefix_stripping() -> None:
    assert _check_log_text("unit-tests\tRun tests\tE       assert 'a\tb' == 'ab'") == "E       assert 'a\tb' == 'ab'"


def test_branch_without_execution_segment_uses_one_unknown_id(context: FlowContext, tmp_path: Path) -> None:
    context.execution_branch = "I-1-demo/P-2-CLA-CO5.5/_"
    reverify_and_report(context, str(tmp_path), RED_RECORD, True, failed_output=lambda r, h: CAPTURED_LOG)

    [row] = _test_failure_rows(tmp_path)
    assert row["execution_id"] == "E-unknown"
    assert row["run_id"] == f"E-unknown:{FINAL}"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_stage5_row_is_committed_and_pushed_leaving_a_clean_tree(context: FlowContext, tmp_path: Path) -> None:
    remote, repo = tmp_path / "remote.git", tmp_path / "repo"
    _git(tmp_path, "init", "--bare", "-q", str(remote))
    _git(tmp_path, "init", "-q", "-b", BRANCH, str(repo))
    for key, value in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        _git(repo, "config", key, value)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    _git(repo, "push", "-q", "origin", BRANCH)
    context.target_worktree = str(repo)

    reverified = reverify_and_report(context, str(repo), RED_RECORD, True, failed_output=lambda r, h: CAPTURED_LOG)

    assert reverified["test_failure_record"]["pushed"] is True
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "rev-parse", "HEAD") == _git(remote, "rev-parse", BRANCH)
    head_after_push = _git(repo, "rev-parse", "HEAD").strip()
    assert reverified["test_failure_record"]["head_after_push"] == head_after_push
    assert f"ledger-only commit {head_after_push}" in reverified["report"]
    assert "test_failure" in _git(remote, "show", BRANCH)
