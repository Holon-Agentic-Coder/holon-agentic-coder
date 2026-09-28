#!/usr/bin/env python3
"""Sandbox executor entrypoint for Holon Agentic Coder.

Clones or reuses a workspace repository, runs the configured agent, captures
execution results into the ledger, and commits/pushes the execution branch.

Key environment variables:
    HOLON_REPO_DIR: Override the default workspace directory.
    HOLON_KEEP_WORKSPACE: Set to '1' to skip cleanup and reuse the existing workspace.
    HOLON_IN_SANDBOX: Set to '1' to explicitly mark sandbox context.
    HOLON_SKIP_PUSH: Set to '1' to skip the git push step.
    HOLON_ROLE: When set, implies sandbox context.

Known limitations:
    - redact_args: Secret values starting with '-' are not masked to avoid
      over-masking legitimate flags that immediately follow a secret flag.
      See _is_secret_flag and the LIMITATION comment in redact_args for details.
"""

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.agent_runner import (
    cleanup_repo_dir,
    get_repo_url,
    get_runner,
    get_workspace_dir,
)

_MAX_REDACT_INPUT_LEN: int = 100_000
_MAX_PRINT_LEN: int = 5000

# Explicit list of flags whose next argument must be masked.
# Note: Suffixes like "-token", "_token", "-secret", "_secret", "-key", and "_key"
# are checked dynamically in _is_secret_flag.
# Any custom command line parameter that needs redaction must be added to this list.
SECRET_FLAGS = {
    "--password",
    "--passwd",
    "--auth",
}


def redact_text(text: str) -> str:
    if not text:
        return text
    # Guard against abnormally large inputs to prevent regex performance degradation on
    # pathological strings (ReDoS prevention). 100,000 chars is well above any realistic log
    # line length. Inputs exceeding this limit are truncated before applying redaction.
    if len(text) > _MAX_REDACT_INPUT_LEN:
        half_len = _MAX_REDACT_INPUT_LEN // 2
        # Align truncation split to line boundary if a newline exists close to the split point
        # to avoid severing tokens/secrets. Otherwise, truncate exactly at half_len.
        head_end = text.rfind("\n", 0, half_len)
        head = text[:half_len] if head_end == -1 or (half_len - head_end) > 1000 else text[:head_end]
        tail_start = text.find("\n", len(text) - half_len)
        tail = (
            text[-half_len:]
            if tail_start == -1 or (tail_start - (len(text) - half_len)) > 1000
            else text[tail_start + 1 :]
        )
        text = head + "\n... (truncated) ...\n" + tail
    s = re.sub(r"(https?://)[^@/]+@", r"\1*******@", text)
    # Redact sensitive URL query parameters including auth_code and code
    s = re.sub(
        r"([?&](?:token|api_key|access_token|secret|password|auth|bearer|auth_code|code)[^=]*=)[^\s&]+",
        r"\1*******",
        s,
        flags=re.IGNORECASE,
    )
    pattern = (
        r'(["\']?)(\b[a-zA-Z0-9_-]*(?:token|access_token|secret|password|api_key|auth|bearer|_pat|-pat|\bpat|_key|-key|secret_key|private_key|signing_key|encryption_key))\1'
        r'\s*(:\s*|=)\s*(?:(["\'])(.*?)\4|([^&\s\'"]+))'
    )

    def _replace_secret(match: re.Match) -> str:
        q_key = match.group(1) or ""
        key = match.group(2)
        sep = match.group(3)
        q_val = match.group(4) or ""
        return f"{q_key}{key}{q_key}{sep}{q_val}*******{q_val}"

    s = re.sub(pattern, _replace_secret, s, flags=re.IGNORECASE)
    s = re.sub(r"(Bearer\s+)[^\s]+", r"\1*******", s, flags=re.IGNORECASE)
    return s


def _is_secret_flag(flag: str) -> bool:
    flag_lowered = flag.lower()
    return flag_lowered in SECRET_FLAGS or (
        flag_lowered.startswith("-")
        and any(flag_lowered.endswith(sfx) for sfx in ("-token", "_token", "-secret", "_secret", "-key", "_key"))
    )


def redact_args(args: list[str]) -> list[str]:
    redacted = []
    mask_next = False
    for arg in args:
        s_arg = str(arg)
        if mask_next:
            is_secret = _is_secret_flag(s_arg)
            # LIMITATION: If a secret value happens to look like a flag (starts with -),
            # it will NOT be masked. This is a deliberate trade-off to avoid over-masking
            # when a flag like --verbose follows --token in the args list.
            if is_secret or re.match(r"^-{1,2}[a-zA-Z0-9_-]+$", s_arg):
                mask_next = False
            else:
                redacted.append("*******")
                mask_next = False
                continue

        parts = s_arg.split("=", 1)
        is_secret_flag = _is_secret_flag(parts[0])
        if is_secret_flag:
            if len(parts) == 2:
                redacted.append(f"{parts[0]}=*******")
            else:
                redacted.append(s_arg)
                mask_next = True  # If trailing (no next arg), the dangling flag is safe — no secret to miss.
        else:
            masked = redact_text(s_arg)
            redacted.append(masked)

    return redacted


def get_agent_log_byte_budget() -> int:
    """Retrieve the byte limit for agent log retention from HOLON_AGENT_LOG_BYTES, defaulting to 65536."""
    val = os.getenv("HOLON_AGENT_LOG_BYTES")
    if val:
        try:
            parsed = int(val)
            if parsed > 0:
                return parsed
        except (ValueError, TypeError):
            pass
    return 65536


def sanitize_agent_output(raw_output: str, max_bytes: int = 65536) -> tuple[str, bool, int]:
    """Sanitize and bound agent output to a maximum byte length.

    Truncates the output to the tail `max_bytes` if the UTF-8 encoded length
    exceeds `max_bytes`, prepending an explicit truncation notice with the
    count of dropped bytes.

    Args:
        raw_output: Raw string output from the agent process.
        max_bytes: Maximum allowed byte length for the tail.

    Returns:
        A tuple of (bounded_output, is_truncated, dropped_bytes).
    """
    if max_bytes <= 0:
        max_bytes = 65536

    encoded = raw_output.encode("utf-8")
    total_bytes = len(encoded)

    if total_bytes <= max_bytes:
        return raw_output, False, 0

    tail_bytes = encoded[-max_bytes:]
    dropped_bytes = total_bytes - len(tail_bytes)
    decoded_tail = tail_bytes.decode("utf-8", errors="replace")
    marker = f"[Agent output truncated: {dropped_bytes} bytes dropped; showing tail {len(tail_bytes)} bytes]\n"
    return marker + decoded_tail, True, dropped_bytes


def redact_agent_secrets(text: str) -> str:
    """Redact secrets from agent output using redact_text and literal environment token values.

    Args:
        text: Input string potentially containing sensitive tokens or credentials.

    Returns:
        Sanitized string with sensitive tokens masked with asterisks.
    """
    if not text:
        return text

    s = redact_text(text)
    for env_var in ("GITHUB_TOKEN", "GH_TOKEN", "HOLON_AGENT_KEY"):
        val = os.getenv(env_var)
        if val and len(val) >= 4:
            s = s.replace(val, "*******")
            if val.strip() != val and len(val.strip()) >= 4:
                s = s.replace(val.strip(), "*******")

    return s


def run_cmd(
    args: list[str],
    cwd: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Runs a command and returns the CompletedProcess.

    NOTE: The returned CompletedProcess contains raw, unredacted stdout/stderr.
    Callers must apply `redact_text` before printing or logging these fields.
    """
    redacted_args = redact_args(args)
    print_args = [arg[:250] + "..." if len(arg) > 250 else arg for arg in redacted_args]
    cmd_str = " ".join(print_args)
    if len(cmd_str) > _MAX_PRINT_LEN:
        cmd_str = cmd_str[: _MAX_PRINT_LEN - 3] + "..."
    print(f"Running: {cmd_str}")
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0 and check:
        print(f"Command failed with code {result.returncode}", file=sys.stderr)
        print(f"Command args: {cmd_str}", file=sys.stderr)
        full_out = redact_text(result.stdout)
        full_err = redact_text(result.stderr)
        out = full_out
        err = full_err
        if len(out) > _MAX_PRINT_LEN:
            half_print = _MAX_PRINT_LEN // 2
            out = out[:half_print] + "\n... (truncated) ...\n" + out[-half_print:]
        if len(err) > _MAX_PRINT_LEN:
            half_print = _MAX_PRINT_LEN // 2
            err = err[:half_print] + "\n... (truncated) ...\n" + err[-half_print:]
        print(f"Stdout:\n{out}", file=sys.stderr)
        print(f"Stderr:\n{err}", file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, redacted_args, output=full_out, stderr=full_err)
    return result


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _sanitize_string(name: str) -> str:
    s = re.sub(r"\s+", "-", name.replace("/", "_"))
    s = re.sub(r"[^a-zA-Z0-9_.-]", "", s)
    s = re.sub(r"-+", "-", s)
    s = re.sub(r"\.+", ".", s)
    s = s[:64].strip(".-")
    return s or "unknown"


def should_decompose(plan_data: dict[str, Any], plan_content: str) -> tuple[bool, list[dict[str, Any]]]:
    """Determine if a plan should be decomposed into sub-intents.

    Returns (should_decompose_bool, list_of_sub_intents).
    Triggers decomposition if:
    1. Plan entropy exceeds entropy_budget (or default threshold 5.0).
    2. Plan content explicitly contains a sub-intents section or table.
    """
    entropy = _safe_float(plan_data.get("entropy"), 0.0)
    budget = _safe_float(plan_data.get("entropy_budget"), 5.0)
    sub_intents = []

    # Check explicit sub-intents block in markdown
    if "## Sub-Intents" in plan_content or "### Sub-Intents" in plan_content:
        lines = plan_content.splitlines()
        in_section = False
        for line in lines:
            if "Sub-Intents" in line:
                in_section = True
                continue
            if in_section and line.startswith("#"):
                break
            stripped_line = line.strip()
            if in_section and (
                stripped_line.startswith("- ")
                or stripped_line.startswith("* ")
                or re.match(r"^\d+\.\s+", stripped_line)
            ):
                text = re.sub(r"^([\s\-*]|\d+\.)\s*", "", stripped_line).strip()
                if text:
                    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
                    sub_intents.append(
                        {
                            "slug": slug or f"sub-intent-{len(sub_intents) + 1}",
                            "description": text,
                            "goal": text,
                        }
                    )

    if entropy > budget or len(sub_intents) > 0:
        if not sub_intents:
            # Generate fallback sub-intents based on plan decomposition
            sub_intents = [
                {
                    "slug": "sub-intent-part-1",
                    "description": f"Decomposed sub-intent part 1 for plan {plan_data.get('plan_id', 'plan')}",
                    "goal": "Implement component core logic",
                },
                {
                    "slug": "sub-intent-part-2",
                    "description": f"Decomposed sub-intent part 2 for plan {plan_data.get('plan_id', 'plan')}",
                    "goal": "Implement integration tests and validation",
                },
            ]
        return True, sub_intents

    return False, []


def main() -> None:
    is_default_repo = False
    repo_dir = None
    keep_workspace = False

    if len(sys.argv) < 2:
        print("Usage: executor.py <plan_branch> [agent_name] [model_name]")
        sys.exit(1)

    plan_branch = sys.argv[1]
    agent_name = sys.argv[2] if len(sys.argv) > 2 else "antigravity-agent"
    model_name = sys.argv[3] if len(sys.argv) > 3 else "gemini-3.5-flash"

    runner = get_runner(agent_name)
    runner.validate()

    plan_branch_prefix = plan_branch
    if plan_branch_prefix.endswith("/_"):
        plan_branch_prefix = plan_branch_prefix[:-2]

    keep_workspace = str(os.getenv("HOLON_KEEP_WORKSPACE", "")).lower() in ("1", "true", "yes")
    in_sandbox_explicit = (
        str(os.getenv("HOLON_IN_SANDBOX", "")).lower() in ("1", "true", "yes")
        or bool(os.getenv("HOLON_ROLE"))
        or os.path.exists("/.dockerenv")
    )
    # Heuristic fallback: sandbox containers without HOLON_ROLE or /.dockerenv.
    # Use HOLON_REPO_DIR to override in ambiguous environments (Linux/macOS/Windows).
    in_sandbox_heuristic = os.getenv("USER") == "holon" or os.getenv("USERNAME") == "holon"
    if in_sandbox_heuristic and not in_sandbox_explicit:
        print(
            "Warning: using heuristic sandbox detection; set HOLON_IN_SANDBOX=1 to suppress this.",
            file=sys.stderr,
        )
    in_sandbox = in_sandbox_explicit or in_sandbox_heuristic

    is_default_repo = not os.getenv("HOLON_REPO_DIR")
    repo_dir = get_workspace_dir()
    if is_default_repo and not keep_workspace:
        if not in_sandbox:
            print(
                f"Warning: Cleaning default local repository directory at {repo_dir}.\n"
                "Set HOLON_KEEP_WORKSPACE=1 to retain.",
                file=sys.stderr,
            )
        cleanup_repo_dir(repo_dir, raise_on_error=True)
    os.makedirs(repo_dir, exist_ok=True)
    try:
        repo_url = get_repo_url()
        if not os.path.exists(os.path.join(repo_dir, ".git")):
            run_cmd(
                ["git", "clone", "--branch", plan_branch, "--single-branch", "--depth", "1", repo_url, "."],
                cwd=repo_dir,
            )
        else:
            # If the directory already contains a .git repository (e.g. workspace is preserved
            # via HOLON_KEEP_WORKSPACE=1), reuse the workspace with git fetch and force checkout.
            if not in_sandbox:
                print(
                    f"Warning: Reusing workspace at {repo_dir}.\n"
                    "Uncommitted changes and untracked files will be discarded.",
                    file=sys.stderr,
                )
            # Validate that the existing .git dir belongs to the expected remote before reusing.
            # A stale .git from a different repository would otherwise silently trigger the
            # reuse path (git fetch <new_url>) instead of a clean clone, producing confusing failures.
            remote_result = run_cmd(["git", "remote", "get-url", "origin"], cwd=repo_dir, check=False)
            if remote_result.returncode != 0 or remote_result.stdout.strip() != repo_url:
                print(
                    f"Warning: Remote URL mismatch or unreadable at {repo_dir}. "
                    "Discarding stale workspace and re-cloning.",
                    file=sys.stderr,
                )
                cleanup_repo_dir(repo_dir, raise_on_error=True)
                os.makedirs(repo_dir, exist_ok=True)
                run_cmd(
                    ["git", "clone", "--branch", plan_branch, "--single-branch", "--depth", "1", repo_url, "."],
                    cwd=repo_dir,
                )
            else:
                run_cmd(["git", "fetch", repo_url, plan_branch], cwd=repo_dir)
                if in_sandbox or repo_dir == os.path.expanduser("~/.holon-sandbox/workspace"):
                    run_cmd(["git", "clean", "-fd"], cwd=repo_dir)
                else:
                    # Deliberate trade-off: preserve local developer files (e.g. .env, local configs)
                    # when HOLON_KEEP_WORKSPACE=1 is used outside the sandbox. Untracked files from
                    # the previous run will NOT be removed. Set HOLON_KEEP_WORKSPACE=0 (the default)
                    # to ensure a clean workspace on every run.
                    print(
                        f"Warning: Skipping 'git clean -fd' as we are in a local workspace at {repo_dir}.\n"
                        "Untracked files from the previous run are preserved. "
                        "Unset HOLON_KEEP_WORKSPACE to ensure a clean workspace.",
                        file=sys.stderr,
                    )
                run_cmd(["git", "checkout", "-f", "-B", plan_branch, "FETCH_HEAD"], cwd=repo_dir)

        exec_seq = int(time.time())
        safe_agent = _sanitize_string(agent_name)
        safe_model = _sanitize_string(model_name)
        exec_id = f"E-{exec_seq}-{safe_agent}-{safe_model}"
        exec_branch = f"{plan_branch_prefix}/E-{exec_seq}-{safe_agent}-{safe_model}/_"

        run_cmd(["git", "checkout", "-b", exec_branch], cwd=repo_dir)

        # Load plan data from plans.jsonl
        plans_file_path = os.path.join(repo_dir, "holon-knowledge/ledger/plans.jsonl")
        plan_data = None
        if os.path.exists(plans_file_path):
            with open(plans_file_path) as f:
                for line_no, line in enumerate(f, start=1):
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        plan_id = data.get("plan_id")
                        if isinstance(plan_id, str) and (
                            plan_branch_prefix.split("/")[-1] == plan_id or plan_branch_prefix.endswith("/" + plan_id)
                        ):
                            plan_data = data
                            break
                    except Exception as e:
                        print(f"Warning: skipping line {line_no} in plans.jsonl: {e}", file=sys.stderr)

        if not plan_data:
            plan_data = {
                "plan_id": plan_branch_prefix.split("/")[-1],
                "intent_branch": plan_branch_prefix.split("/P-")[0] + "/_",
                "agent": agent_name,
                "model": model_name,
                "entropy": 3.0,
                "entropy_budget": 5.0,
            }

        # Load plan markdown content if available
        plan_content = ""
        plan_file_rel = plan_data.get("plan_file")
        if plan_file_rel and os.path.exists(os.path.join(repo_dir, plan_file_rel)):
            with open(os.path.join(repo_dir, plan_file_rel)) as f:
                plan_content = f.read()

        # Load intent data from intents.jsonl
        intents_file_path = os.path.join(repo_dir, "holon-knowledge/ledger/intents.jsonl")
        intent_data = None
        target_intent_branch = plan_data.get("intent_branch", "")
        if os.path.exists(intents_file_path):
            with open(intents_file_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        branch = data.get("branch")
                        if isinstance(branch, str) and (target_intent_branch.rstrip("/_") == branch.rstrip("/_")):
                            intent_data = data
                            break
                    except Exception as e:
                        # Ignore invalid or corrupted lines in intents ledger
                        print(f"Warning: skipping unparseable line in intents.jsonl: {e}", file=sys.stderr)

        if not intent_data:
            intent_data = {"branch": plan_data.get("intent_branch", "I-unknown")}

        timestamp_str = datetime.now(UTC).isoformat()
        ledger_dir = os.path.join(repo_dir, "holon-knowledge/ledger")
        os.makedirs(ledger_dir, exist_ok=True)

        decompose_needed, sub_intents = should_decompose(plan_data, plan_content)

        if decompose_needed:
            print(f"Plan entropy/structure requires decomposition into {len(sub_intents)} sub-intents.")
            created_sub_intents = []
            parent_intent_id = intent_data.get("branch", plan_data.get("intent_branch", "I-unknown"))

            for i, sub in enumerate(sub_intents, start=1):
                sub_slug = sub.get("slug", f"sub-intent-{i}")
                sub_branch_name = f"I-{exec_seq}-{sub_slug}"
                sub_entry = {
                    "branch": sub_branch_name,
                    "slug": sub_slug,
                    "description": sub.get("description", ""),
                    "goal": sub.get("goal", ""),
                    "parent_intent_id": parent_intent_id,
                    "status": "proposed",
                    "created_at": timestamp_str,
                }
                with open(os.path.join(ledger_dir, "intents.jsonl"), "a") as lf:
                    lf.write(json.dumps(sub_entry) + "\n")
                created_sub_intents.append(sub_entry)

            exec_entry = {
                "execution_id": exec_id,
                "plan_branch": plan_branch,
                "agent": agent_name,
                "agent_version": runner.get_version(),
                "model": model_name,
                "status": "decomposed",
                "sub_intents": created_sub_intents,
                "created_at": timestamp_str,
            }
            with open(os.path.join(ledger_dir, "executions.jsonl"), "a") as ef:
                ef.write(json.dumps(exec_entry) + "\n")

            commit_msg = f"execute: decomposed {plan_branch} into {len(created_sub_intents)} sub-intents"
            run_cmd(
                ["git", "add", "holon-knowledge/ledger/intents.jsonl", "holon-knowledge/ledger/executions.jsonl"],
                cwd=repo_dir,
            )
        else:
            print(f"Executing plan {plan_branch} using agent {agent_name}...")
            prompt_file = os.path.join(tempfile.gettempdir(), f"exec_prompt-{exec_seq}.md")
            intent_file = os.path.join(tempfile.gettempdir(), f"exec_intent-{exec_seq}.json")
            full_prompt = (
                f"Execute plan {plan_branch}.\n\n"
                f"Plan content:\n{plan_content}\n\n"
                f"Intent data:\n{json.dumps(intent_data, indent=2)}"
            )

            try:
                with open(prompt_file, "w") as f:
                    f.write(full_prompt)
                with open(intent_file, "w") as f:
                    json.dump(intent_data, f)

                agent_cmd = runner.build_cmd(model_name, prompt_file, intent_file, full_prompt)
                res = run_cmd(agent_cmd, cwd=repo_dir, check=False)
                if res.returncode == 0:
                    exec_status = "success"
                    summary = "Plan executed successfully"
                else:
                    exec_status = "failure"
                    summary = f"Plan execution failed with exit code {res.returncode}"
            finally:
                for tf in (prompt_file, intent_file):
                    if os.path.exists(tf):
                        with contextlib.suppress(Exception):
                            os.remove(tf)

            agent_output_text = ""
            agent_output_truncated = False
            agent_output_bytes = 0

            try:
                stdout = res.stdout or ""
                stderr = res.stderr or ""
                if stdout and stderr:
                    combined = f"{stdout}\n{stderr}" if not stdout.endswith("\n") else f"{stdout}{stderr}"
                else:
                    combined = stdout or stderr

                max_bytes = get_agent_log_byte_budget()
                sanitized_out, agent_output_truncated, _dropped = sanitize_agent_output(combined, max_bytes)
                agent_output_text = redact_agent_secrets(sanitized_out)
                agent_output_bytes = len(agent_output_text.encode("utf-8"))
            except Exception as e:
                print(f"Warning: Failed to capture/sanitize agent output: {e}", file=sys.stderr)
                agent_output_text = ""
                agent_output_truncated = False
                agent_output_bytes = 0

            exec_file_rel = f"executions/{exec_id}.md"
            exec_file_path = os.path.join(repo_dir, exec_file_rel)
            os.makedirs(os.path.dirname(exec_file_path), exist_ok=True)
            try:
                with open(exec_file_path, "w") as ef:
                    ef.write(f"# Execution Record: {exec_id}\n\n")
                    ef.write(f"- Plan Branch: `{plan_branch}`\n")
                    ef.write(f"- Agent: `{agent_name}`\n")
                    ef.write(f"- Agent Version: `{runner.get_version()}`\n")
                    ef.write(f"- Model: `{model_name}`\n")
                    ef.write(f"- Timestamp: `{timestamp_str}`\n\n")
                    ef.write(f"## Status\n{exec_status.capitalize()}\n\n## Summary\n{summary}\n\n")
                    ef.write("## Agent Output\n```\n")
                    if agent_output_text:
                        ef.write(agent_output_text if agent_output_text.endswith("\n") else f"{agent_output_text}\n")
                    ef.write("```\n")
            except Exception as e:
                print(f"Warning: Failed to write execution record {exec_file_path}: {e}", file=sys.stderr)

            exec_entry = {
                "execution_id": exec_id,
                "plan_branch": plan_branch,
                "agent": agent_name,
                "agent_version": runner.get_version(),
                "model": model_name,
                "status": exec_status,
                "summary": summary,
                "execution_file": exec_file_rel,
                "created_at": timestamp_str,
                "agent_output_truncated": agent_output_truncated,
                "agent_output_bytes": agent_output_bytes,
            }
            try:
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "executions.jsonl"), "a") as ef:
                    ef.write(json.dumps(exec_entry) + "\n")
            except Exception as e:
                print(f"Warning: Failed to write execution ledger entry: {e}", file=sys.stderr)

            git_check = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo_dir, check=False)
            if git_check.returncode != 0:
                print(
                    "Warning: git repository invalid or missing after agent execution. Re-initializing git repo...",
                    file=sys.stderr,
                )
                git_dot = os.path.join(repo_dir, ".git")
                if os.path.exists(git_dot):
                    if os.path.isdir(git_dot):
                        shutil.rmtree(git_dot, ignore_errors=True)
                    else:
                        with contextlib.suppress(Exception):
                            os.remove(git_dot)
                run_cmd(["git", "init"], cwd=repo_dir)
                run_cmd(["git", "symbolic-ref", "HEAD", f"refs/heads/{exec_branch}"], cwd=repo_dir)
                repo_url = get_repo_url()
                run_cmd(["git", "remote", "add", "origin", repo_url], cwd=repo_dir, check=False)

            commit_msg = f"execute: {exec_id} completed for plan {plan_branch}"
            add_targets = [
                f
                for f in (exec_file_rel, "holon-knowledge/ledger/executions.jsonl")
                if os.path.exists(os.path.join(repo_dir, f))
            ]
            if add_targets:
                run_cmd(["git", "add", *add_targets], cwd=repo_dir, check=False)
            if exec_status == "success":
                run_cmd(["git", "add", "-A"], cwd=repo_dir)

            # Log staged changes to provide visibility
            status_output = run_cmd(["git", "status", "--short"], cwd=repo_dir, check=False)
            print("Current git repository status:")
            print(redact_text(status_output.stdout))

        staged_check = run_cmd(["git", "diff", "--cached", "--quiet"], cwd=repo_dir, check=False)
        if staged_check.returncode != 0:
            run_cmd(["git", "config", "--local", "user.email", "executor-agent@holon-agentic-coder.com"], cwd=repo_dir)
            run_cmd(["git", "config", "--local", "user.name", "Holon Executor Agent"], cwd=repo_dir)
            run_cmd(["git", "commit", "-m", commit_msg], cwd=repo_dir)
            skip_push = os.getenv("HOLON_SKIP_PUSH")
            if not (skip_push and skip_push.lower() in ("1", "true", "yes")):
                run_cmd(["git", "push", "-u", "origin", exec_branch], cwd=repo_dir)
                print(f"Execution branch '{exec_branch}' successfully committed and pushed.")
            else:
                print(f"Skipping git push for {exec_branch} (push disabled via environment variable).")
                print(f"Execution branch '{exec_branch}' successfully committed locally.")
        else:
            print("No staged changes to commit.")

    except Exception as e:
        print(f"Execution failed: {e}", file=sys.stderr)
        raise
    finally:
        # reuse `keep_workspace` already computed at function start
        if is_default_repo and repo_dir and not keep_workspace:
            cleanup_repo_dir(repo_dir, raise_on_error=False)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
