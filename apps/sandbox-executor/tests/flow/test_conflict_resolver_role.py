"""The conflict resolver end to end through ``sync_with_target``: a fake role dispatcher on real git merges."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from sandbox_executor.flow import host_remote
from sandbox_executor.flow.conflict_resolver_attempt import ARTEFACT_DIR
from sandbox_executor.flow.conflict_resolver_role import conflict_code_resolver
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.target_sync import sync_with_target
from sandbox_executor.flow.target_sync_result import SyncBoundary, SyncRequest
from sandbox_executor.git import run_git
from sandbox_executor.ledger import read_merged_rows
from sandbox_executor.module_budget_guard import MODULE_BUDGET_CONFIG

from tests.flow.helpers import _git
from tests.flow_repo_templates import FlowCommit, build_flow_history, flow_history_copy
from tests.ledger_conflict_repos import FLOW_BRANCH

LEDGER = "holon-knowledge/ledger/executions.jsonl"
FRAGMENT = "holon-knowledge/ledger/pending/executions/I-1-x.jsonl"
APP = "src/app.py"
HAIKU, SONNET, OPUS = "claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-5-5"
Edit = Callable[[Path], None]


def _resolve(work: Path) -> None:
    (work / APP).write_text("x = 1\ny = 2\n", encoding="utf-8")


def _edit_outside(work: Path) -> None:
    _resolve(work)
    (work / "src/other.py").write_text("o = 9\n", encoding="utf-8")


class FakeDispatcher:
    """Scripted attempts: each edits the mounted worktree, then writes its verdict to the result file."""

    def __init__(self, script: list[tuple[Edit | None, str]]) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def __call__(self, role, runner, model, positional_args, **kwargs) -> int:
        work = Path(kwargs["target_worktree"])
        payload = json.loads(Path(kwargs["result_file"]).read_text(encoding="utf-8"))
        self.calls.append(
            {"role": role, "model": model, "tier": positional_args[2], "payload": payload, "app": _app(work)}
        )
        edit, verdict = self.script.pop(0)
        if edit is not None:
            edit(work)
        failing = ["tests/test_app.py::test_x"] if verdict == "tests_failed" else []
        result = {"verdict": verdict, "reason": f"{verdict} reason", "failing_tests": failing}
        Path(kwargs["result_file"]).write_text(json.dumps(result), encoding="utf-8")
        return 0

    def tiers(self) -> list[str]:
        return [call["tier"] for call in self.calls]


def _app(work: Path) -> str:
    return (work / APP).read_text(encoding="utf-8") if (work / APP).exists() else ""


BASE_FILES = {
    LEDGER: json.dumps({"execution_id": "E-base"}) + "\n",
    APP: "x = 0\n",
    "src/other.py": "o = 0\n",
    ".gitignore": "tmp/\n",
}
APP_CONFLICT = (FlowCommit("main", {APP: "y = 2\n"}, "target"), FlowCommit(FLOW_BRANCH, {APP: "x = 1\n"}, "flow"))


@pytest.fixture
def work_with(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> Callable[..., Path]:
    """Makes the flow clone with ``commits`` pushed after the app conflict: a copy of a template built once per run."""

    def make(*commits: FlowCommit, base: dict[str, str] = BASE_FILES) -> Path:
        return flow_history_copy(tmp_path_factory, tmp_path, base, (*APP_CONFLICT, *commits)).work

    return make


@pytest.fixture
def work(work_with) -> Path:
    return work_with()


@pytest.fixture
def pushes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = host_remote.push_commit

    def spy(worktree, sha, branch):
        calls.append(sha)
        return real(worktree, sha, branch)

    monkeypatch.setattr("sandbox_executor.flow.target_sync_merge.push_commit", spy)
    return calls


def _sync(work: Path, dispatcher: FakeDispatcher, agent: str = "claude-agent", intent: dict | None = None):
    context = FlowContext(
        target_worktree=str(work),
        agent=agent,
        model="claude-opus-5-5" if agent == "claude-agent" else "codex",
        intent_data=intent or {"intent_id": "I-1"},
        plan_branch="I-1-x/P-2-y/_",
        dispatcher=dispatcher,
        token_reduce=False,
    )
    request = SyncRequest(
        str(work),
        FLOW_BRANCH,
        "main",
        "execute",
        SyncBoundary.STAGE_START,
        str(work / LEDGER),
        "E-3-z",
        code_resolver=conflict_code_resolver(context),
    )
    return sync_with_target(request)


def _rows(work: Path) -> list[dict]:
    return [row for row in read_merged_rows(str(work / LEDGER)) if row.get("action") == "target_sync"]


def _merging(work: Path) -> bool:
    return run_git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=str(work), check=False).returncode == 0


def test_first_tier_success_merges_with_one_dispatch(work, pushes):
    dispatcher = FakeDispatcher([(_resolve, "resolved")])
    result = _sync(work, dispatcher)
    assert result.status == "merged" and (result.resolver_tier, result.attempts) == (HAIKU, 1)
    assert dispatcher.tiers() == [HAIKU]
    call = dispatcher.calls[0]
    assert call["role"] == "conflict-resolver" and call["payload"]["paths"] == [APP]
    assert call["payload"]["plan_path"] and call["payload"]["intent"] == {"intent_id": "I-1"}
    assert (work / APP).read_text(encoding="utf-8") == "x = 1\ny = 2\n"
    assert [(row["resolver_tier"], row["attempts"]) for row in _rows(work)] == [(HAIKU, 1)]
    assert len(pushes) == 1


def test_lint_failure_then_success_escalates_once(work, pushes):
    dispatcher = FakeDispatcher([(None, "lint_failed"), (_resolve, "resolved")])
    result = _sync(work, dispatcher)
    assert result.status == "merged" and dispatcher.tiers() == [HAIKU, SONNET] and result.attempts == 2
    assert [call["model"] for call in dispatcher.calls] == [HAIKU, SONNET]  # dispatch carries the attempt tier


def test_each_failure_kind_escalates_in_ladder_order(work, pushes):
    script = [(_resolve, "tests_failed"), (None, "cannot_decide"), (_resolve, "resolved")]
    dispatcher = FakeDispatcher(script)
    result = _sync(work, dispatcher)
    assert result.status == "merged" and dispatcher.tiers() == [HAIKU, SONNET, OPUS]
    assert "<<<<<<< " in dispatcher.calls[1]["app"]  # the tests_failed edit was reset before tier 2


def test_an_out_of_set_edit_is_reverted_and_the_next_tier_sees_markers(work, pushes):
    dispatcher = FakeDispatcher([(_edit_outside, "resolved"), (_resolve, "resolved")])
    result = _sync(work, dispatcher)
    assert result.status == "merged" and dispatcher.tiers() == [HAIKU, SONNET]
    assert "<<<<<<< " in dispatcher.calls[1]["app"]
    assert (work / "src/other.py").read_text(encoding="utf-8") == "o = 0\n"


def test_tampering_stops_after_one_dispatch(work, pushes):
    head = _git(work, "rev-parse", "HEAD")

    def commit(work: Path) -> None:
        _resolve(work)
        _git(work, "commit", "-qam", "agent commit")

    dispatcher = FakeDispatcher([(commit, "resolved"), (_resolve, "resolved")])
    result = _sync(work, dispatcher)
    assert result.status == "blocked" and dispatcher.tiers() == [HAIKU]
    assert result.test_result.startswith("tampered") and pushes == []
    assert not _merging(work) and _git(work, "rev-parse", "HEAD~1") == head  # the agent commit is gone
    assert _git(work, "diff", "--name-only", "HEAD~1", "HEAD") == FRAGMENT
    assert result.head_after == _git(work, "rev-parse", "HEAD")


def test_exhaustion_fails_closed_without_a_push(work, pushes):
    head = _git(work, "rev-parse", "HEAD")
    dispatcher = FakeDispatcher([(_resolve, "tests_failed"), (_resolve, "lint_failed"), (_resolve, "tests_failed")])
    result = _sync(work, dispatcher)
    assert result.status == "blocked" and result.error.startswith("code conflicts unresolved")
    assert dispatcher.tiers() == [HAIKU, SONNET, OPUS]
    assert not _merging(work) and pushes == []
    assert _git(work, "rev-parse", "HEAD~1") == head
    assert _git(work, "diff", "--name-only", "HEAD~1", "HEAD") == FRAGMENT
    row = _rows(work)[-1]
    assert (row["outcome"], row["resolver_tier"], row["attempts"]) == ("blocked", OPUS, 3)
    assert "tests/test_app.py::test_x" in row["test_result"] and len(row["test_result"]) <= 500
    assert len(list((work / ARTEFACT_DIR).glob("*.diff"))) == 3
    assert _git(work, "status", "--porcelain") == ""


def test_the_module_budget_file_blocks_before_any_dispatch(work_with, pushes):
    work = work_with(
        FlowCommit("main", {MODULE_BUDGET_CONFIG: "{}\n"}, "target budget"),
        FlowCommit(FLOW_BRANCH, {MODULE_BUDGET_CONFIG: '{"x": 1}\n'}, "flow budget"),
    )
    dispatcher = FakeDispatcher([])
    result = _sync(work, dispatcher)
    assert result.status == "blocked" and dispatcher.calls == []
    assert result.test_result == "module budget file in conflict set" and not _merging(work)


def test_a_one_tier_ladder_fails_closed_after_one_attempt(work, pushes):
    dispatcher = FakeDispatcher([(_resolve, "lint_failed")])
    result = _sync(work, dispatcher, agent="codex-agent")
    assert result.status == "blocked" and dispatcher.tiers() == ["gpt-5-codex"] and result.attempts == 1


def test_a_container_without_a_verdict_counts_as_agent_failure(work, pushes):
    class Silent(FakeDispatcher):
        def __call__(self, role, runner, model, positional_args, **kwargs) -> int:
            self.calls.append({"tier": positional_args[2]})
            return 1

    dispatcher = Silent([])
    result = _sync(work, dispatcher)
    assert result.status == "blocked" and dispatcher.tiers() == [HAIKU, SONNET, OPUS]
    assert result.test_result.startswith("agent_failed")


def test_resolver_test_edits_propagate_to_sync_result(tmp_path, pushes):
    files = {
        LEDGER: json.dumps({"execution_id": "E-base"}) + "\n",
        APP: "x = 0\n",
        "tests/test_app.py": "assert True\n",
    }
    commits = [
        FlowCommit("main", {APP: "y = 2\n", "src/target_only.py": "t = 1\n"}, "target"),
        FlowCommit(FLOW_BRANCH, {APP: "x = 1\n"}, "flow"),
    ]
    work = build_flow_history(tmp_path, {**files, ".gitignore": "tmp/\n"}, commits).work

    def resolve_with_test_edit(w: Path) -> None:
        _resolve(w)
        (w / "tests/test_app.py").write_text("assert 1 == 1\n", encoding="utf-8")

    result = _sync(work, FakeDispatcher([(resolve_with_test_edit, "resolved")]))
    assert result.status == "merged"
    assert result.code_paths == (APP,)
    assert result.resolver_test_edits == ("tests/test_app.py",)
    assert "src/target_only.py" in result.changed_paths
