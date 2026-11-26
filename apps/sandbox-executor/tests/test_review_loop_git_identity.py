"""The review loop carries its git identity in the process environment and never writes it to git config."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.entrypoint import review_loop
from sandbox_executor.flow.review.git_identity import REVIEW_GIT_ENV, review_git_identity

from tests.test_review_loop import FakeGitCommands, _run_entrypoint

#: Environment variables that would hand git an identity or config other than the one under test.
_IDENTITY_LEAKS = ("EMAIL", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", *REVIEW_GIT_ENV)

_CONFIG_SCOPES = ("--global", "--local", "--system", "--worktree", "--file")


def _writes_user_config(argv: list[str]) -> bool:
    """True for a 'git config' argv, in any scope, that names a 'user.' key."""
    if len(argv) < 2 or argv[0] != "git" or argv[1] != "config":
        return False
    return any(a.startswith("user.") for a in argv[2:] if a not in _CONFIG_SCOPES)


def test_main_runs_no_git_config_user_write_in_any_scope(tmp_path):
    fake_git = FakeGitCommands(branch="feature/y")

    code, _loop, _post, _gh = _run_entrypoint(tmp_path, "feature/y", fake_git)

    assert code == 0
    assert fake_git.calls, "the recording run_cmd saw no command"
    assert [c for c in fake_git.calls if _writes_user_config(c)] == []


def test_review_loop_source_names_no_user_config_key():
    source = Path(review_loop.__file__).read_text(encoding="utf-8")
    assert '"user.name"' not in source
    assert '"user.email"' not in source
    assert not re.search(r'"config",\s*"--global",\s*"user\.', source)


def test_identity_is_in_the_environment_inside_the_block_and_removed_after(monkeypatch):
    for key in REVIEW_GIT_ENV:
        monkeypatch.delenv(key, raising=False)

    with review_git_identity(os.environ):
        assert {key: os.environ.get(key) for key in REVIEW_GIT_ENV} == REVIEW_GIT_ENV

    assert not any(key in os.environ for key in REVIEW_GIT_ENV)


def test_identity_restores_previous_values_even_when_the_block_exits(monkeypatch):
    for key in REVIEW_GIT_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Someone Else")

    with pytest.raises(SystemExit), review_git_identity(os.environ):
        assert os.environ["GIT_AUTHOR_NAME"] == REVIEW_GIT_ENV["GIT_AUTHOR_NAME"]
        raise SystemExit(1)

    assert os.environ["GIT_AUTHOR_NAME"] == "Someone Else"
    assert not any(key in os.environ for key in REVIEW_GIT_ENV if key != "GIT_AUTHOR_NAME")


class _EnvRecordingGit(FakeGitCommands):
    """The recording fake run_cmd that also notes the identity in the process environment at each call."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.identities: list[dict[str, str | None]] = []

    def __call__(self, args, cwd=None, env=None, check=True):
        self.identities.append({key: os.environ.get(key) for key in REVIEW_GIT_ENV})
        return super().__call__(args, cwd=cwd, env=env, check=check)


def test_main_holds_the_identity_for_every_git_command_and_leaks_none_after_exit(tmp_path, monkeypatch):
    for key in REVIEW_GIT_ENV:
        monkeypatch.delenv(key, raising=False)
    fake_git = _EnvRecordingGit(branch="feature/y")

    code, mock_loop, _post, _gh = _run_entrypoint(tmp_path, "feature/y", fake_git)

    assert code == 0
    mock_loop.assert_called_once()
    assert fake_git.identities
    assert all(seen == REVIEW_GIT_ENV for seen in fake_git.identities)
    assert not any(key in os.environ for key in REVIEW_GIT_ENV)


@pytest.fixture
def identityless_repo(tmp_path, monkeypatch) -> tuple[Path, Path]:
    """A fresh repository under an empty global config, with no identity anywhere; returns (repo, global file)."""
    global_config = tmp_path / "empty-gitconfig"
    global_config.write_text("", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    for key in _IDENTITY_LEAKS:
        monkeypatch.delenv(key, raising=False)
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "file.txt").write_text("content\n", encoding="utf-8")
    review_loop.run_cmd(["git", "add", "file.txt"], cwd=str(repo))
    return repo, global_config


def _commit(repo: Path, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Commit through the entrypoint's own git helper, as the resolver commit does."""
    return review_loop.run_cmd(["git", "commit", "-q", "-m", "resolve findings"], cwd=str(repo), env=env, check=check)


def test_commit_through_entrypoint_helpers_is_authored_as_the_review_loop_agent(identityless_repo):
    repo, _global_config = identityless_repo
    # Control: with config-only identity and no identity configured, git refuses to commit.
    control_env = {**os.environ, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.useConfigOnly"}
    control_env["GIT_CONFIG_VALUE_0"] = "true"
    assert _commit(repo, env=control_env, check=False).returncode != 0

    with review_git_identity(os.environ):
        _commit(repo)

    log = review_loop.run_cmd(["git", "log", "-1", "--format=%an%n%ae%n%cn%n%ce"], cwd=str(repo))
    assert log.stdout.splitlines() == [
        REVIEW_GIT_ENV["GIT_AUTHOR_NAME"],
        REVIEW_GIT_ENV["GIT_AUTHOR_EMAIL"],
        REVIEW_GIT_ENV["GIT_COMMITTER_NAME"],
        REVIEW_GIT_ENV["GIT_COMMITTER_EMAIL"],
    ]


def test_commit_leaves_no_user_section_in_local_or_global_config(identityless_repo):
    repo, global_config = identityless_repo

    with review_git_identity(os.environ):
        _commit(repo)

    local = subprocess.run(
        ["git", "config", "--local", "--get-regexp", r"^user\."],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert local.returncode == 1
    assert local.stdout == ""
    assert "[user]" not in (repo / ".git" / "config").read_text(encoding="utf-8")
    assert global_config.read_text(encoding="utf-8") == ""
