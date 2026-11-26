"""The review loop applies its git identity only to its own commit, never to git config or the process environment."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest
from sandbox_executor.entrypoint import review_loop
from sandbox_executor.entrypoint.review_loop import CommitOptions
from sandbox_executor.flow.review.git_identity import REVIEW_GIT_ENV, _review_git_env
from sandbox_executor.review_report import FindingDisposition

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


def test_review_git_env_overlays_the_identity_on_a_copy_of_the_environment():
    environ = {"PATH": "/usr/bin", "HOME": "/home/someone"}
    before = dict(environ)

    env = _review_git_env(environ)

    assert env == {**before, **REVIEW_GIT_ENV}
    assert env is not environ
    assert environ == before


def test_review_git_env_overrides_an_existing_identity_only_in_the_copy():
    environ = {"GIT_AUTHOR_NAME": "Someone Else", "PATH": "/usr/bin"}

    env = _review_git_env(environ)

    assert env["GIT_AUTHOR_NAME"] == REVIEW_GIT_ENV["GIT_AUTHOR_NAME"]
    assert environ == {"GIT_AUTHOR_NAME": "Someone Else", "PATH": "/usr/bin"}


def _identity_keys(environ: Mapping[str, str]) -> dict[str, str]:
    """The ``GIT_AUTHOR_*`` and ``GIT_COMMITTER_*`` entries of ``environ``."""
    return {k: v for k, v in environ.items() if k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}


class _EnvRecordingGit(FakeGitCommands):
    """The recording fake run_cmd that also notes any identity in the process or call environment at each call."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.identities: list[dict[str, str]] = []

    def __call__(self, args, cwd=None, env=None, check=True):
        seen = _identity_keys(os.environ)
        if env is not None:
            seen.update(_identity_keys(env))
        self.identities.append(seen)
        return super().__call__(args, cwd=cwd, env=env, check=check)


def _clear_identity(monkeypatch) -> None:
    for key in list(os.environ):
        if key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_")):
            monkeypatch.delenv(key)


def test_main_leaves_no_identity_in_the_environment_of_any_command(tmp_path, monkeypatch):
    _clear_identity(monkeypatch)
    fake_git = _EnvRecordingGit(branch="feature/y")

    code, mock_loop, _post, _gh = _run_entrypoint(tmp_path, "feature/y", fake_git)

    assert code == 0
    mock_loop.assert_called_once()
    assert fake_git.identities
    assert all(seen == {} for seen in fake_git.identities), fake_git.identities
    assert _identity_keys(os.environ) == {}


class _ChildProcessGit(FakeGitCommands):
    """The recording fake run_cmd that, at its first call during main(), launches a real child Python process."""

    _PRINT_IDENTITY = (
        "import os; print(sorted(k for k in os.environ if k.startswith(('GIT_AUTHOR_', 'GIT_COMMITTER_'))))"
    )

    def __init__(self, real_run_cmd, **kwargs):
        super().__init__(**kwargs)
        self._real_run_cmd = real_run_cmd
        self.child_stdout: str | None = None

    def __call__(self, args, cwd=None, env=None, check=True):
        if self.child_stdout is None:
            child = self._real_run_cmd([sys.executable, "-c", self._PRINT_IDENTITY], check=True)
            self.child_stdout = child.stdout.strip()
        return super().__call__(args, cwd=cwd, env=env, check=check)


def test_child_process_launched_during_main_sees_no_identity(tmp_path, monkeypatch):
    _clear_identity(monkeypatch)
    fake_git = _ChildProcessGit(review_loop.run_cmd, branch="feature/y")

    code, _loop, _post, _gh = _run_entrypoint(tmp_path, "feature/y", fake_git)

    assert code == 0
    assert fake_git.child_stdout is not None, "no child process ran during main()"
    assert fake_git.child_stdout == "[]"


@pytest.fixture
def identityless_repo(tmp_path, monkeypatch) -> tuple[Path, Path]:
    """A repository with one base commit and a tracked change, under an empty global config with no identity.

    Returns (repo, global config file). The base commit's identity is passed per call, never configured.
    """
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
    _clear_identity(monkeypatch)
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "file.txt").write_text("content\n", encoding="utf-8")
    review_loop.run_cmd(["git", "add", "file.txt"], cwd=str(repo))
    base_env = {**os.environ, **dict.fromkeys(("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"), "Base")}
    base_env.update(dict.fromkeys(("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"), "base@example.invalid"))
    review_loop.run_cmd(["git", "commit", "-q", "-m", "base"], cwd=str(repo), env=base_env)
    (repo / "file.txt").write_text("content\nresolved\n", encoding="utf-8")
    return repo, global_config


def _applied_disposition() -> FindingDisposition:
    return FindingDisposition(id="F-1", severity="minor", path="file.txt", line=1, summary="fix", disposition="applied")


def _resolve(repo: Path, monkeypatch) -> tuple:
    """Commit the tracked change through the entrypoint's resolver commit, with verification stubbed to pass."""
    monkeypatch.setattr(review_loop, "_verify_before_push", lambda _repo, _iteration: (True, True, None))
    monkeypatch.setattr(review_loop, "_restore_budget_config", lambda _repo, _intent: None)
    return review_loop._commit_resolution(str(repo), "main", 1, (_applied_disposition(),), CommitOptions(dry_run=False))


def test_resolver_commit_is_authored_as_the_review_loop_agent(identityless_repo, monkeypatch):
    repo, _global_config = identityless_repo
    # Control: with config-only identity and no identity configured, git refuses to commit.
    control_env = {**os.environ, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.useConfigOnly"}
    control_env["GIT_CONFIG_VALUE_0"] = "true"
    review_loop.run_cmd(["git", "add", "file.txt"], cwd=str(repo))
    control = review_loop.run_cmd(["git", "commit", "-q", "-m", "x"], cwd=str(repo), env=control_env, check=False)
    assert control.returncode != 0

    updated, new_head, lint_ok, tests_ok, error = _resolve(repo, monkeypatch)

    assert (lint_ok, tests_ok, error) == (True, True, None)
    assert updated[0].commit_sha == new_head
    log = review_loop.run_cmd(["git", "log", "-1", "--format=%an%n%ae%n%cn%n%ce"], cwd=str(repo))
    assert log.stdout.splitlines() == [
        REVIEW_GIT_ENV["GIT_AUTHOR_NAME"],
        REVIEW_GIT_ENV["GIT_AUTHOR_EMAIL"],
        REVIEW_GIT_ENV["GIT_COMMITTER_NAME"],
        REVIEW_GIT_ENV["GIT_COMMITTER_EMAIL"],
    ]
    assert _identity_keys(os.environ) == {}


def test_resolver_commit_leaves_no_user_section_in_local_or_global_config(identityless_repo, monkeypatch):
    repo, global_config = identityless_repo

    assert _resolve(repo, monkeypatch)[4] is None

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


class _StatusDirtyGit(FakeGitCommands):
    """The recording fake run_cmd with a dirty worktree, noting the ``env`` argument of every call."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.envs: list[tuple[list[str], dict[str, str] | None]] = []

    def __call__(self, args, cwd=None, env=None, check=True):
        self.envs.append((list(args), env))
        if args[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(args, 0, stdout=" M file.txt\n", stderr="")
        return super().__call__(args, cwd=cwd, env=env, check=check)


def test_only_the_resolver_git_commit_carries_the_identity(tmp_path, monkeypatch):
    _clear_identity(monkeypatch)
    fake_git = _StatusDirtyGit()
    monkeypatch.setattr(review_loop, "run_cmd", fake_git)

    assert _resolve(tmp_path, monkeypatch)[4] is None

    commits = [env for args, env in fake_git.envs if args[:2] == ["git", "commit"]]
    assert len(commits) == 1
    assert _identity_keys(commits[0]) == REVIEW_GIT_ENV
    others = [(args, env) for args, env in fake_git.envs if args[:2] != ["git", "commit"]]
    assert {args[1] for args, _env in others} >= {"add", "status", "rev-parse"}
    assert all(env is None for _args, env in others), others
