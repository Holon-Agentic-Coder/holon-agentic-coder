import json
import os
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from sandbox_executor import flow as flow_module
from sandbox_executor.budgets import resolve_entropy_budget
from sandbox_executor.decomposition_rule import DecompositionDecision, should_decompose
from sandbox_executor.entrypoint.executor import OVER_BUDGET_EXIT_CODE
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.intent_assumption_rules import default_assumptions_fields
from sandbox_executor.ledger import read_merged_rows, read_merged_rows_at_ref
from sandbox_executor.naming import model_code, ticker_for
from sandbox_executor.review_ensemble import (
    ParsedTrailer,
    ReviewCounts,
    ReviewerResult,
    ReviewerVerdict,
    ReviewFinding,
    render_review_trailer,
)
from sandbox_executor.review_report import ROLES_CLOSE, ROLES_OPEN
from sandbox_executor.review_target import DiffVerdict

from tests.flow.github.fake_github import FakeGitHub, merge_view

_REAL_RESOLVE_REVIEWED_TARGET = flow_module._resolve_reviewed_target


def _verified_diff():
    # why: review-loop test repositories are not real git checkouts, so the empty-diff guard would refuse them
    return patch(
        "sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 changed path(s)")
    )


@contextmanager
def stage4_halts_at_consensus() -> Iterator[MagicMock]:
    halted = StageResult(
        stage=FlowStage.REVIEW, status=StageStatus.HALTED_FOR_HUMAN, payload={"halted_for_human": True}
    )
    with patch("sandbox_executor.flow.pipeline.run_review_stage", return_value=halted) as review:
        yield review


@contextmanager
def _review_loop_gh_stubs() -> Iterator[FakeGitHub]:
    clean_merge = {"is_dirty": False, "merge_state_status": "CLEAN", "mergeable": "MERGEABLE", "source": "github_cli"}
    github = FakeGitHub(standing_view=merge_view())
    with (
        patch(
            "sandbox_executor.flow.stage_conflicts.check_pr_mergeability",
            side_effect=lambda *a, **k: dict(clean_merge),
        ),
        patch("sandbox_executor.flow.stages.review.GitHubAdapter", return_value=github),
        patch("sandbox_executor.flow.review.loop.GitHubAdapter", return_value=github),
    ):
        yield github
    assert github.methods().count("post_review") == 1


class SteppingClock:
    def __init__(self, step):
        self.step = step
        self.now = 0.0

    def __call__(self):
        self.now += self.step
        return self.now


def _configure_identity(repo: Path) -> None:
    _git(repo, "config", "user.email", "test@holon.com")
    _git(repo, "config", "user.name", "Holon Test")
    _git(repo, "config", "commit.gpgsign", "false")


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _ledger_rows(repo: Path, ref: str, name: str) -> list[dict[str, Any]]:
    return read_merged_rows_at_ref(str(repo), ref, name)


PLAN_MARKDOWN = """# Plan for {intent}

- **Plan ID:** {plan_id}

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.8   |
| entropy_pred        | 2.0   |
| impact_pred         | 50.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 35.4  |

## Step 1: Change the application

Edit src/app.py.
"""


class FakeRoleAgents:
    def __init__(  # noqa: PLR0913
        self,
        root: Path,
        origin: Path,
        planner_writes_plan: bool = True,
        executor_mode: str = "change",
        clock_offset: int = 0,
        during_dispatch: Callable[[str, list[str]], None] | None = None,
        recorded_test_pass_rate: Any = None,
        planner_records_decision: bool = True,
    ):
        self.root = root
        # why: False models an older planner image that does not record the decomposition decision on its plan row
        self.planner_records_decision = planner_records_decision
        self.recorded_test_pass_rate = recorded_test_pass_rate
        self.origin = origin
        self.planner_writes_plan = planner_writes_plan
        self.executor_mode = executor_mode
        # why: models host/container clock skew in the seconds that stamp branch names
        self.clock_offset = clock_offset
        self.during_dispatch = during_dispatch
        self.calls: list[dict[str, Any]] = []
        self.intent_payloads: list[dict[str, Any]] = []

    def __call__(self, role: str, runner: str, model: str, positional_args: list[str], **kwargs: Any) -> int:
        self.calls.append({"role": role, "runner": runner, "model": model, "args": list(positional_args), **kwargs})
        work = Path(kwargs["target_worktree"])
        handler = {"intent-creator": self._intent, "planner": self._plan, "executor": self._execute}[role]
        exit_code = handler(work, positional_args, kwargs)
        if self.during_dispatch is not None:
            self.during_dispatch(role, list(positional_args))
        return exit_code

    def now(self) -> int:
        return int(time.time()) + self.clock_offset

    def _commit(self, work: Path, message: str) -> None:
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", message)

    def _intent(self, work: Path, _args: list[str], kwargs: dict[str, Any]) -> int:
        with open(kwargs["intent_file"], encoding="utf-8") as f:
            payload = json.load(f)
        self.intent_payloads.append(payload)
        intent_branch = f"{payload['branch']}/_"
        _git(work, "checkout", "-q", "-B", intent_branch, f"origin/{payload.get('target_branch', 'main')}")
        _append_jsonl(work / "holon-knowledge/ledger/intents.jsonl", {**payload, "status": "proposed"})
        self._commit(work, f"intent: created {intent_branch}")
        return 0

    def _plan(self, work: Path, args: list[str], _kwargs: dict[str, Any]) -> int:
        intent_branch, agent, model = args
        if not self.planner_writes_plan:
            return 0
        plan_id = f"P-{self.now()}-{ticker_for(agent)}-{model_code(model)}"
        plan_branch = f"{intent_branch.removesuffix('/_')}/{plan_id}/_"
        _git(work, "checkout", "-q", "-B", plan_branch, f"refs/remotes/origin/{intent_branch}")
        plan_rel = f"plans/{plan_id}.md"
        (work / "plans").mkdir(exist_ok=True)
        plan_content = PLAN_MARKDOWN.format(intent=intent_branch, plan_id=plan_id)
        (work / plan_rel).write_text(plan_content, encoding="utf-8")
        row = {
            "plan_id": plan_id,
            "intent_branch": intent_branch,
            "agent": agent,
            "model": model,
            "p_success": 0.8,
            "entropy": 2.0,
            "impact": 50.0,
            "cost": 5.0,
            "learning_value": 2.0,
            "ev": 35.4,
            "plan_file": plan_rel,
            "status": "proposed",
        }
        _append_jsonl(work / "holon-knowledge/ledger/plans.jsonl", self._recorded(work, row, plan_content))
        self._commit(work, f"plan: {plan_id} created by {agent} ({model})")
        return 0

    def _recorded(self, work: Path, row: dict[str, Any], plan_content: str) -> dict[str, Any]:
        if not self.planner_records_decision:
            return row
        intents_path = work / "holon-knowledge/ledger/intents.jsonl"
        intents = [json.loads(line) for line in intents_path.read_text().splitlines() if line.strip()]
        return {**row, **should_decompose(row, plan_content, intents[-1] if intents else None).to_plan_fields()}

    def _execute(self, work: Path, args: list[str], _kwargs: dict[str, Any]) -> int:
        plan_branch, agent, model = args
        exec_id = f"E-{self.now()}-{ticker_for(agent)}-{model_code(model)}"
        exec_branch = f"{plan_branch.removesuffix('/_')}/{exec_id}/_"
        _git(work, "checkout", "-q", "-B", exec_branch, f"refs/remotes/origin/{plan_branch}")
        if self.executor_mode in ("change", "change_failure"):
            (work / "src" / "app.py").write_text("print('implemented')\n", encoding="utf-8")
        status = "failure" if self.executor_mode in ("agent_failure", "change_failure") else "success"
        row = {
            "entry_type": "summary",
            "execution_id": exec_id,
            "plan_branch": plan_branch,
            "agent": agent,
            "model": model,
            "status": status,
            "summary": "Plan executed successfully" if status == "success" else "Plan execution failed",
            "agent_output_truncated": False,
            "agent_output_bytes": 12,
            "ledger_revision": 1,
        }
        if self.recorded_test_pass_rate is not None:
            row["test_pass_rate"] = self.recorded_test_pass_rate
        if self.executor_mode == "empty":
            row["test_pass_rate"] = 1.0
        if self.executor_mode == "empty_no_row":
            (work / "plans").mkdir(exist_ok=True)
            (work / "plans" / f"{exec_id}_notes.md").write_text("notes\n", encoding="utf-8")
        else:
            _append_jsonl(work / "holon-knowledge/ledger/executions.jsonl", row)
        self._commit(work, f"execute: {exec_id} completed for plan {plan_branch}")
        return 0

    def roles(self) -> list[str]:
        return [c["role"] for c in self.calls]


def _dispatch_context(host: Path, agents: FakeRoleAgents, **overrides: Any) -> FlowContext:
    params: dict[str, Any] = {
        "repo_dir": str(host),
        "intent_data": {
            **default_assumptions_fields(),
            "slug": "dispatch-test",
            "goal": "Change the application",
            "labels": ["flow"],
        },
        "agent": "claude-agent",
        "model": "claude-opus-5-5",
        "dry_run": False,
        "token_reduce": False,
        "skip_calibrate": True,
        "auto_calibrate": False,
        "dispatcher": agents,
    }
    params.update(overrides)
    return FlowContext(**params)


def _push_child_branch(tmp_path: Path, origin: Path, parent_branch: str, child_id: str) -> str:
    child_branch = f"{parent_branch.removesuffix('/_')}/{child_id}/_"
    work = tmp_path / f"other-{child_id}"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    _git(work, "checkout", "-q", "-b", child_branch, f"origin/{parent_branch}")
    folder = "plans" if child_id.startswith("P-") else "src"
    (work / folder).mkdir(exist_ok=True)
    (work / folder / f"{child_id}.md").write_text("# Other run\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", f"other run {child_id}")
    _git(work, "push", "-q", "origin", child_branch)
    return child_branch


REAL_EXECUTIONS_LEDGER = Path(__file__).resolve().parents[3] / "holon-knowledge" / "ledger" / "executions.jsonl"


HISTORICAL_REVIEW_KEYS = [
    "action_id",
    "execution_id",
    "action",
    "category",
    "target",
    "outcome",
    "entry_type",
    "action_origin",
    "review_severity",
    "review_iteration",
    "review_finding_ids",
    "labels",
    "error_context",
    "created_at",
]


HISTORICAL_RESOLVER_KEYS = [
    "action_id",
    "execution_id",
    "action",
    "category",
    "target",
    "outcome",
    "entry_type",
    "action_origin",
    "review_iteration",
    "labels",
    "created_at",
]


HISTORICAL_KEYS_BY_PREFIX = {
    "ACT-REV": HISTORICAL_REVIEW_KEYS,
    "ACT-FIND": HISTORICAL_REVIEW_KEYS,
    "ACT-RES": HISTORICAL_RESOLVER_KEYS,
    "ACT-POST": HISTORICAL_RESOLVER_KEYS,
}


REVIEWER_ROW_KEYS = [
    "review_reviewer_index",
    "review_agent",
    "review_model",
    "review_status",
    "review_verdict",
    "review_counts",
]


EXECUTION_ID_PATTERNS = {
    "ACT-REV": r"^REV-ITR-\d+-\d+$",
    "ACT-FIND": r"^REV-FIND-\d+-\d+$",
    "ACT-RES": r"^REV-RES-\d+-\d+$",
    "ACT-POST": r"^REV-POST-\d+$",
}


def _ledger_fingerprint(path: Path) -> tuple[int, str]:
    import hashlib

    if not path.exists():
        return (-1, "")
    data = path.read_bytes()
    return (data.count(b"\n"), hashlib.sha256(data).hexdigest())


def seed_empty_ledger(repo: Path) -> Path:
    ledger = repo / "holon-knowledge" / "ledger" / "executions.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("", encoding="utf-8")
    return ledger


def _read_rows(ledger: Path) -> list[dict]:
    return read_merged_rows(str(ledger))


def _prefix(row: dict) -> str:
    return str(row.get("action_id", "")).rsplit("-", 2)[0]


# why: fixture commits must never depend on the host's global git identity or signing configuration
_TEST_GIT_CONFIG = [
    "-c",
    "user.name=Holon Test",
    "-c",
    "user.email=test@holon.invalid",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "tag.gpgsign=false",
]


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *_TEST_GIT_CONFIG, *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, proc.args, proc.stdout, proc.stderr)
    return proc.stdout.strip()


def _init_mergeability_repo(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "shared.txt").write_text("base line\n", encoding="utf-8")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-q", "-m", "base")

    _git(repo, "checkout", "-q", "-b", "clean-feature")
    (repo / "feature.txt").write_text("feature only\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-q", "-m", "clean feature")

    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", "conflict-feature")
    (repo / "shared.txt").write_text("feature change\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "conflicting feature")

    _git(repo, "checkout", "-q", "main")
    (repo / "shared.txt").write_text("main change\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "main change")
    return repo


class BudgetRoleAgents(FakeRoleAgents):
    def __init__(
        self,
        root: Path,
        origin: Path,
        plans: list[tuple[float, str]],
        row_budget: bool = False,
        fail_planning_for: str | None = None,
    ):
        super().__init__(root, origin)
        self.plans = list(plans)
        self.row_budget = row_budget
        self.fail_planning_for = fail_planning_for
        self.planner_calls = 0
        self.replan_feedback_seen: list[str | None] = []
        self.in_progress = False
        self.overlaps = 0
        self.executor_decision: DecompositionDecision | None = None

    def __call__(self, role: str, runner: str, model: str, positional_args: list[str], **kwargs: Any) -> int:
        if self.in_progress:
            self.overlaps += 1
        self.in_progress = True
        try:
            return super().__call__(role, runner, model, positional_args, **kwargs)
        finally:
            self.in_progress = False

    def _plan(self, work: Path, args: list[str], _kwargs: dict[str, Any]) -> int:
        intent_branch, agent, model = args
        self.replan_feedback_seen.append(os.environ.get("HOLON_REPLAN_FEEDBACK"))
        entropy, extra = self.plans[min(self.planner_calls, len(self.plans) - 1)]
        self.planner_calls += 1
        # why: a real planner run takes minutes, so plan branch names (stamped in seconds) never collide
        self.clock_offset += 1
        if self.fail_planning_for and self.fail_planning_for in intent_branch:
            return 0
        plan_id = f"P-{self.now()}-{ticker_for(agent)}-{model_code(model)}"
        plan_branch = f"{intent_branch.removesuffix('/_')}/{plan_id}/_"
        _git(work, "checkout", "-q", "-B", plan_branch, f"refs/remotes/origin/{intent_branch}")
        plan_rel = f"plans/{plan_id}.md"
        (work / "plans").mkdir(exist_ok=True)
        plan_content = PLAN_MARKDOWN.format(intent=intent_branch, plan_id=plan_id) + extra
        (work / plan_rel).write_text(plan_content, encoding="utf-8")
        row: dict[str, Any] = {
            "plan_id": plan_id,
            "intent_branch": intent_branch,
            "agent": agent,
            "model": model,
            "entropy": entropy,
            "plan_file": plan_rel,
            "status": "proposed",
        }
        if self.row_budget:
            intents = [
                json.loads(line)
                for line in (work / "holon-knowledge/ledger/intents.jsonl").read_text().splitlines()
                if line.strip()
            ]
            row["entropy_budget"] = resolve_entropy_budget({}, intents[-1] if intents else None)
        _append_jsonl(work / "holon-knowledge/ledger/plans.jsonl", self._recorded(work, row, plan_content))
        self._commit(work, f"plan: {plan_id} created by {agent} ({model})")
        return 0

    def _execute(self, work: Path, args: list[str], kwargs: dict[str, Any]) -> int:
        plan_branch = args[0]
        plan_ref = f"refs/remotes/origin/{plan_branch}"
        plan_id = plan_branch.removesuffix("/_").rsplit("/", 1)[-1]
        plans = _ledger_rows(work, plan_ref, "plans.jsonl")
        plan_row = next(r for r in reversed(plans) if r.get("plan_id") == plan_id)
        intent_rows = _ledger_rows(work, plan_ref, "intents.jsonl")
        content = _git(work, "show", f"{plan_ref}:{plan_row['plan_file']}")
        # why: mirrors the real executor, which applies the rule only to an older plan row without a decision
        decision = self.executor_decision or DecompositionDecision.from_plan_row(plan_row)
        if decision is None:
            decision = should_decompose(plan_row, content, intent_rows[-1] if intent_rows else None)
        if decision.decision == "execute":
            # why: a child cut after an earlier sibling merged must still have an effective diff
            (work / "src" / f"{plan_id.replace('-', '_')}.py").write_text(f"PLAN = {plan_id!r}\n", encoding="utf-8")
            return super()._execute(work, args, kwargs)
        agent, model = args[1], args[2]
        exec_id = f"E-{self.now()}-{ticker_for(agent)}-{model_code(model)}"
        exec_branch = f"{plan_branch.removesuffix('/_')}/{exec_id}/_"
        _git(work, "checkout", "-q", "-B", exec_branch, plan_ref)
        row: dict[str, Any] = {
            "entry_type": "summary",
            "execution_id": exec_id,
            "plan_branch": plan_branch,
            "status": decision.decision,
            "entropy": decision.entropy,
            "entropy_budget": decision.entropy_budget,
            "ledger_revision": 1,
        }
        if decision.decision == "decomposed":
            # why: the summary refers to the plan row's sub-intent list and never copies it
            row["plan_decision"] = {
                "plan_id": plan_id,
                "plan_branch": plan_branch,
                "decision": "decomposed",
                "sub_intent_count": len(decision.sub_intents),
            }
        _append_jsonl(work / "holon-knowledge/ledger/executions.jsonl", row)
        self._commit(work, f"execute: {exec_id} recorded {decision.decision}")
        return OVER_BUDGET_EXIT_CODE if decision.decision == "over_budget" else 0


SUB_INTENT_LIST = "\n## Sub-intent list\n\n- Add the parser\n- Add the CLI flag\n"


def _budget_context(host: Path, agents: FakeRoleAgents, budget: float, **intent_extra: Any) -> FlowContext:
    intent = {
        **default_assumptions_fields(),
        "slug": "budget-test",
        "goal": "Change the application",
        "labels": ["flow"],
        "entropy_budget": budget,
    }
    intent.update(intent_extra)
    return _dispatch_context(host, agents, intent_data=intent)


def _all_intent_rows(host: Path) -> list[dict[str, Any]]:
    _git(host, "fetch", "-q", "origin")
    rows: dict[str, dict[str, Any]] = {}
    for ref in _git(host, "for-each-ref", "--format=%(refname)", "refs/remotes/origin").splitlines():
        for row in _ledger_rows(host, ref, "intents.jsonl"):
            rows[row["branch"]] = row
    return list(rows.values())


def _reviewer_with_data_block() -> ReviewerResult:
    finding = ReviewFinding("important", "src/app.py", 12, "The handler drops the error.")
    parsed = ParsedTrailer(ReviewerVerdict.CHANGES_REQUESTED, ReviewCounts(important=1), (finding,))
    block = {
        "roles": [{"role": "Security", "verdict": "CHANGES", "review": "The error path leaks the token."}],
        "findings": [{"id": "SEC-7", "recommendation": "Redact the token before logging."}],
        "verified": [],
    }
    text = f"# Review\n\n{ROLES_OPEN}\n{json.dumps(block)}\n{ROLES_CLOSE}\n{render_review_trailer(parsed)}"
    return ReviewerResult.completed(1, "fake-agent", "fake-model", "r1/report.md", text, parsed)


OTHER_PLAN = "I-200-other/P-201-aaaaaaaa-claude-agent-claude-opus-5-5/_"
OTHER_HEAD = "I-200-other/P-201-aaaaaaaa-claude-agent-claude-opus-5-5/E-202-bbbbbbbb-claude-agent-claude-opus-5-5/_"


class FakeGh:
    def __init__(self, prs: list[dict[str, Any]], diffs: dict[int, list[str] | str], fail: bool = False):
        self.prs, self.diffs, self.fail = prs, diffs, fail
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        if self.fail:
            return subprocess.CompletedProcess(["gh", *args], 1, "", "HTTP 502")
        if args[:2] == ["pr", "list"]:
            return subprocess.CompletedProcess(["gh", *args], 0, json.dumps(self.prs), "")
        diff = self.diffs[int(args[2])]
        return subprocess.CompletedProcess(["gh", *args], 0, diff if isinstance(diff, str) else patch_of(diff), "")


def patch_of(paths: list[str]) -> str:
    return "".join(f"diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n+new\n" for p in paths)


def make_plan_branch(host: Path, plan_branch: str, touch_set: list[dict[str, Any]] | None) -> None:
    plan_id = plan_branch.removesuffix("/_").rsplit("/", 1)[-1]
    _git(host, "checkout", "-q", "-b", plan_branch, "main")
    row: dict[str, Any] = {"plan_id": plan_id, "intent_branch": plan_branch.split("/", 1)[0] + "/_"}
    if touch_set is not None:
        row["touch_set"] = touch_set
    with open(host / "holon-knowledge/ledger/plans.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    _git(host, "commit", "-qam", f"plan: {plan_id}")
    _git(host, "checkout", "-q", "main")


def no_worktrees(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    if args[:2] != ["worktree", "list"]:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    return subprocess.CompletedProcess(["git", *args], 0, f"worktree {cwd}\n", "")


def seed_base_files(host: Path, paths: list[str]) -> None:
    # why: admission lists base files from origin/main, so seeded files must be pushed to count as existing
    _git(host, "checkout", "-q", "main")
    for rel in paths:
        (host / rel).parent.mkdir(parents=True, exist_ok=True)
        (host / rel).write_text(f"# {rel}\n", encoding="utf-8")
    _git(host, "add", "-A")
    _git(host, "commit", "-qm", f"seed {len(paths)} base file(s)")
    _git(host, "push", "-q", "origin", "main")
