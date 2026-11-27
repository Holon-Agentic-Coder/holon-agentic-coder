#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import NoReturn

from sandbox_executor import artefacts
from sandbox_executor.agent_api_error import detect_api_error
from sandbox_executor.agent_runner import get_runner, get_workspace_dir
from sandbox_executor.artefacts import PLAN_REQUIRED_METRICS, PLAN_SPEC, plan_write_protocol, validate_artefact
from sandbox_executor.budgets import (
    ENV_REPLAN_FEEDBACK,
    BudgetBreach,
    StageTimer,
    load_budgets,
    resolve_entropy_budget,
)
from sandbox_executor.decomposition_rule import should_decompose
from sandbox_executor.entrypoint.execution_workspace import start_branch_from_host_fetch
from sandbox_executor.entrypoint.plan_id_unique import mint_plan_id
from sandbox_executor.formatting import converge_prettier
from sandbox_executor.git import GitRepository
from sandbox_executor.ledger import (
    _sanitize_model_name,
    atomic_ledger_append,
    read_merged_rows,
    validate_ledger_file_unique_ids,
)
from sandbox_executor.ledger.fragments import ledger_commit_paths
from sandbox_executor.module_budget_guard import module_budget_change_authorised, refusal_message, without_budget_config
from sandbox_executor.plan_attempt import clear_failure_detail, discard_untracked_plan_file, write_failure_detail
from sandbox_executor.plan_paths import plan_file_rel
from sandbox_executor.redaction import redact_agent_secrets, redact_command, redact_command_args
from sandbox_executor.touch_set import parse_touch_set_section
from sandbox_executor.touch_set_overlap import broad_entries
from sandbox_executor.turns import AuthoringOutcome, record_budget_failure, run_turn, supervise_authoring_turn

# why: the mounted host worktree's repository config is shared and read-only, so the identity goes on the command line
PLANNER_GIT_IDENTITY = (
    "git",
    "-c",
    "user.email=planner-agent@holon-agentic-coder.com",
    "-c",
    "user.name=Holon Planner Agent",
)


# why: one pass, so a value is never rescanned and braces inside it are never re-expanded
def render_placeholders(template: str, values: Mapping[str, str]) -> str:
    if not values:
        return template
    names = "|".join(re.escape(name) for name in sorted(values, key=len, reverse=True))
    pattern = re.compile(r"\{(" + names + r")(?![A-Za-z0-9_])\}?")
    return pattern.sub(lambda match: values[match.group(1)], template)


# why: git's own error messages can carry the GitHub token when a remote URL embeds it
def run_cmd(
    args: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    print(f"Running: {redact_command(args)}")
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0 and check:
        print(f"Command failed with code {result.returncode}")
        print(f"Stdout:\n{redact_agent_secrets(result.stdout)}")
        print(f"Stderr:\n{redact_agent_secrets(result.stderr)}")
        sys.exit(result.returncode)
    return result


def get_file_structure(root_dir, max_depth=3):
    lines = []
    root_dir = os.path.abspath(root_dir)
    prefix_len = len(root_dir) + 1

    for root, dirs, files in os.walk(root_dir):
        # why: os.walk only skips the directories removed from its list in place
        dirs[:] = [d for d in dirs if not d.startswith(".")]

        rel_path = root[prefix_len:]
        depth = rel_path.count(os.sep) + 1 if rel_path else 0
        if depth >= max_depth:
            dirs[:] = []

        indent = "  " * depth
        if rel_path:
            lines.append(f"{indent}{os.path.basename(root)}/")
        else:
            lines.append("./")

        for f in files:
            if not f.startswith("."):
                lines.append(f"{indent}  {f}")

    return "\n".join(lines)


# why: the plan validator's parser, so plans.jsonl and holon flow never read different numbers from one plan
def parse_metrics(content):
    return artefacts.plan_metrics(content)


def load_metrics_config(repo_dir: str) -> dict:
    config = {"lambda": 0.3, "mu": 0.5}
    ev_config_path = os.path.join(repo_dir, "holon-config/metrics/ev_config.json")
    if os.path.exists(ev_config_path):
        try:
            with open(ev_config_path) as f:
                data = json.load(f)
                raw_lambda = float(data["lambda"]) if "lambda" in data else config["lambda"]
                raw_mu = float(data["mu"]) if "mu" in data else config["mu"]
                # why: out-of-range values fall back to the docs/metrics.md defaults
                if 0 < raw_lambda <= 1.0:
                    config["lambda"] = raw_lambda
                else:
                    print(
                        f"Warning: 'lambda' value {raw_lambda} out of range (0, 1]. Using default.",
                        file=sys.stderr,
                    )
                if 0 < raw_mu <= 1.0:
                    config["mu"] = raw_mu
                else:
                    print(
                        f"Warning: 'mu' value {raw_mu} out of range (0, 1]. Using default.",
                        file=sys.stderr,
                    )
        except Exception as e:
            print(f"Warning: Failed to load {ev_config_path}: {e}. Using defaults.", file=sys.stderr)
    return config


# why: the ledger record would vanish with the container workspace if it were not committed
def _record_plan_budget_failure(  # noqa: PLR0913
    breach: BudgetBreach,
    repo_dir: str,
    plan_id: str,
    plan_md_rel: str,
    agent_name: str,
    model_name: str,
    stage_timer: StageTimer,
    reason: str = "resume did not recover",
) -> None:
    ledger_rel = "holon-knowledge/ledger/executions.jsonl"
    record_budget_failure(
        os.path.join(repo_dir, ledger_rel),
        breach,
        record_id=plan_id,
        target=plan_md_rel,
        clock_elapsed=stage_timer.elapsed(),
        reason=reason,
    )
    add_targets = ledger_commit_paths(repo_dir, "executions.jsonl")
    if os.path.exists(os.path.join(repo_dir, plan_md_rel)):
        add_targets.append(plan_md_rel)
    run_cmd(["git", "add", *add_targets], cwd=repo_dir, check=False)
    run_cmd(
        [
            *PLANNER_GIT_IDENTITY,
            "commit",
            "-m",
            f"plan: {plan_id} stopped by {breach.budget_name} budget ({agent_name}, {model_name})",
        ],
        cwd=repo_dir,
        check=False,
    )


def build_replan_paragraph(feedback: str, entropy_budget: float) -> str:
    return (
        "\n\n## Re-plan request\n\n"
        f"{feedback}\n\n"
        f"Write a smaller plan whose overall entropy_pred is at or below the entropy budget of {entropy_budget}. "
        "Only if the request above offers a sub-intent list and the work cannot fit, add a level-2 heading "
        "'## Sub-intent list' followed by one bullet per concrete child intent.\n"
    )


_AGENT_ARG_DISPLAY_LEN = 100


# why: redaction runs before the clamp so truncating an argument never splits a credential
def format_agent_cmd_for_display(cmd: list[str]) -> str:
    print_cmd = []
    for arg in redact_command_args(cmd):
        if len(arg) > _AGENT_ARG_DISPLAY_LEN:
            print_cmd.append(arg[:_AGENT_ARG_DISPLAY_LEN] + "...")
        else:
            print_cmd.append(arg)
    return " ".join(print_cmd)


def _unique_temp_file(prefix: str, suffix: str) -> str:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    return path


# why: the rule runs on the plan as committed (after prettier), so sub_intents match what readers parse
def _plan_decision_fields(plan_entry: dict, plan_md_path: str, intent_data: dict | None) -> dict:
    with open(plan_md_path, encoding="utf-8") as f:
        plan_content = f.read()
    return should_decompose(plan_entry, plan_content, intent_data).to_plan_fields()


# why: advisory only; the frozen budget config is dropped so it never serialises Stage 3 admission
def _touch_set_field(plan_md_path: str, intent_data: Mapping | None = None) -> dict:
    try:
        with open(plan_md_path, encoding="utf-8") as f:
            touch_set = parse_touch_set_section(f.read())
    except (OSError, ValueError) as e:
        print(f"Warning: could not parse the plan's touch set: {e}", file=sys.stderr)
        return {"touch_set": None}
    if touch_set is None:
        print("Warning: the plan has no '## Touch Set' section; recording touch_set as null.", file=sys.stderr)
        return {"touch_set": None}
    if not module_budget_change_authorised(intent_data):
        touch_set, removed = without_budget_config(touch_set)
        if removed:
            print(f"Warning: dropped from the touch set. {refusal_message(removed)}", file=sys.stderr)
    if broad := broad_entries(touch_set):
        print(
            f"Warning: the touch set has folder or wildcard entries ({', '.join(broad)}); they match every existing "
            "file below them at Stage 3 admission, so prefer module paths.",
            file=sys.stderr,
        )
    return {"touch_set": touch_set.to_row()}


# why: the failure detail file lets the host's plan stage name the real cause
def _fail_plan_attempt(repo_dir: str, plan_md_rel: str, message: str) -> NoReturn:
    message = redact_agent_secrets(message)
    print(f"Error: {message}", file=sys.stderr)
    discard_untracked_plan_file(repo_dir, plan_md_rel)
    write_failure_detail(repo_dir, message)
    sys.exit(1)


def _strip_markdown_fence(text: str) -> str:
    match = re.search(r"```(?:markdown|md)?\s*\n(.*?)\n```", text, re.DOTALL)
    return match.group(1).strip() if match else text


def _api_error_of(outcome: AuthoringOutcome) -> str | None:
    return detect_api_error(*(text for turn in outcome.turns for text in (turn.stdout, turn.stderr)))


def main():  # noqa: C901, PLR0912, PLR0915
    if len(sys.argv) < 4:
        print("Usage: planner.py <intent_branch> <agent_name> <model_name>")
        sys.exit(1)

    intent_branch = sys.argv[1]
    agent_name = sys.argv[2]
    model_name = sys.argv[3]

    runner = get_runner(agent_name)
    runner.validate()

    intent_branch_prefix = intent_branch
    if intent_branch_prefix.endswith("/_"):
        intent_branch_prefix = intent_branch_prefix[:-2]

    repo_dir = get_workspace_dir()
    clear_failure_detail(repo_dir)

    plan_seq = int(time.time())
    safe_model = _sanitize_model_name(model_name)
    repo = GitRepository(repo_dir, runner=run_cmd)
    try:
        plan_id = mint_plan_id(repo, repo_dir, intent_branch_prefix, plan_seq, (agent_name, model_name))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    plan_branch = f"{intent_branch_prefix}/{plan_id}/_"

    # why: the host already fetched the intent branch into the mounted worktree
    try:
        start_branch_from_host_fetch(repo, repo_dir, intent_branch, plan_branch)
    except RuntimeError as exc:
        print(f"Error: {redact_agent_secrets(str(exc))}")
        sys.exit(1)

    from sandbox_executor.flow.stage_conflicts import check_and_resolve_stage_conflicts

    check_and_resolve_stage_conflicts(repo_dir=repo_dir, target_branch="main", stage_name="plan")

    intents_file_path = os.path.join(repo_dir, "holon-knowledge/ledger/intents.jsonl")
    intent_data = None

    def warn(line_no: int, e: Exception) -> None:
        print(f"Warning: skipping unreadable row in {intents_file_path} at line {line_no}: {e}")

    for data in read_merged_rows(intents_file_path, on_bad_line=warn):
        if data.get("branch") == intent_branch_prefix or data.get("intent_id") == intent_branch_prefix:
            intent_data = data

    if not intent_data:
        print(f"Error: No intent found for branch prefix '{intent_branch_prefix}' in '{intents_file_path}'")
        sys.exit(1)

    # why: one budget for the whole handoff, shared by the prompt, the plans.jsonl row and the executor
    entropy_budget = resolve_entropy_budget({}, intent_data)

    # why: re-plan feedback reaches the agent only via prompt and temp intent file, never a ledger or a merge notice
    raw_feedback = os.getenv(ENV_REPLAN_FEEDBACK, "").strip()
    if "## Target branch merged" in raw_feedback:
        head_part, tail_part = raw_feedback.split("## Target branch merged", 1)
        replan_feedback = head_part.strip()
        merge_notice = f"## Target branch merged{tail_part}".strip()
    else:
        replan_feedback = raw_feedback
        merge_notice = ""

    template_path = os.path.join(repo_dir, "holon-config/prompts/planner.template.md")
    if os.path.exists(template_path):
        with open(template_path) as f:
            template = f.read()
    else:
        template = """Based on the following intent JSON:
{intent_json}
Generate a detailed markdown implementation plan.
Include metrics in this format:
| metric | value |
| p_success_pred | {p_success_pred} |
| entropy_pred | {entropy_pred} |
| impact_pred | {impact_pred} |
| cost_pred | {cost_pred} |
| learning_value_pred | {learning_value_pred} |
| ev_pred | {ev_pred} |
"""

    values = {
        "timestamp": str(plan_seq),
        "agent": agent_name,
        "agent_version": runner.get_version(),
        "model": model_name,
        "intent_id": intent_data.get("intent_id", intent_data.get("branch", intent_branch_prefix)),
        "parent_intent_id": intent_data.get("parent_intent_id", "NONE"),
        "description": intent_data.get("description", ""),
        "plan_id": plan_id,
        "plan_file": plan_file_rel(intent_branch_prefix, plan_id),
        "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "intent_json": json.dumps(intent_data, indent=2),
        "safe_model": safe_model,
        "budget_from_intent": str(entropy_budget),
    }
    template = render_placeholders(template, values)

    # why: unique names so concurrent plans never clobber each other; created inside the try so finally removes both
    prompt_file: str | None = None
    intent_file: str | None = None
    try:
        prompt_file = _unique_temp_file(f"plan_prompt-{plan_seq}-", ".md")
        intent_file = _unique_temp_file(f"intent-{plan_seq}-", ".json")
        with open(prompt_file, "w") as f:
            f.write(template)
            f.write("\n\nRepository file structure:\n")
            f.write(get_file_structure(repo_dir))

            if replan_feedback:
                f.write(build_replan_paragraph(replan_feedback, entropy_budget))
            if merge_notice:
                f.write(f"\n\n{merge_notice}\n")

        with open(intent_file, "w") as f:
            json.dump({**intent_data, "replan_feedback": replan_feedback} if replan_feedback else intent_data, f)

        plan_md_rel = plan_file_rel(intent_branch_prefix, plan_id)
        plan_md_path = os.path.join(repo_dir, plan_md_rel)
        os.makedirs(os.path.dirname(plan_md_path), exist_ok=True)

        # why: some agents do not support file-based parameters
        with open(prompt_file) as f:
            prompt_content = f.read()
        with open(intent_file) as f:
            intent_content = f.read()
        full_prompt = f"{prompt_content}\n\nIntent metadata:\n{intent_content}\n\n{plan_write_protocol(plan_md_rel)}"
        with open(prompt_file, "w") as f:
            f.write(full_prompt)

        runner = get_runner(agent_name)

        def build_agent_cmd(prompt_text: str) -> list[str]:
            cmd = runner.build_cmd(model_name, prompt_file, intent_file, prompt_text)
            print(f"Running command: {format_agent_cmd_for_display(cmd)}")
            return cmd

        budgets = load_budgets(os.environ)
        stage_timer = StageTimer("plan", budgets.stage_seconds("plan"))
        outcome = None
        agent_error = "the planner agent did not run"
        try:
            outcome = supervise_authoring_turn(
                build_agent_cmd,
                full_prompt,
                artefact_path=plan_md_path,
                artefact_rel=plan_md_rel,
                spec=PLAN_SPEC,
                stage="plan",
                turn_budgets=budgets.turn,
                stage_timer=stage_timer,
                run=run_turn,
                context=f"{prompt_content}\n\nIntent metadata:\n{intent_content}",
                cwd=repo_dir,
                stdin_file=prompt_file if getattr(runner, "uses_stdin", False) else None,
                prompt_file=prompt_file,
            )
        except Exception as e:
            agent_error = f"Exception running agent: {e}"
        finally:
            # why: credentials are removed even when a turn breached its budget
            runner.cleanup_ephemeral_credentials()
    finally:
        # why: temp files are removed even when creating them or anything before the turn raised
        for temp_f in (prompt_file, intent_file):
            if temp_f is not None and os.path.exists(temp_f):
                os.remove(temp_f)

    if outcome is None:
        _fail_plan_attempt(repo_dir, plan_md_rel, agent_error)

    result = outcome.last
    report = outcome.report
    # why: an API failure is reported as such, never as a budget breach or a metrics failure
    api_error = None if report is not None and report.usable else _api_error_of(outcome)
    if api_error is not None:
        _fail_plan_attempt(repo_dir, plan_md_rel, f"the planner agent's API call failed: {api_error}")

    if outcome.budget_failure is not None:
        # why: the reason names the follow-up that actually ran, or none when no stage time was left
        reason = {
            "resume": "resume did not recover",
            "reprompt": "the re-prompt for missing content did not finish",
        }.get(outcome.follow_up or "", "no stage time was left to resume")
        _record_plan_budget_failure(
            outcome.budget_failure,
            repo_dir,
            plan_id,
            plan_md_rel,
            agent_name,
            model_name,
            stage_timer,
            reason=reason,
        )
        print(f"Error: {outcome.budget_failure.describe()}; {reason}; the plan is not usable.", file=sys.stderr)
        print(stage_timer.summary("failed"))
        sys.exit(1)

    if report is not None and report.usable:
        if result.returncode != 0 or result.truncated:
            # why: a cut-off final chat message does not matter once the plan on disk is complete
            print(
                f"Info: agent exited with code {result.returncode} (stop reason: {result.stop_reason or 'unknown'}) "
                f"after writing a valid plan file; treating the exit status as informational."
            )
    elif report is None or not report.exists:
        stdout_text = (result.stdout or "").strip()
        stdout_api_error = detect_api_error(stdout_text)
        if stdout_api_error is not None:
            # why: defensive second guard, an API error is never written to the plan path
            _fail_plan_attempt(repo_dir, plan_md_rel, f"the planner agent's API call failed: {stdout_api_error}")
        if stdout_text:
            print(
                f"WARNING: agent did not write {plan_md_rel}; falling back to the plan text captured from stdout.",
                file=sys.stderr,
            )
            with open(plan_md_path, "w") as f:
                f.write(_strip_markdown_fence(stdout_text))
            report = validate_artefact(plan_md_path, PLAN_SPEC)
        else:
            print(f"Error: agent command failed or returned empty. code={result.returncode}", file=sys.stderr)
            print(f"Stdout:\n{redact_agent_secrets(result.stdout)}", file=sys.stderr)
            print(f"Stderr:\n{redact_agent_secrets(result.stderr)}", file=sys.stderr)

    # why: validate before any ledger append or commit; missing metrics are never back-filled with defaults
    if report is None or not report.metrics_ok:
        missing = ", ".join(report.missing_metrics) if report is not None else ", ".join(PLAN_REQUIRED_METRICS)
        _fail_plan_attempt(
            repo_dir,
            plan_md_rel,
            f"metrics validation failed for {plan_md_rel}: missing or non-numeric overall metrics: {missing}.",
        )
    if not report.size_ok:
        _fail_plan_attempt(
            repo_dir,
            plan_md_rel,
            f"Plan generation produced an empty or invalid plan file ({report.size} bytes, at least "
            f"{PLAN_SPEC.size_floor} expected).",
        )
    if report.missing_sections:
        print(f"WARNING: {report.describe()}", file=sys.stderr)

    metrics = dict(report.metrics)

    ev_config = load_metrics_config(repo_dir)
    ev_lambda = ev_config["lambda"]
    ev_mu = ev_config["mu"]

    p_success = metrics["p_success"]
    entropy = metrics["entropy"]
    impact = metrics["impact"]
    cost = metrics["cost"]
    learning_value = metrics["learning_value"]
    ev = p_success * impact + ev_mu * learning_value - ev_lambda * entropy - cost
    metrics["ev"] = ev

    timestamp_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    plan_entry = {
        "plan_id": plan_id,
        "intent_branch": intent_branch,
        "agent": agent_name,
        "agent_version": runner.get_version(),
        "model": model_name,
        "p_success": p_success,
        "entropy": entropy,
        "impact": impact,
        "cost": cost,
        "learning_value": learning_value,
        "ev": ev,
        "created_at": timestamp_str,
        "plan_file": plan_md_rel,
        "status": "proposed",
        "entropy_budget": entropy_budget,
    }

    # why: format before the ledger append so the recorded decision is parsed from the committed file
    try:
        converge_prettier([plan_md_rel], repo_dir=repo_dir)
    except Exception as e:
        print(f"Warning: Failed to converge prettier on {plan_md_rel}: {e}", file=sys.stderr)
    plan_entry.update(_plan_decision_fields(plan_entry, plan_md_path, intent_data))
    plan_entry.update(_touch_set_field(plan_md_path, intent_data))

    plans_ledger_dir = os.path.join(repo_dir, "holon-knowledge/ledger")
    os.makedirs(plans_ledger_dir, exist_ok=True)
    plans_ledger_path = os.path.join(plans_ledger_dir, "plans.jsonl")
    with atomic_ledger_append(plans_ledger_path) as lf:
        lf.write(json.dumps(plan_entry) + "\n")
    validate_ledger_file_unique_ids(plans_ledger_path, id_key="plan_id", parent_key="intent_branch")

    # why: no push here, the host pushes the branch after this container returns
    run_cmd(["git", "add", plan_md_rel, *ledger_commit_paths(repo_dir, "plans.jsonl")], cwd=repo_dir)
    run_cmd(
        [*PLANNER_GIT_IDENTITY, "commit", "-m", f"plan: {plan_id} created by {agent_name} ({model_name})"], cwd=repo_dir
    )

    print(f"Plan branch '{plan_branch}' successfully created and committed.")
    print(stage_timer.summary("ok"))


if __name__ == "__main__":
    main()
