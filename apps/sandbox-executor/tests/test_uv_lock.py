"""Hermetic tests that the flow freezes ``uv.lock`` and that the committed lock needs no rewrite."""

import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from sandbox_executor.entrypoint import review_loop
from sandbox_executor.entrypoint.review_markdown_gate import MarkdownGateOutcome
from sandbox_executor.entrypoint.test_measurement import _run_in_process_group
from sandbox_executor.entrypoint.uv_lock import LockfileMismatchError, check_lockfile, frozen_env

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]


def _fixture_dir(tmp_path: Path) -> str:
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    return str(tmp_path)


def _runner(returncode: int, stderr: str = ""):
    def run(args, cwd):
        return subprocess.CompletedProcess(args, returncode, stdout="", stderr=stderr)

    return run


def test_frozen_env_sets_uv_frozen_without_mutating_the_base(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text("")
    base = {"PATH": "/bin"}
    assert frozen_env(base, str(tmp_path)) == {"PATH": "/bin", "UV_FROZEN": "1"}
    assert base == {"PATH": "/bin"}


def test_frozen_env_leaves_a_directory_without_a_lock_alone(tmp_path: Path) -> None:
    assert frozen_env({"PATH": "/bin"}, str(tmp_path)) == {"PATH": "/bin"}


def test_a_current_lock_passes_the_preflight(tmp_path: Path) -> None:
    assert check_lockfile(_fixture_dir(tmp_path), _runner(0)) is None


def test_a_directory_without_a_lock_is_not_checked(tmp_path: Path) -> None:
    assert check_lockfile(str(tmp_path), _runner(2)) is None


def test_a_stale_lock_raises_a_named_error_naming_both_files(tmp_path: Path) -> None:
    with pytest.raises(LockfileMismatchError) as excinfo:
        check_lockfile(_fixture_dir(tmp_path), _runner(1, "needs update"))
    message = str(excinfo.value)
    assert "uv.lock" in message and "pyproject.toml" in message and "regenerate" in message


def test_a_missing_uv_is_a_structured_failure_not_an_exception(tmp_path: Path) -> None:
    problem = check_lockfile(_fixture_dir(tmp_path), _runner(127, "required tool 'uv' was not found on PATH"))
    assert problem is not None and "uv" in problem


def test_the_test_measurement_subprocess_is_frozen(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text("")
    code = "import os; print(os.environ.get('UV_FROZEN'))"
    result = _run_in_process_group([sys.executable, "-c", code], str(tmp_path), 30)
    assert result.stdout.strip() == "1"


def test_resolver_verification_freezes_lint_and_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "uv.lock").write_text("")
    seen: list[dict[str, str] | None] = []

    def fake_run_cmd(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[:3] == ["uv", "run", "task"]:
            seen.append(kwargs.get("env"))  # type: ignore[arg-type]
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(review_loop, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(review_loop, "run_markdown_gate", lambda *_: MarkdownGateOutcome())
    review_loop._verify_before_push(str(tmp_path))
    assert len(seen) == 2 and all(env and env["UV_FROZEN"] == "1" for env in seen)


def test_resolver_verification_fails_clearly_on_a_stale_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = _fixture_dir(tmp_path)
    monkeypatch.setattr(
        "sandbox_executor.entrypoint.uv_lock._default_runner",
        _runner(1, "lock needs update"),
    )
    monkeypatch.setattr(review_loop, "run_cmd", lambda *a, **k: pytest.fail("uv run must not start"))
    ok, _, error = review_loop._verify_before_push(directory)
    assert ok is False and error is not None and "uv.lock" in error


def test_committed_lock_needs_no_rewrite() -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not installed")
    before = (PACKAGE_DIR / "uv.lock").read_bytes()
    result = subprocess.run(
        [uv, "lock", "--check", "--offline"], cwd=PACKAGE_DIR, capture_output=True, text=True, check=False
    )
    if "No interpreter found" in result.stderr:
        pytest.skip("Python 3.13 is not available")
    assert result.returncode == 0, result.stderr
    assert (PACKAGE_DIR / "uv.lock").read_bytes() == before


def test_lock_python_bound_agrees_with_project_and_dockerfile() -> None:
    lock = tomllib.loads((PACKAGE_DIR / "uv.lock").read_text(encoding="utf-8"))
    project = tomllib.loads((PACKAGE_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    bound = project["project"]["requires-python"]
    assert lock["requires-python"] == bound
    minor = re.search(r"==(\d+\.\d+)\.\*", bound)
    assert minor, "requires-python must pin one minor version"
    dockerfile = (PACKAGE_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert f"FROM python:{minor.group(1)}-slim AS holon-base" in dockerfile
    assert re.search(r"^ARG UV_VERSION=\d+\.\d+\.\d+$", dockerfile, flags=re.MULTILINE)


def test_hygiene_workflow_checks_the_executor_lock() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "test-hygiene.yml").read_text(encoding="utf-8")
    assert "working-directory: apps/sandbox-executor" in workflow
    assert workflow.count("uv lock --check") >= 2
