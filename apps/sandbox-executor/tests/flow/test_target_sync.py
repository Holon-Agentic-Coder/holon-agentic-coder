"""The shared target sync routine against a bare ``origin`` and a flow worktree."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from sandbox_executor.flow import (
    conflict_classes,
    conflict_classes_resolve,
    host_remote,
    target_probe,
    target_sync_merge,
)
from sandbox_executor.flow.target_probe import TargetProbe
from sandbox_executor.flow.target_sync import sync_with_target
from sandbox_executor.flow.target_sync_result import CodeResolution, SyncBoundary, SyncRequest
from sandbox_executor.git import run_git
from sandbox_executor.ledger import read_merged_rows

from tests.flow.helpers import _git
from tests.flow_repo_templates import FlowCommit, FlowRepos, flow_history_copy
from tests.ledger_conflict_repos import FLOW_BRANCH, commit_on

LEDGER = "holon-knowledge/ledger/executions.jsonl"
FRAGMENT = "holon-knowledge/ledger/pending/executions/I-1-x.jsonl"
BUDGETS = Path(__file__).resolve().parents[4] / "holon-config/hygiene/module_budgets.json"
BASE_ROW = json.dumps({"execution_id": "E-base", "created_at": "2026-11-01T00:00:00Z"}) + "\n"
BASE_FILES = {LEDGER: BASE_ROW, "src/app.py": "x = 0\n"}
TARGET_MOVE = FlowCommit("main", {"docs/new.md": "n\n"}, "target")
CODE_CONFLICT = (
    FlowCommit("main", {"src/app.py": "x = 1\n"}, "target"),
    FlowCommit(FLOW_BRANCH, {"src/app.py": "x = 2\n"}, "flow"),
)


@pytest.fixture
def git_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every git argument list the routine runs, recorded while still running it."""
    calls: list[list[str]] = []

    def spy(args, *rest, **kwargs):
        calls.append(list(args))
        return run_git(args, *rest, **kwargs)

    for module in (target_probe, target_sync_merge, host_remote, conflict_classes, conflict_classes_resolve):
        monkeypatch.setattr(module, "run_git", spy)
    return calls


@pytest.fixture
def repos_with(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> Callable[..., FlowRepos]:
    """Makes the bare ``origin`` and flow clone with ``commits`` already pushed: a copy of a template built once."""

    def make(*commits: FlowCommit, base: dict[str, str] = BASE_FILES) -> FlowRepos:
        return flow_history_copy(tmp_path_factory, tmp_path, base, commits)

    return make


@pytest.fixture
def repos(repos_with) -> tuple[Path, Path]:
    copy = repos_with()
    return copy.origin, copy.work


class RecordingResolver:
    """A code resolver that records its calls and resolves (taking the target copy) only when told to."""

    def __init__(self, resolve: bool = False, error: Exception | None = None) -> None:
        self.calls: list[list[str]] = []
        self.resolve = resolve
        self.error = error

    def __call__(self, worktree: str, code_paths: list[str], probe: TargetProbe) -> CodeResolution:
        self.calls.append(list(code_paths))
        if self.error is not None:
            raise self.error
        if not self.resolve:
            return CodeResolution(resolved=False, tier="none")
        for path in code_paths:
            _git(Path(worktree), "checkout", "--theirs", "--", path)
            _git(Path(worktree), "add", "--", path)
        return CodeResolution(resolved=True, tier="fake", attempts=1, test_result="passed")


def _request(work: Path, boundary: SyncBoundary = SyncBoundary.STAGE_START, resolver=None) -> SyncRequest:
    extra = {"code_resolver": resolver} if resolver is not None else {}
    return SyncRequest(str(work), FLOW_BRANCH, "main", "execute", boundary, str(work / LEDGER), "E-3-z", **extra)


def _sync_rows(work: Path) -> list[dict]:
    return [row for row in read_merged_rows(str(work / LEDGER)) if row.get("action") == "target_sync"]


def _origin_flow_sha(work: Path) -> str:
    return _git(work, "ls-remote", "origin", f"refs/heads/{FLOW_BRANCH}").split()[0]


def test_an_unchanged_target_does_nothing(repos, git_calls):
    _, work = repos
    head = _git(work, "rev-parse", "HEAD")

    result = sync_with_target(_request(work))

    assert result.status == "unchanged"
    assert _git(work, "rev-parse", "HEAD") == head
    assert _sync_rows(work) == []
    assert not [call for call in git_calls if call[0] in ("merge", "push")]


def test_a_clean_target_move_is_deferred_at_a_turn(repos_with):
    work = repos_with(TARGET_MOVE).work
    head = _git(work, "rev-parse", "HEAD")

    result = sync_with_target(_request(work, SyncBoundary.TURN))

    assert result.status == "deferred"
    assert _git(work, "rev-parse", "HEAD") == head


def test_a_clean_target_move_is_merged_and_pushed_at_a_stage_start(repos_with, git_calls):
    work = repos_with(TARGET_MOVE).work
    before = _git(BUDGETS.parent, "hash-object", str(BUDGETS)) if BUDGETS.exists() else ""

    result = sync_with_target(_request(work))

    assert result.status == "merged"
    assert len(_git(work, "rev-list", "--parents", "-n1", "HEAD").split()) == 3
    assert _origin_flow_sha(work) == result.head_after == _git(work, "rev-parse", "HEAD")
    assert "docs/new.md" in result.changed_paths
    assert len(_sync_rows(work)) == 1
    assert not [call for call in git_calls if "rebase" in call or any(a.startswith("--force") for a in call)]
    pushes = [call for call in git_calls if call[0] == "push"]
    assert len(pushes) == 1 and not any(arg.startswith("+") for arg in pushes[0])
    after = _git(BUDGETS.parent, "hash-object", str(BUDGETS)) if BUDGETS.exists() else ""
    assert before == after


def test_a_ledger_only_conflict_merges_with_one_row_and_no_agent(repos_with):
    main_ledger = BASE_ROW + json.dumps({"execution_id": "E-main", "created_at": "2026-11-04T00:00:00Z"}) + "\n"
    flow_row = json.dumps({"execution_id": "E-flow", "created_at": "2026-11-03T00:00:00Z"}) + "\n"
    work = repos_with(
        FlowCommit("main", {LEDGER: main_ledger}, "target row"),
        FlowCommit(FLOW_BRANCH, {LEDGER: BASE_ROW + flow_row}, "flow row"),
    ).work
    resolver = RecordingResolver()

    result = sync_with_target(_request(work, resolver=resolver))

    assert result.status == "merged"
    assert resolver.calls == []
    assert result.resolutions == ((LEDGER, "ledger", "mechanical"),)
    text = (work / LEDGER).read_text(encoding="utf-8")
    assert text.startswith(main_ledger + flow_row)
    assert [row["outcome"] for row in _sync_rows(work)] == ["success"]
    assert _git(work, "status", "--porcelain") == ""


def test_a_code_conflict_with_the_default_resolver_blocks_cleanly(repos_with):
    work = repos_with(*CODE_CONFLICT).work
    head, pushed = _git(work, "rev-parse", "HEAD"), _origin_flow_sha(work)

    result = sync_with_target(_request(work))

    assert result.status == "blocked"
    assert result.code_paths == ("src/app.py",)
    assert run_git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=str(work), check=False).returncode != 0
    assert (_git(work, "rev-parse", "HEAD~1"), _origin_flow_sha(work)) == (head, pushed)
    assert result.head_after == _git(work, "rev-parse", "HEAD")
    assert _git(work, "diff", "--name-only", "HEAD~1", "HEAD") == FRAGMENT
    assert [row["outcome"] for row in _sync_rows(work)] == ["blocked"]
    assert _git(work, "status", "--porcelain") == ""


def test_a_blocked_row_that_creates_the_ledger_is_committed(repos_with):
    work = repos_with(*CODE_CONFLICT, base={"src/app.py": "x = 0\n"}).work

    result = sync_with_target(_request(work))

    assert result.status == "blocked"
    assert _git(work, "diff", "--name-only", "HEAD~1", "HEAD") == FRAGMENT
    assert [row["outcome"] for row in _sync_rows(work)] == ["blocked"]
    assert _git(work, "status", "--porcelain") == ""


def test_uncommitted_ledger_rows_block_the_sync_before_it_merges_or_commits(repos_with, git_calls):
    work = repos_with(TARGET_MOVE).work
    head = _git(work, "rev-parse", "HEAD")
    stage_row = json.dumps({"execution_id": "E-3-z", "created_at": "2026-11-05T00:00:00Z"}) + "\n"
    (work / LEDGER).write_text(BASE_ROW + stage_row, encoding="utf-8")

    result = sync_with_target(_request(work))

    assert result.status == "blocked" and "uncommitted" in result.error
    assert _git(work, "rev-parse", "HEAD") == head
    assert not [call for call in git_calls if call[0] in ("merge", "commit", "push")]
    assert (work / LEDGER).read_text(encoding="utf-8") == BASE_ROW + stage_row
    assert _sync_rows(work) == []


def test_a_sync_after_a_blocked_one_merges_a_target_that_changed_the_ledger(repos_with):
    work = repos_with(*CODE_CONFLICT).work
    assert sync_with_target(_request(work)).status == "blocked"
    target_row = json.dumps({"execution_id": "E-main", "created_at": "2026-11-04T00:00:00Z"}) + "\n"
    commit_on(work, "main", {LEDGER: BASE_ROW + target_row}, "target row")

    result = sync_with_target(_request(work, resolver=RecordingResolver(resolve=True)))

    assert result.status == "merged", result.error
    assert [row["outcome"] for row in _sync_rows(work)] == ["blocked", "success"]
    assert (work / LEDGER).read_text(encoding="utf-8").startswith(BASE_ROW + target_row)


def test_marker_like_lines_outside_the_conflicted_paths_do_not_block(repos_with):
    work = repos_with(
        FlowCommit("main", {"docs/markers.md": "<<<<<<< not a conflict\n>>>>>>> either\n"}, "target")
    ).work

    assert sync_with_target(_request(work)).status == "merged"


def test_an_injected_resolver_that_resolves_merges_with_its_tier(repos_with):
    work = repos_with(*CODE_CONFLICT).work
    resolver = RecordingResolver(resolve=True)

    result = sync_with_target(_request(work, resolver=resolver))

    assert result.status == "merged"
    assert resolver.calls == [["src/app.py"]]
    assert (result.resolver_tier, result.attempts, result.test_result) == ("fake", 1, "passed")
    assert (work / "src/app.py").read_text(encoding="utf-8") == "x = 1\n"
    assert _sync_rows(work)[0]["resolver_tier"] == "fake"


def test_a_resolver_error_aborts_the_merge_and_blocks(repos_with):
    work = repos_with(*CODE_CONFLICT).work

    result = sync_with_target(_request(work, resolver=RecordingResolver(error=RuntimeError("boom"))))

    assert result.status == "blocked" and "boom" in result.error
    assert run_git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=str(work), check=False).returncode != 0
    assert len(_sync_rows(work)) == 1
    assert _git(work, "status", "--porcelain") == ""


def test_a_push_failure_keeps_the_local_merge_and_blocks_with_one_row(tmp_path_factory, tmp_path, monkeypatch):
    # The sync reads only the error's text: a refused push and a timed-out one take the same path, each on its own copy.
    errors = [
        host_remote.HostRemoteError("push", FLOW_BRANCH, "origin", "rejected"),
        TimeoutError("push rejected: timed out"),
    ]
    for case, error in enumerate(errors):
        work = flow_history_copy(tmp_path_factory, tmp_path / f"case-{case}", BASE_FILES, [TARGET_MOVE]).work

        def refuse(*_args, error=error, **_kwargs):
            raise error

        monkeypatch.setattr(target_sync_merge, "push_commit", refuse)

        result = sync_with_target(_request(work))

        assert result.status == "blocked" and "rejected" in result.error, error
        assert result.head_after == _git(work, "rev-parse", "HEAD") != result.head_before, error
        assert len(_sync_rows(work)) == 1, error


def test_a_merged_sync_supersedes_the_latest_sync_conflict_row(repos_with):
    conflict_row = {"execution_id": "E-3-z", "status": "sync_conflict", "ledger_revision": 2}
    work = repos_with(
        FlowCommit(FLOW_BRANCH, {LEDGER: BASE_ROW + json.dumps(conflict_row) + "\n"}, "sync conflict"), TARGET_MOVE
    ).work

    sync_with_target(_request(work))

    assert _sync_rows(work)[0]["supersedes"] == "E-3-z@r2"


def test_an_error_after_the_merge_commit_adds_no_second_row(repos_with, monkeypatch):
    work = repos_with(TARGET_MOVE).work

    def crash(*_args, **_kwargs):
        raise RuntimeError("unexpected push crash")

    monkeypatch.setattr(target_sync_merge, "push_commit", crash)

    result = sync_with_target(_request(work))

    assert result.status == "blocked" and "unexpected push crash" in result.error
    assert result.head_after == _git(work, "rev-parse", "HEAD") != result.head_before
    assert len(_sync_rows(work)) == 1
    assert _git(work, "status", "--porcelain") == ""
