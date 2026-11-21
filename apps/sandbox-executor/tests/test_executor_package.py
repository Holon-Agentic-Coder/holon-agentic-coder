"""The executor package's two conversion hazards: the ``python -m`` entry and the facade patch seam.

``role_dispatcher.sh`` runs ``python3 -m sandbox_executor.entrypoint.executor``, which on a package runs its
``__main__``. Tests patch collaborators on ``sandbox_executor.entrypoint.executor``; the step modules must look those
names up on the package at call time, or the patch silently stops reaching the code under test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sandbox_executor.entrypoint.executor as executor
from sandbox_executor.entrypoint.execution_args import ExecutionArgs
from sandbox_executor.entrypoint.executor import (
    agent_output,
    commands,
    outcome,
    pre_push,
    repair,
    run,
    sub_intents,
)

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
USAGE_LINE = "Usage: executor.py <plan_branch> [agent_name] [model_name] [partial_execution_branch]"

#: Every name a test patches on the package, per step module that calls it.
SEAM_NAMES_BY_MODULE: dict[ModuleType, tuple[str, ...]] = {
    commands: ("run_cmd", "_get_local_safe_directories"),
    repair: ("run_cmd", "_get_local_safe_directories", "workspace_mounted"),
    pre_push: ("run_cmd", "sync_and_reconcile_pre_push"),
    sub_intents: ("run_cmd", "atomic_ledger_append", "append_action_record"),
    agent_output: ("run_cmd", "append_action_record", "prepare_agent_output_block"),
    outcome: (
        "run_cmd",
        "atomic_ledger_append",
        "append_action_record",
        "measure_executor_tests",
        "converge_prettier",
    ),
    run: (
        "run_cmd",
        "get_runner",
        "get_workspace_dir",
        "workspace_mounted",
        "validate_mounted_workspace",
        "run_turn",
        "supervise_authoring_turn",
        "classify_execution_outcome",
        "record_budget_failure",
    ),
}


def _run_python(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the repository's interpreter on the in-repo ``src`` tree with a scrubbed environment."""
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(SRC_DIR)}
    return subprocess.run(
        [sys.executable, *args], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60, check=False
    )


def test_python_dash_m_runs_the_package_entry(tmp_path: Path) -> None:
    """``python -m sandbox_executor.entrypoint.executor`` with no arguments prints the usage line and exits 1."""
    result = _run_python(tmp_path, "-m", "sandbox_executor.entrypoint.executor")

    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == [USAGE_LINE]
    assert "cannot be directly executed" not in result.stderr
    assert result.stderr == ""


def test_python_dash_m_resolves_the_in_repo_package(tmp_path: Path) -> None:
    """The entry test above exercises this checkout's package, not another installed copy."""
    probe = "import sandbox_executor.entrypoint.executor as m; print(m.__file__)"
    result = _run_python(tmp_path, "-c", probe)

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == SRC_DIR / "sandbox_executor" / "entrypoint" / "executor" / "__init__.py"


def _recording_run_cmd(calls: list[list[str]], returncode: int = 0) -> Callable[..., Any]:
    """A ``run_cmd`` stand-in that records each command and returns ``returncode`` without running anything."""

    def fake_run_cmd(args: list[str], cwd: str | None = None, check: bool = True, env: Any = None) -> Any:
        calls.append(list(args))
        return subprocess.CompletedProcess(args, returncode, stdout="", stderr="")

    return fake_run_cmd


def _drive_repair(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    """``repair._persist_safe_directory`` shells out through the package's ``run_cmd``."""
    calls: list[list[str]] = []
    monkeypatch.setattr("sandbox_executor.entrypoint.executor.run_cmd", _recording_run_cmd(calls))
    repair._persist_safe_directory(str(tmp_path))
    return calls


def _drive_pre_push(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    """``pre_push.sync_and_reconcile_pre_push`` probes the target ref through the package's ``run_cmd``."""
    calls: list[list[str]] = []
    monkeypatch.setattr("sandbox_executor.entrypoint.executor.run_cmd", _recording_run_cmd(calls, returncode=1))
    ok, message = pre_push.sync_and_reconcile_pre_push(str(tmp_path), target_branch="main")
    assert (ok, message) == (True, "Remote branch origin/main not found")
    return calls


def _drive_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    """``ExecutionRun.run`` takes its runner and workspace from the package's ``get_runner`` and probes."""
    calls: list[list[str]] = []

    class FakeRunner:
        def validate(self) -> None:
            calls.append(["runner.validate"])

    def fake_get_runner(agent_name: str) -> FakeRunner:
        calls.append(["get_runner", agent_name])
        return FakeRunner()

    monkeypatch.setattr("sandbox_executor.entrypoint.executor.get_runner", fake_get_runner)
    monkeypatch.setattr("sandbox_executor.entrypoint.executor.get_workspace_dir", lambda: str(tmp_path))
    monkeypatch.setattr("sandbox_executor.entrypoint.executor.workspace_mounted", lambda: False)
    execution = run.ExecutionRun(ExecutionArgs.from_argv(["executor.py", "I-x/P-1/_", "fake-agent", "fake-model"]))
    with pytest.raises(SystemExit) as excinfo:
        execution.run()
    assert excinfo.value.code == 1
    assert execution.repo_dir == str(tmp_path)
    return calls


def _drive_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    """``run_cmd`` consults the package's ``_get_local_safe_directories`` before injecting ``safe.directory``."""
    calls: list[list[str]] = []

    def fake_safe_directories(repo_dir: str) -> list[str]:
        calls.append(["_get_local_safe_directories", repo_dir])
        return ["*"]

    def fake_subprocess_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("sandbox_executor.entrypoint.executor._get_local_safe_directories", fake_safe_directories)
    monkeypatch.setattr(commands.subprocess, "run", fake_subprocess_run)
    commands.run_cmd(["git", "status"], cwd=str(tmp_path))
    assert ["git", "-c", f"safe.directory={tmp_path}", "status"] in calls
    return calls


@pytest.mark.parametrize(
    "drive",
    [_drive_repair, _drive_pre_push, _drive_run, _drive_commands],
    ids=["repair", "pre_push", "run", "commands"],
)
def test_patching_the_package_reaches_the_step_modules(
    drive: Callable[[pytest.MonkeyPatch, Path], list[list[str]]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A patch on ``sandbox_executor.entrypoint.executor.<name>`` changes what the step module actually calls."""
    calls = drive(monkeypatch, tmp_path)

    assert calls, "the patched package attribute was never called: the step module bypasses the facade"


@pytest.mark.parametrize(
    ("module", "name"),
    [(module, name) for module, names in SEAM_NAMES_BY_MODULE.items() for name in names],
    ids=lambda value: value.__name__.rsplit(".", 1)[-1] if isinstance(value, ModuleType) else value,
)
def test_step_modules_bind_no_seam_name_of_their_own(module: ModuleType, name: str) -> None:
    """Negative control: a ``from x import name`` of a seam name in a step module would bypass a package patch.

    A module that defines the name (``commands`` defines ``run_cmd``) only has to be the one the facade re-exports; the
    seam test above checks that it is still called through the facade.
    """
    defines_it = getattr(getattr(module, name, None), "__module__", None) == module.__name__
    if defines_it:
        assert getattr(executor, name) is getattr(module, name), f"the facade does not re-export {name}"
    else:
        assert not hasattr(module, name), f"{module.__name__} binds {name} itself; read it as executor_facade.{name}"
