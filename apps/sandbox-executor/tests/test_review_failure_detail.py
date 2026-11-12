"""A failed pre-push test run names its failed tests, keeps the tail and writes the full redacted output file.

Hermetic: ``run_cmd`` is faked and every file lands under ``tmp_path``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from sandbox_executor.entrypoint import review_loop
from sandbox_executor.entrypoint.review_failure_detail import (
    MAX_NAMED_FAILURES,
    MESSAGE_MAX_CHARS,
    failed_test_ids,
    with_short_summary,
)
from sandbox_executor.entrypoint.review_verification import verification_error
from sandbox_executor.flow import _bounded_error_context

SECRET = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def _long_output(failures: int = 15) -> str:
    lines = ["=== test session starts ===", "." * 80 + f" token {SECRET}"]
    lines += ["." * 80 for _ in range(200)]
    lines += ["=== short test summary info ==="]
    lines += [f"FAILED tests/test_x.py::test_{n} - AssertionError {SECRET}" for n in range(failures)]
    return "\n".join(lines)


def _fake_run(test_output: str, lint_code: int = 0, seen: list | None = None):
    def fake(args, cwd=None, env=None, check=True):
        if seen is not None:
            seen.append(env)
        if args[-1] == "lint":
            return subprocess.CompletedProcess(args, lint_code, stdout="", stderr="lint broke")
        return subprocess.CompletedProcess(args, 1, stdout=test_output, stderr="")

    return fake


def test_failed_names_survive_and_full_output_is_written_redacted(tmp_path: Path) -> None:
    with patch.object(review_loop, "run_cmd", _fake_run(_long_output())):
        _, tests_ok, message = review_loop._verify_before_push(str(tmp_path))
    assert not tests_ok and message is not None
    assert len(message) <= MESSAGE_MAX_CHARS
    assert message.startswith("Resolver pre-push test failed (exit code 1); failed: tests/test_x.py::test_0")
    assert f"(+{15 - MAX_NAMED_FAILURES} more)" in message
    assert ".holon" in message and SECRET not in message
    written = (tmp_path / ".holon" / "review" / "verification" / "test.log").read_text()
    assert "short test summary" in written and "test_14" in written and SECRET not in written


def test_written_file_is_git_ignored(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    target = repo_root / ".holon" / "review" / "verification" / "test.log"
    proc = subprocess.run(["git", "check-ignore", "-q", str(target)], cwd=repo_root, check=False)
    assert proc.returncode == 0


def test_lint_failure_and_missing_tool_keep_existing_messages(tmp_path: Path) -> None:
    with patch.object(review_loop, "run_cmd", _fake_run("", lint_code=1)):
        _, _, message = review_loop._verify_before_push(str(tmp_path))
    assert message == "Resolver pre-push lint failed (exit code 1): lint broke"
    assert not (tmp_path / ".holon").exists()
    missing = subprocess.CompletedProcess(["uv"], 127, stdout="", stderr="required tool 'uv' was not found on PATH")
    assert verification_error("test", missing, _bounded_error_context, workspace=str(tmp_path)).startswith(
        "Resolver pre-push verification could not run:"
    )


def test_unwritable_holon_degrades_gracefully(tmp_path: Path) -> None:
    (tmp_path / ".holon").write_text("a file, not a directory")
    with patch.object(review_loop, "run_cmd", _fake_run(_long_output())):
        _, _, message = review_loop._verify_before_push(str(tmp_path))
    assert message is not None and "full output file could not be written" in message
    assert "failed: tests/test_x.py::test_0" in message


def test_extraction_handles_ansi_xdist_and_duplicates() -> None:
    output = "\x1b[31mFAILED\x1b[0m tests/a.py::t1 - boom\n[gw3] FAILED tests/b.py::t2\nERROR tests/c.py\nFAILED tests/a.py::t1\n"
    assert failed_test_ids(output) == ["tests/a.py::t1", "tests/b.py::t2", "tests/c.py"]


def test_pytest_addopts_gains_rf_unless_already_reporting() -> None:
    assert with_short_summary({})["PYTEST_ADDOPTS"] == "-rf"
    assert with_short_summary({"PYTEST_ADDOPTS": "-q"})["PYTEST_ADDOPTS"] == "-q -rf"
    assert with_short_summary({"PYTEST_ADDOPTS": "-rA"})["PYTEST_ADDOPTS"] == "-rA"


def test_gate_passes_the_summary_option_to_the_test_run(tmp_path: Path) -> None:
    seen: list = []
    with patch.object(review_loop, "run_cmd", _fake_run(_long_output(), seen=seen)):
        review_loop._verify_before_push(str(tmp_path))
    assert "-rf" in seen[-1]["PYTEST_ADDOPTS"]
