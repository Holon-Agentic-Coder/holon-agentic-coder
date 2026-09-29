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
# How far a truncated tail looks for a line boundary. Advancing to the next newline keeps a
# credential from being cut in half, while capping the look-ahead bounds how much of the newest
# diagnostic output the alignment may drop. sanitize_agent_output may widen it with the budget.
_TAIL_ALIGN_WINDOW: int = 4096

# Room taken out of the byte budget for the truncation marker itself, so the whole committed block
# (marker + tail) fits the budget rather than exceeding it by the marker length. The marker is a
# fixed ~60 character phrase plus at most two 10-digit byte counts, so it stays under 128 bytes for
# any realistic size. A budget below twice this still shares itself with the marker, so the reserve
# is capped at half the budget: a small budget keeps content instead of degrading to a marker-only
# record. Below 256 bytes the marker can still outrun the reserve, which costs budget accuracy, not
# the never-mid-token guarantee.
_TRUNCATION_MARKER_RESERVE: int = 128

# The leading fragment of a raw byte cut: the run of non-whitespace bytes the cut landed inside.
# Applied to bytes, because the cut is made on the encoded stream.
_LEADING_FRAGMENT_RE = re.compile(rb"\S*")

# Well-known provider credential prefixes, swept as whole tokens with no key-name anchor required.
# `redact_text` anchors on a key NAME, so a bare provider token (`token was: sk_live_...`) matches
# nothing there. Deliberately a closed list of literal prefixes: a generic high-entropy rule would
# shred legitimate base64 and minified-JSON diagnostics, which is a coverage regression.
_PROVIDER_TOKEN_RE = re.compile(
    r"(?:"
    r"(?:sk|pk)_(?:live|test)_[A-Za-z0-9_-]{8,}"
    r"|(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_-]{8,}"
    r"|github_pat_[A-Za-z0-9_-]{8,}"
    r"|glpat-[A-Za-z0-9_-]{8,}"
    r"|xox[baprs]-[A-Za-z0-9_-]{8,}"
    r"|AIza[0-9A-Za-z_-]{30,}"
    r"|AKIA[0-9A-Z]{16}"
    r")"
)

# Explicit list of flags whose next argument must be masked.
# Note: Suffixes like "-token", "_token", "-secret", "_secret", "-key", and "_key"
# are checked dynamically in _is_secret_flag.
# Any custom command line parameter that needs redaction must be added to this list.
SECRET_FLAGS = {
    "--password",
    "--passwd",
    "--auth",
}

_CLEAN_GIT_ENV_VARS: tuple[str, ...] = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


def _get_clean_git_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Returns a clean environment dictionary with leaked git variables removed."""
    env = dict(os.environ if base_env is None else base_env)
    for var in _CLEAN_GIT_ENV_VARS:
        env.pop(var, None)
    return env


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
    """Retrieve the byte limit for agent log retention from HOLON_AGENT_LOG_BYTES, defaulting to 65536.

    Clamped to `redact_text`'s input cap: the bounded tail is redacted *after* the byte cut, and
    above the cap that pass head/tail-cuts its own input, which would both discard the newest output
    the bound just retained and sever a key name at its own split. A byte clamp is a safe character
    clamp because UTF-8 never encodes a character in fewer than one byte.
    """
    val = os.getenv("HOLON_AGENT_LOG_BYTES")
    if val:
        try:
            parsed = int(val)
            if parsed > 0:
                if parsed > _MAX_REDACT_INPUT_LEN:
                    print(
                        f"Warning: HOLON_AGENT_LOG_BYTES={parsed} exceeds the {_MAX_REDACT_INPUT_LEN} "
                        "byte redaction input limit; bounding to the limit instead, because output "
                        "beyond it is not redacted faithfully.",
                        file=sys.stderr,
                    )
                    return _MAX_REDACT_INPUT_LEN
                return parsed
        except (ValueError, TypeError):
            pass
    return 65536


def sanitize_agent_output(raw_output: str, max_bytes: int = 65536) -> tuple[str, bool, int]:
    """Sanitize and bound agent output to a maximum byte length.

    Truncates the output to the tail `max_bytes` if the UTF-8 encoded length exceeds `max_bytes`,
    prepending an explicit truncation notice with the count of dropped bytes.

    Field contract: the returned text is `marker + tail`, and for any budget of at least twice the
    marker reserve (256 bytes) that block is at most `max_bytes` before the post-bound redaction.
    `agent_output_bytes` is the only exact size: the redaction applied afterwards rewrites secret
    values, which shrinks any value longer than the 7-character mask but grows a shorter one by a few
    bytes, so the committed block can overshoot the budget by that much per masked value. The
    `showing tail N bytes` figure is likewise an upper bound, measured before that redaction.

    The returned tail never begins mid-token. It either starts on a line boundary or, when no line
    boundary is reachable, immediately after the first whitespace-delimited fragment, which is the
    token the byte cut severed. A retained region containing no whitespace at all yields an empty
    tail: fail-closed, because a single token longer than the whole budget is unrecoverable.

    Args:
        raw_output: Raw string output from the agent process.
        max_bytes: Maximum allowed byte length for the whole returned block.

    Returns:
        A tuple of (bounded_output, is_truncated, dropped_bytes).
    """
    if max_bytes <= 0:
        max_bytes = 65536

    encoded = raw_output.encode("utf-8")
    total_bytes = len(encoded)

    if total_bytes <= max_bytes:
        return raw_output, False, 0

    # Leave room for the marker so marker + tail stays inside the budget, without letting the marker
    # swallow a small budget whole.
    reserve = min(_TRUNCATION_MARKER_RESERVE, max_bytes // 2)
    tail_budget = max_bytes - reserve
    tail_bytes = encoded[-tail_budget:] if tail_budget > 0 else b""
    dropped_bytes = total_bytes - len(tail_bytes)
    # Never sever a line: a credential cut in half matches neither the literal sweep (which needs
    # the whole value) nor the key-name regex (whose anchor the same cut breaks), so the surviving
    # fragment would be committed. Advancing to the next newline discards the *oldest* bytes inside
    # the look-ahead, so the window may scale with the budget, but may never retain less than half
    # of what the operator asked for.
    newline_at = tail_bytes.find(b"\n")
    align_limit = max(_TAIL_ALIGN_WINDOW, max_bytes // 2)
    if 0 <= newline_at < len(tail_bytes) - 1 and newline_at <= align_limit:
        dropped_bytes += newline_at + 1
        tail_bytes = tail_bytes[newline_at + 1 :]
    else:
        # No line boundary is reachable, so whatever the cut landed inside is still severed and no
        # later pass can recover it: the literal sweep needs the whole value and the key-name regex
        # needs the key name the cut discarded. A credential never contains whitespace, so the first
        # whitespace-delimited fragment of a raw cut is by construction that partial token; drop it
        # and every retained token is whole, exactly as for unbounded output.
        severed = _LEADING_FRAGMENT_RE.match(tail_bytes).end()
        dropped_bytes += severed
        tail_bytes = tail_bytes[severed:]
    decoded_tail = tail_bytes.decode("utf-8", errors="replace")
    marker = f"[Agent output truncated: {dropped_bytes} bytes dropped; showing tail {len(tail_bytes)} bytes]\n"
    return marker + decoded_tail, True, dropped_bytes


def redact_env_literals(text: str) -> str:
    """Replace the literal values of the environment credential variables, without any length cap.

    Safe on arbitrarily large streams, which is what makes it usable before the byte bound, where
    a value may still be intact. `redact_text`'s input cap would drop middle content instead of
    redacting it, so the regex pass is reserved for text that is already within the cap.

    Args:
        text: Input string potentially containing literal credential values.

    Returns:
        The input with every whole occurrence of `GITHUB_TOKEN`, `GH_TOKEN` and `HOLON_AGENT_KEY`
        replaced by asterisks.
    """
    if not text:
        return text

    s = text
    for env_var in ("GITHUB_TOKEN", "GH_TOKEN", "HOLON_AGENT_KEY"):
        val = os.getenv(env_var)
        if val and len(val) >= 4:
            s = s.replace(val, "*******")
            if val.strip() != val and len(val.strip()) >= 4:
                s = s.replace(val.strip(), "*******")

    return s


def redact_agent_secrets(text: str) -> str:
    """Redact secrets from agent output using redact_text, provider token shapes and literal env values.

    The provider-token sweep is anchored on the token itself rather than on a key name, so it still
    fires on `token was: sk_live_...`, where the key-name regex has nothing to match.

    Args:
        text: Input string potentially containing sensitive tokens or credentials.

    Returns:
        Sanitized string with sensitive tokens masked with asterisks.
    """
    if not text:
        return text

    s = redact_text(text)
    s = _PROVIDER_TOKEN_RE.sub("*******", s)
    return redact_env_literals(s)


def run_cmd(
    args: list[str],
    cwd: str | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
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

    cmd_env = env
    cmd_args = list(args)
    if cmd_args and cmd_args[0] == "git":
        cmd_env = _get_clean_git_env(env)
        if cwd:
            git_config_path = os.path.join(cwd, ".git", "config")
            if os.path.isfile(git_config_path):
                try:
                    with open(git_config_path) as cf:
                        cfg_content = cf.read()
                        if (
                            (f"directory = {cwd}" in cfg_content or "directory = *" in cfg_content)
                            and len(cmd_args) > 1
                            and cmd_args[1] != "-c"
                        ):
                            cmd_args = [cmd_args[0], "-c", f"safe.directory={cwd}", *cmd_args[1:]]
                except Exception:
                    pass

    result = subprocess.run(cmd_args, cwd=cwd, capture_output=True, text=True, env=cmd_env)
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


def _probe_git_repo(repo_dir: str) -> tuple[bool, str]:
    """Probes the repository at repo_dir to verify if git is healthy and usable.

    Returns a tuple of (is_healthy, error_output).
    """
    res = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo_dir, check=False)
    if res.returncode != 0:
        err = (res.stderr or res.stdout).strip()
        if "detected dubious ownership" in err or "safe.directory" in err:
            retry_res = run_cmd(
                ["git", "-c", f"safe.directory={repo_dir}", "rev-parse", "--is-inside-work-tree"],
                cwd=repo_dir,
                check=False,
            )
            git_config_path = os.path.join(repo_dir, ".git", "config")
            already_configured = False
            if os.path.isfile(git_config_path):
                try:
                    with open(git_config_path) as cf:
                        already_configured = f"directory = {repo_dir}" in cf.read()
                except Exception:
                    pass
            if retry_res.returncode == 0 and already_configured:
                pass
            else:
                return False, err
        else:
            return False, err

    # Check for stale index.lock
    index_lock = os.path.join(repo_dir, ".git", "index.lock")
    if os.path.exists(index_lock):
        return False, "stale .git/index.lock detected"

    # Check for dangling or missing HEAD symref
    res_head = run_cmd(["git", "rev-parse", "--verify", "HEAD"], cwd=repo_dir, check=False)
    if res_head.returncode != 0:
        err = (res_head.stderr or res_head.stdout).strip()
        return False, f"dangling HEAD: {err}"

    return True, ""


_LOCK_AGE_ENV = "HOLON_GIT_LOCK_AGE_SECONDS"
_DEFAULT_LOCK_AGE_SECONDS = 60.0
#: Lock paths that may belong to another live git process and are therefore never removed here.
_PROTECTED_LOCK_NAMES = ("config.lock", "packed-refs.lock", "shallow.lock", "HEAD.lock")


def _lock_age_limit() -> float:
    """Returns the minimum age in seconds after which a git lock file counts as abandoned."""
    raw = os.getenv(_LOCK_AGE_ENV)
    try:
        limit = float(raw) if raw else _DEFAULT_LOCK_AGE_SECONDS
    except (TypeError, ValueError):
        limit = _DEFAULT_LOCK_AGE_SECONDS
    return limit if limit > 0 else _DEFAULT_LOCK_AGE_SECONDS


def _remove_abandoned_locks(repo_dir: str) -> tuple[list[str], list[str]]:
    """Deletes only lock files that can be shown to be abandoned.

    The candidate set is deliberately narrow: ``.git/index.lock`` plus ``*.lock`` under ``.git/refs``
    and ``.git/logs``. Locks whose name is known to be written by long-lived plumbing
    (``config.lock``, ``packed-refs.lock``, ``shallow.lock``, ``HEAD.lock``) are never touched, and no
    lock is removed while it is younger than ``HOLON_GIT_LOCK_AGE_SECONDS``: a young lock can belong
    to a git process that is still running in this same workspace, and deleting it corrupts that
    transaction.

    Returns:
        A tuple of (removed_paths, still_held_paths).
    """
    limit = _lock_age_limit()
    git_dir = os.path.join(repo_dir, ".git")
    removed: list[str] = []
    still_held: list[str] = []
    if not os.path.isdir(git_dir):
        return removed, still_held

    candidates = [os.path.join(git_dir, "index.lock")]
    for subtree in ("refs", "logs"):
        root_dir = os.path.join(git_dir, subtree)
        for root, _, files in os.walk(root_dir):
            candidates.extend(os.path.join(root, name) for name in files if name.endswith(".lock"))

    for lock in candidates:
        if not os.path.isfile(lock) or os.path.basename(lock) in _PROTECTED_LOCK_NAMES:
            continue
        try:
            age = time.time() - os.path.getmtime(lock)
        except OSError:
            continue
        if age < limit:
            still_held.append(lock)
            continue
        with contextlib.suppress(Exception):
            os.remove(lock)
            removed.append(lock)
    return removed, still_held


def _resolve_local_ref(git_dir: str, refname: str) -> str | None:
    """Reads a local ref straight from the filesystem.

    Used while ``.git/HEAD`` itself is damaged: git refuses to run at all in that state
    ('fatal: not a git repository'), so the tip has to be found without shelling out.
    """
    loose = os.path.join(git_dir, "refs", "heads", *refname.split("/"))
    with contextlib.suppress(Exception):
        with open(loose) as rf:
            sha = rf.read().strip()
        if len(sha) >= 40:
            return sha
    packed = os.path.join(git_dir, "packed-refs")
    with contextlib.suppress(Exception), open(packed) as pf:
        for line in pf:
            line = line.strip()
            if not line or line.startswith(("#", "^")):
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1] == f"refs/heads/{refname}":
                return parts[0]
    return None


def _repair_git_repo(
    repo_dir: str,
    probe_err: str,
    default_branch: str = "main",
    plan_branch: str | None = None,
) -> bool:
    """Attempts to repair benign git failures in-place without rebuilding the repository.

    Repairs handled:
    - (a) Dubious ownership: persists safe.directory to local git config.
    - (b) Abandoned lock files: removed only when old enough to be provably stale.
    - (c) Leaked git environment variables: stripped from os.environ.
    - (d) Dangling or missing HEAD symref: restores HEAD onto ``default_branch`` (the execution
      branch), never onto whatever branch happens to sort first.

    Args:
        repo_dir: Workspace path.
        probe_err: Error text produced by :func:`_probe_git_repo`.
        default_branch: Branch HEAD must end up on -- the execution branch.
        plan_branch: Branch the workspace was cloned from, used as the tip source when the
            execution branch does not exist yet.

    Returns True if the repository was successfully restored to a usable state, False otherwise.
    """
    # Case (a): Dubious ownership
    if "detected dubious ownership" in probe_err or "safe.directory" in probe_err:
        run_cmd(
            ["git", "-c", f"safe.directory={repo_dir}", "config", "--local", "--add", "safe.directory", repo_dir],
            cwd=repo_dir,
            check=False,
        )
        git_config = os.path.join(repo_dir, ".git", "config")
        if os.path.isfile(git_config):
            try:
                with open(git_config) as f:
                    cfg = f.read()
                if f"directory = {repo_dir}" not in cfg:
                    with open(git_config, "a") as f:
                        f.write(f"\n[safe]\n\tdirectory = {repo_dir}\n")
            except Exception as e:
                print(f"Warning: unable to write safe.directory to local config: {e}", file=sys.stderr)

    # Case (b): Stale index lock or locked ref
    if (
        "index.lock" in probe_err
        or "cannot lock ref" in probe_err
        or os.path.exists(os.path.join(repo_dir, ".git", "index.lock"))
    ):
        removed, still_held = _remove_abandoned_locks(repo_dir)
        for lock in removed:
            print(f"Warning: removed abandoned git lock file {lock}", file=sys.stderr)
        if still_held:
            print(
                "Warning: git lock file(s) too young to be declared abandoned are left in place: "
                f"{', '.join(still_held)}. They may belong to a running git process.",
                file=sys.stderr,
            )

    # Case (c): Leaked environment variables
    for var in _CLEAN_GIT_ENV_VARS:
        if var in os.environ:
            os.environ.pop(var, None)

    # Case (d): Dangling or missing HEAD symref
    if (
        "dangling HEAD" in probe_err
        or "Needed a single revision" in probe_err
        or "HEAD" in probe_err
        or not os.path.exists(os.path.join(repo_dir, ".git", "HEAD"))
    ):
        git_dir = os.path.join(repo_dir, ".git")
        if os.path.isdir(git_dir):
            head_path = os.path.join(git_dir, "HEAD")
            target_branch = default_branch or "main"
            # HEAD damage makes every git command fail, so the tip has to be read from disk. Guessing
            # an arbitrary branch here would silently put the execution commit on the wrong ref,
            # so the only acceptable sources are the execution branch itself and the plan branch the
            # workspace was cloned from.
            tip_sha = _resolve_local_ref(git_dir, target_branch) or (
                _resolve_local_ref(git_dir, plan_branch) if plan_branch else None
            )
            if tip_sha:
                target_ref_dir = os.path.join(git_dir, "refs", "heads", *target_branch.split("/")[:-1])
                with contextlib.suppress(Exception):
                    os.makedirs(target_ref_dir, exist_ok=True)
                    with open(os.path.join(git_dir, "refs", "heads", *target_branch.split("/")), "w") as rf:
                        rf.write(f"{tip_sha}\n")
                with contextlib.suppress(Exception), open(head_path, "w") as f:
                    f.write(f"ref: refs/heads/{target_branch}\n")
                verify = run_cmd(["git", "symbolic-ref", "HEAD"], cwd=repo_dir, check=False)
                if verify.returncode != 0 or verify.stdout.strip() != f"refs/heads/{target_branch}":
                    run_cmd(["git", "symbolic-ref", "HEAD", f"refs/heads/{target_branch}"], cwd=repo_dir, check=False)
            else:
                print(
                    f"Warning: cannot restore HEAD to '{target_branch}': no local ref for it or for "
                    f"'{plan_branch or 'unknown'}'; leaving HEAD untouched.",
                    file=sys.stderr,
                )

    healthy, _ = _probe_git_repo(repo_dir)
    return healthy


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
    exec_id: str | None = None
    exec_file_path: str | None = None
    exec_file_rel: str | None = None
    timestamp_str: str = datetime.now(UTC).isoformat()
    recovery_triggered: bool = False
    base_tip: str | None = None
    backup_path: str | None = None

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
                # Sweep literal credentials across the whole stream first, while no value is severed,
                # then bound the tail. Ordering matters twice over: bounding first would leave a
                # credential that straddles the cut as a fragment that matches neither the literal
                # sweep nor the key-name regex, while redacting the whole stream first would let
                # redact_text's input cap discard the newest output before the bound sees it.
                pre_swept = redact_env_literals(combined)
                sanitized_out, agent_output_truncated, _dropped = sanitize_agent_output(pre_swept, max_bytes)
                agent_output_text = redact_agent_secrets(sanitized_out)
                agent_output_bytes = len(agent_output_text.encode("utf-8"))
            except Exception as e:
                print(f"Warning: Failed to capture/sanitize agent output: {e}", file=sys.stderr)
                agent_output_text = ""
                agent_output_truncated = False
                agent_output_bytes = 0

            recovery_triggered = False
            base_tip = None
            backup_path = None
            is_healthy, probe_err = _probe_git_repo(repo_dir)
            if not is_healthy:
                repaired = _repair_git_repo(repo_dir, probe_err, default_branch=exec_branch, plan_branch=plan_branch)
                if not repaired:
                    recovery_triggered = True
                    print(
                        "Warning: git repository invalid or missing after agent execution. Re-initializing git repo...",
                        file=sys.stderr,
                    )
                    git_dot = os.path.join(repo_dir, ".git")
                    backup_name = None
                    if os.path.exists(git_dot):
                        timestamp_suffix = int(datetime.now(UTC).timestamp())
                        backup_name = f".git-unusable-{timestamp_suffix}"
                        backup_path = os.path.join(repo_dir, backup_name)
                        if os.path.exists(backup_path):
                            backup_name = f".git-unusable-{timestamp_suffix}-{time.time_ns() % 1_000_000}"
                            backup_path = os.path.join(repo_dir, backup_name)
                        shutil.move(git_dot, backup_path)
                        print(f"Warning: unrepairable git repository moved aside to {backup_path}", file=sys.stderr)

                    run_cmd(["git", "init"], cwd=repo_dir)
                    info_dir = os.path.join(repo_dir, ".git", "info")
                    os.makedirs(info_dir, exist_ok=True)
                    exclude_file = os.path.join(info_dir, "exclude")
                    with open(exclude_file, "a") as ef:
                        if backup_name:
                            ef.write(f"\n{backup_name}\n")
                        ef.write(".git-unusable-*\n")

                    repo_url = get_repo_url()
                    run_cmd(["git", "remote", "add", "origin", repo_url], cwd=repo_dir, check=False)

                    fetch_retries = 3
                    try:
                        fetch_retries = int(os.getenv("HOLON_GIT_FETCH_RETRIES", "3"))
                    except (ValueError, TypeError):
                        fetch_retries = 3

                    fetch_success = False
                    fetch_err = ""
                    for attempt in range(fetch_retries):
                        fetch_res = run_cmd(
                            ["git", "fetch", "--no-tags", "origin", plan_branch],
                            cwd=repo_dir,
                            check=False,
                        )
                        if fetch_res.returncode == 0:
                            fetch_success = True
                            break
                        fetch_err = (fetch_res.stderr or fetch_res.stdout).strip()
                        if attempt < fetch_retries - 1:
                            time.sleep(1)

                    if fetch_success:
                        tip_res = run_cmd(["git", "rev-parse", "FETCH_HEAD"], cwd=repo_dir, check=False)
                        if tip_res.returncode == 0 and tip_res.stdout.strip():
                            base_tip = tip_res.stdout.strip()
                            run_cmd(["git", "update-ref", f"refs/heads/{exec_branch}", base_tip], cwd=repo_dir)
                            run_cmd(["git", "symbolic-ref", "HEAD", f"refs/heads/{exec_branch}"], cwd=repo_dir)
                            run_cmd(["git", "reset", base_tip], cwd=repo_dir)

                    if not base_tip:
                        exec_status = "failure"
                        summary = (
                            f"Git recovery failure: unable to preserve parent history from {plan_branch}. "
                            f"Corrupted repo backed up at {backup_path or 'unknown'}. Remote push aborted."
                        )
                        print(f"Error: {summary}", file=sys.stderr)
                        if fetch_err:
                            print(f"Fetch diagnostic error: {fetch_err}", file=sys.stderr)

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
                    # Agent output is untrusted and may contain a line of three backticks, which would
                    # close the fence early and render the rest of the record as markdown. CommonMark
                    # resolves this by a fence longer than the longest backtick run in the payload; the
                    # raw file and executions.jsonl stay authoritative either way.
                    longest_backtick_run = max((len(r) for r in re.findall(r"`+", agent_output_text)), default=0)
                    fence = "`" * max(3, longest_backtick_run + 1)
                    ef.write(f"## Agent Output\n{fence}\n")
                    if agent_output_text:
                        ef.write(agent_output_text if agent_output_text.endswith("\n") else f"{agent_output_text}\n")
                    ef.write(f"{fence}\n")
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
                "ledger_revision": 1,
            }
            try:
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "executions.jsonl"), "a") as ef:
                    ef.write(json.dumps(exec_entry) + "\n")
            except Exception as e:
                print(f"Warning: Failed to write execution ledger entry: {e}", file=sys.stderr)

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

            can_push = True
            if recovery_triggered:
                if not base_tip:
                    can_push = False
                else:
                    ancestry_check = run_cmd(
                        ["git", "merge-base", "--is-ancestor", base_tip, "HEAD"],
                        cwd=repo_dir,
                        check=False,
                    )
                    has_parent = (
                        run_cmd(["git", "rev-parse", "--verify", "HEAD^"], cwd=repo_dir, check=False).returncode == 0
                    )
                    tree_check = run_cmd(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=repo_dir, check=False)
                    head_files = set(tree_check.stdout.splitlines())
                    base_tree_check = run_cmd(
                        ["git", "ls-tree", "-r", "--name-only", base_tip],
                        cwd=repo_dir,
                        check=False,
                    )
                    base_files = set(base_tree_check.stdout.splitlines())

                    tree_valid = bool(head_files & base_files) if base_files else bool(head_files)

                    if ancestry_check.returncode != 0 or not has_parent or not tree_valid:
                        can_push = False
                        cause = []
                        if ancestry_check.returncode != 0:
                            cause.append(f"HEAD is not a descendant of base {base_tip}")
                        if not has_parent:
                            cause.append("HEAD has no parent commit")
                        if not tree_valid:
                            cause.append("committed tree missing repository files")
                        err_reason = "; ".join(cause)
                        print(f"Error: Git recovery verification failed: {err_reason}", file=sys.stderr)

                        summary = (
                            f"Git recovery failure: {err_reason}. "
                            f"Corrupted repo backed up at {backup_path or 'unknown'}. Remote push aborted."
                        )
                        if exec_file_path:
                            # Append, never rewrite: the agent's own summary stays readable, and the
                            # recovery verdict is recorded below it as its own section.
                            with contextlib.suppress(Exception):
                                fresh = not os.path.exists(exec_file_path)
                                with open(exec_file_path, "w" if fresh else "a") as ef:
                                    if fresh:
                                        ef.write(f"# Execution Record: {exec_id}\n\n")
                                        ef.write(f"- Plan Branch: `{plan_branch}`\n")
                                        ef.write(f"- Agent: `{agent_name}`\n")
                                        ef.write(f"- Agent Version: `{runner.get_version()}`\n")
                                        ef.write(f"- Model: `{model_name}`\n")
                                        ef.write(f"- Timestamp: `{timestamp_str}`\n\n")
                                    ef.write(f"\n## Git Recovery Failure\n{summary}\n\n")
                                    ef.write("- Status after verification: `Failure`\n")
                                    ef.write(f"- Verified base: `{base_tip or 'unresolved'}`\n")
                                    ef.write("- Push: refused, HEAD is not a descendant of the plan base\n")

                        fail_ledger_entry = {
                            "execution_id": exec_id,
                            "plan_branch": plan_branch,
                            "agent": agent_name,
                            "agent_version": runner.get_version(),
                            "model": model_name,
                            "status": "failure",
                            "summary": summary,
                            "execution_file": exec_file_rel,
                            "created_at": datetime.now(UTC).isoformat(),
                            # The ledger is append-only, so this row supersedes the one written before
                            # verification; the revision marks it as authoritative for readers.
                            "ledger_revision": 2,
                        }
                        with open(os.path.join(ledger_dir, "executions.jsonl"), "a") as ef:
                            ef.write(json.dumps(fail_ledger_entry) + "\n")

                        run_cmd(
                            ["git", "add", exec_file_rel, "holon-knowledge/ledger/executions.jsonl"],
                            cwd=repo_dir,
                            check=False,
                        )
                        run_cmd(
                            ["git", "commit", "-m", f"execute: record failure for {exec_id}"],
                            cwd=repo_dir,
                            check=False,
                        )

            # Invariant: A branch with no parent must never be pushed, whatever the execution status.
            parent_verify = run_cmd(["git", "rev-parse", "--verify", "HEAD^"], cwd=repo_dir, check=False)
            if parent_verify.returncode != 0:
                can_push = False

            if not can_push:
                print(
                    "Refusing to push execution branch: branch shares no history with plan base commit.",
                    file=sys.stderr,
                )
            else:
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
