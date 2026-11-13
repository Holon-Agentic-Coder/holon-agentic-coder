import os
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.attribution_check import STRICT_ENV, check_attribution, find_attribution_trailers

TRAILER = "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True)
    return res.stdout


def _runner(repo: Path):
    return lambda args: _git(repo, *args)


def _commit(repo: Path, name: str, message: str) -> None:
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    return tmp_path


def test_trailer_in_own_commit_is_reported(repo: Path) -> None:
    _commit(repo, "a", "base")
    base = _git(repo, "rev-parse", "HEAD").strip()
    _commit(repo, "b", f"feat: x\n\n{TRAILER}")
    head = _git(repo, "rev-parse", "HEAD").strip()
    findings = find_attribution_trailers(base, _runner(repo))
    assert len(findings) == 1
    assert head.startswith(findings[0].sha)
    assert findings[0].trailer == TRAILER
    ok, report = check_attribution(base, _runner(repo))
    assert ok
    assert findings[0].sha in report
    assert _git(repo, "rev-parse", "HEAD").strip() == head


def test_clean_branch_is_silent(repo: Path) -> None:
    _commit(repo, "a", "base")
    base = _git(repo, "rev-parse", "HEAD").strip()
    _commit(repo, "b", "feat: clean")
    assert check_attribution(base, _runner(repo)) == (True, "")


def test_trailer_only_in_base_history_is_silent(repo: Path) -> None:
    _commit(repo, "a", f"old\n\n{TRAILER}")
    base = _git(repo, "rev-parse", "HEAD").strip()
    _commit(repo, "b", "feat: clean")
    assert check_attribution(base, _runner(repo)) == (True, "")


def test_strict_mode_fails(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _commit(repo, "a", "base")
    base = _git(repo, "rev-parse", "HEAD").strip()
    _commit(repo, "b", f"feat: x\n\n{TRAILER}")
    monkeypatch.setenv(STRICT_ENV, "1")
    ok, report = check_attribution(base, _runner(repo))
    assert not ok
    assert report


def test_missing_base_or_git_error_is_silent(repo: Path) -> None:
    _commit(repo, "a", "base")
    assert check_attribution(None, _runner(repo)) == (True, "")
    assert check_attribution("no-such-ref", _runner(repo)) == (True, "")
