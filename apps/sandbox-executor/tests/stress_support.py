"""Child-run orchestration and post-run checks shared by the two stress tests.

The workspace-survival test (``test_workspace_survival_stress.py``) and the hermetic canary guard
(``test_sandbox_hermetic_guard.py``) each re-run the whole unit selection in a child process. The
``Workspace survival`` job runs them concurrently (``-n auto --dist worksteal`` on the outer
command), so each test gets its own :class:`ChildContext`: a private root holding the simulated
``HOME`` (and with it the workspace) and, as a sibling of that ``HOME``, the child's ``TMPDIR`` and
pytest ``--basetemp``. Nothing is shared between the two children: not the workspace, not ``HOME``,
and not the temporary root, so pytest's numbered-directory cleanup in one child can never reach the
other's directories. ``test_stress_support.py`` proves it.

The post-run checks are functions returning human-readable violations rather than inline
assertions, so the negative controls in ``test_stress_support.py`` can show that each guard still
fails when its hazard occurs.

An outer xdist worker's identity (``PYTEST_XDIST_WORKER`` and friends, ``PYTEST_CURRENT_TEST``) is
removed from the child environment, so the child controller never believes it is itself a worker.
``PYTEST_XDIST_AUTO_NUM_WORKERS`` is deliberately kept: it is a setting, not an identity, and lets a
caller choose the child's ``-n auto`` worker count.

Worker count under concurrency: xdist's ``-n auto`` counts *physical* cores through ``psutil``, so
on the 4-vCPU ``ubuntu-latest`` runner each child started only 2 workers. The child suite is
subprocess- and IO-bound, and measured on 4 pinned CPUs with both children running concurrently,
4 workers per child beat 2 by 21 % (3 beat 2 by 14 %; see execution ``E-1795573757-CLA-CO5.5``).
So when the stress test itself runs in an outer xdist worker and the caller has not chosen a
count, :func:`child_env` sets ``PYTEST_XDIST_AUTO_NUM_WORKERS`` to the CPUs this process may use
(``os.process_cpu_count()``, which honours CPU affinity).
"""

import contextlib
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass

from tests.hermetic_fixtures import (
    describe_index_changes,
    knowledge_index_snapshot,
    pytest_suite_command,
    simulated_container_env,
)

#: Environment keys a child must never inherit from an outer pytest-xdist worker.
_XDIST_IDENTITY_PREFIX = "PYTEST_XDIST_"
_XDIST_AUTO_NUM_WORKERS = "PYTEST_XDIST_AUTO_NUM_WORKERS"
_XDIST_KEPT_SETTINGS = frozenset({_XDIST_AUTO_NUM_WORKERS})
_XDIST_WORKER = "PYTEST_XDIST_WORKER"
_PYTEST_CURRENT_TEST = "PYTEST_CURRENT_TEST"

#: Content of the canary marker the hermetic guard seeds into its simulated workspace.
CANARY_MARKER_CONTENT = "canary-alive-guard-marker-2026"


@dataclass(frozen=True)
class ChildContext:
    """Private directory layout of one child suite run; every path lies under ``root``."""

    label: str
    root: str
    home: str
    workspace: str
    tmpdir: str
    basetemp: str

    @classmethod
    def under(cls, label: str, root: str) -> "ChildContext":
        """Lay out a context under ``root``; ``tmpdir`` is a sibling of ``home``, never inside it."""
        home = os.path.join(root, "home")
        tmpdir = os.path.join(root, "tmp")
        return cls(
            label=label,
            root=root,
            home=home,
            workspace=os.path.join(home, ".holon-sandbox", "workspace"),
            tmpdir=tmpdir,
            basetemp=os.path.join(tmpdir, "pytest-basetemp"),
        )


@contextlib.contextmanager
def child_context(label: str) -> Iterator[ChildContext]:
    """Create a private root with ``home/`` and ``tmp/`` for one child run, removed on exit."""
    root = tempfile.mkdtemp(prefix=f"holon-stress-{label}-")
    try:
        context = ChildContext.under(label, root)
        os.makedirs(os.path.dirname(context.workspace))
        os.makedirs(context.tmpdir)
        yield context
    finally:
        shutil.rmtree(root, ignore_errors=True)


def child_env(context: ChildContext, sentinel_var: str) -> dict[str, str]:
    """Build the simulated container environment of ``context``, with its own temporary root."""
    env = simulated_container_env(context.home, sentinel_var)
    for name in ("TMPDIR", "TMP", "TEMP"):
        env[name] = context.tmpdir
    if _XDIST_WORKER in env and _XDIST_AUTO_NUM_WORKERS not in env:
        env[_XDIST_AUTO_NUM_WORKERS] = str(os.process_cpu_count() or 1)
    for name in list(env):
        if name.startswith(_XDIST_IDENTITY_PREFIX) and name not in _XDIST_KEPT_SETTINGS:
            del env[name]
    env.pop(_PYTEST_CURRENT_TEST, None)
    return env


def child_suite_command(context: ChildContext, *extra_args: str) -> list[str]:
    """Return the sanctioned child suite command with pytest's temporary tree private to ``context``."""
    return pytest_suite_command("--basetemp", context.basetemp, *extra_args)


@dataclass(frozen=True)
class ChildRun:
    """Outcome of one child process: the completed process, its wall time and pytest summary."""

    completed: subprocess.CompletedProcess
    wall_seconds: float
    summary: str


def _pytest_summary(stdout: str) -> str:
    lines = [line.strip("= ") for line in stdout.splitlines() if line.strip()]
    return lines[-1] if lines else "<no output>"


def run_child(cmd: list[str], *, label: str, cwd: str, env: dict[str, str], timeout: int) -> ChildRun:
    """Run a child process and print one ``stress-child`` timing line for the job log."""
    start = time.monotonic()
    completed = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    run = ChildRun(completed, time.monotonic() - start, _pytest_summary(completed.stdout))
    print(f"stress-child {label}: {run.summary} in {run.wall_seconds:.1f}s")
    return run


@dataclass(frozen=True)
class CanarySeed:
    """The files the hermetic canary seeds into its simulated workspace."""

    marker: str
    content: str
    dummy_file: str
    git_dir: str


def seed_canary_workspace(context: ChildContext) -> CanarySeed:
    """Seed the canary marker, a dummy repository file and an empty ``.git`` into the workspace."""
    os.makedirs(context.workspace, exist_ok=True)
    seed = CanarySeed(
        marker=os.path.join(context.workspace, "canary.txt"),
        content=CANARY_MARKER_CONTENT,
        dummy_file=os.path.join(context.workspace, "important_code.py"),
        git_dir=os.path.join(context.workspace, ".git"),
    )
    with open(seed.marker, "w") as f:
        f.write(seed.content)
    with open(seed.dummy_file, "w") as f:
        f.write("# important code\n")
    os.makedirs(seed.git_dir, exist_ok=True)
    return seed


def _read(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read()


def canary_violations(
    context: ChildContext, seed: CanarySeed, index_root: str, index_before: dict[str, tuple[int, int]]
) -> list[str]:
    """Return every way the child run broke the hermetic canary; empty means the guard holds."""
    violations: list[str] = []
    index_changes = describe_index_changes(index_before, knowledge_index_snapshot(index_root))
    if index_changes:
        violations.append(
            "A test in the child suite wrote the checkout's holon-knowledge/.index/: " + ", ".join(index_changes)
        )
    if not os.path.exists(context.workspace):
        violations.append("Simulated sandbox workspace was deleted during entrypoint test runs!")
    marker = _read(seed.marker)
    if marker is None:
        violations.append("Canary marker file was deleted during entrypoint test runs!")
    elif marker != seed.content:
        violations.append("Canary marker content was modified during entrypoint test runs!")
    if not os.path.exists(seed.dummy_file):
        violations.append("Dummy workspace file was deleted during entrypoint test runs!")
    if not os.path.exists(seed.git_dir):
        violations.append("Dummy .git directory was deleted during entrypoint test runs!")
    return violations


def git_head(workspace: str) -> subprocess.CompletedProcess:
    """Run ``git rev-parse HEAD`` in ``workspace``."""
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workspace, capture_output=True, text=True, timeout=60, check=False
    )


def _history_violations(workspace: str, head_sha: str) -> list[str]:
    if not os.path.isdir(os.path.join(workspace, ".git")):
        return [
            "The workspace .git disappeared, which is what drives executor.py into its "
            "destructive git re-initialisation fallback."
        ]
    head_after = git_head(workspace)
    if head_after.returncode != 0:
        return [f"git can no longer resolve the workspace repository: {head_after.stderr}"]
    if head_after.stdout.strip() != head_sha:
        return ["The workspace commit history changed during the suite run."]
    return []


def survival_violations(workspace: str, canary_path: str, canary_content: str, head_sha: str) -> list[str]:
    """Return every way the child run damaged the executor workspace; empty means it survived."""
    if not os.path.isdir(workspace):
        return [
            "The executor workspace was deleted by a suite run -- the workspace-deletion incident is "
            "back, and a real execution would now wipe its own commit history."
        ]
    violations: list[str] = []
    canary = _read(canary_path)
    if canary is None:
        violations.append("A file inside the workspace was deleted.")
    elif canary != canary_content:
        violations.append("A file inside the workspace changed.")
    return violations + _history_violations(workspace, head_sha)
