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
from sandbox_executor.ledger import read_merged_rows
from sandbox_executor.resolver_result import (
    OUTPUT_TAIL_MAX_CHARS,
    bounded_output_tail,
    read_container_resolver_result,
    resolver_error_text,
)
from sandbox_executor.review_ensemble import ConsensusVerdict, ReviewerResult, ReviewerVerdict, serialize_review_outcome
from sandbox_executor.review_report import FindingRow
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
    real_git = shutil.which("git")
    assert real_git, "git is required for these tests"
    bin_dir = tmp_path / "bin-git-only"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(real_git)
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


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


@pytest.fixture
def resolver_checkout(tmp_path: Path) -> Path:
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
    # why: the container uses one file for the host's findings payload and the resolver result
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
    assert "pushed" not in outcome
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
        target="feature",
        json=False,
    )

    with (
        patch("sandbox_executor.entrypoint.review_loop._run_resolver_agent", return_value=[]),
        patch(
            "sandbox_executor.entrypoint.review_loop._commit_resolution",
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


@pytest.fixture
def in_simulated_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_reviewers(monkeypatch)


def _entrypoint_failure(findings: list[FindingRow], head_sha: str) -> dict:
    return {
        "resolver": True,
        "dispositions": [{"id": f.id, "disposition": "applied", "rejection_reason": None} for f in findings],
        "head_sha": head_sha,
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
    summary = [r for r in _ledger_rows(tmp_path) if r.get("entry_type") == "summary"]
    assert "resolver failed: Resolver pre-push verification could not run" in summary[-1]["summary"]
    assert stubs.posts() == []
    assert not _rows(tmp_path, "ACT-POST")
    assert any("Not posting a review consensus" in line and "halted" in line for line in ctx.logs)


def _ledger_rows(tmp_path: Path) -> list[dict]:
    return read_merged_rows(str(tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"))


@pytest.mark.usefixtures("in_simulated_sandbox")
def test_incident_failed_resolver_never_turns_a_dissent_into_unanimous_approval(tmp_path: Path) -> None:
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
    assert stubs.posts() == []
    assert not _rows(tmp_path, "ACT-POST")


def _resolve_dispatcher(resolve_exit: int, resolver_result: dict | None, calls: list[list[str]]):

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


def test_clean_exit_with_a_marked_result_keeps_the_payload() -> None:
    data = {"resolver": True, "dispositions": [], "head_sha": "b" * 40, "error": None}

    parsed = read_container_resolver_result(data, 0, "a" * 40)

    assert parsed.error is None
    assert parsed.head_sha == "b" * 40


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


def _resolver_dispatch(tmp_path: Path, *, commit: bool = True, **ctx_kwargs):
    origin = tmp_path / "origin.git"
    clone = tmp_path / "clone"
    subprocess.run(["git", "init", "--bare", "-b", "feature", str(origin)], capture_output=True, check=True)
    subprocess.run(["git", "init", "-b", "feature", str(clone)], capture_output=True, check=True)
    (clone / "app.py").write_text("print('bug')\n", encoding="utf-8")
    _git(clone, "add", "app.py")
    _git(clone, "commit", "-m", "initial")
    _git(clone, "remote", "add", "origin", str(origin))
    _git(clone, "push", "origin", "HEAD:refs/heads/feature")
    base = _git(clone, "rev-parse", "HEAD")
    new_head = base

    def dispatcher(_role, _runner, _model, positional_args, **kwargs):
        nonlocal new_head
        if commit:
            (clone / "app.py").write_text("print('fixed')\n", encoding="utf-8")
            _git(clone, "commit", "-am", "resolve")
            new_head = _git(clone, "rev-parse", "HEAD")
        payload = {"resolver": True, "dispositions": [], "head_sha": new_head, "error": None}
        Path(kwargs["result_file"]).write_text(json.dumps(payload), encoding="utf-8")
        positional_args_seen.extend(positional_args)
        return 0

    positional_args_seen: list[str] = []
    ctx = _make_context(tmp_path, dispatcher=dispatcher, target_worktree=str(clone), **ctx_kwargs)
    target = ReviewTarget(raw="feature", head_branch="feature", head_sha=base)
    row = FindingRow("R1-F1", "nit", "app.py", 1, (1,), "prints bug", "fix it")
    outcome = _dispatch_review_resolver(ctx, target, 1, ResolverInput(findings=[row]))
    assert "--skip-push" not in positional_args_seen
    return outcome, origin, clone, base


def test_host_pushes_the_head_the_container_committed(tmp_path: Path) -> None:
    outcome, origin, _clone, base = _resolver_dispatch(tmp_path)

    assert outcome.error is None
    assert outcome.pushed is True
    assert outcome.head_sha != base
    assert _git(origin, "rev-parse", "refs/heads/feature") == outcome.head_sha


def test_host_does_not_push_an_unchanged_head(tmp_path: Path) -> None:
    outcome, origin, _clone, base = _resolver_dispatch(tmp_path, commit=False)

    assert outcome.error is None
    assert outcome.pushed is False
    assert _git(origin, "rev-parse", "refs/heads/feature") == base


def test_host_does_not_push_under_skip_push(tmp_path: Path) -> None:
    outcome, origin, _clone, base = _resolver_dispatch(tmp_path, skip_push=True)

    assert outcome.error is None
    assert outcome.pushed is False
    assert _git(origin, "rev-parse", "refs/heads/feature") == base


def test_host_does_not_push_after_a_failed_pass(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path, dispatcher=lambda *_a, **_k: 2)
    target = ReviewTarget(raw="feature", head_branch="feature", head_sha="a" * 40)

    with patch("sandbox_executor.flow.review.resolver.push_commit") as host_push:
        outcome = _dispatch_review_resolver(ctx, target, 1, ResolverInput(findings=[]))

    host_push.assert_not_called()
    assert outcome.pushed is False


def test_failed_host_push_names_the_operation_branch_and_remote_without_credentials(tmp_path: Path) -> None:
    secret_url = "https://x-access-token:SECRET@github.com/acme/widgets.git"
    stderr = f"fatal: unable to access '{secret_url}/': The requested URL returned error: 403"

    def dispatcher(_role, _runner, _model, _args, **kwargs):
        payload = {"resolver": True, "dispositions": [], "head_sha": "b" * 40, "error": None}
        Path(kwargs["result_file"]).write_text(json.dumps(payload), encoding="utf-8")
        return 0

    ctx = _make_context(tmp_path, dispatcher=dispatcher)
    target = ReviewTarget(raw="feature", head_branch="feature", head_sha="a" * 40)

    with patch(
        "sandbox_executor.flow.host_remote.run_git",
        return_value=subprocess.CompletedProcess([], 1, stdout="", stderr=stderr),
    ):
        outcome = _dispatch_review_resolver(ctx, target, 1, ResolverInput(findings=[]))

    assert outcome.pushed is False
    assert outcome.error is not None
    assert outcome.error.startswith("Resolver push failed: host push of 'feature' through remote 'origin' failed: ")
    assert "SECRET" not in outcome.error


@pytest.mark.parametrize("reason", [None, "", "unspecified"])
def test_recorder_refuses_a_hand_built_rejection_without_a_reason(tmp_path: Path, reason: str | None) -> None:
    from sandbox_executor.flow.review.resolver import DispositionRecorder
    from sandbox_executor.review_report import FindingDisposition

    ctx = _ensemble_ctx(tmp_path)
    exec_file = str(tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl")
    recorder = DispositionRecorder(ctx, exec_file, EXEC_TARGET, {}, set(), [])
    disp = FindingDisposition("R1-F1", "nit", "src/app.py", 3, "A nit.", "Fix it.", "rejected", reason)

    recorder.record(3, [disp])

    assert _rows(tmp_path, "ACT-RES") == []
    assert recorder.recorded_keys == set()


def _resolver_turn(
    tmp_path: Path, returncode: int, stdout: str = "", file_payload: object | None = None, breach: object = None
) -> list:
    from sandbox_executor.entrypoint.review_loop import _run_resolver_agent
    from sandbox_executor.turns import TurnResult

    dispositions_path = tmp_path / "iter-3" / "dispositions.json"

    class _Runner:
        def build_command(self, prompt: str, model: str) -> list[str]:
            return ["fake-agent", prompt]

    def fake_run_turn(cmd: list[str], cwd: str | None = None, **kwargs: object) -> TurnResult:
        if file_payload is not None:
            dispositions_path.write_text(json.dumps(file_payload), encoding="utf-8")
        return TurnResult(returncode=returncode, stdout=stdout, breach=breach)

    with (
        patch("sandbox_executor.entrypoint.review_loop.get_runner", return_value=_Runner()),
        patch("sandbox_executor.entrypoint.review_loop.run_turn", side_effect=fake_run_turn),
    ):
        return _run_resolver_agent(str(tmp_path), "fake-agent", "m", "prompt", str(dispositions_path))


def test_a_resolver_turn_on_a_session_limit_fails_and_names_the_usage_limit(tmp_path: Path) -> None:
    from sandbox_executor.entrypoint.review_loop import ResolverTurnError

    with pytest.raises(ResolverTurnError, match="usage limit reached") as caught:
        _resolver_turn(tmp_path, 1, stdout="You've hit your session limit")

    assert "session limit" in caught.value.output_tail


def test_a_non_zero_resolver_turn_ignores_the_dispositions_file_it_left(tmp_path: Path) -> None:
    from sandbox_executor.entrypoint.review_loop import ResolverTurnError

    left = [{"id": "R1-F1", "disposition": "rejected", "rejection_reason": "inaccurate"}]
    with pytest.raises(ResolverTurnError, match="exited with code 1"):
        _resolver_turn(tmp_path, 1, file_payload=left)


def test_a_clean_resolver_turn_without_a_dispositions_file_has_no_usable_output(tmp_path: Path) -> None:
    from sandbox_executor.entrypoint.review_loop import ResolverTurnError

    with pytest.raises(ResolverTurnError, match="no usable output"):
        _resolver_turn(tmp_path, 0)


def test_a_timed_out_resolver_turn_that_exits_zero_ignores_the_dispositions_it_left(tmp_path: Path) -> None:
    from sandbox_executor.budgets import BudgetBreach
    from sandbox_executor.entrypoint.review_loop import ResolverTurnError

    left = [{"id": "R1-F1", "disposition": "rejected", "rejection_reason": "inaccurate"}]
    breach = BudgetBreach("review", "turn", 600.0, 601.0)
    with pytest.raises(ResolverTurnError, match="timed out"):
        _resolver_turn(tmp_path, 0, file_payload=left, breach=breach)


def test_a_clean_resolver_turn_returns_the_dispositions_it_wrote(tmp_path: Path) -> None:
    written = [{"id": "R1-F1", "disposition": "applied"}]

    assert _resolver_turn(tmp_path, 0, file_payload={"dispositions": written}) == written


# why: reproduces the PR #203 incident, a resolver on a usage limit leaving reviewer 1's two nits open
SESSION_LIMIT = "You've hit your session limit"


def _two_nit_reviewer() -> ReviewerResult:
    from sandbox_executor.review_ensemble import ParsedTrailer, ReviewCounts, ReviewFinding

    nits = (
        ReviewFinding("nit", "flow/calibrate_rerun.py", 40, "approval_pr_number resolves outside the try"),
        ReviewFinding("nit", "flow/calibrate_rerun.py", 90, "the refreshed StageResult is not stored back"),
    )
    parsed = ParsedTrailer(ReviewerVerdict.CHANGES_REQUESTED, ReviewCounts(nit=2), nits)
    return ReviewerResult.completed(1, "fake-agent", "fake-model", "r1.md", "report", parsed)


def _adjudication_rows(tmp_path: Path) -> list[dict]:
    return [r for r in _rows(tmp_path, "ACT-RES") if r.get("review_finding_ids")]


def _incident_loop(tmp_path: Path, resolver) -> dict:
    ensemble = FakeEnsemble([_two_nit_reviewer(), clean_reviewer(2), clean_reviewer(3)])
    with _LoopStubs():
        return run_review_loop(
            _ensemble_ctx(tmp_path),
            target=EXEC_TARGET,
            max_iterations=3,
            ensemble_size=3,
            run_ensemble_fn=ensemble,
            run_resolver_fn=resolver,
        )


@pytest.mark.usefixtures("in_simulated_sandbox")
def test_incident_resolver_on_a_usage_limit_leaves_both_nits_open_and_writes_no_adjudication(tmp_path: Path) -> None:
    def usage_limited(target, iteration, findings, action_ids, rejected_findings):
        return {"dispositions": [], "exit_code": 1, "error": SESSION_LIMIT, "output_tail": SESSION_LIMIT}

    res = _incident_loop(tmp_path, usage_limited)

    assert "session limit" in res["resolver_error"]
    assert _adjudication_rows(tmp_path) == []
    assert [f["id"] for f in res["open_findings"]] == ["R1-F1", "R1-F2"]
    assert all(f["path"] == "flow/calibrate_rerun.py" for f in res["open_findings"])
    assert res["consensus"]["approved"] is False


@pytest.mark.usefixtures("in_simulated_sandbox")
def test_incident_resolver_that_raises_leaves_both_nits_open_and_writes_no_adjudication(tmp_path: Path) -> None:
    def raising(target, iteration, findings, action_ids, rejected_findings):
        raise RuntimeError(SESSION_LIMIT)

    res = _incident_loop(tmp_path, raising)

    assert "session limit" in res["resolver_error"]
    assert _adjudication_rows(tmp_path) == []
    assert [f["id"] for f in res["open_findings"]] == ["R1-F1", "R1-F2"]
    assert res["consensus"]["approved"] is False


def test_host_dispatched_resolver_exiting_one_writes_no_adjudication_row(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    res = _run_host_loop(tmp_path, monkeypatch, _resolve_dispatcher(1, None, calls))

    assert res["resolver_error"]
    assert _adjudication_rows(tmp_path) == []
    assert [f["id"] for f in res["open_findings"]] == ["R1-F1"]
