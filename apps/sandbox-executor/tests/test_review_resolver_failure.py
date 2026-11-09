"""A resolver pass that cannot verify, crashes or exits non-zero fails loudly and halts the Stage 4 review loop.

Hermetic: real git only in temporary repositories, ``uv`` hidden by a PATH holding nothing but a ``git`` symlink, and
fakes for every reviewer, resolver, container dispatch, CI probe and post.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.entrypoint.review_loop import _run_resolver_mode, _verify_before_push, run_cmd
from sandbox_executor.entrypoint.review_verification import missing_tool_result, verification_error
from sandbox_executor.flow import ResolverInput, _bounded_error_context, run_review_loop
from sandbox_executor.flow.review.resolver import _dispatch_review_resolver
from sandbox_executor.resolver_result import (
    OUTPUT_TAIL_MAX_CHARS,
    bounded_output_tail,
    read_container_resolver_result,
    resolver_error_text,
)
from sandbox_executor.review_ensemble import ConsensusVerdict, ReviewerVerdict, serialize_review_outcome
from sandbox_executor.review_report import FindingRow, build_review_consensus_report
from sandbox_executor.review_target import ReviewTarget

from tests.review_fakes import FakeEnsemble, clean_reviewer, install_fake_reviewers, objecting_reviewer
from tests.test_review_dispatch import EXEC_TARGET, _host_loop_patches, _make_context
from tests.test_review_loop import _ensemble_ctx, _LoopStubs, _rows

UV_MISSING_ERROR = verification_error(
    "lint",
    missing_tool_result(["uv", "run", "task", "lint"], FileNotFoundError(2, "No such file", "uv")),
    _bounded_error_context,
)


def _git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` with a fixed identity and return its stripped stdout."""
    proc = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


@pytest.fixture
def path_without_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Replace PATH with a directory holding only a ``git`` symlink, so ``uv`` cannot be found."""
    real_git = shutil.which("git")
    assert real_git, "git is required for these tests"
    bin_dir = tmp_path / "bin-git-only"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(real_git)
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


# --- (a) _verify_before_push with uv absent -------------------------------------------------------------------------


def test_verify_before_push_without_uv_returns_a_failure_naming_uv(tmp_path: Path, monkeypatch) -> None:
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    lint_ok, tests_ok, error = _verify_before_push(str(tmp_path))

    assert lint_ok is False
    assert tests_ok is False
    assert error is not None
    assert "could not run" in error
    assert "'uv'" in error
    assert "not found" in error


def test_verify_before_push_reports_a_tool_that_cannot_be_started(tmp_path: Path) -> None:
    with patch(
        "sandbox_executor.entrypoint.review_loop.subprocess.run", side_effect=PermissionError(13, "Permission denied")
    ):
        lint_ok, tests_ok, error = _verify_before_push(str(tmp_path))

    assert (lint_ok, tests_ok) == (False, False)
    assert error is not None
    assert "required tool 'uv' could not be started (PermissionError" in error


def test_run_cmd_with_check_exits_127_for_a_missing_tool(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    assert run_cmd(["uv", "--version"], check=False).returncode == 127
    with pytest.raises(SystemExit) as exc_info:
        run_cmd(["uv", "--version"], check=True)

    assert exc_info.value.code == 127
    assert "required tool 'uv' was not found on PATH" in capsys.readouterr().err


# --- (b, entrypoint half) a resolver run without uv records a failure, commits nothing and pushes nothing ----------


@pytest.fixture
def resolver_checkout(tmp_path: Path) -> Path:
    """A one-commit checkout on ``feature`` whose ``origin`` is a local bare repository holding the same commit."""
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", "-b", "feature", str(origin)], capture_output=True, check=True)
    subprocess.run(["git", "init", "-b", "feature", str(repo)], capture_output=True, check=True)
    (repo / "app.py").write_text("print('bug')\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "origin", "HEAD:refs/heads/feature")
    return repo


def test_resolver_mode_without_uv_records_the_failure_without_commit_or_push(
    resolver_checkout: Path, tmp_path: Path, path_without_uv: Path
) -> None:
    repo = resolver_checkout
    origin = tmp_path / "origin.git"
    head_before = _git(repo, "rev-parse", "HEAD")
    origin_before = _git(origin, "rev-parse", "refs/heads/feature")
    commits_before = _git(repo, "rev-list", "--count", "HEAD")
    # The container uses one file for the host's findings payload and the resolver result.
    result_file = tmp_path / "review_result.json"
    findings = [{"id": "R1-F1", "severity": "nit", "path": "app.py", "line": 1, "summary": "prints bug"}]
    result_file.write_text(json.dumps({"iteration": 1, "findings": findings}), encoding="utf-8")

    def fake_agent(repo_dir, *_args):
        Path(repo_dir, "app.py").write_text("print('fixed')\n", encoding="utf-8")
        return [{"id": "R1-F1", "disposition": "applied", "rejection_reason": None}]

    args = argparse.Namespace(
        findings_file=str(result_file),
        result_file=str(result_file),
        iteration=1,
        dry_run=False,
        skip_push=False,
        target="feature",
        json=False,
    )

    with (
        patch("sandbox_executor.entrypoint.review_loop._run_resolver_agent", side_effect=fake_agent),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_resolver_mode(args, str(repo), "feature", "fake-agent", "fake-model")

    assert exc_info.value.code == 1
    outcome = json.loads(result_file.read_text(encoding="utf-8"))
    assert outcome["resolver"] is True
    assert outcome["pushed"] is False
    assert outcome["exit_code"] == 1
    assert "required tool 'uv' was not found on PATH" in outcome["error"]
    assert "uv" in outcome["output_tail"]
    assert outcome["head_sha"] == head_before
    assert _git(repo, "rev-parse", "HEAD") == head_before
    assert _git(repo, "rev-list", "--count", "HEAD") == commits_before
    assert _git(repo, "diff", "--cached", "--name-only") == ""
    assert _git(origin, "rev-parse", "refs/heads/feature") == origin_before


def test_resolver_mode_writes_a_result_when_commit_raises(resolver_checkout: Path, tmp_path: Path) -> None:
    result_file = tmp_path / "review_result.json"
    findings = [{"id": "R1-F1", "severity": "nit", "path": "app.py", "line": 1, "summary": "prints bug"}]
    result_file.write_text(json.dumps({"findings": findings}), encoding="utf-8")
    args = argparse.Namespace(
        findings_file=str(result_file),
        result_file=str(result_file),
        iteration=1,
        dry_run=False,
        skip_push=False,
        target="feature",
        json=False,
    )

    with (
        patch("sandbox_executor.entrypoint.review_loop._run_resolver_agent", return_value=[]),
        patch(
            "sandbox_executor.entrypoint.review_loop._commit_and_push_resolution",
            side_effect=RuntimeError("disk full"),
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_resolver_mode(args, str(resolver_checkout), "feature", "fake-agent", "fake-model")

    assert exc_info.value.code == 1
    outcome = json.loads(result_file.read_text(encoding="utf-8"))
    assert outcome["resolver"] is True
    assert outcome["error"] == "Resolver verification or commit failed: disk full"
    assert outcome["exit_code"] == 1


# --- (b, loop half) the review loop halts on the resolver failure --------------------------------------------------


@pytest.fixture
def in_simulated_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the loop as if inside the sandbox, with fake target resolution and diff; each test injects its ensemble."""
    install_fake_reviewers(monkeypatch)


def _entrypoint_failure(findings: list[FindingRow], head_sha: str) -> dict:
    """The resolver result the entrypoint writes when its pre-push verification cannot run uv."""
    return {
        "resolver": True,
        "dispositions": [{"id": f.id, "disposition": "applied", "rejection_reason": None} for f in findings],
        "head_sha": head_sha,
        "pushed": False,
        "lint_passed": False,
        "tests_passed": False,
        "error": UV_MISSING_ERROR,
        "exit_code": 1,
        "output_tail": UV_MISSING_ERROR,
    }


@pytest.mark.usefixtures("in_simulated_sandbox")
def test_review_loop_halts_when_the_resolver_cannot_run_uv(tmp_path: Path) -> None:
    ensemble = FakeEnsemble([objecting_reviewer(1)])
    resolver_calls: list[int] = []

    def failing_resolver(target, iteration, findings, action_ids, rejected_findings):
        resolver_calls.append(iteration)
        return _entrypoint_failure(findings, target.head_sha)

    ctx = _ensemble_ctx(tmp_path)
    with _LoopStubs() as stubs:
        res = run_review_loop(
            ctx,
            target=EXEC_TARGET,
            max_iterations=3,
            ensemble_size=1,
            run_ensemble_fn=ensemble,
            run_resolver_fn=failing_resolver,
        )

    assert len(ensemble.calls) == 1
    assert resolver_calls == [1]
    assert res["consensus"]["approved"] is False
    assert res["consensus"]["status"] == "failed"
    assert "required tool 'uv' was not found on PATH" in res["resolver_error"]
    assert res["error"].startswith("resolver failed: Resolver pre-push verification could not run")
    assert res["reviewers"][0]["verdict"] == str(ReviewerVerdict.CHANGES_REQUESTED)
    assert not [r for r in _rows(tmp_path, "ACT-RES") if r.get("review_disposition") == "applied"]
    summary = [r for r in map(json.loads, _ledger_lines(tmp_path)) if r.get("entry_type") == "summary"]
    assert "resolver failed: Resolver pre-push verification could not run" in summary[-1]["summary"]
    assert stubs.post.call_args.kwargs["consensus_data"]["approved"] is False


def _ledger_lines(tmp_path: Path) -> list[str]:
    ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
    return [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.usefixtures("in_simulated_sandbox")
def test_incident_failed_resolver_never_turns_a_dissent_into_unanimous_approval(tmp_path: Path) -> None:
    """The observed incident: the resolver crashes on a missing uv while one reviewer still flags a nit."""
    ensemble = FakeEnsemble([clean_reviewer(1), clean_reviewer(2), objecting_reviewer(3, "nit")])

    def crashing_resolver(target, iteration, findings, action_ids, rejected_findings):
        raise FileNotFoundError(2, "No such file or directory", "uv")

    ctx = _ensemble_ctx(tmp_path)
    with _LoopStubs() as stubs:
        res = run_review_loop(
            ctx,
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=3,
            run_ensemble_fn=ensemble,
            run_resolver_fn=crashing_resolver,
        )

    assert len(ensemble.calls) == 1
    assert res["consensus"]["approved"] is False
    assert res["ensemble_verdict"] == str(ConsensusVerdict.CHANGES_REQUESTED)
    assert "uv" in res["resolver_error"]
    posted = stubs.post.call_args.kwargs
    body = build_review_consensus_report(str(tmp_path), EXEC_TARGET, posted["consensus_data"], posted["reviewers"])
    assert "APPROVED (3/3)" not in body
    assert "— 2/3 approved" in body.splitlines()[0]
    assert "0 critical, 0 important and 1 nit findings" in body
    assert "nit problem in the handler" in body


# --- (c) a resolver container that exits non-zero halts the loop ----------------------------------------------------


def _resolve_dispatcher(resolve_exit: int, resolver_result: dict | None, calls: list[list[str]]):
    """Reviewers object every iteration; the resolver exits ``resolve_exit`` and writes ``resolver_result`` if given."""

    def fake_dispatcher(_role, _runner, _model, positional_args, **kwargs):
        calls.append(list(positional_args))
        result_file = kwargs["result_file"]
        if "--resolve" in positional_args:
            if resolver_result is not None:
                Path(result_file).write_text(json.dumps(resolver_result), encoding="utf-8")
            return resolve_exit
        reviewers = [objecting_reviewer(1)]
        payload = serialize_review_outcome(reviewers, ConsensusVerdict.CHANGES_REQUESTED)
        Path(result_file).write_text(json.dumps(payload), encoding="utf-8")
        return 1

    return fake_dispatcher


def _run_host_loop(tmp_path: Path, monkeypatch, dispatcher) -> dict:
    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)
    ctx = _make_context(tmp_path, dispatcher=dispatcher)
    patches = _host_loop_patches()
    for p in patches:
        p.start()
    try:
        return run_review_loop(ctx, target=EXEC_TARGET, max_iterations=3, ensemble_size=1)
    finally:
        for p in reversed(patches):
            p.stop()


def test_resolver_container_exiting_non_zero_without_a_result_halts_the_loop(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    res = _run_host_loop(tmp_path, monkeypatch, _resolve_dispatcher(2, None, calls))

    assert ["--resolve" in c for c in calls] == [False, True]
    assert res["consensus"]["status"] == "failed"
    assert res["resolver_error"] == "Resolver container exited with code 2 and produced no resolver result"


def test_resolver_container_exiting_non_zero_with_dispositions_halts_with_its_output_tail(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[list[str]] = []
    result = {
        "resolver": True,
        "dispositions": [{"id": "R1-F1", "disposition": "applied", "rejection_reason": None}],
        "head_sha": "a" * 40,
        "pushed": False,
        "error": None,
        "exit_code": 1,
        "output_tail": "Traceback (most recent call last): FileNotFoundError: 'uv'",
    }

    res = _run_host_loop(tmp_path, monkeypatch, _resolve_dispatcher(1, result, calls))

    assert ["--resolve" in c for c in calls] == [False, True]
    assert res["consensus"]["status"] == "failed"
    assert res["resolver_error"].startswith("Resolver container exited with code 1")
    assert "output tail: Traceback (most recent call last): FileNotFoundError: 'uv'" in res["resolver_error"]


def test_dispatch_review_resolver_reads_an_untouched_input_payload_as_no_result(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path, dispatcher=lambda *_a, **_k: 2)
    target = ReviewTarget(raw=EXEC_TARGET, head_branch=EXEC_TARGET, head_sha="a" * 40)
    row = FindingRow("R1-F1", "nit", "src/app.py", 12, (1,), "nit problem", "fix it")

    outcome = _dispatch_review_resolver(ctx, target, 1, ResolverInput(findings=[row]))

    assert outcome.error == "Resolver container exited with code 2 and produced no resolver result"
    assert (outcome.pushed, outcome.lint_passed, outcome.tests_passed) == (False, False, False)
    assert outcome.head_sha == "a" * 40


# --- the shared resolver result contract ---------------------------------------------------------------------------


def test_clean_exit_with_a_marked_result_keeps_the_payload() -> None:
    data = {"resolver": True, "dispositions": [], "head_sha": "b" * 40, "pushed": True, "error": None}

    parsed = read_container_resolver_result(data, 0, "a" * 40)

    assert parsed.error is None
    assert (parsed.head_sha, parsed.pushed) == ("b" * 40, True)


def test_clean_exit_without_any_result_is_a_failed_pass() -> None:
    parsed = read_container_resolver_result({"iteration": 1, "findings": []}, 0, "a" * 40)

    assert parsed.error == "Resolver container exited cleanly and produced no resolver result"


def test_output_tail_is_redacted_then_bounded_to_its_end(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "x" * 36)
    text = "start " + "y" * (OUTPUT_TAIL_MAX_CHARS * 2) + " token " + os.environ["GITHUB_TOKEN"] + " end"

    tail = bounded_output_tail(text)

    assert len(tail) == OUTPUT_TAIL_MAX_CHARS + 1
    assert tail.startswith("…")
    assert tail.endswith(" end")
    assert os.environ["GITHUB_TOKEN"] not in tail


def test_resolver_error_text_names_the_exit_code_once_and_skips_a_repeated_tail() -> None:
    assert resolver_error_text(None, 0, "ignored") is None
    assert resolver_error_text("boom", 3, "boom") == "boom (resolver exit code 3)"
    assert resolver_error_text("exited with code 3", 3, None) == "exited with code 3"
