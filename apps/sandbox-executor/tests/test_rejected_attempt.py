"""A failed pre-push test run keeps the rejected diff and a tail-based summary, and feeds both to the next pass.

Hermetic: real git only in a temporary repository, ``uv`` faked, no network.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.entrypoint import review_loop
from sandbox_executor.entrypoint.pytest_summary import counts_line, summary_excerpt
from sandbox_executor.entrypoint.rejected_attempt import (
    capture_rejected_attempt,
    previous_failure_section,
)
from sandbox_executor.flow.review.resolver import ResolverOutcome, record_rejected_attempt
from sandbox_executor.resolver_result import read_container_resolver_result

SECRET = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
COUNTS = "2 failed, 198 passed in 12.34s"


def _pytest_output() -> str:
    lines = ["=== test session starts ===", "." * 80 + f" token {SECRET}"]
    lines += ["." * 80 for _ in range(100)]
    lines += ["=== short test summary info ==="]
    lines += [f"FAILED tests/x.py::test_{n} - AssertionError {SECRET}" for n in ("a", "b")]
    lines += [f"=== {COUNTS} ==="]
    return "\n".join(lines)


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
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "feature-x")
    (tmp_path / "tracked.py").write_text("x = 1\n")
    (tmp_path / ".gitignore").write_text(".holon/\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    (tmp_path / "tracked.py").write_text(f"x = 2\nTOKEN = '{SECRET}'\n")
    (tmp_path / "new_file.py").write_text("y = 1\n")
    return tmp_path


def test_excerpt_keeps_summary_and_counts_when_dots_exceed_the_limit() -> None:
    excerpt = summary_excerpt(_pytest_output(), 2000)
    assert len(excerpt) <= 2000
    assert "short test summary info" in excerpt and "tests/x.py::test_b" in excerpt
    assert COUNTS in excerpt.splitlines()[-1] and "test session starts" not in excerpt
    assert counts_line(_pytest_output()).strip("= ") == COUNTS


def test_excerpt_without_a_summary_section_is_the_tail() -> None:
    text = "start\n" + "." * 5000 + "\nthe very end"
    excerpt = summary_excerpt(text, 500)
    assert len(excerpt) <= 500 and excerpt.endswith("the very end") and "start" not in excerpt


def test_capture_saves_a_redacted_patch_and_leaves_the_index_alone(repo: Path) -> None:
    before = _git(repo, "status", "--porcelain")
    attempt = capture_rejected_attempt(str(repo), 2, _pytest_output())
    assert attempt.patch_path == ".holon/review/rejected/iter-2.patch"
    patch_text = (repo / attempt.patch_path).read_text()
    assert "x = 2" in patch_text and "new_file.py" in patch_text and SECRET not in patch_text
    assert SECRET not in attempt.summary
    assert attempt.failed_tests == ("tests/x.py::test_a", "tests/x.py::test_b")
    assert COUNTS in attempt.summary.splitlines()[-1]
    assert _git(repo, "status", "--porcelain") == before
    assert SECRET not in (repo / ".holon/review/rejected/iter-2.json").read_text()


def test_capture_never_raises_outside_a_repository(tmp_path: Path) -> None:
    attempt = capture_rejected_attempt(str(tmp_path), 1, _pytest_output())
    assert attempt.patch_path is None and attempt.failed_tests


def _fake_run(args, cwd=None, env=None, check=True):
    if args[0] == "uv" and args[-1] == "test":
        return subprocess.CompletedProcess(args, 1, stdout=_pytest_output(), stderr="")
    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


def test_failed_pre_push_run_saves_patch_and_next_prompt_carries_it(repo: Path) -> None:
    with patch.object(review_loop, "run_cmd", _fake_run):
        _, tests_ok, message = review_loop._verify_before_push(str(repo), 1)
    assert not tests_ok and message is not None and SECRET not in message
    head = _git(repo, "rev-parse", "HEAD").strip()
    section = previous_failure_section(str(repo), head)
    assert "Previous attempt failed verification" in section and "`tests/x.py::test_a`" in section
    assert "x = 2" in section and SECRET not in section and COUNTS in section
    assert previous_failure_section(str(repo), "0" * 40) == ""
    with patch.object(review_loop, "run_cmd", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", "")):
        assert review_loop._verify_before_push(str(repo), 2)[1] is True
    assert previous_failure_section(str(repo), head) == ""


def test_no_prior_failure_adds_nothing_to_the_prompt(tmp_path: Path) -> None:
    assert previous_failure_section(str(tmp_path), "abc") == ""


def test_result_fields_are_optional_and_ledger_row_points_at_the_patch(tmp_path: Path) -> None:
    older = read_container_resolver_result({"resolver": True, "dispositions": []}, 0, "h")
    assert older.rejected_patch is None and older.failed_tests == ()
    data = {
        "resolver": True,
        "error": "boom",
        "exit_code": 1,
        "rejected_patch": ".holon/review/rejected/iter-1.patch",
        "failed_tests": ["tests/x.py::test_a", 5],
        "test_summary": f"FAILED {SECRET}",
    }
    parsed = read_container_resolver_result(data, 1, "h")
    assert parsed.failed_tests == ("tests/x.py::test_a",) and SECRET not in (parsed.test_summary or "")
    rows: list[dict] = []
    outcome = ResolverOutcome(
        error="boom",
        rejected_patch=parsed.rejected_patch,
        failed_tests=parsed.failed_tests,
        test_summary=parsed.test_summary,
    )
    with patch("sandbox_executor.flow.review.resolver.record_review_action", lambda c, f, row: rows.append(row)):
        record_rejected_attempt(object(), "ledger", "PR#1", 1, outcome)  # type: ignore[arg-type]
        record_rejected_attempt(object(), "ledger", "PR#1", 1, ResolverOutcome())  # type: ignore[arg-type]
    assert len(rows) == 1
    assert rows[0]["rejected_patch"] == ".holon/review/rejected/iter-1.patch"
    assert rows[0]["failed_tests"] == ["tests/x.py::test_a"] and json.dumps(rows[0])
