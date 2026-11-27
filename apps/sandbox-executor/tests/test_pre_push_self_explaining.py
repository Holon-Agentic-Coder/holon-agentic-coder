import json
import pathlib
import shutil
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.entrypoint.executor import ExecutionPromptInputs, build_execution_prompt, pre_push
from sandbox_executor.entrypoint.executor.pre_push import PrePushSteps
from sandbox_executor.git import GitRepository
from sandbox_executor.ledger import read_merged_rows

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
CHECKER_MODULES = (
    "check_self_explaining.py",
    "self_explaining_comments.py",
    "self_explaining_diff.py",
    "self_explaining_hash.py",
    "module_budget_freeze.py",
)
LEDGER = "holon-knowledge/ledger/executions.jsonl"
EXEC_ID = "E-3-a-b"
DOCSTRING_MODULE_LINES = ["def answer() -> int:", '    """Return the answer."""', "    return 42"]
WHY_COMMENT_MODULE_LINES = [
    "def answer() -> int:",
    "    # why: the protocol fixes the answer at 42.",
    "    return 42",
]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def write_lines(repo: Path, name: str, lines: list[str]) -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def gated_repository(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    for key, value in (("user.email", "t@holon.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        git(work, "config", key, value)
    (work / "scripts").mkdir()
    for module in CHECKER_MODULES:
        shutil.copy(REPO_ROOT / "scripts" / module, work / "scripts" / module)
    write_lines(work, LEDGER, [json.dumps({"execution_id": "E-base"})])
    commit_all(work, "base")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "main")
    git(work, "fetch", "-q", "origin")
    git(work, "checkout", "-q", "-b", "feature")
    return work


class GatedRun(PrePushSteps):
    def __init__(self, work: Path, status: str = "success") -> None:
        self.repo_dir = str(work)
        self.repo = GitRepository(str(work))
        self.exec_entry = {"execution_id": EXEC_ID, "status": status, "ledger_revision": 1}
        self.exec_id = EXEC_ID
        self.ledger_dir = str(work / "holon-knowledge" / "ledger")
        self.plan_tip = git(work, "rev-parse", "HEAD")
        self.intent_data = {"target_branch": "main"}
        self.exec_branch = "feature"
        self.exec_status = status
        self.branch_recorded = False


@pytest.fixture
def sync_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.delenv("HOLON_TARGET_BRANCH", raising=False)

    def recording_sync(repo_dir: str, target_branch: str = "main") -> tuple[bool, str]:
        calls.append(repo_dir)
        return True, "ok"

    monkeypatch.setattr(pre_push, "attribution_allows_publish", lambda plan_tip, runner: True)
    monkeypatch.setattr(pre_push.executor_facade, "sync_and_reconcile_pre_push", recording_sync)
    return calls


def last_ledger_row(work: Path) -> dict:
    return read_merged_rows(str(work / LEDGER))[-1]


def test_docstring_branch_is_refused_with_path_and_line_and_not_pushed(
    gated_repository: Path, sync_calls: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    write_lines(gated_repository, "pkg/module.py", DOCSTRING_MODULE_LINES)
    commit_all(gated_repository, "add module")
    run = GatedRun(gated_repository)

    run.record_branch()

    assert run.branch_recorded is False
    assert run.exec_status == "failure"
    assert sync_calls == []
    assert git(gated_repository, "ls-remote", "origin", "feature") == ""
    assert "pkg/module.py:2" in capsys.readouterr().err
    row = last_ledger_row(gated_repository)
    assert row["status"] == "failure"
    assert row["failure_reason"] == "self_explaining_violation"
    assert "pkg/module.py:2" in row["summary"]
    assert git(gated_repository, "status", "--porcelain", "holon-knowledge") == ""


def test_code_with_only_a_why_comment_is_recorded(gated_repository: Path, sync_calls: list[str]) -> None:
    write_lines(gated_repository, "pkg/module.py", WHY_COMMENT_MODULE_LINES)
    commit_all(gated_repository, "add module")
    run = GatedRun(gated_repository)

    run.record_branch()

    assert run.branch_recorded is True
    assert sync_calls == [str(gated_repository)]


def test_untracked_file_with_a_docstring_does_not_block(gated_repository: Path, sync_calls: list[str]) -> None:
    write_lines(gated_repository, "pkg/module.py", WHY_COMMENT_MODULE_LINES)
    commit_all(gated_repository, "add module")
    write_lines(gated_repository, "scratch.py", DOCSTRING_MODULE_LINES)
    run = GatedRun(gated_repository)

    run.record_branch()

    assert run.branch_recorded is True


def test_non_ascii_tracked_path_with_a_docstring_is_refused(gated_repository: Path, sync_calls: list[str]) -> None:
    write_lines(gated_repository, "pkg/módulo.py", DOCSTRING_MODULE_LINES)
    commit_all(gated_repository, "add module")
    run = GatedRun(gated_repository)

    run.record_branch()

    assert run.branch_recorded is False
    assert "pkg/módulo.py:2" in last_ledger_row(gated_repository)["summary"]


def test_non_main_target_skips_the_gate_with_a_notice(
    gated_repository: Path, sync_calls: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    write_lines(gated_repository, "pkg/module.py", DOCSTRING_MODULE_LINES)
    commit_all(gated_repository, "add module")
    run = GatedRun(gated_repository)
    run.intent_data = {"target_branch": "release"}

    run.record_branch()

    assert run.branch_recorded is True
    assert "skipped for target release" in capsys.readouterr().out


def test_repository_without_the_checker_skips_the_gate(
    gated_repository: Path, sync_calls: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    git(gated_repository, "rm", "-q", "-r", "scripts")
    write_lines(gated_repository, "pkg/module.py", DOCSTRING_MODULE_LINES)
    commit_all(gated_repository, "add module without checker")
    run = GatedRun(gated_repository)

    run.record_branch()

    assert run.branch_recorded is True
    assert "self-explaining check was skipped" in capsys.readouterr().out


def test_failed_execution_skips_the_gate_so_salvaged_work_stays_publishable(
    gated_repository: Path, sync_calls: list[str]
) -> None:
    write_lines(gated_repository, "pkg/module.py", DOCSTRING_MODULE_LINES)
    commit_all(gated_repository, "salvage partial work")
    run = GatedRun(gated_repository, status="failure")

    run.record_branch()

    assert run.branch_recorded is True
    assert last_ledger_row(gated_repository)["execution_id"] == "E-base"


def test_executor_prompt_renders_and_states_the_check_runs_before_push() -> None:
    rendered = build_execution_prompt(
        repo_dir=str(REPO_ROOT),
        plan_branch="P-1-test",
        inputs=ExecutionPromptInputs(
            plan_data={"plan_id": "P-1-test"},
            plan_content="Dummy plan.",
            intent_data={"intent_id": "I-1-test"},
        ),
        exec_id="E-1-test",
    )
    text = " ".join(rendered.split()).lower()

    assert "runs `scripts/check_self_explaining.py` before the push" in text
    assert "{exec_id}" not in rendered
    assert "{notes_path}" not in rendered
