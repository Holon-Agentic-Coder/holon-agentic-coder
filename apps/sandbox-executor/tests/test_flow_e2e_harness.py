from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.e2e_harness import (
    E2EMachineryError,
    E2EModelOutputError,
    E2EPipelineEngine,
    FailureCategory,
    PrefixScopingError,
    ZeroPRGuardViolationError,
    assert_no_skip_review_flag,
    assert_refs_within_intent_prefix,
    build_e2e_intent_prefix,
    classify_attempt_failure,
    enforce_zero_pr_guard,
    is_ref_within_intent_prefix,
    normalise_ref_name,
    run_e2e_flow_test,
    run_e2e_with_retry,
    teardown_e2e_intent,
    validate_e2e_intent_prefix,
    validate_e2e_local_llm_env,
)
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.git import run_git
from sandbox_executor.ledger import LedgerRewriteError
from sandbox_executor.local_llm import LocalLLMConfigError

from tests.flow.helpers import FakeRoleAgents, _git
from tests.repo_templates import copy_template, shared_template


def _build_bare_origin_and_clone(root: Path) -> None:
    origin = root / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)

    repo = root / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
    (repo / ".git" / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n\tlogallrefupdates = true\n"
        "[user]\n\temail = test@holon.invalid\n\tname = Holon Test\n"
        "[commit]\n\tgpgsign = false\n"
        f'[remote "origin"]\n\turl = {origin}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n',
        encoding="utf-8",
    )
    ledger_dir = repo / "holon-knowledge" / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    for name in ("intents.jsonl", "plans.jsonl", "executions.jsonl"):
        (ledger_dir / name).write_text("", encoding="utf-8")
    (repo / ".gitignore").write_text(".holon/\nholon-knowledge/.index/\n", encoding="utf-8")
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "app.py").write_text("print('seed')\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed main")
    _git(repo, "push", "-q", "-u", "origin", "main")


def _build_with_kept_branch(root: Path) -> None:
    _build_bare_origin_and_clone(root)
    _git(root / "repo", "branch", "I-999999-keep/_", "main")
    _git(root / "repo", "push", "-q", "origin", "I-999999-keep/_")


_TEMPLATES = {"seeded": _build_bare_origin_and_clone, "with-kept-branch": _build_with_kept_branch}


def _create_bare_origin_and_clone(
    tmp_path: Path, factory: pytest.TempPathFactory, template: str = "seeded"
) -> tuple[Path, Path]:
    # why: built once per run and copied per test so each test mutates only its own origin.git
    root, _ = shared_template(factory, f"e2e-harness-{template}", _TEMPLATES[template])
    copy_template(root, tmp_path)
    return tmp_path / "repo", tmp_path / "origin.git"


def _local_branches(repo: Path) -> set[str]:
    out = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    return {line.strip() for line in out.splitlines() if line.strip()}


def _remote_branches(origin: Path) -> set[str]:
    out = _git(origin, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    return {line.strip() for line in out.splitlines() if line.strip()}


class FakeE2EGhRunner:
    def __init__(self, prs: list[dict[str, Any]], *, close_returncode: int = 0) -> None:
        self.prs = list(prs)
        self.close_returncode = close_returncode
        self.calls: list[list[str]] = []
        self.closed_pr_numbers: list[int] = []

    def __call__(self, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        if args[:2] == ["pr", "list"]:
            return subprocess.CompletedProcess(["gh", *args], 0, json.dumps(self.prs), "")
        if args[:2] == ["pr", "close"]:
            num = int(args[2])
            if self.close_returncode == 0:
                self.closed_pr_numbers.append(num)
            return subprocess.CompletedProcess(["gh", *args], self.close_returncode, "", "")
        return subprocess.CompletedProcess(["gh", *args], 1, "", "unsupported fake gh command")


class TestPrefixScopingAndRefusal:
    def test_validate_e2e_intent_prefix_accepts_canonical_prefixes(self) -> None:
        assert validate_e2e_intent_prefix("I-1795496321-e2e-flow") == "I-1795496321-e2e-flow"
        assert validate_e2e_intent_prefix("  I-100-a1  ") == "I-100-a1"
        assert build_e2e_intent_prefix("My E2E Slug!", epoch=123456) == "I-123456-my-e2e-slug"

    @pytest.mark.parametrize(
        "bad_prefix",
        [
            "",
            "   ",
            "main",
            "master",
            "HEAD",
            "origin",
            "*",
            "I-",
            "I-123-",
            "I-abc-slug",
            "I-123-UPPER",
            "I-123-slug/_",
            "I-123-slug/*",
            "I-123-slug/P-1-PI-M",
            "feature-branch",
        ],
    )
    def test_validate_e2e_intent_prefix_rejects_invalid_or_protected_prefixes(self, bad_prefix: str) -> None:
        with pytest.raises(PrefixScopingError):
            validate_e2e_intent_prefix(bad_prefix)

    def test_is_ref_within_intent_prefix_enforces_exact_slash_boundary(self) -> None:
        prefix = "I-123456-e2e-run"
        assert is_ref_within_intent_prefix("I-123456-e2e-run", prefix) is True
        assert is_ref_within_intent_prefix("I-123456-e2e-run/_", prefix) is True
        assert is_ref_within_intent_prefix("refs/heads/I-123456-e2e-run/_", prefix) is True
        assert is_ref_within_intent_prefix("refs/remotes/origin/I-123456-e2e-run/P-10-PI-M/_", prefix) is True
        assert is_ref_within_intent_prefix("origin/I-123456-e2e-run/P-10-PI-M/E-20-PI-M/_", prefix) is True
        assert is_ref_within_intent_prefix("I-123456-e2e-run/P-10-PI-M/E-20-PI-M/calibrated", prefix) is True
        assert is_ref_within_intent_prefix("flow-e2e-run", prefix, worktree_branch="flow-e2e-run") is True

        # why: string-prefix siblings without a '/' boundary belong to other intents and must never match
        assert is_ref_within_intent_prefix("I-123456-e2e-run-other/_", prefix) is False
        assert is_ref_within_intent_prefix("I-123456-e2e-runner/_", prefix) is False
        assert is_ref_within_intent_prefix("I-999999-unrelated/_", prefix) is False
        assert is_ref_within_intent_prefix("main", prefix) is False
        assert is_ref_within_intent_prefix("master", prefix) is False
        assert is_ref_within_intent_prefix("HEAD", prefix) is False
        assert is_ref_within_intent_prefix("flow-unrelated", prefix, worktree_branch="flow-e2e-run") is False

    def test_assert_refs_within_intent_prefix_refuses_out_of_prefix_refs(self) -> None:
        prefix = "I-123456-e2e-run"
        valid = assert_refs_within_intent_prefix(
            ["I-123456-e2e-run/_", "origin/I-123456-e2e-run/P-1-PI-M/_"],
            prefix,
        )
        assert valid == ("I-123456-e2e-run/_", "I-123456-e2e-run/P-1-PI-M/_")

        with pytest.raises(PrefixScopingError, match="outside intent prefix"):
            assert_refs_within_intent_prefix(
                ["I-123456-e2e-run/_", "I-123456-e2e-run-other/_"],
                prefix,
            )

        with pytest.raises(PrefixScopingError, match="outside intent prefix"):
            assert_refs_within_intent_prefix(["main"], prefix)

    def test_normalise_ref_name_strips_standard_git_prefixes_only(self) -> None:
        assert normalise_ref_name("refs/heads/I-10-a/_") == "I-10-a/_"
        assert normalise_ref_name("refs/remotes/origin/I-10-a/P-1-PI-M/_") == "I-10-a/P-1-PI-M/_"
        assert normalise_ref_name("origin/I-10-a/_") == "I-10-a/_"
        assert normalise_ref_name("I-10-a/_") == "I-10-a/_"


class TestTeardownAndDeletionEnumeration:
    def test_teardown_removes_scoped_worktree_and_local_remote_branches_only(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        repo, origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory)
        prefix = "I-123456-e2e-run"

        unrelated_branches = [
            "I-999999-unrelated/_",
            "I-999999-unrelated/P-1-PI-M/_",
            "I-123456-e2e-run-other/_",
        ]
        for branch in unrelated_branches:
            _git(repo, "branch", branch, "main")
        _git(repo, "push", "-q", "origin", *unrelated_branches)

        unrelated_wt = tmp_path / "flow-unrelated"
        _git(repo, "worktree", "add", "-q", "-b", "flow-unrelated", str(unrelated_wt), "main")

        disposable_wt = tmp_path / "flow-e2e-run"
        _git(repo, "worktree", "add", "-q", "-b", "flow-e2e-run", str(disposable_wt), "main")
        reviewer_wt = tmp_path / "flow-e2e-run-rev1"
        _git(repo, "worktree", "add", "-q", "--detach", str(reviewer_wt), "main")

        scoped_branches = [
            f"{prefix}/_",
            f"{prefix}/P-10-PI-M/_",
            f"{prefix}/P-10-PI-M/E-20-PI-M/_",
            f"{prefix}/P-10-PI-M/E-20-PI-M/calibrated",
        ]
        for branch in scoped_branches:
            _git(repo, "branch", branch, "main")
        _git(repo, "push", "-q", "origin", *scoped_branches)

        report = teardown_e2e_intent(
            str(repo),
            prefix,
            disposable_worktree=str(disposable_wt),
            worktree_branch="flow-e2e-run",
        )

        assert report.intent_prefix == prefix
        assert report.errors == ()
        assert str(disposable_wt.resolve()) in report.removed_worktrees
        assert str(reviewer_wt.resolve()) in report.removed_worktrees
        assert not disposable_wt.exists()
        assert not reviewer_wt.exists()

        expected_local = set(scoped_branches) | {"flow-e2e-run"}
        assert set(report.deleted_local_branches) == expected_local
        assert set(report.deleted_remote_branches) == set(scoped_branches)
        assert prefix in report.summary_line()

        remaining_local = _local_branches(repo)
        remaining_remote = _remote_branches(origin)
        for branch in expected_local:
            assert branch not in remaining_local
            assert branch not in remaining_remote

        assert unrelated_wt.is_dir()
        assert "main" in remaining_local
        assert "main" in remaining_remote
        assert "flow-unrelated" in remaining_local
        for branch in unrelated_branches:
            assert branch in remaining_local
            assert branch in remaining_remote

    def test_teardown_refuses_candidate_refs_outside_prefix_without_deleting_anything(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        repo, origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory)
        prefix = "I-123456-e2e-run"
        _git(repo, "branch", f"{prefix}/_", "main")
        _git(repo, "push", "-q", "origin", f"{prefix}/_")
        _git(repo, "branch", "I-999999-other/_", "main")
        _git(repo, "push", "-q", "origin", "I-999999-other/_")

        with pytest.raises(PrefixScopingError, match="outside intent prefix"):
            teardown_e2e_intent(
                str(repo),
                prefix,
                candidate_refs=[f"{prefix}/_", "I-999999-other/_"],
            )

        # why: prefix validation runs before any git mutation, so even the in-prefix branch survives
        assert f"{prefix}/_" in _local_branches(repo)
        assert f"{prefix}/_" in _remote_branches(origin)
        assert "I-999999-other/_" in _local_branches(repo)
        assert "I-999999-other/_" in _remote_branches(origin)

    def test_teardown_refuses_primary_repo_or_protected_worktree(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        repo, _origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory)
        with pytest.raises(PrefixScopingError, match="primary repository directory"):
            teardown_e2e_intent(str(repo), "I-123456-e2e-run", disposable_worktree=str(repo))

    def test_teardown_refuses_worktree_on_out_of_prefix_branch_even_when_dir_ends_with_slug(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        repo, _origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory)
        prefix = "I-123456-e2e-run"
        # why: the dir name ends with the slug on purpose; scoping must judge the checked-out branch, not the path
        unrelated_wt = tmp_path / "flow-e2e-run"
        _git(repo, "worktree", "add", "-q", "-b", "feature-unrelated", str(unrelated_wt), "main")

        with pytest.raises(PrefixScopingError, match="out-of-prefix branch 'feature-unrelated'"):
            teardown_e2e_intent(str(repo), prefix, disposable_worktree=str(unrelated_wt))

        assert unrelated_wt.is_dir()
        assert "feature-unrelated" in _local_branches(repo)

    def test_teardown_discovers_remote_only_branches_even_when_local_tracking_ref_exists(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        repo, origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory)
        prefix = "I-123456-e2e-run"
        tracked_branch = f"{prefix}/_"
        remote_only_branch = f"{prefix}/P-10-PI-M/_"

        _git(repo, "branch", tracked_branch, "main")
        _git(repo, "push", "-q", "origin", tracked_branch)
        _git(origin, "branch", remote_only_branch, "main")

        report = teardown_e2e_intent(str(repo), prefix)

        assert report.errors == ()
        assert set(report.deleted_remote_branches) == {tracked_branch, remote_only_branch}
        assert tracked_branch not in _remote_branches(origin)
        assert remote_only_branch not in _remote_branches(origin)

    def test_teardown_records_error_when_remote_push_delete_fails(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        repo, origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory)
        prefix = "I-123456-e2e-run"
        branch = f"{prefix}/_"
        _git(repo, "branch", branch, "main")
        _git(repo, "push", "-q", "origin", branch)

        def _failing_push_delete(
            args: list[str], cwd: str | None = None, check: bool = True
        ) -> subprocess.CompletedProcess[str]:
            if args[:3] == ["push", "origin", "--delete"]:
                return subprocess.CompletedProcess(["git", *args], 1, "", "remote rejected delete")
            return run_git(args, cwd=cwd, check=check)

        report = teardown_e2e_intent(str(repo), prefix, git_runner=_failing_push_delete)

        assert report.deleted_remote_branches == ()
        assert len(report.errors) == 1
        assert "remote rejected delete" in report.errors[0]
        assert branch in _remote_branches(origin)
        # why: dropping the tracking ref after a failed remote delete would hide the surviving remote branch
        tracking_check = run_git(["rev-parse", "--verify", f"refs/remotes/origin/{branch}"], cwd=str(repo), check=False)
        assert tracking_check.returncode == 0


class TestZeroPRGuardAndNoSkipReview:
    def test_assert_no_skip_review_flag_rejects_removed_flag_and_option(self) -> None:
        assert_no_skip_review_flag(["--agent", "pi", "--skip-calibrate"], {"skip_calibrate": True})

        with pytest.raises(ZeroPRGuardViolationError, match="--skip-review was removed"):
            assert_no_skip_review_flag(["--skip-review"])

        with pytest.raises(ZeroPRGuardViolationError, match="--skip-review was removed"):
            assert_no_skip_review_flag(["--skip-review=true"])

        with pytest.raises(ZeroPRGuardViolationError, match="skip_review"):
            assert_no_skip_review_flag(options={"skip_review": True})

    def test_enforce_zero_pr_guard_passes_when_no_prs_match_intent_prefix(self, tmp_path: Path) -> None:
        fake_gh = FakeE2EGhRunner(
            prs=[
                {
                    "number": 41,
                    "headRefName": "I-999999-unrelated/P-1-PI-M/E-2-PI-M/_",
                    "baseRefName": "main",
                    "state": "OPEN",
                },
                {
                    "number": 42,
                    "headRefName": "I-123456-e2e-run-other/P-1-PI-M/E-2-PI-M/_",
                    "baseRefName": "main",
                    "state": "OPEN",
                },
            ]
        )
        res = enforce_zero_pr_guard(str(tmp_path), "I-123456-e2e-run", gh_runner=fake_gh)
        assert res.passed is True
        assert res.matching_prs == ()
        assert res.closed_prs == ()
        assert fake_gh.closed_pr_numbers == []

    def test_enforce_zero_pr_guard_closes_stray_open_pr_and_fails(self, tmp_path: Path) -> None:
        prefix = "I-123456-e2e-run"
        fake_gh = FakeE2EGhRunner(
            prs=[
                {
                    "number": 77,
                    "headRefName": f"{prefix}/P-10-PI-M/E-20-PI-M/_",
                    "baseRefName": "main",
                    "state": "OPEN",
                },
                {
                    "number": 88,
                    "headRefName": "I-999999-unrelated/_",
                    "baseRefName": "main",
                    "state": "OPEN",
                },
            ]
        )
        with pytest.raises(ZeroPRGuardViolationError, match="#77"):
            enforce_zero_pr_guard(str(tmp_path), prefix, gh_runner=fake_gh)

        assert fake_gh.closed_pr_numbers == [77]

    def test_enforce_zero_pr_guard_fails_closed_when_gh_pr_list_exits_nonzero(self, tmp_path: Path) -> None:
        def _failing_gh(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(["gh", *args], 1, "", "HTTP 401: Bad credentials")

        with pytest.raises(E2EMachineryError, match="gh pr list failed"):
            enforce_zero_pr_guard(str(tmp_path), "I-123456-e2e-run", gh_runner=_failing_gh)

    def test_enforce_zero_pr_guard_surfaces_close_failures_in_violation_error(self, tmp_path: Path) -> None:
        prefix = "I-123456-e2e-run"
        fake_gh = FakeE2EGhRunner(
            prs=[
                {
                    "number": 77,
                    "headRefName": f"{prefix}/P-10-PI-M/E-20-PI-M/_",
                    "baseRefName": "main",
                    "state": "OPEN",
                }
            ],
            close_returncode=1,
        )
        with pytest.raises(ZeroPRGuardViolationError, match="close failure"):
            enforce_zero_pr_guard(str(tmp_path), prefix, gh_runner=fake_gh)

    def test_enforce_zero_pr_guard_skips_default_gh_for_local_bare_origin(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo, _origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory)
        gh_invoked = False

        class _BoomGitHubAdapter:
            def __init__(self, _repo_dir: str) -> None:
                self.available = True

            def gh(self, _args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
                nonlocal gh_invoked
                gh_invoked = True
                return subprocess.CompletedProcess(["gh"], 1, "", "should not be invoked for local bare origin")

        monkeypatch.setattr("sandbox_executor.e2e_guards.GitHubAdapter", _BoomGitHubAdapter)

        res = enforce_zero_pr_guard(str(repo), "I-123456-e2e-run", gh_runner=None)
        assert res.passed is True
        assert gh_invoked is False

    def test_enforce_zero_pr_guard_fails_closed_for_github_origin_with_default_runner(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo, _origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory)
        _git(repo, "remote", "set-url", "origin", "https://github.com/holon-test/example.git")

        class _FailingGitHubAdapter:
            def __init__(self, _repo_dir: str) -> None:
                self.available = True

            def gh(self, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(["gh", *args], 1, "", "HTTP 401: Bad credentials")

        monkeypatch.setattr("sandbox_executor.e2e_guards.GitHubAdapter", _FailingGitHubAdapter)

        with pytest.raises(E2EMachineryError, match="gh pr list failed"):
            enforce_zero_pr_guard(str(repo), "I-123456-e2e-run", gh_runner=None)

    def test_enforce_zero_pr_guard_fails_closed_when_gh_missing_for_github_origin(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo, _origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory)
        _git(repo, "remote", "set-url", "origin", "https://github.com/holon-test/example.git")
        monkeypatch.setattr("sandbox_executor.flow.github.adapter.shutil.which", lambda _cmd: None)

        with pytest.raises(E2EMachineryError, match="not found on PATH"):
            enforce_zero_pr_guard(str(repo), "I-123456-e2e-run", gh_runner=None)


class TestRetryClassificationAndUnconditionalTeardown:
    def test_classify_attempt_failure_separates_model_output_from_machinery(self) -> None:
        assert classify_attempt_failure(E2EModelOutputError("assertion failed")) == FailureCategory.MODEL_OUTPUT
        assert (
            classify_attempt_failure(
                stage_results={
                    "plan": StageResult(
                        stage=FlowStage.PLAN,
                        status=StageStatus.FAILED,
                        error="Plan stage failed: no planner agent produced plans/P-*.md",
                    )
                }
            )
            == FailureCategory.MODEL_OUTPUT
        )
        assert (
            classify_attempt_failure(
                stage_results={
                    "execute": StageResult(
                        stage=FlowStage.EXECUTE,
                        status=StageStatus.FAILED,
                        payload={"empty_diff": True},
                        error="Execution produced no changes outside holon-knowledge/ledger/",
                    )
                }
            )
            == FailureCategory.MODEL_OUTPUT
        )
        assert (
            classify_attempt_failure(
                stage_results={
                    "execute": StageResult(
                        stage=FlowStage.EXECUTE,
                        status=StageStatus.FAILED,
                        payload={"status": "failed", "failure_reason": "empty_diff"},
                        error="Execute stage failed: branch carries no diff against plan",
                    )
                }
            )
            == FailureCategory.MODEL_OUTPUT
        )
        assert (
            classify_attempt_failure(
                stage_results={
                    "execute": StageResult(
                        stage=FlowStage.EXECUTE,
                        status=StageStatus.FAILED,
                        payload={"touch_set_violation": ["src/extra.py"]},
                        error="the execution changed files outside the plan's touch set",
                    )
                }
            )
            == FailureCategory.MODEL_OUTPUT
        )

        assert classify_attempt_failure(E2EMachineryError("broken worktree")) == FailureCategory.MACHINERY
        assert classify_attempt_failure(ZeroPRGuardViolationError("stray PR")) == FailureCategory.MACHINERY
        assert classify_attempt_failure(PrefixScopingError("bad prefix")) == FailureCategory.MACHINERY
        assert classify_attempt_failure(LocalLLMConfigError("missing base url")) == FailureCategory.MACHINERY
        assert classify_attempt_failure(LedgerRewriteError("ledger mutated")) == FailureCategory.MACHINERY
        assert classify_attempt_failure(TypeError("unexpected type")) == FailureCategory.MACHINERY
        assert (
            classify_attempt_failure(
                stage_results={
                    "intent": StageResult(
                        stage=FlowStage.INTENT,
                        status=StageStatus.FAILED,
                        error="Intent stage failed: host push of 'I-1-a/_' through remote 'origin' failed",
                    )
                }
            )
            == FailureCategory.MACHINERY
        )

    def test_run_e2e_with_retry_retries_model_output_with_teardown_per_attempt(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        repo, origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory)
        fake_gh = FakeE2EGhRunner(prs=[])
        attempts_seen: list[str] = []

        def _flaky_model_attempt(attempt: int, intent_prefix: str) -> dict[str, Any]:
            attempts_seen.append(intent_prefix)
            branch = f"{intent_prefix}/_"
            _git(repo, "branch", branch, "main")
            _git(repo, "push", "-q", "origin", branch)
            if attempt < 3:
                raise E2EModelOutputError(f"model output assertion failed on attempt {attempt}")
            return {
                "stage_results": {
                    "intent": StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS),
                    "plan": StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS),
                    "execute": StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS),
                }
            }

        outcome = run_e2e_with_retry(
            _flaky_model_attempt,
            repo_dir=str(repo),
            base_slug="retry-model",
            max_attempts=3,
            gh_runner=fake_gh,
            epoch_fn=lambda: 1700000 + len(attempts_seen),
        )

        assert outcome.succeeded is True
        assert outcome.attempts_run == 3
        assert len(outcome.teardown_reports) == 3
        for idx, report in enumerate(outcome.teardown_reports):
            prefix = attempts_seen[idx]
            assert report.intent_prefix == prefix
            assert f"{prefix}/_" in report.deleted_local_branches
            assert f"{prefix}/_" in report.deleted_remote_branches
            assert f"{prefix}/_" not in _local_branches(repo)
            assert f"{prefix}/_" not in _remote_branches(origin)

    def test_run_e2e_with_retry_exhausts_cap_on_persistent_model_output_failure(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        repo, origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory)
        fake_gh = FakeE2EGhRunner(prs=[])
        prefixes: list[str] = []

        def _always_bad_model(attempt: int, intent_prefix: str) -> dict[str, Any]:
            prefixes.append(intent_prefix)
            _git(repo, "branch", f"{intent_prefix}/_", "main")
            _git(repo, "push", "-q", "origin", f"{intent_prefix}/_")
            raise E2EModelOutputError(f"bad model output on attempt {attempt}")

        with pytest.raises(E2EModelOutputError, match="persisted after 2 attempt"):
            run_e2e_with_retry(
                _always_bad_model,
                repo_dir=str(repo),
                base_slug="exhaust-cap",
                max_attempts=2,
                gh_runner=fake_gh,
                epoch_fn=lambda: 1800000 + len(prefixes),
            )

        assert len(prefixes) == 2
        for prefix in prefixes:
            assert f"{prefix}/_" not in _local_branches(repo)
            assert f"{prefix}/_" not in _remote_branches(origin)

    def test_run_e2e_with_retry_fails_immediately_on_machinery_error_and_still_tears_down(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        repo, origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory)
        fake_gh = FakeE2EGhRunner(prs=[])
        prefixes: list[str] = []

        def _machinery_failure(_attempt: int, intent_prefix: str) -> dict[str, Any]:
            prefixes.append(intent_prefix)
            _git(repo, "branch", f"{intent_prefix}/_", "main")
            _git(repo, "push", "-q", "origin", f"{intent_prefix}/_")
            raise E2EMachineryError("simulated docker worktree mount failure")

        with pytest.raises(E2EMachineryError, match="docker worktree mount failure"):
            run_e2e_with_retry(
                _machinery_failure,
                repo_dir=str(repo),
                base_slug="machinery-fail",
                max_attempts=3,
                gh_runner=fake_gh,
                epoch_fn=lambda: 1900000,
            )

        assert len(prefixes) == 1
        assert f"{prefixes[0]}/_" not in _local_branches(repo)
        assert f"{prefixes[0]}/_" not in _remote_branches(origin)

    def test_run_e2e_with_retry_tears_down_even_when_zero_pr_guard_raises(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        repo, origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory)
        stray_prefix = "I-2000000-stray-pr"
        fake_gh = FakeE2EGhRunner(
            prs=[
                {
                    "number": 99,
                    "headRefName": f"{stray_prefix}/P-1-PI-M/E-2-PI-M/_",
                    "baseRefName": "main",
                    "state": "OPEN",
                }
            ]
        )

        def _attempt_with_stray_pr(_attempt: int, intent_prefix: str) -> dict[str, Any]:
            _git(repo, "branch", f"{intent_prefix}/_", "main")
            _git(repo, "push", "-q", "origin", f"{intent_prefix}/_")
            return {}

        with pytest.raises(ZeroPRGuardViolationError, match="#99"):
            run_e2e_with_retry(
                _attempt_with_stray_pr,
                repo_dir=str(repo),
                base_slug="stray-pr",
                max_attempts=3,
                gh_runner=fake_gh,
                epoch_fn=lambda: 2000000,
            )

        assert fake_gh.closed_pr_numbers == [99]
        assert f"{stray_prefix}/_" not in _local_branches(repo)
        assert f"{stray_prefix}/_" not in _remote_branches(origin)

    def test_zero_pr_guard_violation_takes_precedence_over_retryable_model_output_error(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        repo, origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory)
        stray_prefix = "I-2100000-stray-retry"
        fake_gh = FakeE2EGhRunner(
            prs=[
                {
                    "number": 105,
                    "headRefName": f"{stray_prefix}/P-1-PI-M/E-2-PI-M/_",
                    "baseRefName": "main",
                    "state": "OPEN",
                }
            ]
        )
        attempts_called: list[int] = []

        def _failing_model_with_stray_pr(attempt: int, intent_prefix: str) -> dict[str, Any]:
            attempts_called.append(attempt)
            _git(repo, "branch", f"{intent_prefix}/_", "main")
            _git(repo, "push", "-q", "origin", f"{intent_prefix}/_")
            raise E2EModelOutputError("model output assertion failed")

        with pytest.raises(ZeroPRGuardViolationError, match="#105"):
            run_e2e_with_retry(
                _failing_model_with_stray_pr,
                repo_dir=str(repo),
                base_slug="stray-retry",
                max_attempts=3,
                gh_runner=fake_gh,
                epoch_fn=lambda: 2100000,
            )

        assert attempts_called == [1]
        assert fake_gh.closed_pr_numbers == [105]
        assert f"{stray_prefix}/_" not in _local_branches(repo)
        assert f"{stray_prefix}/_" not in _remote_branches(origin)

    @pytest.mark.parametrize("exc_factory", [KeyboardInterrupt, lambda: SystemExit(2)])
    def test_run_e2e_with_retry_reraises_base_exceptions_directly_after_teardown(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, exc_factory: Any
    ) -> None:
        repo, origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory)
        fake_gh = FakeE2EGhRunner(prs=[])
        created_prefixes: list[str] = []

        def _interrupted_attempt(_attempt: int, intent_prefix: str) -> dict[str, Any]:
            created_prefixes.append(intent_prefix)
            _git(repo, "branch", f"{intent_prefix}/_", "main")
            _git(repo, "push", "-q", "origin", f"{intent_prefix}/_")
            raise exc_factory()

        expected_exc_type = type(exc_factory())
        with pytest.raises(expected_exc_type):
            run_e2e_with_retry(
                _interrupted_attempt,
                repo_dir=str(repo),
                base_slug="base-exc",
                max_attempts=3,
                gh_runner=fake_gh,
                epoch_fn=lambda: 2200000,
            )

        assert len(created_prefixes) == 1
        assert f"{created_prefixes[0]}/_" not in _local_branches(repo)
        assert f"{created_prefixes[0]}/_" not in _remote_branches(origin)


class TestPrePRPipelineExecutionAndLocalLLMEnv:
    def test_e2e_pipeline_engine_runs_stages_1_to_3_and_stops_at_stage_4_pre_pr_boundary(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        repo, origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory, "with-kept-branch")
        # why: repos are copied from a shared template; a stale remote URL would tear down another test's origin
        assert _git(repo, "remote", "get-url", "origin") == str(origin)

        agents = FakeRoleAgents(tmp_path, origin, recorded_test_pass_rate=1.0)
        fake_gh = FakeE2EGhRunner(prs=[])
        verified_contexts: list[FlowContext] = []

        def _assert_flow(ctx: FlowContext, results: dict[str, StageResult]) -> None:
            verified_contexts.append(ctx)
            assert results["intent"].status == StageStatus.SUCCESS
            assert results["plan"].status == StageStatus.SUCCESS
            assert results["execute"].status == StageStatus.SUCCESS
            assert results["review"].status == StageStatus.HALTED_FOR_HUMAN
            assert results["review"].payload.get("stopped_before_pr") is True
            assert results["calibrate"].status == StageStatus.SKIPPED

        outcome = run_e2e_flow_test(
            str(repo),
            base_slug="full-pre-pr",
            max_attempts=2,
            agent="claude-agent",
            model="claude-opus-5-5",
            dispatcher=agents,
            assertion_fn=_assert_flow,
            gh_runner=fake_gh,
        )

        assert outcome.succeeded is True
        assert outcome.attempts_run == 1
        assert len(verified_contexts) == 1
        assert agents.roles() == ["intent-creator", "planner", "executor"]

        report = outcome.teardown_reports[0]
        assert len(report.removed_worktrees) == 1
        # why: flow-<slug> plus the intent, plan and execute branches locally; only the last three are pushed
        assert len(report.deleted_local_branches) >= 4
        assert len(report.deleted_remote_branches) == 3

        assert _local_branches(repo) == {"main", "I-999999-keep/_"}
        assert _remote_branches(origin) == {"main", "I-999999-keep/_"}

    def test_run_e2e_flow_test_tears_down_worktree_and_branches_when_attempt_raises(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        repo, origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory, "with-kept-branch")

        agents = FakeRoleAgents(tmp_path, origin, recorded_test_pass_rate=1.0)
        fake_gh = FakeE2EGhRunner(prs=[])
        created_worktrees: list[Path] = []

        def _failing_assertion(ctx: FlowContext, _results: dict[str, StageResult]) -> None:
            wt = Path(ctx.target_worktree)
            assert wt.is_dir()
            created_worktrees.append(wt)
            raise E2EMachineryError("simulated post-stage failure")

        with pytest.raises(E2EMachineryError, match="simulated post-stage failure"):
            run_e2e_flow_test(
                str(repo),
                base_slug="fail-teardown",
                max_attempts=2,
                agent="claude-agent",
                model="claude-opus-5-5",
                dispatcher=agents,
                assertion_fn=_failing_assertion,
                gh_runner=fake_gh,
            )

        assert len(created_worktrees) == 1
        assert not created_worktrees[0].exists()
        assert _local_branches(repo) == {"main", "I-999999-keep/_"}
        assert _remote_branches(origin) == {"main", "I-999999-keep/_"}

    def test_e2e_pipeline_engine_fails_cleanly_if_review_entered_without_execution_branch(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        repo, _origin = _create_bare_origin_and_clone(tmp_path, tmp_path_factory)
        ctx = FlowContext(target_worktree=str(repo), intent_data={"slug": "no-exec", "goal": "g"})
        engine = E2EPipelineEngine(ctx)
        res = engine.execute_stage(FlowStage.REVIEW)
        assert res.status == StageStatus.FAILED
        assert "without an active execution_branch" in (res.error or "")

    def test_validate_e2e_local_llm_env_handles_disabled_and_enabled_modes(self) -> None:
        ready, msg, envelopes = validate_e2e_local_llm_env(env={})
        assert ready is False
        assert "HOLON_LOCAL_LLM is not enabled" in msg
        assert envelopes == []

        valid_env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "http://localhost:11434/v1",
            "HOLON_LOCAL_MODELS": "qwen2.5-coder:14b",
            "HOLON_LOCAL_CONTEXT_WINDOW": "32768",
            "HOLON_CONTEXT_RESERVE_TOKENS": "4096",
            "HOLON_REASONING_MODE": "off",
        }
        ready, summary, envelopes = validate_e2e_local_llm_env(env=valid_env)
        assert ready is True
        assert len(envelopes) == 1
        assert envelopes[0].model_id == "qwen2.5-coder:14b"
        assert envelopes[0].reasoning == "off"
        assert "model=qwen2.5-coder:14b" in summary

        lan_env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "http://192.168.1.50:11434/v1",
            "HOLON_HOST_LOCAL_HOSTS": "http://192.168.1.50:11434/v1",
            "HOLON_LOCAL_MODELS": "qwen2.5-coder:14b",
            "HOLON_AGENT_PROVIDER": "custom-lan",
            "HOLON_LOCAL_CONTEXT_WINDOW": "32768",
        }
        ready_lan, _summary_lan, lan_envelopes = validate_e2e_local_llm_env(env=lan_env)
        assert ready_lan is True
        assert lan_envelopes[0].provider == "custom-lan"

        with pytest.raises(LocalLLMConfigError, match="HOLON_LOCAL_BASE_URL"):
            validate_e2e_local_llm_env(env={"HOLON_LOCAL_LLM": "1", "HOLON_LOCAL_MODELS": "m1"})
        with pytest.raises(LocalLLMConfigError, match="HOLON_LOCAL_MODELS"):
            validate_e2e_local_llm_env(
                env={"HOLON_LOCAL_LLM": "1", "HOLON_LOCAL_BASE_URL": "http://localhost:11434/v1"}
            )

    def test_validate_e2e_local_llm_env_rejects_non_allowlisted_url_without_os_environ_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # why: a valid os.environ config must be ignored when an explicit env is passed
        monkeypatch.setenv("HOLON_LOCAL_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("HOLON_LOCAL_MODELS", "env-fallback-model")
        monkeypatch.setenv("HOLON_LOCAL_CONTEXT_WINDOW", "32768")

        non_allowlisted_env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "http://192.168.1.99:11434/v1",
            "HOLON_LOCAL_MODELS": "qwen2.5-coder:14b",
            "HOLON_LOCAL_CONTEXT_WINDOW": "32768",
        }
        with pytest.raises(LocalLLMConfigError, match="neither a loopback address nor in HOLON_HOST_LOCAL_HOSTS"):
            validate_e2e_local_llm_env(env=non_allowlisted_env)

        invalid_url_env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": "not-a-valid-url",
            "HOLON_LOCAL_MODELS": "qwen2.5-coder:14b",
            "HOLON_LOCAL_CONTEXT_WINDOW": "32768",
        }
        with pytest.raises(LocalLLMConfigError, match="not a valid URL"):
            validate_e2e_local_llm_env(env=invalid_url_env)
