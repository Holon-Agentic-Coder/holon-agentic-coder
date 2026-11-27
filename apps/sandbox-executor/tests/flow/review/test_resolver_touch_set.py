from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import sandbox_executor.entrypoint.review_loop as review_loop_mod
from sandbox_executor.entrypoint.review_loop import (
    CommitOptions,
    _build_resolver_prompt,
    _commit_resolution,
    _load_resolver_touch_set,
    _worktree_changed_paths,
)
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.review.resolver import ResolverInput, _dispatch_review_resolver
from sandbox_executor.resolver_result import RESOLVER_RESULT_MARKER
from sandbox_executor.review_report import FindingDisposition, FindingRow, deserialize_finding_dispositions
from sandbox_executor.review_target import ReviewTarget


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(tmp_path: Path, branch: str = "I-1-demo/P-1/E-1/_") -> tuple[Path, str]:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", branch, str(repo)], capture_output=True, check=True)
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "outside.py").write_text("OTHER = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo, _git(repo, "rev-parse", "HEAD")


def _sample_finding(path: str = "allowed.py", finding_id: str = "R1-F1") -> FindingRow:
    return FindingRow(
        id=finding_id,
        severity="important",
        path=path,
        line=1,
        reviewers=(1,),
        summary="Fix edge case",
        recommendation="Update implementation",
    )


def _applied_disposition(path: str = "allowed.py", finding_id: str = "R1-F1") -> tuple[FindingDisposition, ...]:
    return (
        FindingDisposition(
            id=finding_id,
            severity="important",
            path=path,
            line=1,
            summary="Fix edge case",
            disposition="applied",
        ),
    )


def test_dispatch_review_resolver_writes_touch_set_from_plans_ledger(tmp_path: Path) -> None:
    exec_branch = "I-1-demo/P-1/E-1/_"
    plan_branch = "I-1-demo/P-1/_"
    repo, _ = _init_repo(tmp_path, branch=exec_branch)
    ledger_dir = repo / "holon-knowledge" / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    plan_row = {
        "plan_id": "P-1",
        "intent_branch": "I-1-demo",
        "plan_branch": plan_branch,
        "touch_set": [{"path": "allowed.py"}, {"path": "tests/test_allowed.py"}],
    }
    (ledger_dir / "plans.jsonl").write_text(json.dumps(plan_row) + "\n", encoding="utf-8")
    _git(repo, "add", "holon-knowledge/ledger/plans.jsonl")
    _git(repo, "commit", "-m", "add plan touch set")
    head_sha = _git(repo, "rev-parse", "HEAD")

    captured: dict[str, Any] = {}

    def fake_dispatch(_ctx: FlowContext, _role: str, _args: list[str], *, result_file: str) -> int:
        captured.update(json.loads(Path(result_file).read_text(encoding="utf-8")))
        Path(result_file).write_text(
            json.dumps({RESOLVER_RESULT_MARKER: True, "dispositions": [], "head_sha": head_sha}),
            encoding="utf-8",
        )
        return 0

    ctx = FlowContext(
        repo_dir=str(repo),
        target_worktree=str(repo),
        plan_branch=plan_branch,
        execution_branch=exec_branch,
        skip_push=True,
    )
    target = ReviewTarget(raw=exec_branch, head_branch=exec_branch, head_sha=head_sha)
    with patch("sandbox_executor.flow.review.resolver._dispatch_role", side_effect=fake_dispatch):
        outcome = _dispatch_review_resolver(ctx, target, 1, ResolverInput(findings=[_sample_finding()]))

    assert outcome.error is None
    assert captured["touch_set"] == ["allowed.py", "tests/test_allowed.py"]


def test_dispatch_review_resolver_falls_back_to_intent_scope_and_omits_when_empty(tmp_path: Path) -> None:
    repo, head_sha = _init_repo(tmp_path)
    target = ReviewTarget(raw="feature", head_branch="feature", head_sha=head_sha)
    captured_payloads: list[dict[str, Any]] = []

    def fake_dispatch(_ctx: FlowContext, _role: str, _args: list[str], *, result_file: str) -> int:
        captured_payloads.append(json.loads(Path(result_file).read_text(encoding="utf-8")))
        Path(result_file).write_text(
            json.dumps({RESOLVER_RESULT_MARKER: True, "dispositions": [], "head_sha": head_sha}),
            encoding="utf-8",
        )
        return 0

    scoped_ctx = FlowContext(
        repo_dir=str(repo),
        target_worktree=str(repo),
        intent_data={"scope": ["allowed.py"]},
        skip_push=True,
    )
    unscoped_ctx = FlowContext(repo_dir=str(repo), target_worktree=str(repo), intent_data={}, skip_push=True)
    with patch("sandbox_executor.flow.review.resolver._dispatch_role", side_effect=fake_dispatch):
        _dispatch_review_resolver(scoped_ctx, target, 1, ResolverInput(findings=[_sample_finding()]))
        _dispatch_review_resolver(unscoped_ctx, target, 1, ResolverInput(findings=[_sample_finding()]))

    assert captured_payloads[0]["touch_set"] == ["allowed.py"]
    assert "touch_set" not in captured_payloads[1]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"touch_set": ["./allowed.py", "tests/test_allowed.py"]}, ("allowed.py", "tests/test_allowed.py")),
        ({"touch_set": [{"path": "allowed.py"}, {"path": "pkg/*.py"}]}, ("allowed.py", "pkg/*.py")),
        ({"findings": []}, ()),
        ({"touch_set": []}, ()),
        ({"touch_set": "allowed.py"}, ()),
        ({"touch_set": [123]}, ()),
        (["not", "a", "dict"], ()),
    ],
)
def test_load_resolver_touch_set_parses_valid_and_rejects_malformed(
    tmp_path: Path, payload: Any, expected: tuple[str, ...]
) -> None:
    findings_file = tmp_path / "findings.json"
    findings_file.write_text(json.dumps(payload), encoding="utf-8")
    assert _load_resolver_touch_set(str(findings_file)) == expected


def test_load_resolver_touch_set_handles_missing_and_invalid_json(tmp_path: Path) -> None:
    assert _load_resolver_touch_set(None) == ()
    assert _load_resolver_touch_set(str(tmp_path / "missing.json")) == ()
    broken = tmp_path / "broken.json"
    broken.write_text("{not-json", encoding="utf-8")
    assert _load_resolver_touch_set(str(broken)) == ()


def test_build_resolver_prompt_renders_touch_set_and_preserves_default_shape() -> None:
    findings = [_sample_finding()]
    default_prompt = _build_resolver_prompt("/repo", "feature", 1, findings, "/tmp/disp.json")
    empty_prompt = _build_resolver_prompt("/repo", "feature", 1, findings, "/tmp/disp.json", touch_set=())

    assert default_prompt == empty_prompt
    assert "Allowed Touch Set" not in default_prompt

    scoped_prompt = _build_resolver_prompt(
        "/repo",
        "feature",
        1,
        findings,
        "/tmp/disp.json",
        touch_set=("allowed.py", "tests/test_allowed.py"),
    )
    assert "## Allowed Touch Set" in scoped_prompt
    assert "- `allowed.py`" in scoped_prompt
    assert "- `tests/test_allowed.py`" in scoped_prompt
    assert "do NOT edit the unlisted file" in scoped_prompt
    assert '"disposition": "open"' in scoped_prompt
    assert "requires out-of-touch-set edit:" in scoped_prompt


def test_external_path_open_disposition_survives_and_makes_no_commit(tmp_path: Path) -> None:
    repo, initial_head = _init_repo(tmp_path)
    finding = _sample_finding(path="outside.py", finding_id="R1-F2")
    raw_dispositions = [
        {
            "id": "R1-F2",
            "disposition": "open",
            "rejection_reason": None,
            "evidence": "requires out-of-touch-set edit: outside.py",
        }
    ]
    dispositions = deserialize_finding_dispositions(raw_dispositions, [finding])
    assert len(dispositions) == 1
    assert dispositions[0].disposition == "open"
    assert dispositions[0].evidence == "requires out-of-touch-set edit: outside.py"

    with patch("sandbox_executor.entrypoint.review_loop._verify_before_push") as verify_mock:
        result_disps, head_sha, lint_ok, tests_ok, err = _commit_resolution(
            str(repo),
            "I-1-demo/P-1/E-1/_",
            1,
            dispositions,
            CommitOptions(pre_resolver_head=initial_head, touch_set=("allowed.py",)),
        )

    assert err is None
    assert head_sha == initial_head
    assert (lint_ok, tests_ok) == (False, False)
    assert result_disps == dispositions
    assert _git(repo, "rev-parse", "HEAD") == initial_head
    verify_mock.assert_not_called()


def test_commit_resolution_refuses_out_of_set_worktree_edit_before_verify(tmp_path: Path) -> None:
    repo, initial_head = _init_repo(tmp_path)
    (repo / "outside.py").write_text("OTHER = 2\n", encoding="utf-8")
    (repo / "new_unlisted.py").write_text("NEW = 1\n", encoding="utf-8")

    with patch("sandbox_executor.entrypoint.review_loop._verify_before_push") as verify_mock:
        _, head_sha, lint_ok, tests_ok, err = _commit_resolution(
            str(repo),
            "I-1-demo/P-1/E-1/_",
            1,
            _applied_disposition(),
            CommitOptions(pre_resolver_head=initial_head, touch_set=("allowed.py",)),
        )

    assert err is not None
    assert "touch_set_violation" in err
    assert "outside.py" in err
    assert "new_unlisted.py" in err
    assert head_sha == initial_head
    assert (lint_ok, tests_ok) == (False, False)
    assert _git(repo, "rev-parse", "HEAD") == initial_head
    verify_mock.assert_not_called()


def test_commit_resolution_refuses_local_resolver_commit_outside_touch_set(tmp_path: Path) -> None:
    repo, initial_head = _init_repo(tmp_path)
    (repo / "outside.py").write_text("OTHER = 3\n", encoding="utf-8")
    _git(repo, "add", "outside.py")
    _git(repo, "commit", "-m", "rogue resolver commit")

    with patch("sandbox_executor.entrypoint.review_loop._verify_before_push") as verify_mock:
        _, head_sha, lint_ok, tests_ok, err = _commit_resolution(
            str(repo),
            "I-1-demo/P-1/E-1/_",
            1,
            _applied_disposition(),
            CommitOptions(pre_resolver_head=initial_head, touch_set=("allowed.py",)),
        )

    assert err is not None
    assert "touch_set_violation" in err
    assert "outside.py" in err
    assert head_sha == initial_head
    assert (lint_ok, tests_ok) == (False, False)
    assert _git(repo, "rev-parse", "HEAD") == initial_head
    verify_mock.assert_not_called()


def test_commit_resolution_refuses_rename_outside_touch_set(tmp_path: Path) -> None:
    repo, initial_head = _init_repo(tmp_path)
    _git(repo, "mv", "allowed.py", "moved_outside.py")

    with patch("sandbox_executor.entrypoint.review_loop._verify_before_push") as verify_mock:
        _, head_sha, lint_ok, tests_ok, err = _commit_resolution(
            str(repo),
            "I-1-demo/P-1/E-1/_",
            1,
            _applied_disposition(),
            CommitOptions(pre_resolver_head=initial_head, touch_set=("allowed.py",)),
        )

    assert err is not None
    assert "touch_set_violation" in err
    assert "moved_outside.py" in err
    assert head_sha == initial_head
    assert (lint_ok, tests_ok) == (False, False)
    verify_mock.assert_not_called()


def test_commit_resolution_allows_touch_set_and_provenance_files(tmp_path: Path) -> None:
    repo, initial_head = _init_repo(tmp_path)
    (repo / "allowed.py").write_text("VALUE = 2\n", encoding="utf-8")
    plans_dir = repo / "plans"
    plans_dir.mkdir()
    (plans_dir / "P-1.md").write_text("# Plan\n", encoding="utf-8")
    ledger_dir = repo / "holon-knowledge" / "ledger"
    ledger_dir.mkdir(parents=True)
    (ledger_dir / "notes.jsonl").write_text("{}\n", encoding="utf-8")

    with patch("sandbox_executor.entrypoint.review_loop._verify_before_push", return_value=(True, True, None)):
        updated, head_sha, lint_ok, tests_ok, err = _commit_resolution(
            str(repo),
            "I-1-demo/P-1/E-1/_",
            1,
            _applied_disposition(),
            CommitOptions(pre_resolver_head=initial_head, touch_set=("allowed.py",)),
        )

    assert err is None
    assert (lint_ok, tests_ok) == (True, True)
    assert head_sha != initial_head
    assert updated[0].commit_sha == head_sha


def test_commit_resolution_is_unrestricted_when_touch_set_is_omitted(tmp_path: Path) -> None:
    repo, initial_head = _init_repo(tmp_path)
    (repo / "outside.py").write_text("OTHER = 9\n", encoding="utf-8")

    with patch("sandbox_executor.entrypoint.review_loop._verify_before_push", return_value=(True, True, None)):
        updated, head_sha, lint_ok, tests_ok, err = _commit_resolution(
            str(repo),
            "I-1-demo/P-1/E-1/_",
            1,
            _applied_disposition(path="outside.py"),
            CommitOptions(pre_resolver_head=initial_head),
        )

    assert err is None
    assert (lint_ok, tests_ok) == (True, True)
    assert head_sha != initial_head
    assert updated[0].commit_sha == head_sha


def test_commit_resolution_allows_new_in_set_file_in_new_untracked_directory(tmp_path: Path) -> None:
    repo, initial_head = _init_repo(tmp_path)
    new_file = repo / "new_pkg" / "sub" / "new_mod.py"
    new_file.parent.mkdir(parents=True)
    new_file.write_text("NEW = 1\n", encoding="utf-8")

    with patch("sandbox_executor.entrypoint.review_loop._verify_before_push", return_value=(True, True, None)):
        updated, head_sha, lint_ok, tests_ok, err = _commit_resolution(
            str(repo),
            "I-1-demo/P-1/E-1/_",
            1,
            _applied_disposition(path="new_pkg/sub/new_mod.py"),
            CommitOptions(pre_resolver_head=initial_head, touch_set=("new_pkg/sub/new_mod.py",)),
        )

    assert err is None
    assert (lint_ok, tests_ok) == (True, True)
    assert head_sha != initial_head
    assert updated[0].commit_sha == head_sha


def test_commit_resolution_fails_closed_when_git_status_fails_for_touch_set(tmp_path: Path) -> None:
    repo, initial_head = _init_repo(tmp_path)
    (repo / "allowed.py").write_text("VALUE = 2\n", encoding="utf-8")
    real_run_cmd = review_loop_mod.run_cmd

    def fake_run_cmd(cmd: list[str], *args: Any, **kwargs: Any) -> Any:
        if cmd[:4] == ["git", "status", "--porcelain", "-z"]:
            return subprocess.CompletedProcess(cmd, returncode=128, stdout="", stderr="fatal: status failed")
        return real_run_cmd(cmd, *args, **kwargs)

    with (
        patch("sandbox_executor.entrypoint.review_loop.run_cmd", side_effect=fake_run_cmd),
        patch("sandbox_executor.entrypoint.review_loop._verify_before_push") as verify_mock,
    ):
        assert _worktree_changed_paths(str(repo)) is None
        _, head_sha, lint_ok, tests_ok, err = _commit_resolution(
            str(repo),
            "I-1-demo/P-1/E-1/_",
            1,
            _applied_disposition(),
            CommitOptions(pre_resolver_head=initial_head, touch_set=("allowed.py",)),
        )

    assert err is not None
    assert "touch_set_violation" in err
    assert head_sha == initial_head
    assert (lint_ok, tests_ok) == (False, False)
    assert _git(repo, "rev-parse", "HEAD") == initial_head
    verify_mock.assert_not_called()
