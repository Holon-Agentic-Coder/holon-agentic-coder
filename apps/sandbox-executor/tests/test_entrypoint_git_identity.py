"""The intent creator and planner never write git config: a mounted worktree's repository config is read-only."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.entrypoint import intent_creator, planner

ENTRYPOINT_DIR = Path(intent_creator.__file__).resolve().parent
ROLE_ENTRYPOINTS = ("intent_creator.py", "planner.py")


@pytest.mark.parametrize("name", ROLE_ENTRYPOINTS)
def test_role_entrypoint_runs_no_git_config(name):
    source = (ENTRYPOINT_DIR / name).read_text(encoding="utf-8")
    assert not re.search(r'"git",\s*"config"', source), f"{name} writes git config"
    assert "--local" not in source, f"{name} names the repository-local scope"


def test_planner_identity_is_passed_on_the_command_line():
    identity = planner.PLANNER_GIT_IDENTITY
    assert identity[0] == "git"
    assert "config" not in identity
    assert "user.email=planner-agent@holon-agentic-coder.com" in identity
    assert "user.name=Holon Planner Agent" in identity


def test_commit_with_the_command_line_identity_succeeds_when_the_repository_config_is_read_only(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(HOME=str(tmp_path / "home"), GIT_CONFIG_NOSYSTEM="1")
    (tmp_path / "home").mkdir()
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    (repo / "plan.md").write_text("plan\n", encoding="utf-8")
    subprocess.run(["git", "add", "plan.md"], cwd=repo, check=True, env=env)
    config = repo / ".git" / "config"
    config.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    before = config.read_text(encoding="utf-8")
    subprocess.run([*planner.PLANNER_GIT_IDENTITY, "commit", "-q", "-m", "plan: x"], cwd=repo, check=True, env=env)
    author = subprocess.run(
        ["git", "log", "-1", "--format=%an <%ae>"], cwd=repo, check=True, env=env, capture_output=True, text=True
    ).stdout.strip()
    assert author == "Holon Planner Agent <planner-agent@holon-agentic-coder.com>"
    assert config.read_text(encoding="utf-8") == before
