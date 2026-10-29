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

from sandbox_executor import artefacts
from sandbox_executor.agent_runner import cleanup_repo_dir, get_repo_url, get_runner, get_workspace_dir
from sandbox_executor.artefacts import PLAN_REQUIRED_METRICS, PLAN_SPEC, plan_write_protocol, validate_artefact
from sandbox_executor.budgets import (
    ENV_REPLAN_FEEDBACK,
    BudgetBreach,
    StageTimer,
    load_budgets,
    resolve_entropy_budget,
)
from sandbox_executor.formatting import converge_prettier
from sandbox_executor.ledger import atomic_ledger_append
from sandbox_executor.redaction import redact_agent_secrets, redact_command, redact_command_args
from sandbox_executor.turns import record_budget_failure, run_turn, supervise_authoring_turn


def render_placeholders(template: str, values: Mapping[str, str]) -> str:
    """Render every ``{name}`` or unmatched ``{name`` placeholder in one regular-expression pass.

    Args:
        template: Prompt template text.
        values: Replacement values keyed by bare placeholder name (``"plan_id"``, not ``"{plan_id}"``).

    Returns:
        The rendered text. Values are inserted literally and never rescanned, so braces or backslashes inside a value
        are not re-expanded, and the result does not depend on the mapping's order. Unknown names are left untouched.
    """
    if not values:
        return template
    names = "|".join(re.escape(name) for name in sorted(values, key=len, reverse=True))
    pattern = re.compile(r"\{(" + names + r")(?![A-Za-z0-9_])\}?")
    return pattern.sub(lambda match: values[match.group(1)], template)


def run_cmd(
    args: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command, echoing it with credentials masked, and exit on failure when `check` is set.

    The echo and the failure diagnostics go through the shared redactors, because the clone URL and
    git's own error messages can carry the GitHub token.

    Args:
        args: The command and its arguments.
        cwd: Working directory for the command.
        env: Environment for the command; inherits the current environment when None.
        check: Exit the process with the command's return code if it fails.

    Returns:
        The CompletedProcess, whose stdout and stderr are raw and unredacted.
    """
    print(f"Running: {redact_command(args)}")
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0 and check:
        print(f"Command failed with code {result.returncode}")
        print(f"Stdout:\n{redact_agent_secrets(result.stdout)}")
        print(f"Stderr:\n{redact_agent_secrets(result.stderr)}")
        sys.exit(result.returncode)
    return result


def _sanitize_model_name(model_name: str) -> str:
    """Sanitise a model name so it is safe for use in git branch names and filenames.

    Steps:
    1. Replace path separators (/) with underscores.
    2. Collapse any run of whitespace into a single hyphen.
    3. Strip all characters not in [a-zA-Z0-9_.-].
    4. Collapse consecutive hyphens.
    5. Collapse consecutive dots.
    6. Strip leading/trailing hyphens and dots.
    7. Enforce a maximum length of 64 characters to prevent excessively long branch/filename strings.
    8. Fall back to 'unknown-model' if the result is empty.
    """
    s = re.sub(r"\s+", "-", model_name.replace("/", "_"))
    s = re.sub(r"[^a-zA-Z0-9_.-]", "", s)
    s = re.sub(r"-+", "-", s)
    s = re.sub(r"\.+", ".", s)
    s = s[:64].strip(".-")
    return s or "unknown-model"


def get_file_structure(root_dir, max_depth=3):
    lines = []
    root_dir = os.path.abspath(root_dir)
    prefix_len = len(root_dir) + 1

    for root, dirs, files in os.walk(root_dir):
        # Filter out dot directories in-place to prevent os.walk from recursing into them
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


def parse_metrics(content):
    """Parse the plan-level predicted metrics from a plan markdown document.

    When the document has a ``## Overall Plan Metrics`` section, only the metric rows inside that
    section (up to the next ``## `` heading) are read, so per-step ``### Metrics`` tables before or
    after it cannot overwrite the plan-level values. Documents without that heading fall back to
    scanning every metric row, where the last matching row wins. This delegates to
    :func:`artefacts.plan_metrics`, the same parser the plan validator gates on, so the planner's
    ``plans.jsonl`` row and ``holon flow`` can never read different numbers from one plan.
    """
    return artefacts.plan_metrics(content)


def load_metrics_config(repo_dir: str) -> dict:
    """Load config-driven metrics physics parameters from holon-config/metrics/.

    Returns a dictionary with 'lambda' (entropy penalty) and 'mu' (learning value weight).
    Defaults: lambda = 0.3, mu = 0.5 as specified in docs/metrics.md (§1.2, §11).
    Validation enforces range (0.0, 1.0] for both lambda and mu to prevent invalid or
    unbounded scaling of EV physics coefficients.
    """
    config = {"lambda": 0.3, "mu": 0.5}
    ev_config_path = os.path.join(repo_dir, "holon-config/metrics/ev_config.json")
    if os.path.exists(ev_config_path):
        try:
            with open(ev_config_path) as f:
                data = json.load(f)
                raw_lambda = float(data["lambda"]) if "lambda" in data else config["lambda"]
                raw_mu = float(data["mu"]) if "mu" in data else config["mu"]
                # Validate lambda and mu within range (0.0, 1.0], falling back to
                # docs/metrics.md defaults (lambda=0.3, mu=0.5)
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


def _record_plan_budget_failure(
    breach: BudgetBreach,
    repo_dir: str,
    plan_id: str,
    plan_md_rel: str,
    plan_branch: str,
    agent_name: str,
    model_name: str,
    stage_timer: StageTimer,
    reason: str = "resume did not recover",
) -> None:
    """Record the single ACT-BUDGET action, then commit and push it together with the salvaged partial plan.

    The ledger record would vanish with the container workspace if it were not committed, so the plan branch carries
    it (plus whatever plan text was salvaged) even though no ``plans.jsonl`` entry is made for an unusable plan.
    """
    ledger_rel = "holon-knowledge/ledger/executions.jsonl"
    record_budget_failure(
        os.path.join(repo_dir, ledger_rel),
        breach,
        record_id=plan_id,
        target=plan_md_rel,
        clock_elapsed=stage_timer.elapsed(),
        reason=reason,
    )
    add_targets = [ledger_rel]
    if os.path.exists(os.path.join(repo_dir, plan_md_rel)):
        add_targets.append(plan_md_rel)
    run_cmd(["git", "add", *add_targets], cwd=repo_dir, check=False)
    run_cmd(["git", "config", "--local", "user.email", "planner-agent@holon-agentic-coder.com"], cwd=repo_dir)
    run_cmd(["git", "config", "--local", "user.name", "Holon Planner Agent"], cwd=repo_dir)
    commit = run_cmd(
        ["git", "commit", "-m", f"plan: {plan_id} stopped by {breach.budget_name} budget ({agent_name}, {model_name})"],
        cwd=repo_dir,
        check=False,
    )
    if commit.returncode == 0:
        run_cmd(["git", "push", "-u", "origin", plan_branch], cwd=repo_dir, check=False)


def build_replan_paragraph(feedback: str, entropy_budget: float) -> str:
    """Return the "Re-plan request" paragraph appended to the planner prompt when the flow asks for a new plan.

    Args:
        feedback: The flow's message stating the previous plan's entropy and the budget it exceeded.
        entropy_budget: The resolved entropy budget the new plan must respect.

    Returns:
        A markdown paragraph, starting with blank lines so it can be appended to the prompt as is.
    """
    return (
        "\n\n## Re-plan request\n\n"
        f"{feedback}\n\n"
        f"Write a smaller plan whose overall entropy_pred is at or below the entropy budget of {entropy_budget}. "
        "Only if the request above offers a sub-intent list and the work cannot fit, add a level-2 heading "
        "'## Sub-intent list' followed by one bullet per concrete child intent.\n"
    )


_AGENT_ARG_DISPLAY_LEN = 100


def format_agent_cmd_for_display(cmd: list[str]) -> str:
    """Render the agent command line for the console with credentials masked and long args clamped.

    Redaction runs per argument before the clamp, so truncating a long argument (for example a
    `--settings` JSON blob carrying an OAuth token) can never split a credential and leak its prefix.

    Args:
        cmd: The agent argv exactly as handed to `subprocess`.

    Returns:
        A single space-joined line that is safe to print.
    """
    print_cmd = []
    for arg in redact_command_args(cmd):
        if len(arg) > _AGENT_ARG_DISPLAY_LEN:
            print_cmd.append(arg[:_AGENT_ARG_DISPLAY_LEN] + "...")
        else:
            print_cmd.append(arg)
    return " ".join(print_cmd)


def _unique_temp_file(prefix: str, suffix: str) -> str:
    """Create an empty, private temp file with a collision-free name and return its path (the caller removes it)."""
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    return path


def main():
    if len(sys.argv) < 4:
        print("Usage: planner.py <intent_branch> <agent_name> <model_name>")
        sys.exit(1)

    intent_branch = sys.argv[1]
    agent_name = sys.argv[2]
    model_name = sys.argv[3]

    # Validate agent and environment early before executing any git operations or cloning
    runner = get_runner(agent_name)
    runner.validate()

    intent_branch_prefix = intent_branch
    if intent_branch_prefix.endswith("/_"):
        intent_branch_prefix = intent_branch_prefix[:-2]

    repo_dir = get_workspace_dir()
    cleanup_repo_dir(repo_dir, raise_on_error=True)
    os.makedirs(repo_dir, exist_ok=True)

    # Clone the repo
    repo_url = get_repo_url()
    run_cmd(["git", "clone", "--branch", intent_branch, "--single-branch", "--depth", "1", repo_url, "."], cwd=repo_dir)

    plan_seq = int(time.time())
    safe_model = _sanitize_model_name(model_name)
    plan_id = f"P-{plan_seq}-{agent_name}-{safe_model}"
    plan_branch = f"{intent_branch_prefix}/P-{plan_seq}-{agent_name}-{safe_model}/_"

    # Checkout plan branch
    run_cmd(["git", "checkout", "-b", plan_branch], cwd=repo_dir)

    # Proactively check and resolve any stage conflicts
    from sandbox_executor.flow import check_and_resolve_stage_conflicts

    check_and_resolve_stage_conflicts(repo_dir=repo_dir, target_branch="main", stage_name="plan")

    # Get intent data
    intents_file_path = os.path.join(repo_dir, "holon-knowledge/ledger/intents.jsonl")
    intent_data = None
    if os.path.exists(intents_file_path):
        with open(intents_file_path) as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    # Match branch name prefix (e.g. branch == "I-timestamp-slug")
                    if data.get("branch") == intent_branch_prefix or data.get("intent_id") == intent_branch_prefix:
                        intent_data = data
                except json.JSONDecodeError as e:
                    print(f"Warning: skipping invalid JSON in {intents_file_path} at line {line_no}: {e}")
                except Exception as e:
                    print(f"Warning: error processing line in {intents_file_path} at line {line_no}: {e}")

    if not intent_data:
        print(f"Error: No intent found for branch prefix '{intent_branch_prefix}' in '{intents_file_path}'")
        sys.exit(1)

    # One budget for the whole handoff: shown to the agent here, persisted on the plans.jsonl row, enforced by the
    # executor through the same resolver.
    entropy_budget = resolve_entropy_budget({}, intent_data)

    # Transient re-plan feedback from the flow (HOLON_REPLAN_FEEDBACK). It reaches the agent through the prompt and the
    # temporary intent file only; it is never written to any ledger.
    replan_feedback = os.getenv(ENV_REPLAN_FEEDBACK, "").strip()

    # Prepare prompt using template
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
        "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "intent_json": json.dumps(intent_data, indent=2),
        "safe_model": safe_model,
        "budget_from_intent": str(entropy_budget),
    }
    template = render_placeholders(template, values)

    # Unique temp files (mkstemp), so two plans started in the same second, in one process or several, never share or
    # clobber each other's prompt or intent file. Both are created inside the try, so the finally block below removes
    # whichever exist even when creating the second one raises.
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

        with open(intent_file, "w") as f:
            json.dump({**intent_data, "replan_feedback": replan_feedback} if replan_feedback else intent_data, f)

        # Invoke AI agent
        plan_md_rel = f"plans/P-{plan_seq}-{agent_name}-{safe_model}.md"
        plan_md_path = os.path.join(repo_dir, plan_md_rel)
        os.makedirs(os.path.dirname(plan_md_path), exist_ok=True)

        # Load full prompt content for agents that don't support file-based parameters
        with open(prompt_file) as f:
            prompt_content = f.read()
        with open(intent_file) as f:
            intent_content = f.read()
        full_prompt = f"{prompt_content}\n\nIntent metadata:\n{intent_content}\n\n{plan_write_protocol(plan_md_rel)}"

        runner = get_runner(agent_name)

        def build_agent_cmd(prompt_text: str) -> list[str]:
            cmd = runner.build_cmd(model_name, prompt_file, intent_file, prompt_text)
            # Credentials are masked per argument before long arguments are clamped.
            print(f"Running command: {format_agent_cmd_for_display(cmd)}")
            return cmd

        budgets = load_budgets(os.environ)
        stage_timer = StageTimer("plan", budgets.stage_seconds("plan"))
        outcome = None
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
            )
        except Exception as e:
            print(f"Error: Exception running agent: {redact_agent_secrets(str(e))}", file=sys.stderr)
        finally:
            # Credentials are removed even when a turn breached its budget.
            runner.cleanup_ephemeral_credentials()
    finally:
        # Temp files are removed even when creating or writing them, or anything before the turn, raised.
        for temp_f in (prompt_file, intent_file):
            if temp_f is not None and os.path.exists(temp_f):
                os.remove(temp_f)

    if outcome is None:
        sys.exit(1)

    if outcome.budget_failure is not None:
        # Name the follow-up that actually ran: a resume after a breach, the re-prompt of a clean turn, or none at all
        # (no stage time was left).
        reason = {
            "resume": "resume did not recover",
            "reprompt": "the re-prompt for missing content did not finish",
        }.get(outcome.follow_up or "", "no stage time was left to resume")
        _record_plan_budget_failure(
            outcome.budget_failure,
            repo_dir,
            plan_id,
            plan_md_rel,
            plan_branch,
            agent_name,
            model_name,
            stage_timer,
            reason=reason,
        )
        print(f"Error: {outcome.budget_failure.describe()}; {reason}; the plan is not usable.", file=sys.stderr)
        print(stage_timer.summary("failed"))
        sys.exit(1)

    result = outcome.last
    report = outcome.report
    if report is not None and report.usable:
        if result.returncode != 0 or result.truncated:
            # The plan on disk is complete enough to commit; a cut-off final chat message does not matter (Bean 0138).
            print(
                f"Info: agent exited with code {result.returncode} (stop reason: {result.stop_reason or 'unknown'}) "
                f"after writing a valid plan file; treating the exit status as informational."
            )
    elif report is None or not report.exists:
        stdout_text = (result.stdout or "").strip()
        if stdout_text:
            print(
                f"WARNING: agent did not write {plan_md_rel}; falling back to the plan text captured from stdout.",
                file=sys.stderr,
            )
            # Extract markdown block if output is wrapped in ```markdown ... ``` or ```md ... ```
            match = re.search(r"```(?:markdown|md)?\s*\n(.*?)\n```", stdout_text, re.DOTALL)
            if match:
                stdout_text = match.group(1).strip()
            with open(plan_md_path, "w") as f:
                f.write(stdout_text)
            report = validate_artefact(plan_md_path, PLAN_SPEC)
        else:
            print(f"Error: agent command failed or returned empty. code={result.returncode}", file=sys.stderr)
            print(f"Stdout:\n{redact_agent_secrets(result.stdout)}", file=sys.stderr)
            print(f"Stderr:\n{redact_agent_secrets(result.stderr)}", file=sys.stderr)

    # Validate before any ledger append or commit. Missing metrics are a failure, never back-filled with defaults.
    if report is None or not report.metrics_ok:
        missing = ", ".join(report.missing_metrics) if report is not None else ", ".join(PLAN_REQUIRED_METRICS)
        print(
            f"Error: metrics validation failed for {plan_md_rel}: missing or non-numeric overall metrics: {missing}.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not report.size_ok:
        print(
            f"Error: Plan generation produced an empty or invalid plan file ({report.size} bytes, at least "
            f"{PLAN_SPEC.size_floor} expected).",
            file=sys.stderr,
        )
        sys.exit(1)
    if report.missing_sections:
        print(f"WARNING: {report.describe()}", file=sys.stderr)

    metrics = dict(report.metrics)

    # Recalculate EV based on config-driven formula: EV = P(success)*Impact + mu*LV - lambda*Entropy - Cost
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

    # Append plan summary to ledger
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

    plans_ledger_dir = os.path.join(repo_dir, "holon-knowledge/ledger")
    os.makedirs(plans_ledger_dir, exist_ok=True)
    plans_ledger_path = os.path.join(plans_ledger_dir, "plans.jsonl")
    with atomic_ledger_append(plans_ledger_path) as lf:
        lf.write(json.dumps(plan_entry) + "\n")

    # Format plan markdown before git commit
    try:
        converge_prettier([plan_md_rel], repo_dir=repo_dir)
    except Exception as e:
        print(f"Warning: Failed to converge prettier on {plan_md_rel}: {e}", file=sys.stderr)

    # Git commit and push
    run_cmd(["git", "add", plan_md_rel, "holon-knowledge/ledger/plans.jsonl"], cwd=repo_dir)
    run_cmd(["git", "config", "--local", "user.email", "planner-agent@holon-agentic-coder.com"], cwd=repo_dir)
    run_cmd(["git", "config", "--local", "user.name", "Holon Planner Agent"], cwd=repo_dir)
    run_cmd(["git", "commit", "-m", f"plan: {plan_id} created by {agent_name} ({model_name})"], cwd=repo_dir)
    run_cmd(["git", "push", "-u", "origin", plan_branch], cwd=repo_dir)

    print(f"Plan branch '{plan_branch}' successfully created, committed, and pushed.")
    print(stage_timer.summary("ok"))


if __name__ == "__main__":
    main()
