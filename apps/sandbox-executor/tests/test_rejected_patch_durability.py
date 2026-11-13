"""The rejected resolver patch survives the container: a safe-directory-proof capture and a host-side copy.

Hermetic: real git only in temporary repositories, ``uv`` faked, no network.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sandbox_executor.entrypoint import rejected_attempt, review_loop
from sandbox_executor.flow.review.rejected_attempt_record import _record_rejected_attempt
from sandbox_executor.flow.review.resolver import ResolverOutcome
from sandbox_executor.resolver_result import read_container_resolver_result

SECRET = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
PATCH = ".holon/review/rejected/iter-3.patch"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    """A linked worktree with a staged edit, an unstaged edit, a new file and a secret-looking token."""
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "base")
    (main / "staged.py").write_text("a = 1\n")
    (main / "unstaged.py").write_text("b = 1\n")
    (main / ".gitignore").write_text(".holon/\n")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "base")
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "-b", "feature", str(linked))
    (linked / "staged.py").write_text("a = 2\n")
    _git(linked, "add", "staged.py")
    (linked / "unstaged.py").write_text(f"b = 2\nTOKEN = '{SECRET}'\n")
    (linked / "new_file.py").write_text("c = 1\n")
    return linked


def _failing_run(args, cwd=None, env=None, check=True):
    code = 1 if args[0] == "uv" and args[-1] == "test" else 0
    out = "FAILED tests/x.py::test_a - boom\n=== 1 failed in 0.1s ===" if code else ""
    return subprocess.CompletedProcess(args, code, stdout=out, stderr="")


def test_failed_pre_push_run_in_a_linked_worktree_saves_the_whole_change(worktree: Path) -> None:
    with patch.object(review_loop, "run_cmd", _failing_run):
        review_loop._verify_before_push(str(worktree), 3)
    text = (worktree / PATCH).read_text()
    assert "+a = 2" in text and "+b = 2" in text and "new_file.py" in text and SECRET not in text
    record = json.loads((worktree / ".holon/review/rejected/iter-3.json").read_text())
    assert record["patch_path"] == PATCH and not list((worktree / ".holon/review/rejected").glob("*.tmp"))


def test_patch_survives_git_refusing_the_workspace_owner(worktree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Git's own test hook for the "dubious ownership" refusal a container user meets on a bind mount.
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    assert rejected_attempt.uncommitted_diff(str(worktree))
    attempt = rejected_attempt.capture_rejected_attempt(str(worktree), 3, "FAILED tests/x.py::test_a")
    assert attempt.patch_path == PATCH and (worktree / PATCH).is_file()


def test_result_payload_carries_the_patch_and_the_host_writes_it_when_the_file_is_lost(tmp_path: Path) -> None:
    data = {
        "resolver": True,
        "error": "boom",
        "exit_code": 1,
        "rejected_patch": PATCH,
        "rejected_patch_text": f"+{SECRET}\n+x = 2\n",
    }
    parsed = read_container_resolver_result(data, 1, "h")
    assert SECRET not in parsed.rejected_patch_text and "+x = 2" in parsed.rejected_patch_text
    ledger = tmp_path / "executions.jsonl"
    context = SimpleNamespace(log=lambda *a, **k: None, repo_dir=str(tmp_path))
    outcome = ResolverOutcome(
        error="boom",
        rejected_patch=parsed.rejected_patch,
        rejected_patch_text=parsed.rejected_patch_text,
        failed_tests=("tests/x.py::test_a",),
    )
    _record_rejected_attempt(context, str(ledger), "PR#1", 3, outcome)  # type: ignore[arg-type]
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert rows[0]["rejected_patch"] == PATCH
    assert "+x = 2" in (tmp_path / PATCH).read_text() and SECRET not in (tmp_path / PATCH).read_text()


def test_host_keeps_an_existing_patch_file(tmp_path: Path) -> None:
    (tmp_path / PATCH).parent.mkdir(parents=True)
    (tmp_path / PATCH).write_text("container copy")
    from sandbox_executor.flow.review.rejected_patch_host import _persist_rejected_patch

    assert _persist_rejected_patch(str(tmp_path), 3, PATCH, "payload copy") == PATCH
    assert (tmp_path / PATCH).read_text() == "container copy"
    assert _persist_rejected_patch(str(tmp_path), 4, None, "") is None
