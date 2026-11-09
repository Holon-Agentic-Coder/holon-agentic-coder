"""Shared helpers and fakes for the flow package tests (no fixtures; those live in conftest.py)."""

import json
import os
import stat
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sandbox_executor import flow as flow_module
from sandbox_executor.budgets import resolve_entropy_budget
from sandbox_executor.decomposition_rule import DecompositionDecision, should_decompose
from sandbox_executor.entrypoint.executor import OVER_BUDGET_EXIT_CODE, _sanitize_string
from sandbox_executor.entrypoint.planner import _sanitize_model_name
from sandbox_executor.flow import FlowContext
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

from tests.flow.lookup_patch import patch_lookup_sites

_REAL_RESOLVE_REVIEWED_TARGET = flow_module._resolve_reviewed_target


def _verified_diff():
    """Patch the empty-diff guard to pass, for review-loop tests whose repository is not a real git checkout."""
    return patch(
        "sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 changed path(s)")
    )


@contextmanager
def _review_loop_gh_stubs() -> Iterator[None]:
    """Stub every review-loop helper that shells out to ``gh``, so an approving loop issues no real gh call."""
    clean_merge = {"is_dirty": False, "merge_state_status": "CLEAN", "mergeable": "MERGEABLE", "source": "github_cli"}
    local_ci = {
        "source": "local_tests",
        "total": 1,
        "passed": 1,
        "failed": 0,
        "pending": 0,
        "skipped": 0,
        "all_passed": True,
        "checks": [{"name": "local_tests", "bucket": "pass", "state": "SUCCESS"}],
    }
    with (
        patch_lookup_sites(
            "sandbox_executor.flow.review.loop.check_pr_mergeability",
            "sandbox_executor.flow.stage_conflicts.check_pr_mergeability",
            side_effect=lambda *a, **k: dict(clean_merge),
        ),
        patch(
            "sandbox_executor.flow.review.loop.probe_ci_checks",
            side_effect=lambda *a, **k: json.loads(json.dumps(local_ci)),
        ),
        patch(
            "sandbox_executor.flow.review.loop.post_pr_review_consensus",
            return_value={"dual_posted": True, "official_review": {"success": True}},
        ) as mock_post,
    ):
        yield
    mock_post.assert_called_once()


class SteppingClock:
    """Fake monotonic clock that advances by ``step`` seconds on every reading."""

    def __init__(self, step):
        self.step = step
        self.now = 0.0

    def __call__(self):
        self.now += self.step
        return self.now


# ---------------------------------------------------------------------------
# Role-agent harness: a local bare origin plus a stub dispatcher that behaves like the containers
# ---------------------------------------------------------------------------
# The shared ``_git`` fixture helper (with identity/signing overrides) is defined with the mergeability tests below.
def _configure_identity(repo: Path) -> None:
    _git(repo, "config", "user.email", "test@holon.com")
    _git(repo, "config", "user.name", "Holon Test")
    _git(repo, "config", "commit.gpgsign", "false")


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _ledger_rows(repo: Path, ref: str, name: str) -> list[dict[str, Any]]:
    content = _git(repo, "show", f"{ref}:holon-knowledge/ledger/{name}")
    return [json.loads(line) for line in content.splitlines() if line.strip()]


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
    """Recording dispatcher that simulates the intent-creator, planner and executor containers.

    Each call clones origin into a fresh directory (as the containers clone HOLON_REPO_URL), creates
    the branch the real entrypoint would create, commits and pushes. Behaviour switches let tests
    model a silent planner or an executor that produces no effective diff.
    """

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
        # The current planner image records the decomposition decision on its plan row; False models an older image.
        self.planner_records_decision = planner_records_decision
        # Written onto the executor's summary row as 'test_pass_rate' when not None (models a container test run).
        self.recorded_test_pass_rate = recorded_test_pass_rate
        self.origin = origin
        self.planner_writes_plan = planner_writes_plan
        self.executor_mode = executor_mode
        # Seconds added to the container clock that stamps branch names (models host/container skew).
        self.clock_offset = clock_offset
        # Called with (role, positional_args) while the container runs, e.g. to push a concurrent sibling.
        self.during_dispatch = during_dispatch
        self.calls: list[dict[str, Any]] = []
        self.intent_payloads: list[dict[str, Any]] = []

    def __call__(self, role: str, runner: str, model: str, positional_args: list[str], **kwargs: Any) -> int:
        self.calls.append({"role": role, "runner": runner, "model": model, "args": list(positional_args), **kwargs})
        work = self.root / f"agent-{len(self.calls)}-{role}"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(work)], check=True, capture_output=True)
        handler = {"intent-creator": self._intent, "planner": self._plan, "executor": self._execute}[role]
        exit_code = handler(work, positional_args, kwargs)
        if self.during_dispatch is not None:
            self.during_dispatch(role, list(positional_args))
        return exit_code

    def now(self) -> int:
        return int(time.time()) + self.clock_offset

    def _push(self, work: Path, branch: str, message: str) -> None:
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", message)
        _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")

    def _intent(self, work: Path, _args: list[str], kwargs: dict[str, Any]) -> int:
        with open(kwargs["intent_file"], encoding="utf-8") as f:
            payload = json.load(f)
        self.intent_payloads.append(payload)
        intent_branch = f"{payload['branch']}/_"
        _git(work, "checkout", "-q", "-B", intent_branch, f"origin/{payload.get('target_branch', 'main')}")
        _append_jsonl(work / "holon-knowledge/ledger/intents.jsonl", {**payload, "status": "proposed"})
        self._push(work, intent_branch, f"intent: created {intent_branch}")
        return 0

    def _plan(self, work: Path, args: list[str], _kwargs: dict[str, Any]) -> int:
        intent_branch, agent, model = args
        if not self.planner_writes_plan:
            return 0
        plan_id = f"P-{self.now()}-{agent}-{_sanitize_model_name(model)}"
        plan_branch = f"{intent_branch.removesuffix('/_')}/{plan_id}/_"
        _git(work, "fetch", "-q", "origin", intent_branch)
        _git(work, "checkout", "-q", "-B", plan_branch, "FETCH_HEAD")
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
        self._push(work, plan_branch, f"plan: {plan_id} created by {agent} ({model})")
        return 0

    def _recorded(self, work: Path, row: dict[str, Any], plan_content: str) -> dict[str, Any]:
        """Return the plan row as the planner image writes it: with the recorded decision unless it is an older one."""
        if not self.planner_records_decision:
            return row
        intents_path = work / "holon-knowledge/ledger/intents.jsonl"
        intents = [json.loads(line) for line in intents_path.read_text().splitlines() if line.strip()]
        return {**row, **should_decompose(row, plan_content, intents[-1] if intents else None).to_plan_fields()}

    def _execute(self, work: Path, args: list[str], _kwargs: dict[str, Any]) -> int:
        plan_branch, agent, model = args
        exec_id = f"E-{self.now()}-{_sanitize_string(agent)}-{_sanitize_string(model)}"
        exec_branch = f"{plan_branch.removesuffix('/_')}/{exec_id}/_"
        _git(work, "fetch", "-q", "origin", plan_branch)
        _git(work, "checkout", "-q", "-B", exec_branch, "FETCH_HEAD")
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
            # Model a container that claims success with perfect tests while changing nothing.
            row["test_pass_rate"] = 1.0
        if self.executor_mode == "empty_no_row":
            # Model a container that pushed only bookkeeping (no ledger summary row, no effective change).
            (work / "plans").mkdir(exist_ok=True)
            (work / "plans" / f"{exec_id}_notes.md").write_text("notes\n", encoding="utf-8")
        else:
            _append_jsonl(work / "holon-knowledge/ledger/executions.jsonl", row)
        self._push(work, exec_branch, f"execute: {exec_id} completed for plan {plan_branch}")
        return 0

    def roles(self) -> list[str]:
        return [c["role"] for c in self.calls]


def _set_user_write(root: Path, enabled: bool) -> None:
    """Add or strip the owner's write bit on every directory and file under ``root``."""
    for dirpath, _dirnames, filenames in os.walk(root):
        for path in [dirpath, *(os.path.join(dirpath, name) for name in filenames)]:
            if os.path.islink(path):
                continue
            mode = os.stat(path).st_mode
            os.chmod(path, mode | stat.S_IWUSR if enabled else mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _dispatch_context(host: Path, agents: FakeRoleAgents, **overrides: Any) -> FlowContext:
    params: dict[str, Any] = {
        "repo_dir": str(host),
        "intent_data": {"slug": "dispatch-test", "goal": "Change the application", "labels": ["flow"]},
        "agent": "claude-agent",
        "model": "claude-opus-5-5",
        "dry_run": False,
        "token_reduce": False,
        "skip_review": True,
        "skip_calibrate": True,
        "auto_calibrate": False,
        "dispatcher": agents,
    }
    params.update(overrides)
    return FlowContext(**params)


def _push_child_branch(tmp_path: Path, origin: Path, parent_branch: str, child_id: str) -> str:
    """Push '<parent>/<child_id>/_' to origin from a separate clone, as another (stale or concurrent) run would."""
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


# ---------------------------------------------------------------------------
# Stage 4 ledger provenance regression tests
# ---------------------------------------------------------------------------
REAL_EXECUTIONS_LEDGER = Path(__file__).resolve().parents[3] / "holon-knowledge" / "ledger" / "executions.jsonl"


# Historical row shapes for each Stage 4 record type, as found in the canonical ledger.
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
    """Return the line count and SHA-256 digest of a ledger file (or a sentinel if absent)."""
    import hashlib

    if not path.exists():
        return (-1, "")
    data = path.read_bytes()
    return (data.count(b"\n"), hashlib.sha256(data).hexdigest())


def _read_rows(ledger: Path) -> list[dict]:
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]


def _prefix(row: dict) -> str:
    return str(row.get("action_id", "")).rsplit("-", 2)[0]


# Identity and signing overrides so fixture commits never depend on the host's global git configuration.
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
    """Run a git command in a fixture repository and return its stripped stdout."""
    proc = subprocess.run(
        ["git", *_TEST_GIT_CONFIG, *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _init_mergeability_repo(repo: Path) -> Path:
    """Build a local repository (no origin remote) for real merge-tree checks.

    Layout:
        main:             shared.txt edited to "main change" after the branches fork.
        clean-feature:    adds feature.txt only, so it merges cleanly into main.
        conflict-feature: edits the same line of shared.txt differently, so it conflicts with main.

    The repository is left with main checked out.
    """
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


# ---------------------------------------------------------------------------
# Entropy budget handoff: Stage 3 never halts for a human, re-plans, and drives explicit sub-intent lists
# ---------------------------------------------------------------------------
class BudgetRoleAgents(FakeRoleAgents):
    """FakeRoleAgents whose planner writes chosen entropies and sections, and whose executor reads the plan row's
    recorded decision like the real entrypoint, recording ``decomposed`` and ``over_budget`` rows instead of running.

    ``plans`` is consumed one item per planner call: ``(entropy, extra_markdown)``; the last item repeats. A plan for
    an intent branch containing ``fail_planning_for`` writes nothing, so that child's Stage 2 fails.
    """

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
        # When set, the fake executor reports this decision whatever the plan row records (models a disagreement).
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
        # A real planner run takes minutes; keep each plan branch name (stamped in seconds) unique.
        self.clock_offset += 1
        if self.fail_planning_for and self.fail_planning_for in intent_branch:
            return 0
        plan_id = f"P-{self.now()}-{agent}-{_sanitize_model_name(model)}"
        plan_branch = f"{intent_branch.removesuffix('/_')}/{plan_id}/_"
        _git(work, "fetch", "-q", "origin", intent_branch)
        _git(work, "checkout", "-q", "-B", plan_branch, "FETCH_HEAD")
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
        self._push(work, plan_branch, f"plan: {plan_id} created by {agent} ({model})")
        return 0

    def _execute(self, work: Path, args: list[str], kwargs: dict[str, Any]) -> int:
        plan_branch = args[0]
        _git(work, "fetch", "-q", "origin", plan_branch)
        plan_id = plan_branch.removesuffix("/_").rsplit("/", 1)[-1]
        plans = _ledger_rows(work, "FETCH_HEAD", "plans.jsonl")
        plan_row = next(r for r in reversed(plans) if r.get("plan_id") == plan_id)
        intent_rows = _ledger_rows(work, "FETCH_HEAD", "intents.jsonl")
        content = _git(work, "show", f"FETCH_HEAD:{plan_row['plan_file']}")
        # Like the real executor: the newest plan row's recorded decision, the rule only for an older row.
        decision = self.executor_decision or DecompositionDecision.from_plan_row(plan_row)
        if decision is None:
            decision = should_decompose(plan_row, content, intent_rows[-1] if intent_rows else None)
        if decision.decision == "execute":
            return super()._execute(work, args, kwargs)
        agent, model = args[1], args[2]
        exec_id = f"E-{self.now()}-{_sanitize_string(agent)}-{_sanitize_string(model)}"
        exec_branch = f"{plan_branch.removesuffix('/_')}/{exec_id}/_"
        _git(work, "checkout", "-q", "-B", exec_branch, "FETCH_HEAD")
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
            # The summary refers to the plan row's list and never copies it.
            row["plan_decision"] = {
                "plan_id": plan_id,
                "plan_branch": plan_branch,
                "decision": "decomposed",
                "sub_intent_count": len(decision.sub_intents),
            }
        _append_jsonl(work / "holon-knowledge/ledger/executions.jsonl", row)
        self._push(work, exec_branch, f"execute: {exec_id} recorded {decision.decision}")
        # Like the real executor: a refused plan exits non-zero after pushing its row.
        return OVER_BUDGET_EXIT_CODE if decision.decision == "over_budget" else 0


SUB_INTENT_LIST = "\n## Sub-intent list\n\n- Add the parser\n- Add the CLI flag\n"


def _budget_context(host: Path, agents: FakeRoleAgents, budget: float, **intent_extra: Any) -> FlowContext:
    intent = {"slug": "budget-test", "goal": "Change the application", "labels": ["flow"], "entropy_budget": budget}
    intent.update(intent_extra)
    return _dispatch_context(host, agents, intent_data=intent)


def _all_intent_rows(host: Path) -> list[dict[str, Any]]:
    """Every intents.jsonl row on every origin branch, de-duplicated by branch."""
    _git(host, "fetch", "-q", "origin")
    rows: dict[str, dict[str, Any]] = {}
    for ref in _git(host, "for-each-ref", "--format=%(refname)", "refs/remotes/origin").splitlines():
        for row in _ledger_rows(host, ref, "intents.jsonl"):
            rows[row["branch"]] = row
    return list(rows.values())


def _reviewer_with_data_block() -> ReviewerResult:
    """A completed reviewer whose report carries the reviewer data block before its trailer."""
    finding = ReviewFinding("important", "src/app.py", 12, "The handler drops the error.")
    parsed = ParsedTrailer(ReviewerVerdict.CHANGES_REQUESTED, ReviewCounts(important=1), (finding,))
    block = {
        "roles": [{"role": "Security", "verdict": "CHANGES", "review": "The error path leaks the token."}],
        "findings": [{"id": "SEC-7", "recommendation": "Redact the token before logging."}],
        "verified": [],
    }
    text = f"# Review\n\n{ROLES_OPEN}\n{json.dumps(block)}\n{ROLES_CLOSE}\n{render_review_trailer(parsed)}"
    return ReviewerResult.completed(1, "fake-agent", "fake-model", "r1/report.md", text, parsed)
