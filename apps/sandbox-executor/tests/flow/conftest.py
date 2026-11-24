"""Fixtures for the flow package tests: sandboxed fake reviewers, no real npx and the template flow repositories."""

import shutil
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.knowledge import consult
from sandbox_executor.knowledge.consult import DEFAULT_LEDGERS

from tests.flow.helpers import _git, _set_user_write
from tests.gh_probe_stub import stubbed_mergeability_read
from tests.review_fakes import install_fake_reviewers


@pytest.fixture(autouse=True)
def _sandboxed_clean_reviewers(monkeypatch):
    """Run every review-loop test inside a simulated sandbox with clean fake reviewers (no runner is ever called).

    Mergeability and CI then decide the outcome as they did before the real ensemble was wired in; the tests of the
    ensemble wiring and of the host refusal override this explicitly.
    """
    install_fake_reviewers(monkeypatch)


@pytest.fixture(autouse=True)
def _no_real_prettier(monkeypatch):
    """Fake the Stage 5 report formatter and fail any test that would still start the real npx (no Node, no network).

    The calibrate stage formats its report through ``calibration._prettier_failure``; faking that seam to report a
    passing check keeps the flow tests independent of npx being installed. Tests of the formatter itself live in
    test_calibration.py.

    The same wrapper answers the mergeability probe's ``gh pr view`` read in-process with the failure the host's real
    ``gh`` gives in a temporary repository (``tests/gh_probe_stub.py``), so the local git fallback decides as before
    and no ``gh`` process starts. A test's own fake ``gh`` on ``PATH`` or its own ``subprocess.run`` patch still wins.
    """
    monkeypatch.setattr("sandbox_executor.calibration._prettier_failure", lambda report_rel, repo_dir: None)
    real_run = subprocess.run

    def guarded_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "npx":
            raise AssertionError(f"real npx invoked: {cmd}")
        stubbed = stubbed_mergeability_read(cmd)
        if stubbed is not None:
            return stubbed
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)


@pytest.fixture(scope="session")
def _flow_repos_template(tmp_path_factory, hermetic_git_config) -> Path:
    """Build the seeded origin and host clone once per session (per xdist worker); ``flow_repos`` copies it.

    The tree is made read-only, so a test that pushed to or committed in the template instead of its own
    copy would fail loudly rather than leak state into the tests after it.
    """
    root = tmp_path_factory.mktemp("flow-template")
    origin = root / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    seed = root / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True, capture_output=True)
    for name in ("intents.jsonl", "plans.jsonl", "executions.jsonl"):
        (seed / "holon-knowledge" / "ledger").mkdir(parents=True, exist_ok=True)
        (seed / "holon-knowledge" / "ledger" / name).write_text("", encoding="utf-8")
    # Like the real repository: the flow's own state lives in the worktree, but never in a role's commit.
    (seed / ".gitignore").write_text(".holon/\nholon-knowledge/.index/\n", encoding="utf-8")
    (seed / "src").mkdir()
    (seed / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "seed")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(root / "host")], check=True, capture_output=True)
    # Git's sample hooks are never run (the hermetic config sets core.hooksPath); do not copy them per test.
    for sample in root.glob("**/hooks/*.sample"):
        sample.unlink()
    _set_user_write(root, enabled=False)
    return root


@pytest.fixture
def flow_repos(tmp_path, monkeypatch, _flow_repos_template):
    """Bare origin with a seeded main branch, plus a host clone the flow runs in (private copies per test)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    origin = tmp_path / "origin.git"
    host = tmp_path / "host"
    for name, target in (("origin.git", origin), ("host", host)):
        shutil.copytree(_flow_repos_template / name, target, symlinks=True)
        _set_user_write(target, enabled=True)
    # Re-point the host's origin at the test's own copy without a git process: the clone recorded the template's path.
    config = host / ".git" / "config"
    config.write_text(
        config.read_text(encoding="utf-8").replace(str(_flow_repos_template / "origin.git"), str(origin)),
        encoding="utf-8",
    )
    repointed = config.read_text(encoding="utf-8")
    assert str(origin) in repointed, "host does not point at its own origin"
    assert str(_flow_repos_template) not in repointed, "host still points at the shared template"
    # Production runs the flow from the host checkout, so the consult's relative default ledgers are the host's own.
    # The test host is this temporary copy, not the checkout pytest was started in.
    ledger_dir = host / "holon-knowledge" / "ledger"
    monkeypatch.setattr(consult, "DEFAULT_LEDGERS", [str(ledger_dir / Path(path).name) for path in DEFAULT_LEDGERS])
    return host, origin
