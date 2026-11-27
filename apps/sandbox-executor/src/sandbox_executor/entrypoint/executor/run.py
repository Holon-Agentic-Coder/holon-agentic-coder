import contextlib
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any

import sandbox_executor.entrypoint.executor as executor_facade
from sandbox_executor.artefacts import EXECUTION_SPEC, ArtefactReport, execution_write_protocol
from sandbox_executor.budgets import BudgetBreach, StageTimer, load_budgets, read_replan_feedback
from sandbox_executor.decomposition_rule import DECISION_OVER_BUDGET, DecompositionDecision
from sandbox_executor.entrypoint.execution_args import ExecutionArgs, _read_intent_row, _read_plan_row
from sandbox_executor.entrypoint.execution_workspace import (
    continuation_note,
    format_skipped_scratch_message,
    resolve_partial_head,
    unstage_sidecar_scratch,
)
from sandbox_executor.entrypoint.executor.agent_output import AgentOutputSteps
from sandbox_executor.entrypoint.executor.commands import _get_repo
from sandbox_executor.entrypoint.executor.outcome import CheckWorkSteps, OutcomeSteps
from sandbox_executor.entrypoint.executor.pre_push import PrePushSteps, _reconcile_conflicted_merge
from sandbox_executor.entrypoint.executor.repair import _configure_executor_identity
from sandbox_executor.entrypoint.executor.sub_intents import SubIntentSteps
from sandbox_executor.entrypoint.partial_resume import merge_plan_tip, start_execution_branch
from sandbox_executor.entrypoint.taken_ids import taken_child_ids
from sandbox_executor.git import GitRepository
from sandbox_executor.ledger import LedgerRewriteError, build_execution_id
from sandbox_executor.ledger.fragments import ledger_commit_paths
from sandbox_executor.plan_paths import recorded_plan_file
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.ref_names import INTENT_SUFFIX


@dataclass(frozen=True)
class ExecutionPromptInputs:
    plan_data: dict[str, Any]
    plan_content: str
    intent_data: dict[str, Any]


def build_execution_prompt(repo_dir: str, plan_branch: str, inputs: ExecutionPromptInputs, exec_id: str) -> str:
    plan_data, plan_content, intent_data = inputs.plan_data, inputs.plan_content, inputs.intent_data
    intent_json = json.dumps(intent_data, indent=2)
    template_path = os.path.join(repo_dir, "holon-config/prompts/executor.template.md")
    template = None
    if os.path.isfile(template_path):
        try:
            with open(template_path, encoding="utf-8") as f:
                template = f.read()
        except OSError as e:
            print(f"Warning: Failed to read {template_path}: {e}; using the built-in prompt.", file=sys.stderr)
    if not template:
        return f"Execute plan {plan_branch}.\n\nPlan content:\n{plan_content}\n\nIntent data:\n{intent_json}"
    replacements = {
        "{repo_dir}": repo_dir,
        "{plan_id}": str(plan_data.get("plan_id", plan_branch)),
        "{plan_branch}": plan_branch,
        "{intent_id}": str(intent_data.get("intent_id") or intent_data.get("branch") or "unknown"),
        "{exec_id}": exec_id,
        "{notes_path}": f"executions/{exec_id}.md",
        "{intent_json}": intent_json,
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    # why: substituted last so placeholder-like text inside the plan itself is never expanded
    return template.replace("{plan_content}", plan_content)


# why: patch seams are read through the package facade at call time, so tests patch one place
class ExecutionRun(SubIntentSteps, AgentOutputSteps, CheckWorkSteps, OutcomeSteps, PrePushSteps):
    def __init__(self, args: ExecutionArgs) -> None:
        self.args = args
        self.plan_branch = args.plan_branch
        self.plan_branch_prefix = args.plan_branch[:-2] if args.plan_branch.endswith("/_") else args.plan_branch
        self.runner: Any = None
        self.repo_dir = ""
        self._repo: GitRepository | None = None
        self.exec_seq = 0
        self.exec_id: str | None = None
        self.exec_branch = ""
        self.plan_tip: str | None = None
        self.partial_head: str | None = None
        self.timestamp_str = datetime.now(UTC).isoformat()
        self.plan_data: dict[str, Any] = {}
        self.plan_content = ""
        self.intent_data: dict[str, Any] = {}
        self.ledger_dir = ""
        self._decision: DecompositionDecision | None = None
        self.exec_entry: dict[str, Any] = {}
        self.commit_msg = ""
        # why: the host pushes a salvaged commit even with nothing else staged
        self.salvaged_head: str | None = None
        # why: the host pushes the execution branch after this container returns
        self.branch_recorded = False
        # why: only an executed (not decomposed or refused) plan sets the authoring turn fields below
        self.notes_rel = ""
        self.notes_path = ""
        self._stage_timer: StageTimer | None = None
        self.agent_result: Any = None
        self.notes_report: ArtefactReport | None = None
        self.exec_status = ""
        self.summary = ""
        self.budget_breach: BudgetBreach | None = None
        self.budget_reason = ""
        self.action_origin = "initial_implementation"
        self.review_severity: str | None = None
        self.review_iteration: int | None = None
        self.review_finding_ids: list[str] | None = None
        self.agent_output_text = ""
        self.agent_output_truncated = False
        self.agent_output_bytes = 0
        self.notes_valid = False
        self.failure_reason: str | None = None
        self.test_fields: dict[str, Any] = {}

    @property
    def repo(self) -> GitRepository:
        assert self._repo is not None, "repo read before the workspace was validated"
        return self._repo

    @property
    def decision(self) -> DecompositionDecision:
        assert self._decision is not None, "decision read before the plan was read"
        return self._decision

    @property
    def stage_timer(self) -> StageTimer:
        assert self._stage_timer is not None, "stage_timer read before the authoring turn started"
        return self._stage_timer

    @property
    def executions_ledger(self) -> str:
        return os.path.join(self.ledger_dir, "executions.jsonl")

    def run(self) -> None:
        self.runner = executor_facade.get_runner(self.args.agent_name)
        self.runner.validate()
        self.repo_dir = executor_facade.get_workspace_dir()
        if not executor_facade.workspace_mounted():
            print(
                "Error: the host worktree is not mounted at the sandbox workspace; the executor never clones or "
                "fetches.",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            self._run_steps()
        except Exception as e:
            print(f"Execution failed: {redact_agent_secrets(str(e))}", file=sys.stderr)
            raise
        finally:
            if self.runner is not None and hasattr(self.runner, "cleanup_ephemeral_credentials"):
                self.runner.cleanup_ephemeral_credentials()

    def _run_steps(self) -> None:
        self.prepare_workspace()
        self.load_plan()
        skip_agent = self.record_decision()
        if not skip_agent:
            self.run_turns()
            self.check_work()
            self.salvage()
            self.record_ledger()
            self.stage_changes()
        if self.commit_steps():
            self.record_branch()
        self._exit_with_status(skip_agent)

    def prepare_workspace(self) -> None:
        self._repo = repo = _get_repo(self.repo_dir)
        # why: the host provisioned, fetched and mounted the worktree, so no clone, fetch, reset or clean here
        mounted_plan_tip = executor_facade.validate_mounted_workspace(repo, self.repo_dir, self.plan_branch)

        prefix = self.plan_branch_prefix
        self.exec_seq = int(time.time())
        self.exec_id = build_execution_id(
            self.exec_seq, prefix, self.args.agent_name, self.args.model_name, taken=taken_child_ids(repo, prefix, "E")
        )
        self.exec_branch = f"{prefix}/{self.exec_id}{INTENT_SUFFIX}"

        # why: the plan tip is the empty-diff guard's base even on a resume
        self.plan_tip = mounted_plan_tip
        merge = partial(
            merge_plan_tip,
            repo,
            configure_identity=partial(_configure_executor_identity, self.repo_dir),
            reconcile=partial(_reconcile_conflicted_merge, repo, self.repo_dir),
        )
        resolve = partial(resolve_partial_head, repo, plan_tip=self.plan_tip, plan_branch_prefix=prefix)
        self.partial_head = start_execution_branch(
            repo, (self.plan_branch, self.exec_branch), self.args.partial_branch, self.plan_tip, (resolve, merge)
        )
        # why: before the agent runs, so the commits it makes step by step land on the execution branch
        _configure_executor_identity(self.repo_dir)

        from sandbox_executor.flow.stage_conflicts import check_and_resolve_stage_conflicts

        check_and_resolve_stage_conflicts(repo_dir=self.repo_dir, target_branch="main", stage_name="execute")

    def load_plan(self) -> None:
        prefix = self.plan_branch_prefix
        self.plan_data = _read_plan_row(self.repo_dir, prefix) or {
            "plan_id": prefix.split("/")[-1],
            "intent_branch": prefix.rsplit("/", 1)[0] + "/_",
            "agent": self.args.agent_name,
            "model": self.args.model_name,
            "entropy": 3.0,
        }

        plan_file_rel = recorded_plan_file(self.plan_data.get("plan_file"), prefix)
        if plan_file_rel and os.path.exists(os.path.join(self.repo_dir, plan_file_rel)):
            with open(os.path.join(self.repo_dir, plan_file_rel)) as f:
                self.plan_content = f.read()

        intent_branch = self.plan_data.get("intent_branch", "")
        self.intent_data = _read_intent_row(self.repo_dir, intent_branch) or {
            "branch": self.plan_data.get("intent_branch", "I-unknown")
        }

    def run_turns(self) -> None:
        print(f"Executing plan {self.plan_branch} using agent {self.args.agent_name}...")
        self.notes_rel = f"executions/{self.exec_id}.md"
        self.notes_path = os.path.join(self.repo_dir, self.notes_rel)
        base_prompt = build_execution_prompt(
            self.repo_dir,
            self.plan_branch,
            ExecutionPromptInputs(self.plan_data, self.plan_content, self.intent_data),
            self.exec_id,
        )
        if self.args.partial_branch and self.partial_head:
            base_prompt = f"{base_prompt}\n\n{continuation_note(self.args.partial_branch, self.partial_head)}"
        merged_notice = read_replan_feedback(os.environ, consume=True)
        if merged_notice and "## Target branch merged" in merged_notice:
            base_prompt = f"{base_prompt}\n\n{merged_notice}"
        full_prompt = f"{base_prompt}\n\n{execution_write_protocol(self.notes_rel)}"

        budgets = load_budgets(os.environ)
        self._stage_timer = StageTimer("execute", budgets.stage_seconds("execute"))
        self._supervise_authoring(base_prompt, full_prompt, budgets.turn)
        self._record_budget_failure()
        self._resolve_action_provenance()
        self._record_agent_action()
        self._capture_agent_output()

    def _supervise_authoring(self, base_prompt: str, full_prompt: str, turn_budgets: Any) -> None:
        prompt_file = os.path.join(tempfile.gettempdir(), f"exec_prompt-{self.exec_seq}.md")
        intent_file = os.path.join(tempfile.gettempdir(), f"exec_intent-{self.exec_seq}.json")
        runner, model_name = self.runner, self.args.model_name
        try:
            with open(prompt_file, "w") as f:
                f.write(full_prompt)
            with open(intent_file, "w") as f:
                json.dump(self.intent_data, f)

            outcome = executor_facade.supervise_authoring_turn(
                lambda text: runner.build_cmd(model_name, prompt_file, intent_file, text),
                full_prompt,
                artefact_path=self.notes_path,
                artefact_rel=self.notes_rel,
                spec=EXECUTION_SPEC,
                stage="execute",
                turn_budgets=turn_budgets,
                stage_timer=self.stage_timer,
                run=executor_facade.run_turn,
                modify_code=False,
                context=base_prompt,
                watch_tree=self.repo_dir,
                cwd=self.repo_dir,
                stdin_file=prompt_file if getattr(runner, "uses_stdin", False) else None,
                prompt_file=prompt_file,
            )
            self.exec_status, self.summary, self.budget_breach, self.budget_reason = (
                executor_facade.classify_execution_outcome(outcome)
            )
            # why: follow-ups only touch the notes, so output size and error context describe the implementation turn
            self.agent_result = outcome.first
            self.notes_report = outcome.report
        finally:
            for tf in (prompt_file, intent_file):
                if os.path.exists(tf):
                    with contextlib.suppress(Exception):
                        os.remove(tf)

    def _record_budget_failure(self) -> None:
        if self.budget_breach is None:
            return
        # why: not gated on action logging, and the only write site, so each stage failure has exactly one audit record
        try:
            os.makedirs(self.ledger_dir, exist_ok=True)
            executor_facade.record_budget_failure(
                self.executions_ledger,
                self.budget_breach,
                record_id=self.exec_id,
                target=self.notes_rel,
                clock_elapsed=self.stage_timer.elapsed(),
                reason=self.budget_reason,
            )
        except LedgerRewriteError:
            raise
        except Exception as e:
            print(f"Warning: Failed to record budget failure: {redact_agent_secrets(str(e))}", file=sys.stderr)

    def stage_changes(self) -> None:
        repo_dir, repo = self.repo_dir, self.repo
        add_targets = ledger_commit_paths(repo_dir, "executions.jsonl")
        if self.exec_status != "success" and os.path.exists(self.notes_path):
            # why: the code changes are already in the partial commit, so only the notes explain the failure
            add_targets.append(self.notes_rel)
        if add_targets:
            repo.run(["add", *add_targets], check=False)
        if self.exec_status == "success":
            repo.add(all_files=True)
            if skipped := unstage_sidecar_scratch(repo):
                print(format_skipped_scratch_message(skipped), file=sys.stderr)
        status_output = repo.run(["status", "--short"], check=False)
        print("Current git repository status:")
        print(redact_agent_secrets(status_output.stdout))

    def commit_steps(self) -> bool:
        staged_check = self.repo.diff(cached=True, quiet=True)
        if staged_check.returncode == 0 and not self.salvaged_head:
            print("No staged changes to commit.")
            return False
        if staged_check.returncode != 0:
            _configure_executor_identity(self.repo_dir)
            self.repo.commit(self.commit_msg)
        return True

    def _exit_with_status(self, skip_agent: bool) -> None:
        if not skip_agent:
            print(self.stage_timer.summary("ok" if self.exec_status == "success" else "failed"))
            if self.budget_breach is not None:
                sys.exit(1)
            return
        decision = self.decision
        if decision.decision != DECISION_OVER_BUDGET:
            return
        refusal = (
            f"Error: plan {self.plan_branch} was not executed: its entropy {decision.entropy} exceeds the entropy "
            f"budget {decision.entropy_budget} and it lists no sub-intent list."
        )
        if not self.branch_recorded:
            # why: no execution branch carries the over_budget row, so this is a failed run, not a refusal
            print(f"{refusal} Its over_budget row was not recorded on an execution branch.", file=sys.stderr)
            sys.exit(1)
        print(
            f"{refusal} Re-plan it within the budget, or add an explicit sub-intent list.",
            file=sys.stderr,
        )
        sys.exit(executor_facade.OVER_BUDGET_EXIT_CODE)
