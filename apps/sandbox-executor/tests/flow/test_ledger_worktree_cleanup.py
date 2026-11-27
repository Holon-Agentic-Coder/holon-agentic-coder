from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.flow import FlowContext, git_refs, ledger_worktree
from sandbox_executor.git import run_git as real_run_git
from sandbox_executor.ledger import LedgerRewriteError, validate_ledger_file_unique_ids

from tests.flow.helpers import _git, _ledger_rows
from tests.flow_repo_templates import derived_template, replace_with_copy

BRANCH = "I-1-x/P-2-y/E-3-z/_"
LEDGER_REL = "holon-knowledge/ledger/executions.jsonl"
PENDING_REL = "holon-knowledge/ledger/pending/executions"
FRAGMENT_REL = f"{PENDING_REL}/I-1-x.jsonl"
EARLIER_ROW: dict[str, Any] = {"entry_type": "summary", "execution_id": "E-3-z", "plan_branch": "I-1-x/P-2-y/_"}
NEW_ROW: dict[str, Any] = {**EARLIER_ROW, "status": "success", "ledger_revision": 2}
MESSAGE = "execute: record success row for E-3-z"
SOURCE_ROOT = Path(git_refs.__file__).resolve().parents[2]

KILLED_STEP = """
import os, signal, sys
from sandbox_executor.flow.ledger_worktree import _ledger_worktree
from sandbox_executor.git import run_git

with _ledger_worktree(sys.argv[1], sys.argv[2], run_git) as worktree:
    print(worktree, flush=True)
    os.kill(os.getpid(), signal.SIGKILL)
"""


@dataclass(frozen=True)
class LedgerRepos:
    host: Path
    origin: Path
    context: FlowContext
    temp_dir: Path

    def append(self, row: dict[str, Any] = NEW_ROW) -> str | None:
        return git_refs._append_ledger_row(self.context, BRANCH, "executions.jsonl", row, MESSAGE)

    def origin_tip(self) -> str:
        return _git(self.origin, "rev-parse", f"refs/heads/{BRANCH}")


def _execution_branch_with_a_row(root: Path) -> None:
    host = root / "host"
    _git(host, "checkout", "-q", "-b", BRANCH)
    (host / LEDGER_REL).write_text(json.dumps(EARLIER_ROW) + "\n", encoding="utf-8")
    _git(host, "commit", "-qam", "execute: E-3-z completed")
    _git(host, "push", "-q", "origin", BRANCH)
    _git(host, "checkout", "-q", "main")
    _git(host, "fetch", "-q", "origin")


@pytest.fixture
def ledger_repos(flow_repos, tmp_path, monkeypatch, tmp_path_factory, _flow_repos_template) -> LedgerRepos:
    host, origin = flow_repos
    names = ("origin.git", "host")
    template = derived_template(
        tmp_path_factory, "ledger-worktree-cleanup", _flow_repos_template, names, _execution_branch_with_a_row
    )
    replace_with_copy(template, tmp_path, names)
    temp_dir = tmp_path / "ledger-tmp"
    temp_dir.mkdir()
    # why: under xdist, leftovers are only attributable to this test if its ledger worktrees land nowhere else.
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))
    return LedgerRepos(host, origin, FlowContext(repo_dir=str(host), token_reduce=False), temp_dir)


class RecordingGit:
    def __init__(self, failures: dict[tuple[str, ...], Exception] | None = None) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.failures = failures or {}

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(args), dict(kwargs)))
        for leading, error in self.failures.items():
            if tuple(args[: len(leading)]) == leading:
                raise error
        return real_run_git(args, **kwargs)


def _registered_worktrees(repo: Path) -> list[str]:
    listing = _git(repo, "worktree", "list", "--porcelain")
    return [line.removeprefix("worktree ") for line in listing.splitlines() if line.startswith("worktree ")]


def _ledger_worktrees(repo: Path) -> list[str]:
    return [path for path in _registered_worktrees(repo) if "holon-flow-ledger-" in path]


def _ledger_temp_entries(temp_dir: Path) -> list[Path]:
    return sorted(temp_dir.glob("holon-flow-ledger-*"))


def _lock_as_an_unfinished_add(repo: Path, worktree: str) -> None:
    _git(repo, "worktree", "lock", "--reason", "initializing", worktree)


def _german_git_environment(directory: Path) -> dict[str, str]:
    # why: hermetic only where glibc's C.utf8 locale exists; elsewhere de_DE.UTF-8 may name a missing locale.
    catalogue = directory / "messages" / "de" / "LC_MESSAGES" / "git.mo"
    catalogue.parent.mkdir(parents=True)
    # why: GNU .mo layout is a header, then one (length, offset) pair for the message and one for its translation.
    header = struct.pack("<11I", 0x950412DE, 0, 1, 28, 36, 0, 0, 12, 44, 15, 57)
    catalogue.write_bytes(header + b"initializing\0Initialisierung\0")
    locales = directory / "locales"
    locales.mkdir()
    if os.path.isdir("/usr/lib/locale/C.utf8"):
        shutil.copytree("/usr/lib/locale/C.utf8", locales / "de_DE.UTF-8")
    return {
        "LANGUAGE": "de",
        "LC_ALL": "de_DE.UTF-8",
        "LOCPATH": str(locales),
        "GIT_TEXTDOMAINDIR": str(catalogue.parents[2]),
    }


def _plant_leftover(
    repos: LedgerRepos, name: str, owner_lock: bool = False, keep_directory: bool = True, unfinished_add: bool = False
) -> Path:
    tmp_root = repos.temp_dir / f"holon-flow-ledger-{name}"
    tmp_root.mkdir()
    _git(repos.host, "worktree", "add", "--detach", str(tmp_root / "worktree"), f"refs/remotes/origin/{BRANCH}")
    if unfinished_add:
        _lock_as_an_unfinished_add(repos.host, str(tmp_root / "worktree"))
    if owner_lock:
        (tmp_root / "owner.lock").touch()
    if not keep_directory:
        shutil.rmtree(tmp_root)
    return tmp_root


def _registered_path(tmp_root: Path) -> str:
    # why: git resolves symlinks in the paths it registers.
    return os.path.join(os.path.realpath(tmp_root), "worktree")


def _unstamped(row: dict[str, Any]) -> dict[str, Any]:
    assert str(row.get("created_at", "")).endswith("Z")
    return {key: value for key, value in row.items() if key != "created_at"}


def _show(repo: Path, spec: str) -> str:
    # why: the shared _git helper strips output, and this needs it exact.
    return subprocess.run(["git", "show", spec], cwd=repo, check=True, capture_output=True, text=True).stdout


def test_a_successful_ledger_step_leaves_no_worktree_and_no_temp_directory(ledger_repos):
    assert ledger_repos.append() is None

    assert _registered_worktrees(ledger_repos.host) == [os.path.realpath(ledger_repos.host)]
    assert _ledger_temp_entries(ledger_repos.temp_dir) == []


def test_a_successful_ledger_step_makes_the_same_commit_and_push(ledger_repos):
    host, origin = ledger_repos.host, ledger_repos.origin
    previous_tip = ledger_repos.origin_tip()
    previous_content = _show(origin, f"{previous_tip}:{LEDGER_REL}")

    assert ledger_repos.append() is None

    tip = ledger_repos.origin_tip()
    identity = {**git_refs.EXECUTOR_GIT_ENV, **os.environ}
    assert _git(origin, "log", "-1", "--format=%P%n%s%n%an%n%ae%n%cn%n%ce", tip).splitlines() == [
        previous_tip,
        MESSAGE,
        identity["GIT_AUTHOR_NAME"],
        identity["GIT_AUTHOR_EMAIL"],
        identity["GIT_COMMITTER_NAME"],
        identity["GIT_COMMITTER_EMAIL"],
    ]
    assert _git(origin, "diff", "--name-only", previous_tip, tip).splitlines() == [FRAGMENT_REL]
    assert _show(origin, f"{tip}:{LEDGER_REL}") == previous_content
    [written] = [json.loads(line) for line in _show(origin, f"{tip}:{FRAGMENT_REL}").splitlines()]
    assert _unstamped(written) == NEW_ROW
    assert _ledger_rows(origin, tip, "executions.jsonl") == [EARLIER_ROW, written]
    assert _git(host, "rev-parse", f"refs/remotes/origin/{BRANCH}") == tip
    assert _git(host, "rev-parse", f"refs/heads/{BRANCH}") == tip


def test_a_successful_ledger_step_runs_the_same_git_commands_in_its_worktree(ledger_repos, monkeypatch):
    git = RecordingGit()
    monkeypatch.setattr("sandbox_executor.flow.git_refs.run_git", git)

    assert ledger_repos.append() is None

    # why: other git worktree administration (listing, removing, pruning) is not part of this contract.
    adds = [(args, options) for args, options in git.calls if args[:2] == ["worktree", "add"]]
    assert len(adds) == 1
    add_args, add_options = adds[0]
    worktree = add_args[3]
    assert add_args == ["worktree", "add", "--detach", worktree, f"refs/remotes/origin/{BRANCH}"]
    # why: the sweep recognises an unfinished add by the lock reason git writes untranslated in the C locale.
    assert add_options == {"cwd": str(ledger_repos.host), "env": {**os.environ, "LC_ALL": "C"}}
    tmp_root = os.path.dirname(worktree)
    assert os.path.basename(worktree) == "worktree"
    assert os.path.basename(tmp_root).startswith("holon-flow-ledger-")
    assert os.path.dirname(tmp_root) == str(ledger_repos.temp_dir)

    in_worktree = [(args, options) for args, options in git.calls if options.get("cwd") == worktree]
    commit_env = {**git_refs.EXECUTOR_GIT_ENV, **os.environ, "DOCKER_CLI_HINTS": "false"}
    assert in_worktree == [
        (["add", LEDGER_REL, PENDING_REL], {"cwd": worktree}),
        (["commit", "-m", MESSAGE], {"cwd": worktree, "env": commit_env}),
        (["rev-parse", "HEAD"], {"cwd": worktree}),
        (["push", "origin", f"HEAD:refs/heads/{BRANCH}"], {"cwd": worktree, "timeout": 300}),
    ]


def test_a_ledger_step_that_raises_midway_removes_its_worktree_before_the_error_propagates(ledger_repos, monkeypatch):
    previous_tip = ledger_repos.origin_tip()
    registered_while_running: list[str] = []

    def rejecting_validator(_ledger_path: str, **_keys: str) -> None:
        registered_while_running.extend(_ledger_worktrees(ledger_repos.host))
        raise LedgerRewriteError("an earlier row was rewritten")

    monkeypatch.setattr("sandbox_executor.flow.git_refs.validate_ledger_file_unique_ids", rejecting_validator)

    with pytest.raises(LedgerRewriteError, match="an earlier row was rewritten"):
        ledger_repos.append()

    assert len(registered_while_running) == 1
    assert _ledger_worktrees(ledger_repos.host) == []
    assert _ledger_temp_entries(ledger_repos.temp_dir) == []
    assert ledger_repos.origin_tip() == previous_tip


def test_a_ledger_step_that_returns_an_error_leaves_no_worktree_and_no_temp_directory(ledger_repos, monkeypatch):
    previous_tip = ledger_repos.origin_tip()
    git = RecordingGit({("push",): RuntimeError("the remote refused the push")})
    monkeypatch.setattr("sandbox_executor.flow.git_refs.run_git", git)

    assert ledger_repos.append() == "the remote refused the push"

    assert _ledger_worktrees(ledger_repos.host) == []
    assert _ledger_temp_entries(ledger_repos.temp_dir) == []
    assert ledger_repos.origin_tip() == previous_tip


def test_a_failed_worktree_removal_neither_fails_the_pushed_row_nor_leaves_a_registration(ledger_repos, monkeypatch):
    git = RecordingGit({("worktree", "remove"): TimeoutError("git worktree remove timed out")})
    monkeypatch.setattr("sandbox_executor.flow.git_refs.run_git", git)

    assert ledger_repos.append() is None

    assert _unstamped(_ledger_rows(ledger_repos.origin, ledger_repos.origin_tip(), "executions.jsonl")[-1]) == NEW_ROW
    assert _ledger_worktrees(ledger_repos.host) == []
    assert _ledger_temp_entries(ledger_repos.temp_dir) == []


def test_a_worktree_add_that_times_out_midway_leaves_no_registration_and_no_temp_directory(ledger_repos, monkeypatch):
    previous_tip = ledger_repos.origin_tip()

    def git_whose_worktree_add_times_out(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args[:2] != ["worktree", "add"]:
            return real_run_git(args, **kwargs)
        # why: a runner that kills the add mid-checkout leaves the worktree registered and still locked by git.
        real_run_git(args, **kwargs)
        _lock_as_an_unfinished_add(ledger_repos.host, args[3])
        raise TimeoutError("git worktree add timed out")

    monkeypatch.setattr("sandbox_executor.flow.git_refs.run_git", git_whose_worktree_add_times_out)

    assert ledger_repos.append() == "git worktree add timed out"

    assert _ledger_worktrees(ledger_repos.host) == []
    assert _ledger_temp_entries(ledger_repos.temp_dir) == []
    assert ledger_repos.origin_tip() == previous_tip


@pytest.mark.parametrize("owner_lock", [False, True], ids=["without-a-lock-file", "with-an-unheld-owner-lock"])
def test_the_next_step_removes_the_worktree_a_killed_step_left_with_its_directory(ledger_repos, owner_lock):
    leftover = _plant_leftover(ledger_repos, "killed", owner_lock=owner_lock)
    assert _ledger_worktrees(ledger_repos.host) == [_registered_path(leftover)]

    assert ledger_repos.append() is None

    assert _ledger_worktrees(ledger_repos.host) == []
    assert not leftover.exists()
    assert _ledger_temp_entries(ledger_repos.temp_dir) == []


def test_the_next_step_unregisters_the_worktree_a_killed_step_left_without_its_directory(ledger_repos):
    leftover = _plant_leftover(ledger_repos, "killed", keep_directory=False)
    assert _ledger_worktrees(ledger_repos.host) == [_registered_path(leftover)]

    assert ledger_repos.append() is None

    assert _ledger_worktrees(ledger_repos.host) == []


def test_the_next_step_removes_the_worktree_of_a_step_whose_process_was_killed(ledger_repos):
    environment = {**os.environ, "TMPDIR": str(ledger_repos.temp_dir), "PYTHONPATH": str(SOURCE_ROOT)}
    killed = subprocess.run(
        [sys.executable, "-c", KILLED_STEP, str(ledger_repos.host), f"refs/remotes/origin/{BRANCH}"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert killed.returncode == -signal.SIGKILL, killed.stderr
    leftover = Path(killed.stdout.strip()).parent
    assert leftover.parent == ledger_repos.temp_dir
    assert _ledger_worktrees(ledger_repos.host) == [_registered_path(leftover)]
    assert (leftover / "owner.lock").exists()

    assert ledger_repos.append() is None

    assert _ledger_worktrees(ledger_repos.host) == []
    assert _ledger_temp_entries(ledger_repos.temp_dir) == []


@pytest.mark.parametrize(
    ("owner_lock", "keep_directory"),
    [(False, True), (True, True), (False, False)],
    ids=["without-a-lock-file", "with-an-unheld-owner-lock", "without-its-directory"],
)
def test_the_next_step_removes_the_locked_worktree_a_killed_worktree_add_left(ledger_repos, owner_lock, keep_directory):
    leftover = _plant_leftover(
        ledger_repos, "killed", owner_lock=owner_lock, keep_directory=keep_directory, unfinished_add=True
    )
    assert _ledger_worktrees(ledger_repos.host) == [_registered_path(leftover)]
    assert "locked initializing" in _git(ledger_repos.host, "worktree", "list", "--porcelain").splitlines()

    assert ledger_repos.append() is None

    assert _ledger_worktrees(ledger_repos.host) == []
    assert _ledger_temp_entries(ledger_repos.temp_dir) == []


def test_the_next_step_removes_the_worktree_of_a_step_whose_processes_were_killed_during_worktree_add(
    ledger_repos, tmp_path
):
    host = ledger_repos.host
    # why: killing the process group from the checkout filter kills the step while git still holds the lock.
    (host / ".git" / "info").mkdir(exist_ok=True)
    (host / ".git" / "info" / "attributes").write_text("* filter=kill-the-step\n", encoding="utf-8")
    _git(host, "config", "filter.kill-the-step.smudge", "kill -KILL 0")
    environment = {**os.environ, "TMPDIR": str(ledger_repos.temp_dir), "PYTHONPATH": str(SOURCE_ROOT)}
    # why: git translates this lock reason, so only the add's own C locale keeps it recognisable.
    environment.update(_german_git_environment(tmp_path / "german"))
    killed = subprocess.run(
        [sys.executable, "-c", KILLED_STEP, str(host), f"refs/remotes/origin/{BRANCH}"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        start_new_session=True,
    )
    assert killed.returncode == -signal.SIGKILL, killed.stderr
    assert killed.stdout == ""
    _git(host, "config", "--unset", "filter.kill-the-step.smudge")
    (leftover,) = _ledger_temp_entries(ledger_repos.temp_dir)
    assert _ledger_worktrees(host) == [_registered_path(leftover)]
    assert "locked initializing" in _git(host, "worktree", "list", "--porcelain").splitlines()
    assert (leftover / "owner.lock").exists()

    assert ledger_repos.append() is None

    assert _ledger_worktrees(host) == []
    assert _ledger_temp_entries(ledger_repos.temp_dir) == []


def test_the_next_step_unregisters_the_worktree_of_a_worktree_add_killed_before_git_wrote_its_head(ledger_repos):
    host = ledger_repos.host
    leftover = _plant_leftover(ledger_repos, "killed", owner_lock=True, unfinished_add=True)
    # why: git writes HEAD after locking, and will not remove a HEAD-less registration while its directory exists.
    Path(_git(leftover / "worktree", "rev-parse", "--absolute-git-dir"), "HEAD").unlink()
    assert _ledger_worktrees(host) == [_registered_path(leftover)]
    assert "locked initializing" in _git(host, "worktree", "list", "--porcelain").splitlines()

    assert ledger_repos.append() is None

    assert _ledger_worktrees(host) == []
    assert _ledger_temp_entries(ledger_repos.temp_dir) == []


@pytest.mark.parametrize("unfinished_add", [False, True], ids=["after-its-worktree-add", "during-its-worktree-add"])
def test_a_worktree_whose_owner_is_alive_is_left_alone_until_the_owner_lets_go(ledger_repos, unfinished_add):
    leftover = _plant_leftover(ledger_repos, "live", owner_lock=True, unfinished_add=unfinished_add)
    owner = os.open(leftover / "owner.lock", os.O_RDWR)
    try:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)

        assert ledger_repos.append() is None

        assert _ledger_worktrees(ledger_repos.host) == [_registered_path(leftover)]
        assert (leftover / "worktree" / LEDGER_REL).exists()
    finally:
        os.close(owner)

    assert ledger_repos.append({**NEW_ROW, "ledger_revision": 3}) is None

    assert _ledger_worktrees(ledger_repos.host) == []
    assert not leftover.exists()


def test_a_step_that_starts_while_another_is_running_leaves_the_running_steps_worktree_alone(ledger_repos, monkeypatch):
    host = ledger_repos.host
    running: list[str] = []
    registered_after_the_other_step: list[str] = []

    def validator_during_which_another_step_runs(ledger_path: str, **keys: str) -> None:
        running.append(ledger_path.removesuffix(f"/{LEDGER_REL}"))
        # why: the other step sweeps on entry, so only the lock the running step holds spares its worktree.
        with ledger_worktree._ledger_worktree(str(host), f"refs/remotes/origin/{BRANCH}", real_run_git) as other:
            assert other != running[0]
        registered_after_the_other_step.extend(_ledger_worktrees(host))
        validate_ledger_file_unique_ids(ledger_path, **keys)

    monkeypatch.setattr(
        "sandbox_executor.flow.git_refs.validate_ledger_file_unique_ids", validator_during_which_another_step_runs
    )

    assert ledger_repos.append() is None

    assert registered_after_the_other_step == [_registered_path(Path(running[0]).parent)]
    assert _unstamped(_ledger_rows(ledger_repos.origin, ledger_repos.origin_tip(), "executions.jsonl")[-1]) == NEW_ROW
    assert _ledger_worktrees(host) == []
    assert _ledger_temp_entries(ledger_repos.temp_dir) == []


def test_worktrees_that_are_not_stale_ledger_worktrees_survive_the_sweep(ledger_repos, tmp_path):
    host = ledger_repos.host
    operator = tmp_path / "operator" / "worktree"
    _git(host, "worktree", "add", "--detach", str(operator), "main")
    locked = _plant_leftover(ledger_repos, "locked")
    _git(host, "worktree", "lock", str(locked / "worktree"))
    locked_for_a_reason = _plant_leftover(ledger_repos, "locked-for-a-reason")
    _git(host, "worktree", "lock", "--reason", "kept for an inspection", str(locked_for_a_reason / "worktree"))
    on_a_branch = ledger_repos.temp_dir / "holon-flow-ledger-branch" / "worktree"
    _git(host, "worktree", "add", "-b", "kept-branch", str(on_a_branch), "main")
    kept = sorted(
        [
            os.path.realpath(operator),
            _registered_path(locked),
            _registered_path(locked_for_a_reason),
            os.path.realpath(on_a_branch),
        ]
    )
    assert sorted(_registered_worktrees(host)[1:]) == kept

    assert ledger_repos.append() is None

    assert sorted(_registered_worktrees(host)[1:]) == kept
    assert all(os.path.exists(os.path.join(path, ".git")) for path in kept)


def test_a_sweep_that_cannot_list_worktrees_does_not_fail_the_ledger_step(ledger_repos, monkeypatch):
    git = RecordingGit({("worktree", "list"): TimeoutError("git worktree list timed out")})
    monkeypatch.setattr("sandbox_executor.flow.git_refs.run_git", git)

    assert ledger_repos.append() is None

    assert _unstamped(_ledger_rows(ledger_repos.origin, ledger_repos.origin_tip(), "executions.jsonl")[-1]) == NEW_ROW
    assert _ledger_worktrees(ledger_repos.host) == []
    assert _ledger_temp_entries(ledger_repos.temp_dir) == []


def test_discarding_a_worktree_deletes_only_a_real_ledger_temp_directory(ledger_repos, tmp_path):
    host = ledger_repos.host
    elsewhere = tmp_path / "elsewhere"
    _git(host, "worktree", "add", "--detach", str(elsewhere / "worktree"), "main")
    real_root = tmp_path / "real-root"
    linked = ledger_repos.temp_dir / "holon-flow-ledger-linked"
    _git(host, "worktree", "add", "--detach", str(real_root / "worktree"), "main")
    linked.symlink_to(real_root, target_is_directory=True)

    ledger_worktree._discard_worktree(str(host), str(elsewhere / "worktree"), real_run_git)
    ledger_worktree._discard_worktree(str(host), str(linked / "worktree"), real_run_git)

    assert _registered_worktrees(host) == [os.path.realpath(host)]
    assert elsewhere.is_dir()
    assert real_root.is_dir() and linked.is_symlink()
