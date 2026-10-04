#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime

from sandbox_executor.agent_runner import cleanup_repo_dir, get_repo_url, get_runner, get_workspace_dir
from sandbox_executor.formatting import converge_prettier


def run_cmd(args, cwd=None, env=None, check=True):
    print(f"Running: {' '.join(args)}")
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0 and check:
        print(f"Command failed with code {result.returncode}")
        print(f"Stdout:\n{result.stdout}")
        print(f"Stderr:\n{result.stderr}")
        sys.exit(result.returncode)
    return result


def _sanitize_model_name(model_name: str) -> str:
    """Sanitize a model name so it is safe for use in git branch names and filenames.

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
    import re

    metrics = {}
    row_pat = re.compile(r"\|\s*([a-zA-Z_0-9]+)\s*\|\s*([0-9\.\-]+)\s*\|")
    for line in content.splitlines():
        m = row_pat.search(line)
        if m:
            name = m.group(1).strip()
            try:
                val = float(m.group(2).strip())
                # Normalize standard metric names
                name_clean = name.replace("_pred", "").strip()
                metrics[name_clean] = val
            except ValueError:
                # Ignore malformed numeric values and continue parsing other metric rows.
                continue
    return metrics


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

    replacements = {
        "{timestamp}": str(plan_seq),
        "{agent}": agent_name,
        "{agent_version}": runner.get_version(),
        "{model}": model_name,
        "{intent_id}": intent_data.get("intent_id", intent_data.get("branch", intent_branch_prefix)),
        "{parent_intent_id}": intent_data.get("parent_intent_id", "NONE"),
        "{description}": intent_data.get("description", ""),
        "{plan_id": plan_id,  # cover partial braces
        "{plan_id}": plan_id,
        "{date}": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "{intent_json}": json.dumps(intent_data, indent=2),
        "{safe_model}": safe_model,
        "{budget_from_intent}": str(intent_data.get("entropy_budget", "15.0")),
    }
    for k, v in replacements.items():
        template = template.replace(k, v)

    prompt_file = f"/tmp/plan_prompt-{plan_seq}.md"
    with open(prompt_file, "w") as f:
        f.write(template)
        f.write("\n\nRepository file structure:\n")
        f.write(get_file_structure(repo_dir))

    intent_file = f"/tmp/intent-{plan_seq}.json"
    with open(intent_file, "w") as f:
        json.dump(intent_data, f)

    # Invoke AI agent
    plan_md_rel = f"plans/P-{plan_seq}-{agent_name}-{safe_model}.md"
    plan_md_path = os.path.join(repo_dir, plan_md_rel)
    os.makedirs(os.path.dirname(plan_md_path), exist_ok=True)

    # Load full prompt content for agents that don't support file-based parameters
    with open(prompt_file) as f:
        prompt_content = f.read()
    with open(intent_file) as f:
        intent_content = f.read()
    full_prompt = (
        f"{prompt_content}\n\n"
        f"Intent metadata:\n{intent_content}\n\n"
        "IMPORTANT INSTRUCTION: Output the complete markdown plan directly adhering to the template format. "
        "Include the full '# Plan for ...' document with the overall metrics table '| metric | value |'."
    )

    runner = get_runner(agent_name)
    cmd = runner.build_cmd(model_name, prompt_file, intent_file, full_prompt)

    # Show a truncated print command if the prompt argument is very large
    print_cmd = []
    for arg in cmd:
        if len(arg) > 100:
            print_cmd.append(arg[:100] + "...")
        else:
            print_cmd.append(arg)
    print(f"Running command: {' '.join(print_cmd)}")
    agent_failed = False
    exit_code = 0
    try:
        # Run agent and capture output
        result = subprocess.run(cmd, capture_output=True, text=True)
        wrote_file_directly = False
        if os.path.exists(plan_md_path) and os.path.getsize(plan_md_path) > 0:
            with open(plan_md_path) as f:
                direct_content = f.read().strip()
            if len(direct_content) >= 50 and ("# Plan" in direct_content or "| metric |" in direct_content):
                wrote_file_directly = True

        if not wrote_file_directly:
            if result.returncode == 0 and result.stdout.strip():
                stdout_text = result.stdout.strip()
                # Extract markdown block if output is wrapped in ```markdown ... ``` or ```md ... ```
                match = re.search(r"```(?:markdown|md)?\s*\n(.*?)\n```", stdout_text, re.DOTALL)
                if match:
                    stdout_text = match.group(1).strip()
                with open(plan_md_path, "w") as f:
                    f.write(stdout_text)
            else:
                print(f"Error: agent command failed or returned empty. code={result.returncode}", file=sys.stderr)
                print(f"Stdout:\n{result.stdout}", file=sys.stderr)
                print(f"Stderr:\n{result.stderr}", file=sys.stderr)
                agent_failed = True
                exit_code = result.returncode if result.returncode != 0 else 1
    except Exception as e:
        print(f"Error: Exception running agent: {e}", file=sys.stderr)
        agent_failed = True
        exit_code = 1

    # Clean up temp files
    for temp_f in [prompt_file, intent_file]:
        if os.path.exists(temp_f):
            os.remove(temp_f)

    if agent_failed:
        sys.exit(exit_code)

    if not os.path.exists(plan_md_path) or os.path.getsize(plan_md_path) < 50:
        print("Error: Plan generation produced an empty or invalid plan file.", file=sys.stderr)
        sys.exit(1)

    # Parse metrics
    with open(plan_md_path) as f:
        plan_content = f.read()

    # Validate that the generated plan contains structure and not just conversational chatter
    has_plan_structure = (
        "# Plan" in plan_content
        or "## Overall Plan" in plan_content
        or "## Metrics" in plan_content
        or "## Proposed Steps" in plan_content
        or "| metric |" in plan_content
    )
    if not has_plan_structure:
        print(
            f"Error: Generated plan does not contain standard plan structure or headers.\nContent:\n{plan_content}",
            file=sys.stderr,
        )
        sys.exit(1)

    metrics = parse_metrics(plan_content)

    # Ensure required metrics exist with default heuristics if parsing fails
    defaults = {"p_success": 0.7, "entropy": 3.0, "impact": 1.0, "cost": 0.5, "learning_value": 0.0}
    for k, default_val in defaults.items():
        if k not in metrics:
            metrics[k] = default_val

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
    }

    plans_ledger_dir = os.path.join(repo_dir, "holon-knowledge/ledger")
    os.makedirs(plans_ledger_dir, exist_ok=True)
    plans_ledger_path = os.path.join(plans_ledger_dir, "plans.jsonl")
    with open(plans_ledger_path, "a") as lf:
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


if __name__ == "__main__":
    main()
