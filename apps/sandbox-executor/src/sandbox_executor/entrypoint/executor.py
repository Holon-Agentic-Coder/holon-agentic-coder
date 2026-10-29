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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.agent_defaults import FALLBACK_AGENT, default_model_for
from sandbox_executor.agent_runner import (
    cleanup_repo_dir,
    get_repo_url,
    get_runner,
    get_workspace_dir,
)
from sandbox_executor.artefacts import EXECUTION_SPEC, ArtefactReport, execution_write_protocol
from sandbox_executor.budgets import (
    DEFAULT_ENTROPY_BUDGET,
    BudgetBreach,
    StageTimer,
    load_budgets,
    resolve_entropy_budget,
)
from sandbox_executor.formatting import converge_prettier
from sandbox_executor.ledger import (
    ActionRecord,
    append_action_record,
    atomic_ledger_append,
    extract_action_provenance_from_commit,
    format_commit_trailers,
    generate_action_id,
)

# The redactors live in the shared module; they are re-exported here so existing importers of
# `sandbox_executor.entrypoint.executor` keep working unchanged.
from sandbox_executor.redaction import _MAX_REDACT_INPUT_LEN as _MAX_REDACT_INPUT_LEN
from sandbox_executor.redaction import _PROVIDER_TOKEN_RE as _PROVIDER_TOKEN_RE
from sandbox_executor.redaction import SECRET_FLAGS as SECRET_FLAGS
from sandbox_executor.redaction import _is_secret_flag as _is_secret_flag
from sandbox_executor.redaction import redact_agent_secrets as redact_agent_secrets
from sandbox_executor.redaction import redact_args as redact_args
from sandbox_executor.redaction import redact_command_args
from sandbox_executor.redaction import redact_env_literals as redact_env_literals
from sandbox_executor.redaction import redact_text as redact_text
from sandbox_executor.turns import AuthoringOutcome, record_budget_failure, run_turn, supervise_authoring_turn

_MAX_PRINT_LEN: int = 5000
# Room taken out of the byte budget for the truncation marker itself, so the whole committed block
# (marker + tail) fits the budget rather than exceeding it by the marker length. The marker is a
# fixed ~60 character phrase plus at most two 10-digit byte counts, so it stays under 128 bytes for
# any realistic size. A budget below twice this still shares itself with the marker, so the reserve
# is capped at half the budget: a small budget keeps content instead of degrading to a marker-only
# record. Below 256 bytes the marker can still outrun the reserve, which costs budget accuracy, not
# the never-mid-line guarantee.
_TRUNCATION_MARKER_RESERVE: int = 128

# How far back past the byte-cut point the redaction region reaches, so that a key name which sits
# BEFORE its value is inside the text `redact_text` is given. Its anchor is the key name, and the
# separator between a key name and its value (`\s*(:\s*|=)\s*`, `Bearer\s+`) is pure whitespace,
# which includes newlines, so `api_key:` legally sits on the line ABOVE the value that gets it
# masked. Bounding first discarded that anchor line and committed the value whole. This is a COST
# knob (more context means more regex), never a safety knob: the guarantee that the committed block
# carries no unmasked credential comes from `_drop_unanchorable_front`, and it holds however far
# back the anchor actually sat.
_ANCHOR_CONTEXT_BYTES: int = 4096

# The largest byte budget the redactors can serve faithfully. The redaction region is
# `max_bytes + _ANCHOR_CONTEXT_BYTES` long and `redact_text` head/tail-cuts anything longer than
# `_MAX_REDACT_INPUT_LEN`, which is exactly the raw cut this pipeline exists to avoid.
_AGENT_LOG_BYTE_CEILING: int = _MAX_REDACT_INPUT_LEN - _ANCHOR_CONTEXT_BYTES

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


def is_action_logging_enabled(intent_data: dict[str, Any] | None = None) -> bool:
    """Determine if discrete action logging should be recorded to executions.jsonl."""
    flag = os.getenv("HOLON_ACTION_LOGGING") or os.getenv("HOLON_RECORD_ACTIONS")
    if flag is not None:
        return flag.lower() in ("1", "true", "yes", "on")
    if os.getenv("HOLON_ACTION_ORIGIN") or os.getenv("HOLON_REVIEW_SEVERITY"):
        return True
    if intent_data and any(
        k in intent_data for k in ("action_origin", "review_severity", "review_iteration", "review_finding_ids")
    ):
        return True
    return "PYTEST_CURRENT_TEST" not in os.environ


def get_agent_log_byte_budget() -> int:
    """Retrieve the byte limit for agent log retention from HOLON_AGENT_LOG_BYTES, defaulting to 65536.

    Clamped to `_AGENT_LOG_BYTE_CEILING`, which is `redact_text`'s input cap minus the anchor context
    the byte cut needs: the redaction region is the budget plus that context, and above the cap the
    regex pass head/tail-cuts its own input, which would both discard the newest output the bound
    just retained and sever a key name at its own split. A byte clamp is a safe character clamp
    because UTF-8 never encodes a character in fewer than one byte.
    """
    val = os.getenv("HOLON_AGENT_LOG_BYTES")
    if val:
        try:
            parsed = int(val)
            if parsed > 0:
                if parsed > _AGENT_LOG_BYTE_CEILING:
                    print(
                        f"Warning: HOLON_AGENT_LOG_BYTES={parsed} exceeds the {_AGENT_LOG_BYTE_CEILING} "
                        f"byte redaction limit (the {_MAX_REDACT_INPUT_LEN} byte redaction input cap minus "
                        f"the {_ANCHOR_CONTEXT_BYTES} bytes of anchor context the byte cut needs); bounding "
                        "to the limit instead, because output beyond it is not redacted faithfully.",
                        file=sys.stderr,
                    )
                    return _AGENT_LOG_BYTE_CEILING
                return parsed
        except (ValueError, TypeError):
            pass
    return 65536


def _marker_line(dropped_bytes: int, tail_len: int, reason: str = "") -> str:
    """Render the truncation notice.

    Units, because the two numbers deliberately do not reconcile: `tail_len` is a fact about the
    committed block (the exact byte length of the tail that follows the marker), while
    `dropped_bytes` counts everything discarded, which spans sizes -- bytes of the swept stream before
    the redaction region, the discarded region front, and whole lines trimmed from the already
    redacted text. Masking rewrites sizes as it goes, so `dropped_bytes + tail_len` is neither the
    size of the stream the agent produced nor the budget. Witnesses measured on this shape: an
    env-swept 130,000-byte stream reports `14013 + 51987 = 66000`, a masking-grown 138,000-byte
    stream reports `86807 + 65408 = 152215`. The wording is pinned by many tests; the meaning lives
    here.
    """
    return f"[Agent output truncated: {dropped_bytes} bytes dropped; showing tail {tail_len} bytes{reason}]\n"


def _marker_reason(*causes: str) -> str:
    """Render marker causes as the marker's trailing clauses, in the order they were hit."""
    return "".join(f"; {cause}" for cause in causes if cause)


def _bound_tail(encoded: bytes, max_bytes: int) -> tuple[bytes, int, str]:
    """Cut `encoded` down to a tail that fits the budget next to a marker.

    Returns (tail_bytes, dropped_bytes, marker_cause), where the cause is the bare phrase the marker
    renders, not a punctuated clause.

    The tail must start on a line boundary, or be empty: this helper bounds text and never redacts it,
    so a partial line is the one thing it will not hand back. That is a completeness rule for a
    bound-only helper. It is NOT a credential-safety argument -- a line boundary says nothing about
    whether the key name of a value on the first kept line is still present, and `redact_text`'s
    separator between a key name and its value is pure whitespace that includes newlines, so the
    anchor may sit on a line the cut already discarded. Callers that redact must redact a region and
    then drop its unanchorable front; see `prepare_agent_output_block`.
    """
    reserve = min(_TRUNCATION_MARKER_RESERVE, max_bytes // 2)
    tail_budget = max_bytes - reserve
    tail_bytes = encoded[-tail_budget:] if tail_budget > 0 else b""
    dropped_bytes = len(encoded) - len(tail_bytes)
    newline_at = tail_bytes.find(b"\n")
    if 0 <= newline_at < len(tail_bytes) - 1:
        dropped_bytes += newline_at + 1
        return tail_bytes[newline_at + 1 :], dropped_bytes, ""
    # One unterminated line longer than the whole budget: it cannot be redacted faithfully, so the
    # record keeps the notice and none of the line.
    return b"", dropped_bytes + len(tail_bytes), "no complete line within budget"


def _drop_unanchorable_front(redacted_region: str) -> tuple[str, int]:
    """Discard the front of a redacted region: returns (kept_text, dropped_bytes).

    Drops the leading whitespace, then the next complete line. That line is the only committed line
    whose anchor can sit outside the region: a matched value never spans a line (both value branches
    of `redact_text`'s pattern exclude whitespace and the quoted branch is non-DOTALL) and everything
    between a key name and its value is pure whitespace, so any later line is preceded inside the
    region by that first non-whitespace line, which a whitespace-only separator cannot cross.
    Dropping it is what makes the guarantee structural rather than a measurement of how far back an
    anchor happened to sit, of how much masking shrank or grew the text, or of the value being an
    environment literal.
    """
    leading_len = len(redacted_region) - len(redacted_region.lstrip())
    newline_at = redacted_region.find("\n", leading_len)
    if newline_at < 0:
        # The region's first line is also its last: none of it can be anchored from inside.
        return "", len(redacted_region.encode("utf-8"))
    front = redacted_region[: newline_at + 1]
    return redacted_region[len(front) :], len(front.encode("utf-8"))


def prepare_agent_output_block(raw_output: str, max_bytes: int = 65536) -> tuple[str, bool, int]:
    """Produce the `## Agent Output` block: redacted, and no larger than the byte budget.

    Field contract: the returned text is `marker + tail` and, for any budget of at least twice the
    marker reserve (256 bytes), that block is at most `max_bytes`. Below that the marker itself does
    not shrink, so it outruns its own reserve and the block overshoots by the difference -- a budget
    accuracy cost, never a redaction one.

    Ordering matters four times over:

    1. Sweep environment-literal credentials across the whole stream first, uncapped, while no value
       has been severed. `redact_text` cannot run here: its input cap would discard the newest output
       the bound is about to keep.
    2. Redact a REGION that reaches back over the byte-cut point, so the key name of a credential
       whose value the cut lands on is inside the text the regexes see.
    3. Discard the region's front, which is what makes the guarantee structural rather than a
       measurement of how far back an anchor happened to sit.
    4. Bound the REDACTED bytes. Masking rewrites sizes -- any value shorter than the 7-character
       mask gets LONGER (`api_key=ab` -> `api_key=*******`), growth unbounded in the number of
       matches, which once committed 95 KB under a 65,536 byte budget. Because the byte cut now runs
       after the redaction, that overshoot is impossible by construction rather than refitted after
       the fact, and only whole leading lines are ever dropped.

    INVARIANT: no credential that the redactors can recognise survives the cut unmasked, because the
    committed block never begins with the first value line of the redaction region. A matched value
    can never span a line -- both value branches of `redact_text`'s pattern exclude whitespace and the
    quoted branch is non-DOTALL -- and everything between a key name and its value is pure whitespace
    (`\\s*(:\\s*|=)\\s*`, `Bearer\\s+`). Therefore a value whose anchor fell outside the region can
    only be sitting on the region's first non-whitespace line: every later line is preceded inside the
    region by that non-whitespace line, which the whitespace-only separator cannot cross. Dropping
    that one line makes the guarantee structural: it does not depend on how far back the anchor
    actually was, on how much the masking shrank or grew the text, or on the value being an
    environment literal.

    RESIDUAL, stated honestly: the URL-query redactor's anchor (`[?&]token...=`) can be separated from
    its value by non-whitespace text spanning lines, so a `=`-adjacent secret on the region's SECOND
    line can still be beyond this rule. Broadening `redact_text`'s key alternation and adding entropy
    heuristics are both rejected in the loop ledger as coverage regressions, so that gap is deferred
    to its own bean rather than widened into this change.

    Args:
        raw_output: Combined stdout/stderr of the agent process.
        max_bytes: Maximum allowed byte length of the whole returned block, clamped to
            `_AGENT_LOG_BYTE_CEILING` because redaction above that is not faithful.

    Returns:
        A tuple of (block, is_truncated, dropped_bytes).
    """
    if max_bytes <= 0:
        max_bytes = 65536
    if max_bytes > _AGENT_LOG_BYTE_CEILING:
        # Above that ceiling `redact_text` head/tail-cuts the redaction region itself. That cut is
        # another raw byte cut with the same severing problem as the bound, and it also throws away
        # output this function was asked to retain, so the budget can never exceed what the redactors
        # handle faithfully -- the anchor context the region needs included.
        max_bytes = _AGENT_LOG_BYTE_CEILING

    swept = redact_env_literals(raw_output)
    encoded = swept.encode("utf-8")
    if len(encoded) <= max_bytes:
        # Redact the text that was measured, never the pre-sweep original: sweeping shrinks every
        # credential occurrence, so the original can sit above redact_text's input cap while the swept
        # text fits the budget. Redacting that original lets the cap fire on a path that reports
        # `truncated=False`, and a secret straddling its split is committed unredacted.
        redacted = redact_agent_secrets(swept)
        if len(redacted.encode("utf-8")) <= max_bytes:
            return redacted, False, 0

    reserve = min(_TRUNCATION_MARKER_RESERVE, max_bytes // 2)
    tail_budget = max_bytes - reserve

    # The byte cut lands at `cut_offset`; the REDACTION region reaches `_ANCHOR_CONTEXT_BYTES` past
    # it and then out to that line's start, so a key name sitting above the value the cut lands on is
    # inside the text the regexes see and gets masked instead of being dropped with the front.
    cut_offset = max(0, len(encoded) - tail_budget)
    context_start = max(0, cut_offset - _ANCHOR_CONTEXT_BYTES)
    context_break = encoded.rfind(b"\n", 0, context_start)
    ideal_start = 0 if context_break < 0 else context_break + 1
    # `redact_text` head/tail-cuts anything longer than its input cap, which is the raw cut this
    # pipeline exists to avoid, so the cap always wins over the context window: a single line longer
    # than the context pushes the ideal start past it and the region is pulled forward instead. The
    # front-drop rule below is what keeps unredactable text out of the block, never this window, so
    # losing context costs diagnostics and not safety -- and the marker says so.
    region_start = max(ideal_start, len(encoded) - _MAX_REDACT_INPUT_LEN)
    causes: list[str] = []
    if region_start > ideal_start:
        causes.append("anchor context dropped")

    dropped_bytes = region_start
    region = encoded[region_start:].decode("utf-8", errors="replace")
    redacted_region = redact_agent_secrets(region)
    if region_start:
        # None of this can be anchored from inside the region, so the region's first value line is
        # never committed.
        redacted_region, front_bytes = _drop_unanchorable_front(redacted_region)
        dropped_bytes += front_bytes
        if not redacted_region:
            causes.append("no complete line within budget")

    # Bound the REDACTED bytes, which is the only bound that can honour the budget once masking has
    # rewritten sizes: growing a value past the budget is no longer a refit problem, because nothing
    # is committed that was not already redacted. Whole leading lines are dropped, never a partial
    # line, and the newest bytes are always the ones kept.
    encoded_tail = redacted_region.encode("utf-8")
    pos = 0
    while len(encoded_tail) - pos > tail_budget:
        newline_at = encoded_tail.find(b"\n", pos)
        if newline_at < 0 or newline_at == len(encoded_tail) - 1:
            # No complete line fits: the record keeps the notice and none of the line.
            pos = len(encoded_tail)
            if "no complete line within budget" not in causes:
                causes.append("no complete line within budget")
            break
        pos = newline_at + 1
    dropped_bytes += pos
    encoded_tail = encoded_tail[pos:]

    tail = encoded_tail.decode("utf-8", errors="replace")
    marker = _marker_line(dropped_bytes, len(encoded_tail), _marker_reason(*causes))
    return marker + tail, True, dropped_bytes


def sanitize_agent_output(raw_output: str, max_bytes: int = 65536) -> tuple[str, bool, int]:
    """Bound `raw_output` to a trailing `max_bytes` block with a truncation notice.

    Field contract: the returned text is `marker + tail` and, for any budget of at least twice the
    marker reserve (256 bytes), that block is at most `max_bytes`. The returned tail never begins
    mid-line: it starts immediately after a newline, or the budget could not reach a line boundary at
    all and the tail is empty with the marker saying why. A partial line is never committed, because
    the bytes a byte cut deletes are exactly the bytes the redactors need in order to recognise a
    secret. This bounds only; it does not redact -- a caller that also redacts must redact the region
    around the cut and refit afterwards, which is what `prepare_agent_output_block` does.

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
    tail_bytes, dropped_bytes, cause = _bound_tail(encoded, max_bytes)
    decoded_tail = tail_bytes.decode("utf-8", errors="replace")
    marker = _marker_line(dropped_bytes, len(tail_bytes), _marker_reason(cause))
    return marker + decoded_tail, True, dropped_bytes


_FALLBACK_SAFE_DIRECTORIES: set[str] = set()


def _get_local_safe_directories(repo_dir: str) -> list[str]:
    """Retrieve safe.directory entries registered in the repository's local git config via git plumbing.

    Paths are returned as stripped strings.
    """
    cmd = ["git", "-c", f"safe.directory={repo_dir}", "config", "--local", "--get-all", "safe.directory"]
    res = subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True)
    if res.returncode == 0:
        return [line.strip() for line in res.stdout.splitlines() if line.strip()]
    return []


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
    redacted_args = redact_command_args(args)
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
            norm_cwd = os.path.realpath(cwd)
            is_safe = norm_cwd in _FALLBACK_SAFE_DIRECTORIES
            if not is_safe:
                safe_dirs = _get_local_safe_directories(cwd)
                for sd in safe_dirs:
                    if sd == "*" or os.path.realpath(sd) == norm_cwd:
                        is_safe = True
                        break
            # If configured as safe in local config, or if falling back, inject safe.directory per-invocation
            has_safe_dir = any(arg == "safe.directory" or arg.startswith("safe.directory=") for arg in cmd_args)
            if is_safe and not has_safe_dir and len(cmd_args) > 1:
                cmd_args = [cmd_args[0], "-c", f"safe.directory={cwd}", *cmd_args[1:]]

    result = subprocess.run(cmd_args, cwd=cwd, capture_output=True, text=True, env=cmd_env)
    if result.returncode != 0 and check:
        print(f"Command failed with code {result.returncode}", file=sys.stderr)
        print(f"Command args: {cmd_str}", file=sys.stderr)
        full_out = redact_agent_secrets(result.stdout)
        full_err = redact_agent_secrets(result.stderr)
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
            norm_repo = os.path.realpath(repo_dir)
            configured_dirs = _get_local_safe_directories(repo_dir)
            already_configured = norm_repo in _FALLBACK_SAFE_DIRECTORIES or any(
                sd == "*" or os.path.realpath(sd) == norm_repo for sd in configured_dirs
            )
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
        res = run_cmd(
            ["git", "-c", f"safe.directory={repo_dir}", "config", "--local", "--add", "safe.directory", repo_dir],
            cwd=repo_dir,
            check=False,
        )
        if res.returncode != 0:
            err_msg = (res.stderr or res.stdout).strip()
            print(
                f"Warning: unable to write safe.directory to local git config ({err_msg}); "
                f"falling back to per-invocation -c safe.directory",
                file=sys.stderr,
            )
            _FALLBACK_SAFE_DIRECTORIES.add(os.path.realpath(repo_dir))

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


def _entrypoint_git_runner(repo_dir: str) -> Callable[[list[str]], str]:
    """Adapt run_cmd (sandbox safe.directory and clean git environment) to the git.py runner interface."""

    def _run(args: list[str]) -> str:
        res = run_cmd(["git", *args], cwd=repo_dir, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"git {' '.join(args[:2])} failed: {redact_text((res.stderr or '').strip())}")
        return res.stdout if isinstance(res.stdout, str) else ""

    return _run


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


# Decision values returned by should_decompose().
DECISION_EXECUTE = "execute"
DECISION_DECOMPOSED = "decomposed"
DECISION_OVER_BUDGET = "over_budget"
# Exit status of an executor run that refused an over-budget plan and committed its over_budget row to the execution
# branch (pushed unless pushing is disabled). Distinct from 1 (a failed run) so the flow can tell the refusal apart and
# read that row, while a direct 'holon execute' still exits non-zero. Every other over_budget outcome exits 1.
OVER_BUDGET_EXIT_CODE = 3

# The repo-local identity of every commit the executor (and the agent it runs) makes inside the container.
EXECUTOR_GIT_EMAIL = "executor-agent@holon-agentic-coder.com"
EXECUTOR_GIT_NAME = "Holon Executor Agent"
EXECUTIONS_LEDGER_REL = "holon-knowledge/ledger/executions.jsonl"

# Marker lines around the unpushed-work patch printed on stderr when the execution branch push is refused.
UNPUSHED_PATCH_BEGIN = "BEGIN UNPUSHED EXECUTION PATCH"
UNPUSHED_PATCH_END = "END UNPUSHED EXECUTION PATCH"
# 96 KiB: large enough for a typical execution's patch, and below the redaction input limit, so redaction never cuts
# the middle out of a patch that is still printed. The raw patch is checked against it before redaction.
UNPUSHED_PATCH_MAX_BYTES = 96 * 1024
# Env files, credential stores and secrets directories, matched case-insensitively at any depth. They are left out of
# the unpushed patch and its diffstat, because pattern redaction cannot recognise every value they may hold.
UNPUSHED_PATCH_EXCLUDED_GLOBS = ("**/.env", "**/.env.*", "**/*credential*", "**/.netrc", "**/secrets/**")

# A resumable execution branch name (validated before it reaches a refspec) and a full commit sha.
_PARTIAL_BRANCH_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*/E-\d+-[A-Za-z0-9._-]+/_$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")

# Normalised heading texts (lower-cased, hyphens, underscores and runs of spaces collapsed to one space) that open an
# explicit sub-intent list. The legacy capitalised plural heading normalises to "sub intents".
_SUB_INTENT_HEADINGS = frozenset({"sub intent list", "sub intents"})
# An ATX heading (matched on a stripped line): 1-6 "#" followed by whitespace and the text, or by nothing at all, so
# a bare "#" or "##" is an empty heading, as in CommonMark; group 2 is None for an empty heading.
_HEADING_RE = re.compile(r"^(#{1,6})(?:\s+(.*?)\s*#*\s*)?$")
_LIST_ITEM_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+(.*)$")
# An item is a placeholder ("no child work") when, once normalised by _normalise_placeholder_item, its first whole
# word is one of these, whatever follows: "None (plan fits the budget)", "N/A (single step)" and "No sub-intents
# needed" are placeholders, while "Nonetheless ...", "Normalise ...", "Notify ..." and "Note ..." name real work. The
# planner template requires every real item to start with an imperative verb, so a real item never opens with one.
_PLACEHOLDER_RE = re.compile(r"^(?:none|n/a|na|nil|no|not)\b")
# Wrapping characters stripped from both ends of an item before the placeholder test: brackets, parentheses, straight
# and curly quotes, and markdown emphasis or code markers.
_PLACEHOLDER_WRAPPERS = "()[]{}<>\"'`*_~\u2018\u2019\u201c\u201d"
# Code fences, matched against a stripped line so a fence nested in a list item at any indentation is still a fence: an
# opener is 3+ backticks or tildes (a backtick fence's info string may not contain a backtick); it closes only on a line
# of the same character, at least as long, with nothing but whitespace after it.
_FENCE_OPEN_RE = re.compile(r"^(`{3,}(?=[^`]*$)|~{3,})")
_FENCE_CLOSE_RE = re.compile(r"^(`{3,}|~{3,})$")
# A thematic break: 3+ of the same "*", "-" or "_", optionally separated by spaces or tabs (matched on a stripped line).
_THEMATIC_BREAK_RE = re.compile(r"^(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
# A setext heading underline (matched on a stripped line): only "=" (level 1) or only "-" (level 2), no inner spaces.
_SETEXT_UNDERLINE_RE = re.compile(r"^(=+|-+)$")


@dataclass(frozen=True)
class DecompositionDecision:
    """Outcome of :func:`should_decompose`.

    Attributes:
        decision: ``"execute"``, ``"decomposed"`` (the plan lists an explicit sub-intent list) or ``"over_budget"``
            (the plan entropy exceeds the budget and the plan lists no sub-intent list).
        sub_intents: The parsed sub-intent list, one ``{slug, description, goal}`` dict per item; empty unless
            ``decision`` is ``"decomposed"``.
        entropy: The plan entropy compared against the budget.
        entropy_budget: The resolved entropy budget (see :func:`sandbox_executor.budgets.resolve_entropy_budget`).
    """

    decision: str
    sub_intents: list[dict[str, Any]] = field(default_factory=list)
    entropy: float = 0.0
    entropy_budget: float = DEFAULT_ENTROPY_BUDGET


_MAX_LIST_INDENT = 3
"""Deepest indentation, in columns, at which CommonMark still reads a list item as top-level."""


def _normalise_heading(text: str) -> str:
    """Lower-case a heading and collapse hyphens, underscores, non-breaking hyphens and whitespace to single spaces."""
    return re.sub(r"[\s\-_\u2010\u2011]+", " ", text.strip().lower()).strip()


def _normalise_placeholder_item(text: str) -> str:
    """Lower-case an item, strip wrapping brackets, quotes and emphasis markers, and drop trailing punctuation."""
    normalised = re.sub(r"\s+", " ", text).strip()
    previous = None
    while normalised != previous:
        previous = normalised
        normalised = normalised.strip(_PLACEHOLDER_WRAPPERS + " ").rstrip(".!?;:,")
    return normalised.lower()


def is_placeholder_item(text: str) -> bool:
    """True when a sub-intent list item says there is no child work rather than naming any (see _PLACEHOLDER_RE)."""
    return bool(_PLACEHOLDER_RE.match(_normalise_placeholder_item(text)))


def _paragraph_state(
    stripped: str, indent: int, kind: str | None, list_col: int | None
) -> tuple[str | None, int | None]:
    """Return the ``(paragraph kind, list content column)`` after a line outside fences that is not an ATX heading.

    The kind is ``"text"`` for a plain paragraph, ``"item"`` for a list item and its content (including lazy
    continuation lines, which CommonMark folds into the item's paragraph even at column 0) and ``None`` after a blank
    line, a thematic break or an indented code line. ``list_col`` is the content column of the open top-level item, so
    a line after a blank line still belongs to the item when indented at least that far.
    """
    if not stripped:
        return None, list_col
    nested = list_col is not None and indent >= list_col
    if indent <= _MAX_LIST_INDENT and _THEMATIC_BREAK_RE.match(stripped):
        return None, list_col if nested else None
    item = _LIST_ITEM_RE.match(stripped)
    if item and (indent <= _MAX_LIST_INDENT or nested):
        return "item", list_col if nested else indent + len(stripped) - len(item.group(1))
    if kind is not None:
        # A continuation line, lazy or not, stays in the paragraph it continues.
        return kind, list_col
    if nested:
        return "item", list_col
    return ("text" if indent <= _MAX_LIST_INDENT else None), None


def _setext_level(lines: list[str], index: int) -> int | None:
    """Return the setext level (1 for ``=``, 2 for ``-``) when the line after a plain paragraph line underlines it.

    The caller only asks for a line of a plain paragraph (``"text"`` from :func:`_paragraph_state`), never for a list
    item, its content or its lazy continuation, so a ``---`` after any of them stays a thematic break and a ``===``
    stays continuation text. The underline is only ``=`` or only ``-`` at 0-3 columns.
    """
    if index + 1 >= len(lines):
        return None
    following = lines[index + 1].expandtabs(4)
    underline = _SETEXT_UNDERLINE_RE.match(following.strip())
    if not underline or len(following) - len(following.lstrip()) > _MAX_LIST_INDENT:
        return None
    return 1 if underline.group(1)[0] == "=" else 2


def parse_sub_intent_list(plan_content: str) -> list[dict[str, Any]]:
    """Parse the explicit sub-intent list from a plan's markdown.

    Only a level-2 or level-3 heading whose normalised text is ``sub intent list`` or ``sub intents`` opens the list;
    top-level bullet and numbered items are collected until the next heading. The first item fixes the top-level
    indentation (0-3 columns, as CommonMark allows); lines indented deeper than it, such as nested detail bullets,
    never become items of their own: they are appended to the preceding item's description. A fence opens at any
    indentation (so a code block nested in an item counts) and every line inside it is ignored, headings included; it
    closes only on a run of the same character at least as long as its opener, and an unclosed fence runs to the end of
    the plan. Outside fences, only a heading indented 0-3 columns ends the section, whether ATX (``## Next``) or setext
    (a plain paragraph underlined with ``===`` or ``---``, see :func:`_setext_level`; a level-2 setext heading opens
    the list like its ATX form), and thematic breaks (``* * *``, or ``---`` not under a plain paragraph, such as one
    under an item or its lazy continuation line) at 0-3 columns are skipped.
    Per-step sub-intent recommendation bullets are never under such a heading, so they are ignored. Each item becomes
    ``{slug, description, goal}``; the slug and goal come from the item text only. Empty items, items with no
    alphanumeric character and placeholder items are skipped (an item that, stripped of wrapping brackets, quotes and
    emphasis and lower-cased, has ``none``, ``n/a``, ``na``, ``nil``, ``no`` or ``not`` as its first whole word; see
    :func:`is_placeholder_item`), and duplicate slugs get a numeric suffix.

    Args:
        plan_content: The plan markdown.

    Returns:
        The parsed items, in plan order; empty when the plan has no such section.
    """
    items: list[tuple[str, list[str]]] = []
    in_section = False
    fence: str | None = None
    base_indent: int | None = None
    lines = plan_content.splitlines()
    skip_underline = False
    paragraph: str | None = None
    paragraph_lines: list[str] = []
    list_col: int | None = None
    for index, line in enumerate(lines):
        if skip_underline:
            # The underline of the setext heading on the line above.
            skip_underline = False
            continue
        expanded = line.expandtabs(4)
        stripped = expanded.strip()
        if fence is not None:
            # Inside a fenced block nothing is a heading or an item; only a matching closing fence ends it.
            closing = _FENCE_CLOSE_RE.match(stripped)
            if closing and closing.group(1)[0] == fence[0] and len(closing.group(1)) >= len(fence):
                fence = None
            continue
        indent = len(expanded) - len(expanded.lstrip())
        opening = _FENCE_OPEN_RE.match(stripped)
        if opening:
            fence = opening.group(1)
            paragraph = None
            list_col = list_col if list_col is not None and indent >= list_col else None
            continue
        heading = _HEADING_RE.match(stripped) if indent <= _MAX_LIST_INDENT else None
        setext = None
        if not heading:
            previous = paragraph
            paragraph, list_col = _paragraph_state(stripped, indent, paragraph, list_col)
            if paragraph == "text":
                paragraph_lines = [*paragraph_lines, stripped] if previous == "text" else [stripped]
                setext = _setext_level(lines, index)
        if heading or setext:
            # A setext heading's text is its whole paragraph, so "Sub-intent" then "list" over "---" opens the list.
            if heading:
                level, text = len(heading.group(1)), heading.group(2) or ""
            else:
                level, text = setext, " ".join(paragraph_lines)
            in_section = level in (2, 3) and _normalise_heading(text) in _SUB_INTENT_HEADINGS
            base_indent = None
            paragraph, list_col = None, None
            skip_underline = setext is not None
            continue
        if not in_section or not stripped:
            continue
        if indent <= _MAX_LIST_INDENT and _THEMATIC_BREAK_RE.match(stripped):
            # "* * *" or "- - -" is a separator, never an item or an item's detail.
            continue
        if base_indent is not None and indent > base_indent:
            # Nested bullets and continuation lines belong to the item above them, never to a child of their own.
            items[-1][1].append(stripped)
            continue
        item = _LIST_ITEM_RE.match(stripped)
        if item and item.group(1).strip() and indent <= _MAX_LIST_INDENT:
            # CommonMark allows a top-level list to be indented 0-3 spaces; the first item fixes the section's level.
            if base_indent is None:
                base_indent = indent
            items.append((item.group(1).strip(), []))

    sub_intents: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for text, details in items:
        if is_placeholder_item(text) or not any(c.isalnum() for c in text):
            # Placeholders and punctuation-only items name no child work.
            continue
        base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].strip("-") or "sub-intent"
        count = seen.get(base, 0) + 1
        seen[base] = count
        slug = base if count == 1 else f"{base}-{count}"
        description = "\n".join([text, *details])
        sub_intents.append({"slug": slug, "description": description, "goal": text})
    return sub_intents


def should_decompose(
    plan_data: dict[str, Any], plan_content: str, intent_data: dict[str, Any] | None = None
) -> DecompositionDecision:
    """Decide whether a plan is executed, decomposed into its own sub-intent list, or rejected as over budget.

    The budget is the one the planner showed the agent and persisted on the plan row, resolved by
    :func:`sandbox_executor.budgets.resolve_entropy_budget` (plan row, then intent row, then the shared default). A
    non-empty explicit sub-intent list gives ``decomposed`` whatever the entropy; a plan above the budget without a
    list gives ``over_budget``; anything else gives ``execute``. No child work is ever invented. The flow, not this
    function, enforces ``MAX_SUB_INTENT_DEPTH``: it fails Stage 3 on a ``decomposed`` plan nested beyond that limit.

    Args:
        plan_data: The plan's ``plans.jsonl`` row.
        plan_content: The plan markdown.
        intent_data: The parent intent's ``intents.jsonl`` row, when known.

    Returns:
        A :class:`DecompositionDecision`.
    """
    entropy = _safe_float(plan_data.get("entropy"), 0.0)
    budget = resolve_entropy_budget(plan_data, intent_data)
    sub_intents = parse_sub_intent_list(plan_content)
    if sub_intents:
        return DecompositionDecision(DECISION_DECOMPOSED, sub_intents, entropy, budget)
    if entropy > budget:
        return DecompositionDecision(DECISION_OVER_BUDGET, [], entropy, budget)
    return DecompositionDecision(DECISION_EXECUTE, [], entropy, budget)


def sync_and_reconcile_pre_push(repo_dir: str, target_branch: str = "main") -> tuple[bool, str]:
    """Inspect whether remote target branch has advanced, merge it, and reconcile ledger conflicts.

    If the target branch on remote origin has diverged from HEAD, attempts an in-flight
    merge. If merge conflicts are strictly confined to holon-knowledge/ledger/*.jsonl,
    reconciles each conflicted ledger via deterministic union, stages the resolved files,
    verifies that no unmerged paths remain, and commits the merge.
    If any non-ledger file has conflicts, aborts the merge via `git merge --abort` and fails loudly.

    Args:
        repo_dir: Working directory of the git repository.
        target_branch: Target base branch name (defaults to "main").

    Returns:
        tuple[bool, str]: (success, message).

    Raises:
        None: Aborts any in-flight merge on failure and returns (False, err_msg).
    """
    from sandbox_executor.flow import reconcile_ledger_file

    # 1. Check if remote origin exists
    remotes_res = run_cmd(["git", "remote", "get-url", "origin"], cwd=repo_dir, check=False)
    if remotes_res.returncode != 0:
        return True, "No remote origin configured"

    # 2. Fetch target branch tip
    run_cmd(["git", "fetch", "origin", target_branch], cwd=repo_dir, check=False)
    target_ref = f"origin/{target_branch}"
    ref_check = run_cmd(["git", "rev-parse", "--verify", target_ref], cwd=repo_dir, check=False)
    if ref_check.returncode != 0:
        return True, f"Remote branch {target_ref} not found"

    remote_tip = run_cmd(["git", "rev-parse", target_ref], cwd=repo_dir, check=False).stdout.strip()

    # 3. Check divergence with HEAD
    mb_cmd = run_cmd(["git", "merge-base", "HEAD", target_ref], cwd=repo_dir, check=False)
    if mb_cmd.returncode != 0:
        is_shallow = run_cmd(["git", "rev-parse", "--is-shallow-repository"], cwd=repo_dir, check=False)
        if is_shallow.stdout.strip().lower() == "true":
            print(f"Warning: Shallow repository detected. Deepening history for {target_ref}...", file=sys.stderr)
            run_cmd(["git", "fetch", "--deepen=50", "origin", target_branch], cwd=repo_dir, check=False)
            mb_cmd = run_cmd(["git", "merge-base", "HEAD", target_ref], cwd=repo_dir, check=False)

    if mb_cmd.returncode != 0:
        print(f"Warning: Unable to compute merge-base with {target_ref}; skipping pre-push sync.", file=sys.stderr)
        return True, f"Unable to compute merge-base with {target_ref}"

    merge_base = mb_cmd.stdout.strip()
    if merge_base == remote_tip:
        return True, f"Branch is already up to date with {target_ref}"

    # 4. Divergence detected: Attempt merge
    _configure_executor_identity(repo_dir)

    merge_res = run_cmd(["git", "merge", target_ref, "--no-commit", "--no-ff"], cwd=repo_dir, check=False)
    if merge_res.returncode == 0:
        from sandbox_executor.flow import reconcile_ledgers

        reconciled_all = reconcile_ledgers(repo_dir)
        for rel in reconciled_all:
            run_cmd(["git", "add", rel], cwd=repo_dir, check=False)
        staged = run_cmd(["git", "diff", "--cached", "--quiet"], cwd=repo_dir, check=False)
        if staged.returncode != 0:
            run_cmd(
                ["git", "commit", "-m", f"chore(sync): sync target branch {target_branch} before push"],
                cwd=repo_dir,
                check=False,
            )
        return True, f"Cleanly merged {target_ref}"

    # Merge conflict detected
    unmerged_res = run_cmd(["git", "diff", "--name-only", "--diff-filter=U"], cwd=repo_dir, check=False)
    conflicted_files = [f.strip() for f in unmerged_res.stdout.splitlines() if f.strip()]
    if not conflicted_files:
        status_res = run_cmd(["git", "status", "--porcelain"], cwd=repo_dir, check=False)
        for line in status_res.stdout.splitlines():
            if any(line.startswith(c) for c in ("UU ", "AA ", "UD ", "DU ", "AU ", "UA ", "DD ")):
                conflicted_files.append(line[3:].strip())

    non_ledger_files = [
        f for f in conflicted_files if not (f.startswith("holon-knowledge/ledger/") and f.endswith(".jsonl"))
    ]
    if non_ledger_files:
        run_cmd(["git", "merge", "--abort"], cwd=repo_dir, check=False)
        err_msg = (
            f"Non-ledger merge conflicts detected with {target_ref} in files: "
            f"{non_ledger_files}. Aborting auto-merge and refusing push."
        )
        print(f"Error: {err_msg}", file=sys.stderr)
        return False, err_msg

    ledger_conflicts = [f for f in conflicted_files if f.startswith("holon-knowledge/ledger/") and f.endswith(".jsonl")]
    try:
        for ledger_path in ledger_conflicts:
            full_path = os.path.join(repo_dir, ledger_path)
            if not reconcile_ledger_file(full_path):
                run_cmd(["git", "merge", "--abort"], cwd=repo_dir, check=False)
                return False, f"Failed to reconcile ledger file {ledger_path}"
            run_cmd(["git", "add", ledger_path], cwd=repo_dir, check=True)

        from sandbox_executor.flow import reconcile_ledgers

        reconciled_all = reconcile_ledgers(repo_dir)
        for rel in reconciled_all:
            run_cmd(["git", "add", rel], cwd=repo_dir, check=False)

        rem_unmerged = run_cmd(["git", "diff", "--name-only", "--diff-filter=U"], cwd=repo_dir, check=False)
        if rem_unmerged.stdout.strip():
            rem_paths = rem_unmerged.stdout.strip()
            run_cmd(["git", "merge", "--abort"], cwd=repo_dir, check=False)
            return False, f"Unmerged paths remain after ledger reconciliation: {rem_paths}"

        run_cmd(
            ["git", "commit", "-m", f"chore(ledger): reconcile concurrent ledger updates with {target_ref}"],
            cwd=repo_dir,
            check=True,
        )
        return True, f"Successfully reconciled ledger conflicts and committed merge with {target_ref}"
    except Exception as exc:
        run_cmd(["git", "merge", "--abort"], cwd=repo_dir, check=False)
        return False, f"Exception during ledger reconciliation merge: {exc}"


def build_execution_prompt(
    repo_dir: str,
    plan_branch: str,
    plan_data: dict[str, Any],
    plan_content: str,
    intent_data: dict[str, Any],
    exec_id: str,
) -> str:
    """Render ``holon-config/prompts/executor.template.md`` when present, else the built-in execution prompt.

    The write protocol for the execution notes is appended by the caller, so it is always present whichever prompt is
    used.
    """
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
    # Substituted last so placeholder-like text inside the plan itself is never expanded.
    return template.replace("{plan_content}", plan_content)


def classify_execution_outcome(outcome: AuthoringOutcome) -> tuple[str, str, BudgetBreach | None, str]:
    """Execution status, summary, the budget breach to record (if any) and its reason for an authoring outcome.

    The *first* turn implements the plan; executor follow-ups (resume after a breach, re-prompt for missing sections)
    may only touch the execution notes. Therefore the outcome is decided from the first turn:

    - a breach of the implementation turn is a non-success, even when a notes-only resume made the notes usable: the
      code changes were cut off mid-implementation, so the breach is recorded and the partial code is salvaged into a
      ``partial work (failed)`` commit rather than recorded as a completed execution;
    - a breach confined to the notes-only re-prompt after a clean implementation turn never fails the execution: the
      code is complete, the breach is only logged and ``execution_notes_valid`` reports the notes as they are;
    - a non-zero first-turn exit counts as success only when the runner reported a structured truncation of its final
      message (``stopReason: length``) and the notes are complete (Bean 0138); a generic exit 1 (auth or API errors)
      never does.
    """
    first = outcome.first
    report = outcome.report
    if first.breach is not None:
        # The resume (if any) breached too only when budget_failure is set; record the budget that fired last.
        breach = outcome.budget_failure or first.breach
        if outcome.follow_up == "resume" and outcome.budget_failure is not None:
            reason = "resume did not recover"
        else:
            reason = "the implementation turn did not finish (partial code and execution notes salvaged)"
        return "failure", f"Plan execution stopped: {breach.describe()}; {reason}", breach, reason
    if outcome.breach is not None:
        print(
            f"WARNING: the notes-only follow-up turn stopped ({outcome.breach.describe()}); the implementation turn "
            "finished, so the code changes are kept and the notes are reported as they are.",
            file=sys.stderr,
        )
    if first.returncode == 0:
        return "success", "Plan executed successfully", None, ""
    if first.truncated and report is not None and report.complete:
        print(
            f"Info: agent exited with code {first.returncode} (stop reason: {first.stop_reason}) after writing "
            "complete execution notes; treating the exit status as informational."
        )
        return "success", "Plan executed successfully", None, ""
    return "failure", f"Plan execution failed with exit code {first.returncode}", None, ""


def finalise_execution_notes(notes_path: str, report: ArtefactReport | None) -> bool:
    """Validate the execution notes the agent wrote and return True when they are usable.

    Validation is report-only for the executor: missing sections are logged and recorded in the ledger summary, but
    never discard the code changes the agent made. When the agent wrote no notes, the ledger summary records
    ``execution_notes_valid: false`` with the size of the implementation turn's output (``agent_output_bytes`` and
    ``agent_output_truncated``), not the output text; the executor deliberately does not synthesise an ``executions/``
    file itself, because the ledger summary remains the authoritative execution record.
    """
    if report is not None and report.exists:
        if not report.usable or report.missing_sections:
            print(f"WARNING: {report.describe()}", file=sys.stderr)
        return report.usable
    print(
        f"WARNING: agent did not write executions/{os.path.basename(notes_path)}; the execution ledger summary "
        "records execution_notes_valid=false and only the size of the agent output (agent_output_bytes), not its text.",
        file=sys.stderr,
    )
    return False


def superseding_failure_row(base_row: dict[str, Any], summary: str) -> dict[str, Any]:
    """Failure summary row that supersedes ``base_row`` in the append-only ``executions.jsonl``.

    Readers select the row with the highest ``ledger_revision`` and never merge fields backwards
    (docs/ledger_schema.md, "Reader Contract for Superseding Rows"), so the new row repeats every
    authoritative field of the row it replaces (agent output accounting, execution notes, elapsed and
    budget seconds, sub-intents) and changes only the status, summary, timestamp and revision.
    """
    return {
        **base_row,
        "status": "failure",
        "summary": summary,
        "created_at": datetime.now(UTC).isoformat(),
        "ledger_revision": int(base_row.get("ledger_revision", 1)) + 1,
    }


def _configure_executor_identity(repo_dir: str) -> None:
    """Set the repo-local executor git identity, so the agent's own commits and the executor's commits can land.

    Called straight after the execution branch is created (before the agent runs) and again before the executor
    commits, because a git recovery re-initialises the repository and discards the local config.
    """
    run_cmd(["git", "config", "--local", "user.email", EXECUTOR_GIT_EMAIL], cwd=repo_dir, check=False)
    run_cmd(["git", "config", "--local", "user.name", EXECUTOR_GIT_NAME], cwd=repo_dir, check=False)


def _salvage_pathspec(notes_rel: str) -> list[str]:
    """Pathspec for everything in the working tree except the executions ledger and this run's notes file."""
    return ["--", ".", f":(exclude){EXECUTIONS_LEDGER_REL}", f":(exclude){notes_rel}"]


def _salvage_partial_work(
    repo_dir: str, exec_id: str, plan_branch: str, notes_rel: str, diff_base: str | None
) -> str | None:
    """Commit the agent's working-tree changes on a failed execution and return the partial head, if any.

    Tracked edits and deletions and untracked files that ``.gitignore`` does not exclude are committed in a commit of
    their own, ahead of the failure commit that carries the notes and the ledger row. Nothing is amended, squashed or
    reset. Returns the sha of HEAD when it differs from ``diff_base`` (the salvage commit or the agent's own last
    commit), else None: there was nothing to salvage.
    """
    run_cmd(["git", "add", "-A", *_salvage_pathspec(notes_rel)], cwd=repo_dir, check=False)
    staged = run_cmd(["git", "diff", "--cached", "--quiet"], cwd=repo_dir, check=False)
    if staged.returncode != 0:
        _configure_executor_identity(repo_dir)
        commit = run_cmd(
            ["git", "commit", "-m", f"execute: {exec_id} partial work (failed) for plan {plan_branch}"],
            cwd=repo_dir,
            check=False,
        )
        if commit.returncode != 0:
            left = run_cmd(["git", "diff", "--cached", "--name-only"], cwd=repo_dir, check=False).stdout or ""
            print(
                "Warning: could not commit the partial work; these files are left uncommitted: "
                f"{redact_agent_secrets(', '.join(left.split()))}; reason: "
                f"{redact_agent_secrets((commit.stderr or commit.stdout or '').strip())}",
                file=sys.stderr,
            )
            # Unstage them, so the failure commit still carries only the notes and the ledger row.
            run_cmd(["git", "reset", "-q", *_salvage_pathspec(notes_rel)], cwd=repo_dir, check=False)
    head = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo_dir, check=False)
    sha = (head.stdout or "").strip() if head.returncode == 0 else ""
    return sha if sha and sha != diff_base else None


def _unpushed_patch_text(repo_dir: str, plan_tip: str) -> str:
    """The redacted patch block for ``<plan tip>..HEAD``, or a redacted diffstat when it exceeds the size cap.

    Paths matching ``UNPUSHED_PATCH_EXCLUDED_GLOBS`` are left out of both, and a line before the block names the
    excluded paths that changed, so the reader knows the patch is incomplete.
    """
    excluded = [f":(exclude,glob,icase){glob}" for glob in UNPUSHED_PATCH_EXCLUDED_GLOBS]
    pathspec = ["--", ".", *excluded]
    rev_range = f"{plan_tip}..HEAD"
    matched = [f":(glob,icase){glob}" for glob in UNPUSHED_PATCH_EXCLUDED_GLOBS]
    left_out = run_cmd(["git", "diff", "--name-only", rev_range, "--", *matched], cwd=repo_dir, check=False)
    left_out_paths = (left_out.stdout or "").split()
    notice = (
        f"Unpushed patch excludes env and credential files: {redact_agent_secrets(', '.join(left_out_paths))}\n"
        if left_out_paths
        else ""
    )
    patch = run_cmd(["git", "format-patch", "--stdout", rev_range, *pathspec], cwd=repo_dir, check=False)
    raw_patch = patch.stdout or ""
    # Checked raw first: redaction cuts any input over its limit, which would hide an oversized patch.
    if patch.returncode == 0 and len(raw_patch.encode("utf-8")) <= UNPUSHED_PATCH_MAX_BYTES:
        patch_text = redact_agent_secrets(raw_patch)
        if len(patch_text.encode("utf-8")) <= UNPUSHED_PATCH_MAX_BYTES:
            return f"{notice}{UNPUSHED_PATCH_BEGIN}\n{patch_text.rstrip(chr(10))}\n{UNPUSHED_PATCH_END}"
    stat = run_cmd(["git", "diff", "--stat", rev_range, *pathspec], cwd=repo_dir, check=False)
    return (
        f"{notice}{redact_agent_secrets(stat.stdout or '').rstrip()}\n"
        f"Unpushed patch omitted: it could not be produced or exceeded UNPUSHED_PATCH_MAX_BYTES "
        f"({UNPUSHED_PATCH_MAX_BYTES} bytes)."
    )


def _report_unpushed_work(repo_dir: str, exec_branch: str, plan_tip: str | None, reason: str) -> None:
    """Report a refused push on stderr with a recoverable copy of the unpushed commits.

    The host captures the container's stderr, so the patch between the marker lines survives the ``--rm`` container
    without any new mount. Every line printed is redacted.
    """
    head = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo_dir, check=False)
    head_sha = (head.stdout or "").strip() or "unknown"
    print(
        f"Error: push of execution branch '{exec_branch}' was refused; local head {head_sha}: "
        f"{redact_agent_secrets(reason.strip())}",
        file=sys.stderr,
    )
    if not plan_tip:
        print("Unpushed patch omitted: the plan tip is unknown.", file=sys.stderr)
        return
    print(_unpushed_patch_text(repo_dir, plan_tip), file=sys.stderr)


def _resume_refusal(repo_dir: str, repo_url: str, partial_branch: str, plan_branch_prefix: str) -> str | None:
    """Fetch a failed execution branch for a resume and return why it cannot be used, or None when it can."""
    shaped = _PARTIAL_BRANCH_RE.match(partial_branch) and ".." not in partial_branch
    if not shaped or not partial_branch.startswith(f"{plan_branch_prefix}/E-"):
        return f"'{partial_branch}' is not an execution branch of this plan"
    fetch = run_cmd(
        ["git", "fetch", "--no-tags", repo_url, f"+refs/heads/{partial_branch}:refs/remotes/origin/{partial_branch}"],
        cwd=repo_dir,
        check=False,
    )
    return "fetch failed" if fetch.returncode != 0 else None


def _resolve_partial_head(
    repo_dir: str, repo_url: str, partial_branch: str, plan_tip: str | None, plan_branch_prefix: str
) -> tuple[str | None, str]:
    """Return the partial head to resume from (or None) and the reason, read from the failed branch's own ledger.

    The partial head must be recorded on the branch's latest summary row of a failed execution and must descend
    from the plan tip; otherwise the caller starts from the plan tip and logs the reason.
    """
    if not plan_tip:
        return None, "the plan tip is unknown"
    refusal = _resume_refusal(repo_dir, repo_url, partial_branch, plan_branch_prefix)
    if refusal:
        return None, refusal
    from sandbox_executor.flow import failed_partial_head

    ref = f"refs/remotes/origin/{partial_branch}"
    exec_id = partial_branch.removesuffix("/_").rsplit("/", 1)[-1]
    sha = failed_partial_head(repo_dir, ref, exec_id)
    if not sha or not _SHA_RE.match(sha):
        return None, "no partial_head"
    if run_cmd(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo_dir, check=False).returncode != 0:
        return None, "the partial head is not on the fetched branch"
    ancestry = ["git", "merge-base", "--is-ancestor", plan_tip, sha]
    if run_cmd(ancestry, cwd=repo_dir, check=False).returncode != 0:
        shallow = run_cmd(["git", "rev-parse", "--is-shallow-repository"], cwd=repo_dir, check=False)
        if (shallow.stdout or "").strip() != "true":
            return None, "not a descendant of the plan tip"
        run_cmd(["git", "fetch", "--deepen=50", repo_url, f"refs/heads/{partial_branch}"], cwd=repo_dir, check=False)
        if run_cmd(ancestry, cwd=repo_dir, check=False).returncode != 0:
            return None, "not a descendant of the plan tip"
    return sha, "resumed"


def continuation_note(partial_branch: str, partial_head: str) -> str:
    """Prompt note telling the agent it continues the partial work of a failed execution."""
    return (
        f"Continuation: the working tree already holds partial work from the failed execution {partial_branch} "
        f"(commit {partial_head}); its execution notes stay on that branch. Review the existing commits and the "
        "plan's completed steps, then continue from where that run stopped rather than redoing them."
    )


def main() -> None:
    is_default_repo = False
    repo_dir = None
    keep_workspace = False
    exec_id: str | None = None
    timestamp_str: str = datetime.now(UTC).isoformat()
    recovery_triggered: bool = False
    base_tip: str | None = None
    backup_path: str | None = None

    if len(sys.argv) < 2:
        print("Usage: executor.py <plan_branch> [agent_name] [model_name] [partial_execution_branch]")
        sys.exit(1)

    plan_branch = sys.argv[1]
    # The host CLI always passes resolved values; no harness detection inside the container.
    agent_name = sys.argv[2] if len(sys.argv) > 2 else FALLBACK_AGENT
    model_name = sys.argv[3] if len(sys.argv) > 3 else default_model_for(agent_name)
    # Passed by 'holon flow' only on a Stage 3 resume: a failed execution branch whose partial work to continue.
    partial_branch = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None

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
            # A workspace cloned before HOLON_REPO_URL became credential-free stores the tokenised
            # URL as its origin, so it mismatches here and is re-cloned, which also purges that
            # persisted token from the preserved .git/config.
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

        # Plan branch tip: the base the empty-diff guard compares the agent's work against, even on a resume.
        plan_tip_res = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo_dir, check=False)
        plan_tip = plan_tip_res.stdout.strip() if plan_tip_res.returncode == 0 and plan_tip_res.stdout else None
        partial_head: str | None = None
        if partial_branch:
            partial_head, resume_reason = _resolve_partial_head(
                repo_dir, repo_url, partial_branch, plan_tip, plan_branch_prefix
            )
            if partial_head:
                print(f"Resuming partial work from {partial_branch} at {partial_head}")
            else:
                print(
                    f"Starting from the plan tip of {plan_branch}: {redact_agent_secrets(resume_reason)}",
                    file=sys.stderr,
                )
        run_cmd(["git", "checkout", "-b", exec_branch, *([partial_head] if partial_head else [])], cwd=repo_dir)
        # Before the agent runs, so the commits it makes step by step land on the execution branch.
        _configure_executor_identity(repo_dir)

        # Proactively check and resolve any stage conflicts
        from sandbox_executor.flow import check_and_resolve_stage_conflicts

        check_and_resolve_stage_conflicts(repo_dir=repo_dir, target_branch="main", stage_name="execute")

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

        decision = should_decompose(plan_data, plan_content, intent_data)
        # Both non-execute outcomes record one summary row, commit and push it for the flow to read, and skip the agent.
        skip_agent = decision.decision != DECISION_EXECUTE
        # Set when a failed execution's code is salvaged; that commit is pushed even when nothing else is staged.
        salvaged_head: str | None = None

        if skip_agent:
            exec_entry = {
                "entry_type": "summary",
                "execution_id": exec_id,
                "plan_branch": plan_branch,
                "agent": agent_name,
                "agent_version": runner.get_version(),
                "model": model_name,
                "status": decision.decision,
                "entropy": decision.entropy,
                "entropy_budget": decision.entropy_budget,
                "created_at": timestamp_str,
            }
            if decision.decision == DECISION_DECOMPOSED:
                print(f"Plan lists an explicit sub-intent list of {len(decision.sub_intents)} item(s); recording it.")
                exec_entry["sub_intents"] = decision.sub_intents
                exec_entry["summary"] = (
                    f"Plan {plan_branch} lists {len(decision.sub_intents)} sub-intent(s); the flow runs each one "
                    "through the full pipeline."
                )
                action_text = f"Recorded the sub-intent list of {plan_branch} ({len(decision.sub_intents)} item(s))"
                commit_msg = (
                    f"execute: recorded the sub-intent list of {plan_branch} ({len(decision.sub_intents)} item(s))"
                )
            else:
                exec_entry["summary"] = (
                    f"Plan entropy {decision.entropy} exceeds the entropy budget {decision.entropy_budget} and the "
                    "plan lists no sub-intent list; the plan was not executed."
                )
                print(exec_entry["summary"])
                action_text = f"Refused over-budget plan {plan_branch}"
                commit_msg = f"execute: plan {plan_branch} is over its entropy budget"
            with atomic_ledger_append(os.path.join(ledger_dir, "executions.jsonl")) as ef:
                ef.write(json.dumps(exec_entry) + "\n")

            if is_action_logging_enabled(intent_data):
                with contextlib.suppress(Exception):
                    append_action_record(
                        os.path.join(ledger_dir, "executions.jsonl"),
                        ActionRecord(
                            action_id=generate_action_id(),
                            execution_id=exec_id,
                            event_type="plan_decomposition",
                            action=action_text,
                            category="config",
                            labels=["decomposition", "workflow"],
                            target=plan_branch,
                            outcome="success" if decision.decision == DECISION_DECOMPOSED else "failure",
                            action_origin="initial_implementation",
                        ),
                    )

            run_cmd(["git", "add", "holon-knowledge/ledger/executions.jsonl"], cwd=repo_dir)
        else:
            print(f"Executing plan {plan_branch} using agent {agent_name}...")
            prompt_file = os.path.join(tempfile.gettempdir(), f"exec_prompt-{exec_seq}.md")
            intent_file = os.path.join(tempfile.gettempdir(), f"exec_intent-{exec_seq}.json")
            notes_rel = f"executions/{exec_id}.md"
            notes_path = os.path.join(repo_dir, notes_rel)
            base_prompt = build_execution_prompt(repo_dir, plan_branch, plan_data, plan_content, intent_data, exec_id)
            if partial_branch and partial_head:
                base_prompt = f"{base_prompt}\n\n{continuation_note(partial_branch, partial_head)}"
            full_prompt = f"{base_prompt}\n\n{execution_write_protocol(notes_rel)}"

            budgets = load_budgets(os.environ)
            stage_timer = StageTimer("execute", budgets.stage_seconds("execute"))
            budget_breach: BudgetBreach | None = None
            notes_report: ArtefactReport | None = None
            try:
                with open(prompt_file, "w") as f:
                    f.write(full_prompt)
                with open(intent_file, "w") as f:
                    json.dump(intent_data, f)

                outcome = supervise_authoring_turn(
                    lambda text: runner.build_cmd(model_name, prompt_file, intent_file, text),
                    full_prompt,
                    artefact_path=notes_path,
                    artefact_rel=notes_rel,
                    spec=EXECUTION_SPEC,
                    stage="execute",
                    turn_budgets=budgets.turn,
                    stage_timer=stage_timer,
                    run=run_turn,
                    modify_code=False,
                    context=base_prompt,
                    watch_tree=repo_dir,
                    cwd=repo_dir,
                )
                exec_status, summary, budget_breach, budget_reason = classify_execution_outcome(outcome)
                # The implementation turn is the one classify_execution_outcome judges; follow-ups only touch the
                # notes, so output size and error context describe the implementation turn.
                res = outcome.first
                notes_report = outcome.report
            finally:
                for tf in (prompt_file, intent_file):
                    if os.path.exists(tf):
                        with contextlib.suppress(Exception):
                            os.remove(tf)

            if budget_breach is not None:
                # Written unconditionally (not gated on action logging): an unrecovered budget failure must always be
                # auditable, and this is the only write site, so there is exactly one record per stage failure.
                try:
                    os.makedirs(ledger_dir, exist_ok=True)
                    record_budget_failure(
                        os.path.join(ledger_dir, "executions.jsonl"),
                        budget_breach,
                        record_id=exec_id,
                        target=notes_rel,
                        clock_elapsed=stage_timer.elapsed(),
                        reason=budget_reason,
                    )
                except Exception as e:
                    print(f"Warning: Failed to record budget failure: {e}", file=sys.stderr)

            action_origin = (
                os.getenv("HOLON_ACTION_ORIGIN")
                or (intent_data.get("action_origin") or intent_data.get("origin") if intent_data else None)
                or "initial_implementation"
            )
            review_severity = os.getenv("HOLON_REVIEW_SEVERITY") or (
                intent_data.get("review_severity") if intent_data else None
            )
            review_iteration = None
            raw_iter = os.getenv("HOLON_REVIEW_ITERATION") or (
                intent_data.get("review_iteration") if intent_data else None
            )
            if raw_iter is not None:
                try:
                    review_iteration = int(raw_iter)
                except (ValueError, TypeError):
                    review_iteration = None
            review_finding_ids = None
            raw_findings = os.getenv("HOLON_REVIEW_FINDING_IDS") or (
                intent_data.get("review_finding_ids") if intent_data else None
            )
            if raw_findings:
                if isinstance(raw_findings, str):
                    review_finding_ids = [f.strip() for f in re.split(r"[,;\s]+", raw_findings) if f.strip()]
                elif isinstance(raw_findings, list):
                    review_finding_ids = [str(f).strip() for f in raw_findings if str(f).strip()]

            if action_origin == "initial_implementation":
                extracted_prov = extract_action_provenance_from_commit(repo_dir=repo_dir, commit_ref="HEAD")
                if extracted_prov.get("action_origin") != "initial_implementation":
                    action_origin = extracted_prov["action_origin"]
                    review_severity = extracted_prov.get("review_severity") or review_severity
                    review_iteration = (
                        extracted_prov.get("review_iteration") if review_iteration is None else review_iteration
                    )
                    review_finding_ids = extracted_prov.get("review_finding_ids") or review_finding_ids

            if is_action_logging_enabled(intent_data):
                with contextlib.suppress(Exception):
                    os.makedirs(ledger_dir, exist_ok=True)
                    append_action_record(
                        os.path.join(ledger_dir, "executions.jsonl"),
                        ActionRecord(
                            action_id=generate_action_id(),
                            execution_id=exec_id,
                            event_type="command_execution",
                            action=f"Executed agent {agent_name} with model {model_name}",
                            category="code",
                            labels=["agent", agent_name, "execution"],
                            target=f"{agent_name}:{model_name}",
                            outcome="success" if exec_status == "success" else "failure",
                            error_context=(
                                redact_text(res.stderr[:500]) if res.returncode != 0 and res.stderr else None
                            ),
                            action_origin=action_origin,
                            review_severity=review_severity,
                            review_iteration=review_iteration,
                            review_finding_ids=review_finding_ids,
                        ),
                    )

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
                # Sweep, bound on a line boundary, redact, then refit: see prepare_agent_output_block
                # for why that order and that final refit are both required.
                agent_output_text, agent_output_truncated, _dropped = prepare_agent_output_block(combined, max_bytes)
                agent_output_bytes = len(agent_output_text.encode("utf-8"))
            except Exception as e:
                print(f"Warning: Failed to capture/sanitise agent output: {e}", file=sys.stderr)
                agent_output_text = ""
                agent_output_truncated = False
                agent_output_bytes = 0

            notes_valid = finalise_execution_notes(notes_path, notes_report)

            recovery_triggered = False
            base_tip = None
            backup_path = None
            is_healthy, probe_err = _probe_git_repo(repo_dir)
            if not is_healthy:
                repaired = _repair_git_repo(repo_dir, probe_err, default_branch=exec_branch, plan_branch=plan_branch)
                if not repaired:
                    recovery_triggered = True
                    print(
                        "Warning: git repository invalid or missing after agent execution. Re-initialising git repo...",
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
                            print(f"Fetch diagnostic error: {redact_agent_secrets(fetch_err)}", file=sys.stderr)

                    if is_action_logging_enabled(intent_data):
                        with contextlib.suppress(Exception):
                            append_action_record(
                                os.path.join(ledger_dir, "executions.jsonl"),
                                ActionRecord(
                                    action_id=generate_action_id(),
                                    execution_id=exec_id,
                                    event_type="git_mutation",
                                    action="Re-initialised git repo due to corrupted repository",
                                    category="config",
                                    labels=["git", "recovery"],
                                    target=".git",
                                    outcome="success" if base_tip else "failure",
                                    action_origin=action_origin,
                                ),
                            )

            # Empty-diff guard (shared with holon flow and the review loop): an agent run that changes nothing
            # outside holon-knowledge/ledger/, plans/ and executions/ is never recorded as success, and whatever it did
            # change is committed only as failed partial work. Fail closed when the diff cannot be computed.
            failure_reason: str | None = None
            if exec_status == "success":
                # Imported here: sandbox_executor.git imports redact_text from this module.
                from sandbox_executor.git import (
                    EMPTY_DIFF_FAILURE_REASON,
                    EMPTY_DIFF_STATUS,
                    empty_diff_summary,
                    has_effective_diff,
                )

                diff_base = base_tip if recovery_triggered else plan_tip
                try:
                    if not diff_base:
                        raise RuntimeError(f"tip of {plan_branch} is unknown")
                    effective = has_effective_diff(repo_dir, diff_base, git_runner=_entrypoint_git_runner(repo_dir))
                except Exception as e:
                    exec_status = EMPTY_DIFF_STATUS
                    failure_reason = "diff_unverified"
                    summary = (
                        f"Could not verify the execution diff against {plan_branch}; treating it as a failure: "
                        f"{redact_text(str(e))}"
                    )
                else:
                    if not effective:
                        exec_status = EMPTY_DIFF_STATUS
                        failure_reason = EMPTY_DIFF_FAILURE_REASON
                        summary = empty_diff_summary(plan_branch)
                if failure_reason:
                    print(f"Error: {summary}", file=sys.stderr)

            # Format modified/untracked markdown files with prettier
            try:
                md_files: list[str] = []
                status_res = run_cmd(["git", "status", "--porcelain"], cwd=repo_dir, check=False)
                if status_res.returncode == 0:
                    for line in status_res.stdout.splitlines():
                        line = line.strip()
                        if not line or len(line) < 3:
                            continue
                        file_part = line[2:].strip()
                        if " -> " in file_part:
                            file_part = file_part.split(" -> ")[1].strip()
                        file_part = file_part.strip("\"'")
                        if file_part.endswith(".md"):
                            md_files.append(file_part)
                if md_files:
                    converge_prettier(md_files, repo_dir=repo_dir)
                    if is_action_logging_enabled(intent_data):
                        with contextlib.suppress(Exception):
                            append_action_record(
                                os.path.join(ledger_dir, "executions.jsonl"),
                                ActionRecord(
                                    action_id=generate_action_id(),
                                    execution_id=exec_id,
                                    event_type="doc_update",
                                    action=f"Formatted {len(md_files)} markdown files with prettier",
                                    category="doc",
                                    labels=["formatting", "prettier"],
                                    target=", ".join(md_files[:3]),
                                    outcome="success",
                                    action_origin=action_origin,
                                ),
                            )
            except Exception as e:
                print(f"Warning: Prettier formatting failed: {e}", file=sys.stderr)

            # Salvage on failure, in two commits: first the agent's code changes ("partial work (failed)"), then the
            # notes and the ledger row ("failed for plan"), so a failed execution never loses its code.
            salvage_base = base_tip if recovery_triggered else plan_tip
            if exec_status != "success" and salvage_base:
                salvaged_head = _salvage_partial_work(repo_dir, exec_id, plan_branch, notes_rel, salvage_base)

            exec_entry = {
                "entry_type": "summary",
                "execution_id": exec_id,
                "plan_branch": plan_branch,
                "agent": agent_name,
                "agent_version": runner.get_version(),
                "model": model_name,
                "status": exec_status,
                "summary": summary,
                "created_at": timestamp_str,
                "agent_output_truncated": agent_output_truncated,
                "agent_output_bytes": agent_output_bytes,
                "ledger_revision": 1,
                "execution_notes": notes_rel,
                "execution_notes_valid": notes_valid,
                "elapsed_seconds": round(stage_timer.elapsed(), 1),
                "budget_seconds": stage_timer.budget_seconds,
            }
            if failure_reason:
                exec_entry["failure_reason"] = failure_reason
            if salvaged_head:
                exec_entry["partial_head"] = salvaged_head
            try:
                os.makedirs(ledger_dir, exist_ok=True)
                with atomic_ledger_append(os.path.join(ledger_dir, "executions.jsonl")) as ef:
                    ef.write(json.dumps(exec_entry) + "\n")
            except Exception as e:
                print(f"Warning: Failed to write execution ledger entry: {e}", file=sys.stderr)

            if exec_status == "success":
                commit_msg = f"execute: {exec_id} completed for plan {plan_branch}"
            else:
                # The failure commit (ledger row and notes, after the partial-work commit) never reads as completed.
                commit_msg = f"execute: {exec_id} failed for plan {plan_branch}"
            trailers = format_commit_trailers(
                action_origin=action_origin,
                review_severity=review_severity,
                review_iteration=review_iteration,
                review_finding_ids=review_finding_ids,
            )
            if action_origin != "initial_implementation" or review_severity or review_iteration or review_finding_ids:
                commit_msg = f"{commit_msg}\n\n{trailers}"

            if is_action_logging_enabled(intent_data):
                with contextlib.suppress(Exception):
                    append_action_record(
                        os.path.join(ledger_dir, "executions.jsonl"),
                        ActionRecord(
                            action_id=generate_action_id(),
                            execution_id=exec_id,
                            event_type="git_mutation",
                            action=f"Committed execution changes: {commit_msg.splitlines()[0]}",
                            category="code",
                            labels=["git", "commit"],
                            target="HEAD",
                            outcome="success",
                            action_origin=action_origin,
                            review_severity=review_severity,
                            review_iteration=review_iteration,
                            review_finding_ids=review_finding_ids,
                        ),
                    )

            add_targets = [
                f for f in ("holon-knowledge/ledger/executions.jsonl",) if os.path.exists(os.path.join(repo_dir, f))
            ]
            if exec_status != "success" and os.path.exists(notes_path):
                # The notes (partial or not) explain the failure; the code changes are already in the partial commit.
                add_targets.append(notes_rel)
            if add_targets:
                run_cmd(["git", "add", *add_targets], cwd=repo_dir, check=False)
            if exec_status == "success":
                run_cmd(["git", "add", "-A"], cwd=repo_dir)

            # Log staged changes to provide visibility
            status_output = run_cmd(["git", "status", "--short"], cwd=repo_dir, check=False)
            print("Current git repository status:")
            print(redact_text(status_output.stdout))

        # Set once the commit is pushed (or kept locally when pushing is disabled), never when the push is refused.
        branch_recorded = False
        staged_check = run_cmd(["git", "diff", "--cached", "--quiet"], cwd=repo_dir, check=False)
        if staged_check.returncode != 0 or salvaged_head:
            if staged_check.returncode != 0:
                _configure_executor_identity(repo_dir)
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
                        fail_ledger_entry = superseding_failure_row(exec_entry, summary)
                        with atomic_ledger_append(os.path.join(ledger_dir, "executions.jsonl")) as ef:
                            ef.write(json.dumps(fail_ledger_entry) + "\n")

                        run_cmd(
                            ["git", "add", "holon-knowledge/ledger/executions.jsonl"],
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

            if can_push:
                target_base = (
                    os.getenv("HOLON_TARGET_BRANCH")
                    or (intent_data.get("target_branch") if intent_data else None)
                    or "main"
                )
                sync_ok, sync_msg = sync_and_reconcile_pre_push(repo_dir, target_branch=target_base)
                if not sync_ok:
                    can_push = False
                    print(f"Error: Pre-push sync failed: {sync_msg}", file=sys.stderr)
                    sync_summary = f"Pre-push sync failure: {sync_msg}. Remote push aborted."
                    # Also reachable when the agent was skipped, whose exec_entry is the decomposed or over_budget row.
                    fail_ledger_entry = superseding_failure_row(exec_entry, sync_summary)
                    with atomic_ledger_append(os.path.join(ledger_dir, "executions.jsonl")) as ef:
                        ef.write(json.dumps(fail_ledger_entry) + "\n")
                    run_cmd(
                        ["git", "add", "holon-knowledge/ledger/executions.jsonl"],
                        cwd=repo_dir,
                        check=False,
                    )
                    run_cmd(
                        ["git", "commit", "-m", f"execute: record sync failure for {exec_id}"],
                        cwd=repo_dir,
                        check=False,
                    )

            if not can_push:
                print(
                    "Refusing to push execution branch: safety or sync verification failed.",
                    file=sys.stderr,
                )
            else:
                skip_push = os.getenv("HOLON_SKIP_PUSH")
                if not (skip_push and skip_push.lower() in ("1", "true", "yes")):
                    push = run_cmd(["git", "push", "-u", "origin", exec_branch], cwd=repo_dir, check=False)
                    if push.returncode != 0:
                        # The container is removed on exit, so the commits survive only as the patch on stderr.
                        _report_unpushed_work(repo_dir, exec_branch, plan_tip, push.stderr or push.stdout or "")
                        if not skip_agent:
                            print(stage_timer.summary("failed"))
                        sys.exit(1)
                    branch_recorded = True
                    print(f"Execution branch '{exec_branch}' successfully committed and pushed.")
                else:
                    branch_recorded = True
                    print(f"Skipping git push for {exec_branch} (push disabled via environment variable).")
                    print(f"Execution branch '{exec_branch}' successfully committed locally.")
        else:
            print("No staged changes to commit.")

        if not skip_agent:
            print(stage_timer.summary("ok" if exec_status == "success" else "failed"))
            if budget_breach is not None:
                sys.exit(1)
        elif decision.decision == DECISION_OVER_BUDGET:
            refusal = (
                f"Error: plan {plan_branch} was not executed: its entropy {decision.entropy} exceeds the entropy "
                f"budget {decision.entropy_budget} and it lists no sub-intent list."
            )
            if not branch_recorded:
                # Nothing was pushed, so no execution branch records the over_budget row: a failed run, not a refusal.
                print(f"{refusal} Its over_budget row was not recorded on an execution branch.", file=sys.stderr)
                sys.exit(1)
            print(
                f"{refusal} Re-plan it within the budget, or add an explicit sub-intent list.",
                file=sys.stderr,
            )
            sys.exit(OVER_BUDGET_EXIT_CODE)

    except Exception as e:
        print(f"Execution failed: {redact_agent_secrets(str(e))}", file=sys.stderr)
        raise
    finally:
        if "runner" in locals() and hasattr(runner, "cleanup_ephemeral_credentials"):
            runner.cleanup_ephemeral_credentials()
        # reuse `keep_workspace` already computed at function start
        if is_default_repo and repo_dir and not keep_workspace:
            cleanup_repo_dir(repo_dir, raise_on_error=False)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
