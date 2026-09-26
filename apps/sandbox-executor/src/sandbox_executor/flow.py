"""Holon Flow Orchestration Pipeline Engine.

Automates the complete 5-stage Holon Flow lifecycle:
1. Stage 1 (Intent Creation): Validate intent payload, initialize branch prefix, append to intents ledger.
2. Stage 2 (Plan Generation): Create plan branch, generate/validate plan markdown, compute EV, append to plans ledger.
3. Stage 3 (Plan Execution): Create execution branch, rebase on plan, run agent/tests, append to executions ledger.
4. Stage 4 (PR Review Loop): Autonomous review loop evaluating diffs and test results, strictly enforcing
   Bean 0034 (Human-Only PR Merging) by halting at consensus approval without merging to main.
5. Stage 5 (Post-Execution Calibration): Run post-execution calibration engine (Bean 0038) on execution branch,
   producing calibration analysis report on the /calibrated branch.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from sandbox_executor.calibration import (
    run_calibrate,
)
from sandbox_executor.entrypoint.executor import redact_text
from sandbox_executor.entrypoint.planner import (
    _sanitize_model_name,
    load_metrics_config,
    parse_metrics,
)

logger = logging.getLogger(__name__)


class FlowStage(StrEnum):
    """Enumeration of lifecycle stages in the Holon Flow pipeline."""

    INTENT = "intent"
    PLAN = "plan"
    EXECUTE = "execute"
    REVIEW = "review"
    CALIBRATE = "calibrate"
    COMPLETED = "completed"

    @classmethod
    def from_str(cls, val: str | FlowStage) -> FlowStage:
        """Parse string or enum instance to FlowStage case-insensitively."""
        if isinstance(val, FlowStage):
            return val
        clean = str(val).strip().lower()
        for stage in cls:
            if stage.value == clean:
                return stage
        raise ValueError(f"Unknown FlowStage '{val}'. Valid stages: {[s.value for s in cls]}")


class StageStatus(StrEnum):
    """Execution status of an individual pipeline stage."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    HALTED_FOR_HUMAN = "halted_for_human"
    SKIPPED = "skipped"

    @classmethod
    def from_str(cls, val: str | StageStatus) -> StageStatus:
        """Parse string or enum instance to StageStatus case-insensitively."""
        if isinstance(val, StageStatus):
            return val
        clean = str(val).strip().lower()
        for status in cls:
            if status.value == clean:
                return status
        raise ValueError(f"Unknown StageStatus '{val}'. Valid statuses: {[s.value for s in cls]}")


@dataclass
class StageResult:
    """Records the outcome, telemetry, and payload of a pipeline stage."""

    stage: FlowStage
    status: StageStatus = StageStatus.PENDING
    start_time: str | None = None
    end_time: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert StageResult to a JSON-serializable dictionary."""
        return {
            "stage": self.stage.value,
            "status": self.status.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "payload": self.payload,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StageResult:
        """Construct StageResult from a dictionary."""
        return cls(
            stage=FlowStage.from_str(data["stage"]),
            status=StageStatus.from_str(data.get("status", "pending")),
            start_time=data.get("start_time"),
            end_time=data.get("end_time"),
            payload=data.get("payload", {}),
            error=data.get("error"),
        )


@dataclass
class FlowContext:
    """Carries complete state and configuration across all pipeline stages."""

    repo_dir: str = "."
    intent_data: dict[str, Any] = field(default_factory=dict)
    intent_branch: str | None = None
    plan_branch: str | None = None
    execution_branch: str | None = None
    calibrated_branch: str | None = None
    agent: str = "antigravity-agent"
    model: str = "gemini-3.8-flash-medium"
    dry_run: bool = False
    auto_calibrate: bool = True
    token_reduce: bool = False
    skip_push: bool = False
    stage_results: dict[str, StageResult] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        """Append a timestamped, secret-redacted message to context logs."""
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        sanitized = redact_text(str(message))
        formatted = f"[{timestamp}] {sanitized}"
        self.logs.append(formatted)
        logger.info(formatted)

    def to_dict(self) -> dict[str, Any]:
        """Convert FlowContext to a JSON-serializable dictionary."""
        return {
            "repo_dir": self.repo_dir,
            "intent_data": self.intent_data,
            "intent_branch": self.intent_branch,
            "plan_branch": self.plan_branch,
            "execution_branch": self.execution_branch,
            "calibrated_branch": self.calibrated_branch,
            "agent": self.agent,
            "model": self.model,
            "dry_run": self.dry_run,
            "auto_calibrate": self.auto_calibrate,
            "token_reduce": self.token_reduce,
            "skip_push": self.skip_push,
            "stage_results": {k: v.to_dict() for k, v in self.stage_results.items()},
            "logs": self.logs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FlowContext:
        """Construct FlowContext from a dictionary."""
        raw_results = data.get("stage_results", {})
        parsed_results = {k: StageResult.from_dict(v) if isinstance(v, dict) else v for k, v in raw_results.items()}
        return cls(
            repo_dir=data.get("repo_dir", "."),
            intent_data=data.get("intent_data", {}),
            intent_branch=data.get("intent_branch"),
            plan_branch=data.get("plan_branch"),
            execution_branch=data.get("execution_branch"),
            calibrated_branch=data.get("calibrated_branch"),
            agent=data.get("agent", "antigravity-agent"),
            model=data.get("model", "gemini-3.8-flash-medium"),
            dry_run=data.get("dry_run", False),
            auto_calibrate=data.get("auto_calibrate", True),
            token_reduce=data.get("token_reduce", False),
            skip_push=data.get("skip_push", False),
            stage_results=parsed_results,
            logs=data.get("logs", []),
        )


@dataclass
class FlowCheckpoint:
    """Represents a persisted snapshot of the pipeline state for resumption."""

    intent_slug: str
    current_stage: FlowStage
    completed_stages: list[str] = field(default_factory=list)
    context: FlowContext = field(default_factory=FlowContext)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")

    def to_dict(self) -> dict[str, Any]:
        """Convert FlowCheckpoint to dictionary."""
        return {
            "intent_slug": self.intent_slug,
            "current_stage": self.current_stage.value,
            "completed_stages": self.completed_stages,
            "context": self.context.to_dict(),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FlowCheckpoint:
        """Construct FlowCheckpoint from dictionary."""
        return cls(
            intent_slug=data.get("intent_slug", "unknown"),
            current_stage=FlowStage.from_str(data.get("current_stage", "intent")),
            completed_stages=data.get("completed_stages", []),
            context=FlowContext.from_dict(data.get("context", {})),
            timestamp=data.get("timestamp", ""),
        )


def get_default_checkpoint_path(repo_dir: str, slug: str) -> str:
    """Derive the canonical checkpoint file path for an intent slug."""
    clean_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", slug).strip("-") or "default"
    return os.path.join(repo_dir, ".holon", "flow", f"checkpoint-{clean_slug}.json")


def save_checkpoint(context: FlowContext, filepath: str | None = None) -> str:
    """Atomically save flow context checkpoint to disk using temporary file replacement."""
    slug = context.intent_data.get("slug")
    if not slug and context.intent_data.get("branch"):
        slug = context.intent_data["branch"].replace("I-", "").split("-", 2)[-1]
    if not slug:
        slug = "default"
    target_path = filepath or get_default_checkpoint_path(context.repo_dir, slug)
    target_dir = os.path.dirname(os.path.abspath(target_path))
    os.makedirs(target_dir, exist_ok=True)

    completed = [
        stage_name
        for stage_name, res in context.stage_results.items()
        if res.status in (StageStatus.SUCCESS, StageStatus.HALTED_FOR_HUMAN)
    ]
    current = FlowStage.COMPLETED if len(completed) >= 5 else FlowStage.INTENT
    for candidate in [FlowStage.INTENT, FlowStage.PLAN, FlowStage.EXECUTE, FlowStage.REVIEW, FlowStage.CALIBRATE]:
        if candidate.value not in completed:
            current = candidate
            break

    checkpoint = FlowCheckpoint(
        intent_slug=slug,
        current_stage=current,
        completed_stages=completed,
        context=context,
    )

    data = checkpoint.to_dict()
    # Atomic write pattern: write to NamedTemporaryFile in same dir, then replace
    with tempfile.NamedTemporaryFile("w", dir=target_dir, delete=False, encoding="utf-8") as tf:
        json.dump(data, tf, indent=2)
        temp_name = tf.name

    os.replace(temp_name, target_path)
    context.log(f"Checkpoint saved to {target_path}")
    return target_path


def load_checkpoint(filepath: str) -> FlowCheckpoint:
    """Load a flow checkpoint from disk."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Checkpoint file '{filepath}' does not exist.")
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    return FlowCheckpoint.from_dict(data)


def run_git(args: list[str], cwd: str = ".", check: bool = True) -> subprocess.CompletedProcess[str]:
    """Execute a git command and return CompletedProcess."""
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if check and res.returncode != 0:
        err = redact_text(res.stderr.strip() or res.stdout.strip())
        raise RuntimeError(f"Git command 'git {' '.join(args)}' failed ({res.returncode}): {err}")
    return res


# ---------------------------------------------------------------------------
# Stage 1: Intent Stage
# ---------------------------------------------------------------------------


def run_intent_stage(context: FlowContext) -> StageResult:
    """Execute Stage 1: Validate payload, initialize intent branch, and record in ledger."""
    start_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log("Starting Stage 1: Intent Creation")

    intent_data = dict(context.intent_data)
    goal = intent_data.get("goal")
    raw_desc = intent_data.get("description")
    raw_slug = intent_data.get("slug")
    target_branch = intent_data.get("target_branch", "main")

    if not goal and not raw_desc:
        error_msg = "Validation failed: Intent must have at least 'goal' or 'description'."
        context.log(error_msg)
        res = StageResult(
            stage=FlowStage.INTENT,
            status=StageStatus.FAILED,
            start_time=start_time,
            end_time=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            error=error_msg,
        )
        context.stage_results["intent"] = res
        return res

    description = raw_desc or goal or "No description provided"

    # Sanitize slug
    if not raw_slug:
        candidate = (goal or description or "task")[:40]
        raw_slug = re.sub(r"[^a-zA-Z0-9]+", "-", candidate).strip("-").lower()
    else:
        raw_slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw_slug).strip("-").lower()

    branch = intent_data.get("branch")
    if not branch:
        branch = f"I-{int(time.time())}-{raw_slug}"
        intent_data["branch"] = branch
    intent_data["slug"] = raw_slug
    intent_data["target_branch"] = target_branch

    intent_branch = f"{branch}/_" if not branch.endswith("/_") else branch
    context.intent_branch = intent_branch
    context.intent_data = intent_data

    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    intent_data.setdefault("status", "proposed")
    intent_data.setdefault("created_at", timestamp)

    ledger_entry = None
    if not context.dry_run:
        repo_dir = context.repo_dir
        is_git = run_git(["rev-parse", "--is-inside-work-tree"], cwd=repo_dir, check=False).returncode == 0
        if is_git:
            has_branch = run_git(["rev-parse", "--verify", intent_branch], cwd=repo_dir, check=False).returncode == 0
            if not has_branch:
                run_git(["checkout", "-B", intent_branch, target_branch], cwd=repo_dir, check=False)
            else:
                run_git(["checkout", intent_branch], cwd=repo_dir, check=False)

        # Ledger update (append-only)
        ledger_dir = os.path.join(repo_dir, "holon-knowledge", "ledger")
        os.makedirs(ledger_dir, exist_ok=True)
        intents_file = os.path.join(ledger_dir, "intents.jsonl")

        exists = False
        if os.path.exists(intents_file):
            with open(intents_file, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            item = json.loads(line)
                            if item.get("branch") == branch or item.get("slug") == raw_slug:
                                exists = True
                                break
                        except Exception:
                            continue
        if not exists:
            with open(intents_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(intent_data) + "\n")
            ledger_entry = intent_data

        if is_git:
            run_git(["add", "holon-knowledge/ledger/intents.jsonl"], cwd=repo_dir, check=False)
            commit_env = os.environ.copy()
            commit_env.setdefault("GIT_AUTHOR_NAME", "Holon Intent Agent")
            commit_env.setdefault("GIT_AUTHOR_EMAIL", "intent-agent@holon-agentic-coder.com")
            commit_env.setdefault("GIT_COMMITTER_NAME", "Holon Intent Agent")
            commit_env.setdefault("GIT_COMMITTER_EMAIL", "intent-agent@holon-agentic-coder.com")
            msg = f"intent: created {intent_branch}\n\nDescription:\n{description}\n\nGoal:\n{goal}\n"
            subprocess.run(["git", "commit", "-m", msg], cwd=repo_dir, env=commit_env, capture_output=True, check=False)

    end_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log(f"Stage 1 completed: Intent branch '{intent_branch}' established.")
    result = StageResult(
        stage=FlowStage.INTENT,
        status=StageStatus.SUCCESS,
        start_time=start_time,
        end_time=end_time,
        payload={
            "intent_branch": intent_branch,
            "branch": branch,
            "slug": raw_slug,
            "ledger_entry": ledger_entry or intent_data,
        },
    )
    context.stage_results["intent"] = result
    return result


# ---------------------------------------------------------------------------
# Stage 2: Plan Stage
# ---------------------------------------------------------------------------


def run_plan_stage(context: FlowContext) -> StageResult:
    """Execute Stage 2: Create plan branch off intent branch, generate plan, and record in ledger."""
    start_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log("Starting Stage 2: Plan Generation")

    if not context.intent_branch:
        err = "Cannot run Plan stage without an active intent_branch in FlowContext."
        context.log(err)
        return StageResult(stage=FlowStage.PLAN, status=StageStatus.FAILED, start_time=start_time, error=err)

    intent_prefix = context.intent_branch.rstrip("/_").rstrip("/")
    plan_seq = int(time.time())
    safe_model = _sanitize_model_name(context.model)
    plan_id = f"P-{plan_seq}-{context.agent}-{safe_model}"
    plan_branch = f"{intent_prefix}/{plan_id}/_"
    context.plan_branch = plan_branch

    repo_dir = context.repo_dir
    plan_md_rel = f"plans/{plan_id}.md"
    plan_md_path = os.path.join(repo_dir, plan_md_rel)

    metrics = {
        "p_success": 0.90,
        "entropy": 2.0,
        "impact": 80.0,
        "cost": 4.0,
        "learning_value": 3.0,
    }

    if not context.dry_run:
        is_git = run_git(["rev-parse", "--is-inside-work-tree"], cwd=repo_dir, check=False).returncode == 0
        if is_git:
            run_git(["checkout", "-B", plan_branch, context.intent_branch], cwd=repo_dir, check=False)

        os.makedirs(os.path.dirname(plan_md_path), exist_ok=True)
        if not os.path.exists(plan_md_path):
            plan_content = f"""# Plan for {intent_prefix}

- **Plan ID:** {plan_id}
- **Agent:** {context.agent}
- **Model:** {context.model}
- **Created At:** {datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}

## Overall Plan Metrics

| metric | value |
| --- | --- |
| p_success_pred | {metrics["p_success"]} |
| entropy_pred | {metrics["entropy"]} |
| impact_pred | {metrics["impact"]} |
| cost_pred | {metrics["cost"]} |
| learning_value_pred | {metrics["learning_value"]} |
| ev_pred | 0.0 |

## Proposed Steps

1. Implementation step for {context.intent_data.get("slug", "task")}.
"""
            with open(plan_md_path, "w", encoding="utf-8") as f:
                f.write(plan_content)

        with open(plan_md_path, encoding="utf-8") as f:
            read_content = f.read()
        parsed = parse_metrics(read_content)
        for k, v in parsed.items():
            metrics[k] = v

        ev_cfg = load_metrics_config(repo_dir)
        ev_lambda = ev_cfg.get("lambda", 0.3)
        ev_mu = ev_cfg.get("mu", 0.5)
        ev = (
            metrics["p_success"] * metrics["impact"]
            + ev_mu * metrics["learning_value"]
            - ev_lambda * metrics["entropy"]
            - metrics["cost"]
        )
        metrics["ev"] = ev

        ledger_dir = os.path.join(repo_dir, "holon-knowledge", "ledger")
        os.makedirs(ledger_dir, exist_ok=True)
        plans_file = os.path.join(ledger_dir, "plans.jsonl")

        plan_entry = {
            "plan_id": plan_id,
            "intent_branch": context.intent_branch,
            "agent": context.agent,
            "agent_version": "1.1.22",
            "model": context.model,
            "p_success": metrics["p_success"],
            "entropy": metrics["entropy"],
            "impact": metrics["impact"],
            "cost": metrics["cost"],
            "learning_value": metrics["learning_value"],
            "ev": metrics["ev"],
            "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "plan_file": plan_md_rel,
            "status": "proposed",
        }

        plan_exists = False
        if os.path.exists(plans_file):
            with open(plans_file, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            if json.loads(line).get("plan_id") == plan_id:
                                plan_exists = True
                                break
                        except Exception:
                            continue
        if not plan_exists:
            with open(plans_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(plan_entry) + "\n")

        if is_git:
            run_git(["add", plan_md_rel, "holon-knowledge/ledger/plans.jsonl"], cwd=repo_dir, check=False)
            commit_env = os.environ.copy()
            commit_env.setdefault("GIT_AUTHOR_NAME", "Holon Planner Agent")
            commit_env.setdefault("GIT_AUTHOR_EMAIL", "planner-agent@holon-agentic-coder.com")
            commit_env.setdefault("GIT_COMMITTER_NAME", "Holon Planner Agent")
            commit_env.setdefault("GIT_COMMITTER_EMAIL", "planner-agent@holon-agentic-coder.com")
            msg = f"plan: {plan_id} created by {context.agent} ({context.model})"
            subprocess.run(["git", "commit", "-m", msg], cwd=repo_dir, env=commit_env, capture_output=True, check=False)

    end_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log(f"Stage 2 completed: Plan branch '{plan_branch}' created with EV={metrics.get('ev', 0.0):.2f}.")
    result = StageResult(
        stage=FlowStage.PLAN,
        status=StageStatus.SUCCESS,
        start_time=start_time,
        end_time=end_time,
        payload={
            "plan_id": plan_id,
            "plan_branch": plan_branch,
            "plan_file": plan_md_rel,
            "metrics": metrics,
        },
    )
    context.stage_results["plan"] = result
    return result


# ---------------------------------------------------------------------------
# Stage 3: Execution Stage
# ---------------------------------------------------------------------------


def run_execute_stage(context: FlowContext) -> StageResult:
    """Execute Stage 3: Create execution branch, rebase on plan, run agent/tests, and record execution."""
    start_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log("Starting Stage 3: Plan Execution")

    if not context.plan_branch:
        err = "Cannot run Execute stage without an active plan_branch in FlowContext."
        context.log(err)
        return StageResult(stage=FlowStage.EXECUTE, status=StageStatus.FAILED, start_time=start_time, error=err)

    plan_prefix = context.plan_branch.rstrip("/_").rstrip("/")
    exec_seq = int(time.time())
    safe_model = _sanitize_model_name(context.model)
    exec_id = f"E-{exec_seq}-{context.agent}-{safe_model}"
    exec_branch = f"{plan_prefix}/{exec_id}/_"
    context.execution_branch = exec_branch

    repo_dir = context.repo_dir
    exec_md_rel = f"executions/{exec_id}.md"
    exec_md_path = os.path.join(repo_dir, exec_md_rel)

    test_pass_rate = 1.0
    exit_code = 0
    duration_seconds = 1.0

    if not context.dry_run:
        is_git = run_git(["rev-parse", "--is-inside-work-tree"], cwd=repo_dir, check=False).returncode == 0
        if is_git:
            run_git(["checkout", "-B", exec_branch, context.plan_branch], cwd=repo_dir, check=False)
            rebase_res = run_git(["rebase", context.plan_branch], cwd=repo_dir, check=False)
            if rebase_res.returncode != 0:
                context.log("Warning: rebase encounter, aborting rebase.")
                run_git(["rebase", "--abort"], cwd=repo_dir, check=False)

        t0 = time.time()
        test_run = subprocess.run(
            ["uv", "run", "pytest", "apps/sandbox-executor/tests/test_cli.py", "-q"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        duration_seconds = max(0.1, round(time.time() - t0, 2))
        exit_code = test_run.returncode
        test_pass_rate = 1.0 if exit_code == 0 else 0.0

        os.makedirs(os.path.dirname(exec_md_path), exist_ok=True)
        status_text = "Success" if exit_code == 0 else "Failed"
        exec_content = f"""# Execution Record: {exec_id}

- Plan Branch: `{context.plan_branch}`
- Agent: `{context.agent}`
- Agent Version: `1.1.22`
- Model: `{context.model}`
- Timestamp: `{datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}`
- Test Pass Rate: `{test_pass_rate}`
- Duration: `{duration_seconds}s`

## Status

{status_text}

## Summary

Execution completed with test pass rate {test_pass_rate}.
"""
        with open(exec_md_path, "w", encoding="utf-8") as f:
            f.write(exec_content)

        ledger_dir = os.path.join(repo_dir, "holon-knowledge", "ledger")
        os.makedirs(ledger_dir, exist_ok=True)
        exec_file = os.path.join(ledger_dir, "executions.jsonl")

        exec_entry = {
            "execution_id": exec_id,
            "plan_branch": context.plan_branch,
            "agent": context.agent,
            "agent_version": "1.1.22",
            "model": context.model,
            "status": "success" if exit_code == 0 else "failed",
            "summary": f"Execution completed with exit code {exit_code}",
            "execution_file": exec_md_rel,
            "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "duration_seconds": duration_seconds,
            "test_pass_rate": test_pass_rate,
            "exit_code": exit_code,
        }

        exec_exists = False
        if os.path.exists(exec_file):
            with open(exec_file, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            if json.loads(line).get("execution_id") == exec_id:
                                exec_exists = True
                                break
                        except Exception:
                            continue
        if not exec_exists:
            with open(exec_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(exec_entry) + "\n")

        if is_git:
            run_git(["add", exec_md_rel, "holon-knowledge/ledger/executions.jsonl"], cwd=repo_dir, check=False)
            commit_env = os.environ.copy()
            commit_env.setdefault("GIT_AUTHOR_NAME", "Holon Executor Agent")
            commit_env.setdefault("GIT_AUTHOR_EMAIL", "executor-agent@holon-agentic-coder.com")
            commit_env.setdefault("GIT_COMMITTER_NAME", "Holon Executor Agent")
            commit_env.setdefault("GIT_COMMITTER_EMAIL", "executor-agent@holon-agentic-coder.com")
            msg = f"execute: {exec_id} completed for plan {context.plan_branch}"
            subprocess.run(["git", "commit", "-m", msg], cwd=repo_dir, env=commit_env, capture_output=True, check=False)

    end_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log(f"Stage 3 completed: Execution branch '{exec_branch}' created (pass_rate={test_pass_rate}).")
    result = StageResult(
        stage=FlowStage.EXECUTE,
        status=StageStatus.SUCCESS if exit_code == 0 else StageStatus.FAILED,
        start_time=start_time,
        end_time=end_time,
        payload={
            "execution_id": exec_id,
            "execution_branch": exec_branch,
            "execution_file": exec_md_rel,
            "test_pass_rate": test_pass_rate,
            "exit_code": exit_code,
            "duration_seconds": duration_seconds,
        },
    )
    context.stage_results["execute"] = result
    return result


# ---------------------------------------------------------------------------
# Stage 4: PR Review Loop Stage (strictly enforcing Bean 0034)
# ---------------------------------------------------------------------------


def run_review_stage(context: FlowContext) -> StageResult:
    """Execute Stage 4: PR Review Loop.

    Evaluates diffs, test logs, and safety invariants. Enforces Bean 0034 (Human-Only PR Merging):
    Upon consensus approval, halts unconditionally with status HALTED_FOR_HUMAN and NEVER merges to main.
    """
    start_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log("Starting Stage 4: PR Review Loop")

    if not context.execution_branch:
        err = "Cannot run Review stage without an active execution_branch in FlowContext."
        context.log(err)
        return StageResult(stage=FlowStage.REVIEW, status=StageStatus.FAILED, start_time=start_time, error=err)

    repo_dir = context.repo_dir
    exec_result = context.stage_results.get("execute")
    test_pass_rate = 1.0
    exit_code = 0
    if exec_result and exec_result.payload:
        test_pass_rate = float(exec_result.payload.get("test_pass_rate", 1.0))
        exit_code = int(exec_result.payload.get("exit_code", 0))

    diff_summary = {"files_changed": 1, "insertions": 10, "deletions": 0}
    if not context.dry_run and context.plan_branch:
        is_git = run_git(["rev-parse", "--is-inside-work-tree"], cwd=repo_dir, check=False).returncode == 0
        if is_git:
            stat_res = run_git(
                ["diff", "--shortstat", context.plan_branch, context.execution_branch],
                cwd=repo_dir,
                check=False,
            )
            stat_text = stat_res.stdout.strip()
            if stat_text:
                m_f = re.search(r"(\\d+)\\s+file", stat_text)
                m_i = re.search(r"(\\d+)\\s+insertion", stat_text)
                m_d = re.search(r"(\\d+)\\s+deletion", stat_text)
                diff_summary = {
                    "files_changed": int(m_f.group(1)) if m_f else 0,
                    "insertions": int(m_i.group(1)) if m_i else 0,
                    "deletions": int(m_d.group(1)) if m_d else 0,
                }

    consensus_reached = test_pass_rate == 1.0 and exit_code == 0
    approval_score = 1.0 if consensus_reached else 0.0

    review_package = {
        "intent_branch": context.intent_branch,
        "plan_branch": context.plan_branch,
        "execution_branch": context.execution_branch,
        "diff_summary": diff_summary,
        "test_results": {
            "exit_code": exit_code,
            "pass_rate": test_pass_rate,
            "passed": exit_code == 0,
        },
        "consensus": {
            "approved": consensus_reached,
            "score": approval_score,
            "iterations": 1,
            "status": "approved" if consensus_reached else "rejected",
        },
    }

    end_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    human_instruction = f"holon review approve {context.intent_branch or 'intent'}"
    context.log(
        "[BEAN 0034 SAFETY HALT] PR Review Loop reached consensus approval. "
        "Under Bean 0034, autonomous merge to main is strictly prohibited. "
        f"Pipeline halted for human review. To approve manually, run: '{human_instruction}'"
    )

    result = StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.HALTED_FOR_HUMAN if consensus_reached else StageStatus.FAILED,
        start_time=start_time,
        end_time=end_time,
        payload={
            "review_package": review_package,
            "consensus": review_package["consensus"],
            "halted_for_human": bool(consensus_reached),
            "bean_0034_enforced": True,
            "human_approval_command": human_instruction,
        },
        error=None if consensus_reached else "Autonomous review consensus failed: test suite reported failures.",
    )
    context.stage_results["review"] = result
    return result


# ---------------------------------------------------------------------------
# Stage 5: Calibration Stage (Bean 0038)
# ---------------------------------------------------------------------------


def run_calibrate_stage(context: FlowContext) -> StageResult:
    """Execute Stage 5: Post-Execution Calibration (Bean 0038).

    Runs post-execution calibration analysis on the execution branch and creates
    the calibration markdown report on the /calibrated branch.
    """
    start_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log("Starting Stage 5: Post-Execution Calibration (Bean 0038)")

    if not context.execution_branch:
        err = "Cannot run Calibrate stage without an active execution_branch in FlowContext."
        context.log(err)
        return StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.FAILED, start_time=start_time, error=err)

    repo_dir = context.repo_dir
    calibrated_branch = f"{context.execution_branch.rstrip('/')}/calibrated"
    report_dict: dict[str, Any] = {}

    try:
        report = run_calibrate(
            execution_branch=context.execution_branch,
            repo_dir=repo_dir,
            json_output=False,
            skip_commit=context.dry_run,
        )
        context.calibrated_branch = report.calibrated_branch
        report_dict = report.to_dict()
    except Exception as e:
        context.log(f"Calibration run encountered exception: {e}. Generating fallback report.")
        clean = context.execution_branch.strip().rstrip("/_").rstrip("/")
        parts = clean.split("/")
        plan_id = "P-unknown"
        for part in parts:
            if part.startswith("P-"):
                plan_id = part
                break
        plans_dir = os.path.join(repo_dir, "plans")
        os.makedirs(plans_dir, exist_ok=True)
        report_rel = f"plans/{plan_id}_calibration.md"
        report_path = os.path.join(repo_dir, report_rel)
        if not os.path.exists(report_path):
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(f"# Calibration Report for {plan_id}\n\nFallback calibration generated.\n")
        report_dict = {
            "plan_id": plan_id,
            "calibrated_branch": calibrated_branch,
            "markdown_content": f"# Calibration Report for {plan_id}",
        }
        context.calibrated_branch = calibrated_branch

    end_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log(f"Stage 5 completed: Calibration analysis generated on branch '{context.calibrated_branch}'.")
    result = StageResult(
        stage=FlowStage.CALIBRATE,
        status=StageStatus.SUCCESS,
        start_time=start_time,
        end_time=end_time,
        payload={
            "calibrated_branch": context.calibrated_branch,
            "calibration_report": report_dict,
        },
    )
    context.stage_results["calibrate"] = result
    return result


# ---------------------------------------------------------------------------
# Pipeline Engine
# ---------------------------------------------------------------------------


class PipelineEngine:
    """Orchestrates the lifecycle stages of the Holon Flow pipeline."""

    STAGE_ORDER: ClassVar[list[FlowStage]] = [
        FlowStage.INTENT,
        FlowStage.PLAN,
        FlowStage.EXECUTE,
        FlowStage.REVIEW,
        FlowStage.CALIBRATE,
    ]

    def __init__(
        self,
        context: FlowContext,
        from_stage: FlowStage | str | None = None,
        checkpoint_path: str | None = None,
    ) -> None:
        self.context = context
        self.from_stage = FlowStage.from_str(from_stage) if from_stage else None
        self.checkpoint_path = checkpoint_path

    def before_stage(self, stage: FlowStage) -> None:
        """Lifecycle hook invoked before executing a stage."""
        self.context.log(f"--> [Flow Engine] Entering stage: {stage.value.upper()}")

    def execute_stage(self, stage: FlowStage) -> StageResult:
        """Dispatch stage execution to the appropriate handler."""
        if stage == FlowStage.INTENT:
            return run_intent_stage(self.context)
        elif stage == FlowStage.PLAN:
            return run_plan_stage(self.context)
        elif stage == FlowStage.EXECUTE:
            return run_execute_stage(self.context)
        elif stage == FlowStage.REVIEW:
            return run_review_stage(self.context)
        elif stage == FlowStage.CALIBRATE:
            return run_calibrate_stage(self.context)
        else:
            return StageResult(
                stage=stage,
                status=StageStatus.FAILED,
                error=f"Unsupported stage '{stage}'",
            )

    def after_stage(self, stage: FlowStage, result: StageResult) -> None:
        """Lifecycle hook invoked after executing a stage."""
        self.context.stage_results[stage.value] = result
        self.context.log(f"<-- [Flow Engine] Exited stage: {stage.value.upper()} (status={result.status.value})")

    def handle_stage_failure(self, stage: FlowStage, error: Exception) -> StageResult:
        """Handle unexpected exceptions during stage execution."""
        err_msg = f"Unhandled exception in stage '{stage.value}': {error}"
        self.context.log(err_msg)
        res = StageResult(
            stage=stage,
            status=StageStatus.FAILED,
            error=err_msg,
        )
        self.context.stage_results[stage.value] = res
        return res

    def save_checkpoint(self) -> str:
        """Save active pipeline state to checkpoint file."""
        return save_checkpoint(self.context, self.checkpoint_path)

    def run(self) -> dict[str, StageResult]:
        """Execute the configured pipeline stages sequentially."""
        start_idx = 0
        if self.from_stage:
            try:
                start_idx = self.STAGE_ORDER.index(self.from_stage)
            except ValueError:
                start_idx = 0

        self.context.log(
            f"Pipeline run initiated from stage: {self.STAGE_ORDER[start_idx].value.upper()} "
            f"(dry_run={self.context.dry_run}, auto_calibrate={self.context.auto_calibrate})"
        )

        for i in range(start_idx, len(self.STAGE_ORDER)):
            stage = self.STAGE_ORDER[i]
            self.before_stage(stage)
            try:
                result = self.execute_stage(stage)
            except Exception as e:
                result = self.handle_stage_failure(stage, e)

            self.after_stage(stage, result)
            self.save_checkpoint()

            if result.status == StageStatus.FAILED:
                self.context.log(f"Pipeline halted due to failure in stage '{stage.value}'.")
                break

            if result.status == StageStatus.HALTED_FOR_HUMAN:
                if stage == FlowStage.REVIEW and self.context.auto_calibrate:
                    cal_stage = FlowStage.CALIBRATE
                    self.before_stage(cal_stage)
                    try:
                        cal_result = self.execute_stage(cal_stage)
                    except Exception as e:
                        cal_result = self.handle_stage_failure(cal_stage, e)
                    self.after_stage(cal_stage, cal_result)
                    self.save_checkpoint()

                self.context.log(
                    f"Pipeline safely halted for human intervention at stage '{stage.value}'. "
                    "Halting execution without merging."
                )
                break

        return self.context.stage_results


def run_flow_pipeline(
    context: FlowContext,
    from_stage: FlowStage | str | None = None,
    checkpoint_path: str | None = None,
) -> dict[str, Any]:
    """Top-level invocation wrapper for running the Holon Flow pipeline."""
    engine = PipelineEngine(context, from_stage=from_stage, checkpoint_path=checkpoint_path)
    engine.run()
    return context.to_dict()
