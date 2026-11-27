from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.repo_templates import copy_template, shared_template


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _build_hermetic_repos(root_dir: Path) -> None:
    src_dir = root_dir / "src_repo"
    src_dir.mkdir()
    _git(src_dir, "init")
    _git(src_dir, "config", "user.name", "Hermetic Test")
    _git(src_dir, "config", "user.email", "test@hermetic.local")
    _git(src_dir, "checkout", "-B", "main")
    (src_dir / "README.md").write_text("# Hermetic Repository\n", encoding="utf-8")
    _git(src_dir, "add", "README.md")
    _git(src_dir, "commit", "-m", "Initial commit on main")
    _git(root_dir, "clone", "--bare", str(src_dir), str(root_dir / "bare.git"))


# why: building the repositories with real git once per run and copying them keeps each test fast.
def hermetic_repos_copy(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> dict[str, Path]:
    template, _ = shared_template(tmp_path_factory, "pipeline-hermetic-repos", _build_hermetic_repos)
    root_dir = copy_template(template, tmp_path / "hermetic_env")
    return {"root": root_dir, "src": root_dir / "src_repo", "bare": root_dir / "bare.git"}
