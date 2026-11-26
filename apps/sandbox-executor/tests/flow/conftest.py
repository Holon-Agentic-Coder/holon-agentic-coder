"""Fixtures for the flow package tests: sandboxed fake reviewers, no real npx, a knowledge index that does not wait
for the disk and the template flow repositories."""

import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from sandbox_executor.knowledge import consult, ledger_sync
from sandbox_executor.knowledge import index as knowledge_index
from sandbox_executor.knowledge.consult import DEFAULT_LEDGERS

from tests.flow.helpers import _git
from tests.flow.stage_templates import DEFAULT_SPEC, AgentsSpec, StageStart, StageTemplates
from tests.gh_probe_stub import stubbed_mergeability_read
from tests.repo_templates import copy_template, shared_template
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

    The calibrate stage formats its report through ``calibration_format._prettier_failure``; faking that seam to report
    a passing check keeps the flow tests independent of npx being installed. Tests of the formatter itself live in
    test_calibration_commit.py.

    The same wrapper answers the mergeability probe's ``gh pr view`` read in-process with the failure the host's real
    ``gh`` gives in a temporary repository (``tests/gh_probe_stub.py``), so the local git fallback decides as before
    and no ``gh`` process starts. A test's own fake ``gh`` on ``PATH`` or its own ``subprocess.run`` patch still wins.
    """
    monkeypatch.setattr("sandbox_executor.calibration_format._prettier_failure", lambda report_rel, repo_dir: None)
    real_run = subprocess.run

    def guarded_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "npx":
            raise AssertionError(f"real npx invoked: {cmd}")
        stubbed = stubbed_mergeability_read(cmd)
        if stubbed is not None:
            return stubbed
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)


@pytest.fixture(autouse=True)
def _knowledge_index_without_disk_wait(monkeypatch) -> Callable[[str], sqlite3.Connection]:
    """Open the knowledge index of every flow test without waiting for the disk; returns production's own opener.

    Production opens the index with ``synchronous=NORMAL`` (``ledger_sync.connect_wal``), which protects the file
    from a power loss. A test's index is in its temporary host copy and is deleted with it, and each open, commit
    and close of it waited for the disk: several hundred times in one test file, for nothing a test reads. The real
    ``connect_wal`` still runs first, so every other setting is production's own; test_flow_repos_hermetic.py
    proves that.
    """
    production_connect = ledger_sync.connect_wal

    def connect(path: str) -> sqlite3.Connection:
        conn = production_connect(path)
        conn.execute("PRAGMA synchronous=OFF;")
        return conn

    # index.py imports the function by name, so it is replaced in both modules that look it up.
    monkeypatch.setattr(ledger_sync, "connect_wal", connect)
    monkeypatch.setattr(knowledge_index, "connect_wal", connect)
    return production_connect


def _build_flow_repos(root: Path) -> None:
    """The seeded origin and a host clone of it, in ``root``."""
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


@pytest.fixture(scope="session")
def _flow_repos_template(tmp_path_factory, hermetic_git_config) -> Path:
    """The seeded origin and host clone, built once per test run (also under xdist); ``flow_repos`` copies it.

    The tree is made read-only, so a test that pushed to or committed in the template instead of its own
    copy would fail loudly rather than leak state into the tests after it. Git's sample hooks are never run (the
    hermetic config sets core.hooksPath), so the finished template has none to copy per test.
    """
    return shared_template(tmp_path_factory, "flow-template", _build_flow_repos)[0]


@pytest.fixture
def flow_repos(tmp_path, monkeypatch, _flow_repos_template):
    """Bare origin with a seeded main branch, plus a host clone the flow runs in (private copies per test)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    origin = tmp_path / "origin.git"
    host = tmp_path / "host"
    # The copy re-points the host's origin at the test's own origin: the clone recorded the template's path.
    copy_template(_flow_repos_template, tmp_path, names=("origin.git", "host"))
    # Production runs the flow from the host checkout, so the consult's relative default ledgers are the host's own.
    # The test host is this temporary copy, not the checkout pytest was started in.
    ledger_dir = host / "holon-knowledge" / "ledger"
    monkeypatch.setattr(consult, "DEFAULT_LEDGERS", [str(ledger_dir / Path(path).name) for path in DEFAULT_LEDGERS])
    return host, origin


@pytest.fixture
def stage_start(tmp_path_factory, tmp_path, monkeypatch, _flow_repos_template) -> Callable[..., StageStart]:
    """Start a test at a later stage from a template of the flow after its real earlier stages ran.

    ``stage_start(through, spec)`` copies the template whose Stages 1 to ``through`` ran with the fake containers of
    ``spec`` (``tests.flow.stage_templates``) into ``tmp_path``, or into ``target`` when given. Function-scoped: the
    build runs production code under this test's autouse guards.
    """
    templates = StageTemplates(tmp_path_factory, _flow_repos_template)

    def start(through: str, spec: AgentsSpec = DEFAULT_SPEC, target: Path | None = None) -> StageStart:
        return templates.start(target or tmp_path, monkeypatch, through, spec)

    return start
