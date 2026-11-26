"""The mounted-workspace cleanliness check ignores untracked plan directories an aborted attempt left behind.

Each test runs `validate_mounted_workspace` against a real temporary git repository: an untracked file under the
engine-owned `plans/I-*/` prefix is accepted, every other uncommitted change is still refused.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.entrypoint.execution_workspace import _foreign_status_lines, validate_mounted_workspace
from sandbox_executor.git import GitRepository

_PLAN = "I-1795691579-planner-ignores-aborted-plan-dir/P-1-x/_"
_PLAN_DIR = "plans/I-1795691579-planner-ignores-aborted-plan-dir"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _isolate_git_identity(monkeypatch: pytest.MonkeyPatch, config_dir: Path) -> None:
    # validate_mounted_workspace adds safe.directory to the global config, so each test owns its own file.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config_dir / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Holon Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@holon.com")


@pytest.fixture(scope="module")
def template_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A committed README.md and tracked plans/README.md on main, plus the plan branch ref; built once per module."""
    root = tmp_path_factory.mktemp("untracked-plans-template")
    with pytest.MonkeyPatch.context() as mp:
        _isolate_git_identity(mp, root)
        repo = root / "repo"
        (repo / "plans").mkdir(parents=True)
        (repo / "README.md").write_text("seed\n", encoding="utf-8")
        (repo / "plans" / "README.md").write_text("plans\n", encoding="utf-8")
        _git(root, "init", "-q", "-b", "main", str(repo))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "seed")
        _git(repo, "branch", _PLAN)
    return repo


@pytest.fixture
def repo(template_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _isolate_git_identity(monkeypatch, tmp_path)
    copy = tmp_path / "repo"
    shutil.copytree(template_repo, copy, symlinks=True)
    return copy.resolve()


def _validate(repo: Path) -> str:
    return validate_mounted_workspace(GitRepository(repo), str(repo), _PLAN)


def _write(repo: Path, rel_path: str, text: str = "leftover\n") -> None:
    path = repo / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_an_untracked_plan_directory_from_an_aborted_attempt_is_accepted(repo: Path) -> None:
    _write(repo, f"{_PLAN_DIR}/P-1-x.md")

    assert _validate(repo) == _git(repo, "rev-parse", f"refs/heads/{_PLAN}")
    assert (repo / _PLAN_DIR / "P-1-x.md").read_text(encoding="utf-8") == "leftover\n"


@pytest.mark.parametrize("rel_path", ["notes.txt", "plans/scratch.md", "plans/I-notes.md"])
def test_an_untracked_file_outside_the_plan_directories_is_refused(repo: Path, rel_path: str) -> None:
    _write(repo, rel_path)

    with pytest.raises(RuntimeError, match="has uncommitted changes"):
        _validate(repo)


@pytest.mark.parametrize("rel_path", ["README.md", "plans/README.md"])
def test_a_modified_tracked_file_is_refused_even_under_plans(repo: Path, rel_path: str) -> None:
    _write(repo, rel_path, "edited\n")

    with pytest.raises(RuntimeError, match="has uncommitted changes"):
        _validate(repo)


def test_a_foreign_untracked_file_beside_a_leftover_plan_directory_is_refused(repo: Path) -> None:
    _write(repo, f"{_PLAN_DIR}/P-1-x.md")
    _write(repo, "notes.txt")

    with pytest.raises(RuntimeError, match="has uncommitted changes"):
        _validate(repo)


def test_the_porcelain_filter_keeps_tracked_codes_and_strips_quoted_plan_paths() -> None:
    stdout = (
        '?? "plans/I-1-a b/x.md"\n?? plans/I-2-y/\n M plans/I-3-z/P-1.md\nA  plans/I-4-w/P-2.md\n?? plans/\n'
        "?? plans/I-5.md\n"
    )

    assert _foreign_status_lines(stdout) == [
        " M plans/I-3-z/P-1.md",
        "A  plans/I-4-w/P-2.md",
        "?? plans/",
        "?? plans/I-5.md",
    ]
