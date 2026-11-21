#!/usr/bin/env python3
"""Sandbox executor entrypoint for Holon Agentic Coder.

Works in place on the host worktree that 'holon execute' and 'holon flow' bind-mount into the container, runs the
configured agent, captures execution results into the ledger, and commits the execution branch. The container never
touches the remote: the host fetches the plan and target branches first and pushes the execution branch afterwards.

Key environment variables:
    HOLON_TARGET_WORKTREE: The sandbox mountpoint, which marks the mounted host worktree (required).
    HOLON_IN_SANDBOX: Set to '1' to explicitly mark sandbox context.
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
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any, Self

from sandbox_executor.agent_defaults import FALLBACK_AGENT, default_model_for
from sandbox_executor.agent_runner import get_runner, get_workspace_dir
from sandbox_executor.artefacts import EXECUTION_SPEC, ArtefactReport, execution_write_protocol
from sandbox_executor.attribution_check import attribution_allows_publish
from sandbox_executor.budgets import BudgetBreach, StageTimer, load_budgets

# The decomposition rule lives in the neutral module; it is re-exported here so existing importers keep working.
from sandbox_executor.decomposition_rule import DECISION_DECOMPOSED as DECISION_DECOMPOSED
from sandbox_executor.decomposition_rule import DECISION_EXECUTE as DECISION_EXECUTE
from sandbox_executor.decomposition_rule import DECISION_OVER_BUDGET as DECISION_OVER_BUDGET
from sandbox_executor.decomposition_rule import DecompositionDecision as DecompositionDecision
from sandbox_executor.decomposition_rule import InvalidPlanDecisionError
from sandbox_executor.decomposition_rule import is_placeholder_item as is_placeholder_item
from sandbox_executor.decomposition_rule import parse_sub_intent_list as parse_sub_intent_list
from sandbox_executor.decomposition_rule import should_decompose as should_decompose
from sandbox_executor.entrypoint.changed_markdown import changed_markdown_files
from sandbox_executor.entrypoint.empty_diff_excerpt import report_empty_diff_output
from sandbox_executor.entrypoint.execution_workspace import continuation_note as continuation_note
from sandbox_executor.entrypoint.execution_workspace import (
    resolve_partial_head,
    validate_mounted_workspace,
    workspace_mounted,
)
from sandbox_executor.entrypoint.executor_test_run import measure_executor_tests
from sandbox_executor.entrypoint.partial_resume import merge_plan_tip, start_execution_branch
from sandbox_executor.entrypoint.sync_conflict import SyncConflictMessage, record_sync_failure
from sandbox_executor.entrypoint.sync_conflict import superseding_failure_row as superseding_failure_row
from sandbox_executor.entrypoint.taken_ids import taken_child_ids
from sandbox_executor.formatting import converge_prettier
from sandbox_executor.git import GitRepository
from sandbox_executor.ledger import (
    ActionRecord,
    LedgerRewriteError,
    append_action_record,
    atomic_ledger_append,
    build_execution_id,
    extract_action_provenance_from_commit,
    format_commit_trailers,
    generate_action_id,
    suppress_ledger_failures,
)
from sandbox_executor.plan_paths import is_row_of_plan, recorded_plan_file

# The redactors live in the shared module; they are re-exported here so existing importers of
# `sandbox_executor.entrypoint.executor` keep working unchanged.
from sandbox_executor.redaction import _MAX_REDACT_INPUT_LEN as _MAX_REDACT_INPUT_LEN
from sandbox_executor.redaction import _PROVIDER_TOKEN_RE as _PROVIDER_TOKEN_RE
from sandbox_executor.redaction import SECRET_FLAGS as SECRET_FLAGS
from sandbox_executor.redaction import _is_secret_flag as _is_secret_flag
from sandbox_executor.redaction import redact_agent_secrets as redact_agent_secrets
from sandbox_executor.redaction import redact_args as redact_args
from sandbox_executor.redaction import redact_command_args, redact_then_truncate
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
    return {k: v for k, v in (os.environ if base_env is None else base_env).items() if k not in _CLEAN_GIT_ENV_VARS}


def is_action_logging_enabled(intent_data: dict[str, Any] | None = None) -> bool:
    """Determine if discrete action logging should be recorded to executions.jsonl."""
    flag = os.getenv("HOLON_ACTION_LOGGING") or os.getenv("HOLON_RECORD_ACTIONS")
    if flag is not None:
        return flag.lower() in ("1", "true", "yes", "on")
    if os.getenv("HOLON_ACTION_ORIGIN") or os.getenv("HOLON_REVIEW_SEVERITY"):
        return True
    if intent_data and (
        intent_data.keys() & {"action_origin", "review_severity", "review_iteration", "review_finding_ids"}
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
    to its own intent rather than widened into this change.

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
    redacted_region, dropped_bytes, causes = _redact_cut_region(encoded, tail_budget)

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


def _redact_cut_region(encoded: bytes, tail_budget: int) -> tuple[str, int, list[str]]:
    """Redact the region around the byte cut and drop its unanchorable front.

    Returns (redacted_region, dropped_bytes, marker_causes): the bytes before the region and its dropped front count
    as dropped, and the causes are bare marker phrases in the order they were hit.
    """
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
    return redacted_region, dropped_bytes, causes


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
        # If configured as safe in local config, or if falling back, inject safe.directory per-invocation
        has_safe_dir = any(arg == "safe.directory" or arg.startswith("safe.directory=") for arg in cmd_args)
        if cwd and _is_safe_directory(cwd) and not has_safe_dir and len(cmd_args) > 1:
            cmd_args = [cmd_args[0], "-c", f"safe.directory={cwd}", *cmd_args[1:]]

    result = subprocess.run(cmd_args, cwd=cwd, capture_output=True, text=True, env=cmd_env)
    if result.returncode != 0 and check:
        print(f"Command failed with code {result.returncode}", file=sys.stderr)
        print(f"Command args: {cmd_str}", file=sys.stderr)
        full_out = redact_agent_secrets(result.stdout)
        full_err = redact_agent_secrets(result.stderr)
        print(f"Stdout:\n{_clip_for_print(full_out)}", file=sys.stderr)
        print(f"Stderr:\n{_clip_for_print(full_err)}", file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, redacted_args, output=full_out, stderr=full_err)
    return result


def _is_safe_directory(cwd: str) -> bool:
    """Whether ``cwd`` is a fallback safe directory or registered (or wildcarded) in the local safe.directory config."""
    norm_cwd = os.path.realpath(cwd)
    if norm_cwd in _FALLBACK_SAFE_DIRECTORIES:
        return True
    return any(sd == "*" or os.path.realpath(sd) == norm_cwd for sd in _get_local_safe_directories(cwd))


def _clip_for_print(text: str) -> str:
    """Keep the head and tail of ``text`` when it is longer than ``_MAX_PRINT_LEN``, joined by a truncation line."""
    if len(text) <= _MAX_PRINT_LEN:
        return text
    half_print = _MAX_PRINT_LEN // 2
    return text[:half_print] + "\n... (truncated) ...\n" + text[-half_print:]


def _get_repo(repo_dir: str) -> GitRepository:
    """Return a GitRepository adapter bound to repo_dir and the executor run_cmd runner."""
    return GitRepository(repo_dir, runner=run_cmd)


def _probe_git_repo(repo_dir: str) -> tuple[bool, str]:
    """Probes the repository at repo_dir to verify if git is healthy and usable.

    Returns a tuple of (is_healthy, error_output).
    """
    repo = _get_repo(repo_dir)
    res = repo.rev_parse("--is-inside-work-tree")
    if res.returncode != 0:
        err = (res.stderr or res.stdout).strip()
        if "detected dubious ownership" in err or "safe.directory" in err:
            retry_res = repo.run(
                ["-c", f"safe.directory={repo_dir}", "rev-parse", "--is-inside-work-tree"],
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
    res_head = repo.rev_parse("HEAD", verify=True)
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
        plan_branch: Branch the workspace was checked out from, used as the tip source when the
            execution branch does not exist yet.

    Returns True if the repository was successfully restored to a usable state, False otherwise.
    """
    # Case (a): Dubious ownership
    if "detected dubious ownership" in probe_err or "safe.directory" in probe_err:
        _persist_safe_directory(repo_dir)

    # Case (b): Stale index lock or locked ref
    if (
        "index.lock" in probe_err
        or "cannot lock ref" in probe_err
        or os.path.exists(os.path.join(repo_dir, ".git", "index.lock"))
    ):
        _clear_abandoned_locks(repo_dir)

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
    ) and os.path.isdir(os.path.join(repo_dir, ".git")):
        _restore_head(repo_dir, default_branch or "main", plan_branch)

    healthy, _ = _probe_git_repo(repo_dir)
    return healthy


def _persist_safe_directory(repo_dir: str) -> None:
    """Add ``repo_dir`` to the local safe.directory config, or fall back to per-invocation ``-c safe.directory``."""
    res = _get_repo(repo_dir).run(
        ["-c", f"safe.directory={repo_dir}", "config", "--local", "--add", "safe.directory", repo_dir],
        check=False,
    )
    if res.returncode != 0:
        err_msg = (res.stderr or res.stdout).strip()
        print(
            f"Warning: unable to write safe.directory to local git config ({redact_agent_secrets(err_msg)}); "
            f"falling back to per-invocation -c safe.directory",
            file=sys.stderr,
        )
        _FALLBACK_SAFE_DIRECTORIES.add(os.path.realpath(repo_dir))


def _clear_abandoned_locks(repo_dir: str) -> None:
    """Remove the provably abandoned git lock files and report the ones still held as warnings."""
    removed, still_held = _remove_abandoned_locks(repo_dir)
    for lock in removed:
        print(f"Warning: removed abandoned git lock file {lock}", file=sys.stderr)
    if still_held:
        print(
            "Warning: git lock file(s) too young to be declared abandoned are left in place: "
            f"{', '.join(still_held)}. They may belong to a running git process.",
            file=sys.stderr,
        )


def _restore_head(repo_dir: str, target_branch: str, plan_branch: str | None) -> None:
    """Point a dangling or missing HEAD at ``target_branch``, read from disk, or warn and leave HEAD untouched."""
    git_dir = os.path.join(repo_dir, ".git")
    head_path = os.path.join(git_dir, "HEAD")
    # HEAD damage makes every git command fail, so the tip has to be read from disk. Guessing
    # an arbitrary branch here would silently put the execution commit on the wrong ref,
    # so the only acceptable sources are the execution branch itself and the plan branch the
    # workspace was checked out from.
    tip_sha = _resolve_local_ref(git_dir, target_branch) or (
        _resolve_local_ref(git_dir, plan_branch) if plan_branch else None
    )
    if not tip_sha:
        print(
            f"Warning: cannot restore HEAD to '{target_branch}': no local ref for it or for "
            f"'{plan_branch or 'unknown'}'; leaving HEAD untouched.",
            file=sys.stderr,
        )
        return
    target_ref_dir = os.path.join(git_dir, "refs", "heads", *target_branch.split("/")[:-1])
    with contextlib.suppress(Exception):
        os.makedirs(target_ref_dir, exist_ok=True)
        with open(os.path.join(git_dir, "refs", "heads", *target_branch.split("/")), "w") as rf:
            rf.write(f"{tip_sha}\n")
    with contextlib.suppress(Exception), open(head_path, "w") as f:
        f.write(f"ref: refs/heads/{target_branch}\n")
    repo = _get_repo(repo_dir)
    verify = repo.run(["symbolic-ref", "HEAD"], check=False)
    if verify.returncode != 0 or verify.stdout.strip() != f"refs/heads/{target_branch}":
        repo.run(["symbolic-ref", "HEAD", f"refs/heads/{target_branch}"], check=False)


def _entrypoint_git_runner(repo_dir: str) -> Callable[[list[str]], str]:
    """Adapt run_cmd (sandbox safe.directory and clean git environment) to the git.py runner interface."""
    repo = _get_repo(repo_dir)

    def _run(args: list[str]) -> str:
        res = repo.run(args, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"git {' '.join(args[:2])} failed: {redact_agent_secrets((res.stderr or '').strip())}")
        return res.stdout if isinstance(res.stdout, str) else ""

    return _run


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return default if val is None else float(val)
    except (ValueError, TypeError):
        return default


def _sanitize_string(name: str, fallback: str = "unknown") -> str:
    s = re.sub(r"\s+", "-", name.replace("/", "_"))
    s = re.sub(r"[^a-zA-Z0-9_.-]", "", s)
    s = re.sub(r"-+", "-", s)
    s = re.sub(r"\.+", ".", s)
    s = s[:64].strip(".-")
    return s or fallback


# Exit status of an executor run that refused an over-budget plan and committed its over_budget row to the execution
# branch (the host pushes it afterwards). Distinct from 1 (a failed run) so the flow can tell the refusal apart and
# read that row, while a direct 'holon execute' still exits non-zero. Every other over_budget outcome exits 1.
OVER_BUDGET_EXIT_CODE = 3

# The repo-local identity of every commit the executor (and the agent it runs) makes inside the container.
EXECUTOR_GIT_EMAIL = "executor-agent@holon-agentic-coder.com"
EXECUTOR_GIT_NAME = "Holon Executor Agent"
EXECUTIONS_LEDGER_REL = "holon-knowledge/ledger/executions.jsonl"


def _recorded_decision(
    plan_data: dict[str, Any], plan_content: str, intent_data: dict[str, Any] | None
) -> DecompositionDecision:
    """Return the decomposition decision the plan row records.

    Only a row that predates the record (an older planner image) is decided here, by the same rule: in a direct
    'holon execute', or in a flow run whose back-fill write failed. A corrupt record exits 1 before any row is written;
    the flow fails such a plan before dispatch.
    """
    try:
        recorded = DecompositionDecision.from_plan_row(plan_data)
    except InvalidPlanDecisionError as exc:
        print(
            f"Error: the plan row of {plan_data.get('plan_id')} records an invalid decomposition decision: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)
    return recorded if recorded is not None else should_decompose(plan_data, plan_content, intent_data)


def sync_and_reconcile_pre_push(repo_dir: str, target_branch: str = "main") -> tuple[bool, str]:
    """Inspect whether the fetched target branch has advanced, merge it, and reconcile ledger conflicts.

    The host fetched the target branch into ``refs/remotes/origin/<target_branch>`` before the container started, so
    this reads no remote. If that ref has diverged from HEAD, attempts an in-flight
    merge. If merge conflicts are strictly confined to holon-knowledge/ledger/*.jsonl,
    resolves each conflicted ledger by pure append (the target branch's bytes stay a prefix and the
    branch's missing lines follow in their original order), stages the resolved files, verifies that
    no unmerged paths remain, and commits the merge. A clean merge leaves the ledgers as git merged them.
    If any non-ledger file has conflicts, aborts the merge via `git merge --abort` and fails loudly.

    Args:
        repo_dir: Working directory of the git repository.
        target_branch: Target base branch name (defaults to "main").

    Returns:
        tuple[bool, str]: (success, message).

    Raises:
        None: Aborts any in-flight merge on failure and returns (False, err_msg).
    """
    repo = _get_repo(repo_dir)
    # 1. The target branch tip the host fetched
    target_ref = f"origin/{target_branch}"
    ref_check = repo.rev_parse(target_ref, verify=True)
    if ref_check.returncode != 0:
        return True, f"Remote branch {target_ref} not found"

    remote_tip = repo.rev_parse(target_ref).stdout.strip()

    # 3. Check divergence with HEAD
    mb_cmd = repo.merge_base("HEAD", target_ref)
    if mb_cmd.returncode != 0:
        print(f"Warning: Unable to compute merge-base with {target_ref}; skipping pre-push sync.", file=sys.stderr)
        return True, f"Unable to compute merge-base with {target_ref}"

    merge_base = mb_cmd.stdout.strip()
    if merge_base == remote_tip:
        return True, f"Branch is already up to date with {target_ref}"

    # 4. Divergence detected: Attempt merge
    _configure_executor_identity(repo_dir)

    merge_res = repo.merge(target_ref, no_commit=True, no_ff=True)
    if merge_res.returncode == 0:
        # A clean merge of append-only ledgers is already correct; rewriting them here would reorder rows.
        staged = repo.diff(cached=True, quiet=True)
        if staged.returncode != 0:
            repo.commit(f"chore(sync): sync target branch {target_branch} before push", check=False)
        return True, f"Cleanly merged {target_ref}"

    return _reconcile_conflicted_merge(repo, repo_dir, target_ref)


def _conflicted_files(repo: GitRepository) -> list[str]:
    """Return the paths left conflicted by an in-flight merge, from the unmerged index or else the porcelain status."""
    unmerged_res = repo.diff("--name-only", "--diff-filter=U")
    conflicted_files = [f.strip() for f in unmerged_res.stdout.splitlines() if f.strip()]
    if not conflicted_files:
        status_res = repo.run(["status", "--porcelain"], check=False)
        for line in status_res.stdout.splitlines():
            if any(line.startswith(c) for c in ("UU ", "AA ", "UD ", "DU ", "AU ", "UA ", "DD ")):
                conflicted_files.append(line[3:].strip())
    return conflicted_files


def _reconcile_conflicted_merge(repo: GitRepository, repo_dir: str, target_ref: str) -> tuple[bool, str]:
    """Resolve an in-flight merge whose conflicts are all append-only ledgers, or abort it and say why."""
    from sandbox_executor.ledger.reconcile import reconcile_ledger_file

    conflicted_files = _conflicted_files(repo)
    non_ledger_files = [
        f for f in conflicted_files if not (f.startswith("holon-knowledge/ledger/") and f.endswith(".jsonl"))
    ]
    if non_ledger_files:
        repo.merge_abort()
        err_msg = (
            f"Non-ledger merge conflicts detected with {target_ref} in files: "
            f"{non_ledger_files}. Aborting auto-merge and refusing push."
        )
        print(f"Error: {err_msg}", file=sys.stderr)
        return False, SyncConflictMessage(err_msg, non_ledger_files)

    ledger_conflicts = [f for f in conflicted_files if f.startswith("holon-knowledge/ledger/") and f.endswith(".jsonl")]
    try:
        for ledger_path in ledger_conflicts:
            full_path = os.path.join(repo_dir, ledger_path)
            # The target branch is published history, so its ledger stays a byte prefix of the merge result.
            if not reconcile_ledger_file(full_path, prefix_side="theirs"):
                repo.merge_abort()
                return False, f"Failed to reconcile ledger file {ledger_path}"
            repo.add(ledger_path, check=True)

        rem_unmerged = repo.diff("--name-only", "--diff-filter=U")
        if rem_unmerged.stdout.strip():
            rem_paths = rem_unmerged.stdout.strip()
            repo.merge_abort()
            return False, f"Unmerged paths remain after ledger reconciliation: {rem_paths}"

        repo.commit(f"chore(ledger): reconcile concurrent ledger updates with {target_ref}", check=True)
        return True, f"Successfully reconciled ledger conflicts and committed merge with {target_ref}"
    except LedgerRewriteError:
        repo.merge_abort()
        raise
    except Exception as exc:
        repo.merge_abort()
        return False, f"Exception during ledger reconciliation merge: {redact_agent_secrets(str(exc))}"


@dataclass(frozen=True)
class ExecutionPromptInputs:
    """The plan and intent an execution prompt describes: the plans.jsonl row, the plan markdown and the intent row."""

    plan_data: dict[str, Any]
    plan_content: str
    intent_data: dict[str, Any]


def build_execution_prompt(repo_dir: str, plan_branch: str, inputs: ExecutionPromptInputs, exec_id: str) -> str:
    """Render ``holon-config/prompts/executor.template.md`` when present, else the built-in execution prompt.

    The write protocol for the execution notes is appended by the caller, so it is always present whichever prompt is
    used.
    """
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
      message (``stopReason: length``) and the notes are complete; a generic exit 1 (auth or API errors)
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


def _configure_executor_identity(repo_dir: str) -> None:
    """Set the executor git identity, so the agent's own commits and the executor's commits can land.

    Called straight after the execution branch is created (before the agent runs) and again before the executor
    commits, because a git recovery re-initialises the repository and discards the local config. A mounted host
    worktree's repository config is shared with the host and mounted read-only, so the identity then goes in the
    container's own global config.
    """
    scope = "--global" if workspace_mounted() else "--local"
    repo = _get_repo(repo_dir)
    repo.run(["config", scope, "user.email", EXECUTOR_GIT_EMAIL], check=False)
    repo.run(["config", scope, "user.name", EXECUTOR_GIT_NAME], check=False)


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
    repo = _get_repo(repo_dir)
    repo.add(_salvage_pathspec(notes_rel), all_files=True, check=False)
    staged = repo.diff(cached=True, quiet=True)
    if staged.returncode != 0:
        _configure_executor_identity(repo_dir)
        commit = repo.commit(f"execute: {exec_id} partial work (failed) for plan {plan_branch}", check=False)
        if commit.returncode != 0:
            left = repo.diff("--name-only", cached=True).stdout or ""
            print(
                "Warning: could not commit the partial work; these files are left uncommitted: "
                f"{redact_agent_secrets(', '.join(left.split()))}; reason: "
                f"{redact_agent_secrets((commit.stderr or commit.stdout or '').strip())}",
                file=sys.stderr,
            )
            # Unstage them, so the failure commit still carries only the notes and the ledger row.
            repo.reset(None, *_salvage_pathspec(notes_rel))
    head = repo.rev_parse("HEAD")
    sha = (head.stdout or "").strip() if head.returncode == 0 else ""
    return sha if sha and sha != diff_base else None


def _split_diff_prefix(line: str) -> tuple[str, str]:
    """Split a diff line into its ``+``, ``-`` or space prefix (empty when there is none) and its content."""
    return (line[0], line[1:]) if line[:1] in ("+", "-", " ") else ("", line)


def _is_patch_structure(line: str) -> bool:
    """Whether ``line`` is a patch header that a key mask must never swallow or cross."""
    return line.startswith(("diff --git ", "@@ ", "From ", "index ", "--- ", "+++ ")) or line == "-- "


def _read_plan_row(repo_dir: str, plan_branch_prefix: str) -> dict[str, Any] | None:
    """Return the newest plans.jsonl row of the plan, or None; unparseable lines are skipped with a warning."""
    plans_file_path = os.path.join(repo_dir, "holon-knowledge/ledger/plans.jsonl")
    plan_data = None
    if os.path.exists(plans_file_path):
        with open(plans_file_path) as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    # Keyed on the intent and the plan id together; the newest matching row wins, so a
                    # superseding row the flow back-filled is read.
                    if is_row_of_plan(data, plan_branch_prefix):
                        plan_data = data
                except Exception as e:
                    print(f"Warning: skipping line {line_no} in plans.jsonl: {e}", file=sys.stderr)
    return plan_data


def _read_intent_row(repo_dir: str, target_intent_branch: str) -> dict[str, Any] | None:
    """Return the first intents.jsonl row of the intent branch, or None, skipping unparseable lines with a warning."""
    intents_file_path = os.path.join(repo_dir, "holon-knowledge/ledger/intents.jsonl")
    if not os.path.exists(intents_file_path):
        return None
    with open(intents_file_path) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                branch = data.get("branch")
                if isinstance(branch, str) and (target_intent_branch.rstrip("/_") == branch.rstrip("/_")):
                    return data
            except Exception as e:
                # Ignore invalid or corrupted lines in intents ledger
                print(f"Warning: skipping unparseable line in intents.jsonl: {e}", file=sys.stderr)
    return None


def _parse_review_iteration(raw_iter: Any) -> int | None:
    """The review iteration as an int, or None when it is absent or not a number."""
    if raw_iter is None:
        return None
    try:
        return int(raw_iter)
    except (ValueError, TypeError):
        return None


def _parse_review_finding_ids(raw_findings: Any) -> list[str] | None:
    """The review finding ids from a comma, semicolon or whitespace separated string or a list, or None."""
    if not raw_findings:
        return None
    if isinstance(raw_findings, str):
        return [f.strip() for f in re.split(r"[,;\s]+", raw_findings) if f.strip()]
    if isinstance(raw_findings, list):
        return [str(f).strip() for f in raw_findings if str(f).strip()]
    return None


@dataclass(frozen=True)
class ExecutionArgs:
    """The executor command line: ``<plan_branch> [agent_name] [model_name] [partial_execution_branch]``.

    The host CLI always passes resolved values; there is no harness detection inside the container.
    """

    plan_branch: str
    agent_name: str
    model_name: str
    # Passed by 'holon flow' only on a Stage 3 resume: a failed execution branch whose partial work to continue.
    partial_branch: str | None

    @classmethod
    def from_argv(cls, argv: list[str]) -> Self:
        """Parse ``sys.argv`` (at least the plan branch): a missing agent or model takes its default, an empty fourth
        argument means no partial branch."""
        agent_name = argv[2] if len(argv) > 2 else FALLBACK_AGENT
        return cls(
            plan_branch=argv[1],
            agent_name=agent_name,
            model_name=argv[3] if len(argv) > 3 else default_model_for(agent_name),
            partial_branch=argv[4] if len(argv) > 4 and argv[4] else None,
        )


class ExecutionRun:
    """One Stage 3 execution of a plan, in place in the host worktree mounted into the container.

    The named steps run in a fixed order (see `run`) and share the run's state as attributes. They write the
    ``executions.jsonl`` summary row (and, when action logging is on, its action records), commit the execution branch
    locally for the host to push, and exit 1 on a failed run or `OVER_BUDGET_EXIT_CODE` on a refused plan.
    """

    def __init__(self, args: ExecutionArgs) -> None:
        self.args = args
        self.plan_branch = args.plan_branch
        self.plan_branch_prefix = args.plan_branch[:-2] if args.plan_branch.endswith("/_") else args.plan_branch
        self.runner: Any = None
        self.repo_dir = ""
        self.repo: GitRepository | None = None
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
        self.decision: DecompositionDecision | None = None
        self.exec_entry: dict[str, Any] = {}
        self.commit_msg = ""
        # Set when a failed execution's code is salvaged; the host pushes that commit even with nothing else staged.
        self.salvaged_head: str | None = None
        # Set once the execution branch carries its commit; the host pushes it after this container returns.
        self.branch_recorded = False
        # The authoring turn and its outcome; only an executed (not decomposed or refused) plan sets these.
        self.notes_rel = ""
        self.notes_path = ""
        self.stage_timer: StageTimer | None = None
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
    def executions_ledger(self) -> str:
        """Absolute path of the ``executions.jsonl`` ledger in the worktree."""
        return os.path.join(self.ledger_dir, "executions.jsonl")

    def run(self) -> None:
        """Run every step in order.

        Exits 1 when the workspace is not mounted. Inside the mounted workspace an escaping exception is reported as
        ``Execution failed: ...`` and re-raised, while a step's ``SystemExit`` passes through untouched; either way the
        runner's ephemeral credentials are cleaned up.
        """
        self.runner = get_runner(self.args.agent_name)
        self.runner.validate()
        self.repo_dir = get_workspace_dir()
        if not workspace_mounted():
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
        """The steps inside the mounted workspace; a decomposed or refused plan skips the agent and its checks."""
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
        """Validate the mounted worktree, choose the execution id and check out the new execution branch.

        A Stage 3 resume merges the partial branch's work into the new branch. The executor git identity is configured
        before the agent runs, and stage conflicts are resolved. Never clones, fetches, resets or cleans.
        """
        self.repo = repo = _get_repo(self.repo_dir)
        # The host provisioned, fetched and mounted the worktree: no clone, fetch, reset or clean here.
        mounted_plan_tip = validate_mounted_workspace(repo, self.repo_dir, self.plan_branch)

        prefix = self.plan_branch_prefix
        self.exec_seq = int(time.time())
        self.exec_id = build_execution_id(
            self.exec_seq, prefix, self.args.agent_name, self.args.model_name, taken=taken_child_ids(repo, prefix, "E")
        )
        self.exec_branch = f"{prefix}/{self.exec_id}/_"

        # Plan branch tip: the base the empty-diff guard compares the agent's work against, even on a resume.
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
        # Before the agent runs, so the commits it makes step by step land on the execution branch.
        _configure_executor_identity(self.repo_dir)

        # Proactively check and resolve any stage conflicts
        from sandbox_executor.flow.stage_conflicts import check_and_resolve_stage_conflicts

        check_and_resolve_stage_conflicts(repo_dir=self.repo_dir, target_branch="main", stage_name="execute")

    def load_plan(self) -> None:
        """Read the plan row, the plan markdown and the intent row, falling back to rows derived from the branch."""
        prefix = self.plan_branch_prefix
        self.plan_data = _read_plan_row(self.repo_dir, prefix) or {
            "plan_id": prefix.split("/")[-1],
            "intent_branch": prefix.rsplit("/", 1)[0] + "/_",
            "agent": self.args.agent_name,
            "model": self.args.model_name,
            "entropy": 3.0,
        }

        # Load plan markdown content if available
        plan_file_rel = recorded_plan_file(self.plan_data.get("plan_file"), prefix)
        if plan_file_rel and os.path.exists(os.path.join(self.repo_dir, plan_file_rel)):
            with open(os.path.join(self.repo_dir, plan_file_rel)) as f:
                self.plan_content = f.read()

        intent_branch = self.plan_data.get("intent_branch", "")
        self.intent_data = _read_intent_row(self.repo_dir, intent_branch) or {
            "branch": self.plan_data.get("intent_branch", "I-unknown")
        }

    def record_decision(self) -> bool:
        """Decide whether the plan is executed; returns True when the agent is skipped.

        A decomposed or over-budget plan gets one ``executions.jsonl`` summary row (and a ``plan_decomposition`` action
        record), staged for the commit. A corrupt recorded decision exits 1 before any row is written.
        """
        self.timestamp_str = datetime.now(UTC).isoformat()
        self.ledger_dir = os.path.join(self.repo_dir, "holon-knowledge/ledger")
        os.makedirs(self.ledger_dir, exist_ok=True)

        self.decision = _recorded_decision(self.plan_data, self.plan_content, self.intent_data)
        # Both non-execute outcomes record one summary row, commit it (the host pushes it) and skip the agent.
        if self.decision.decision == DECISION_EXECUTE:
            return False
        self._record_non_execute_row(self.decision)
        return True

    def _record_non_execute_row(self, decision: DecompositionDecision) -> None:
        """Append the decomposed or over-budget summary row and its action record, and stage the ledger."""
        self.exec_entry = {
            "entry_type": "summary",
            "execution_id": self.exec_id,
            "plan_branch": self.plan_branch,
            "agent": self.args.agent_name,
            "agent_version": self.runner.get_version(),
            "model": self.args.model_name,
            "status": decision.decision,
            "entropy": decision.entropy,
            "entropy_budget": decision.entropy_budget,
            "created_at": self.timestamp_str,
        }
        action_text = self._describe_non_execute_decision(decision)
        with atomic_ledger_append(self.executions_ledger) as ef:
            ef.write(json.dumps(self.exec_entry) + "\n")

        if is_action_logging_enabled(self.intent_data):
            with suppress_ledger_failures():
                append_action_record(
                    self.executions_ledger,
                    ActionRecord(
                        action_id=generate_action_id(),
                        execution_id=self.exec_id,
                        event_type="plan_decomposition",
                        action=action_text,
                        category="config",
                        labels=["decomposition", "workflow"],
                        target=self.plan_branch,
                        outcome="success" if decision.decision == DECISION_DECOMPOSED else "failure",
                        action_origin="initial_implementation",
                    ),
                )

        self.repo.add("holon-knowledge/ledger/executions.jsonl")

    def _describe_non_execute_decision(self, decision: DecompositionDecision) -> str:
        """Fill the row's summary (and decomposition reference) and the commit message; returns the action text."""
        plan_branch = self.plan_branch
        if decision.decision == DECISION_DECOMPOSED:
            count = len(decision.sub_intents)
            print(f"The plan records an explicit sub-intent list of {count} item(s); referring to it.")
            # The plan row is the only record of the list; this summary refers to it and never copies it.
            self.exec_entry["plan_decision"] = {
                "plan_id": self.plan_data.get("plan_id"),
                "plan_branch": plan_branch,
                "decision": DECISION_DECOMPOSED,
                "sub_intent_count": count,
            }
            self.exec_entry["summary"] = (
                f"The plan {plan_branch} records {count} sub-intent(s); the flow runs each one through the full "
                "pipeline."
            )
            self.commit_msg = f"execute: the plan {plan_branch} records {count} sub-intent(s)"
            return f"The plan {plan_branch} records {count} sub-intent(s)"
        self.exec_entry["summary"] = (
            f"Plan entropy {decision.entropy} exceeds the entropy budget {decision.entropy_budget} and the "
            "plan lists no sub-intent list; the plan was not executed."
        )
        print(self.exec_entry["summary"])
        self.commit_msg = f"execute: plan {plan_branch} is over its entropy budget"
        return f"Refused over-budget plan {plan_branch}"

    def run_turns(self) -> None:
        """Run the supervised authoring turn and classify its outcome.

        Records an unrecovered budget failure in ``executions.jsonl`` unconditionally, resolves the review provenance,
        records the agent's ``command_execution`` action and captures the redacted, byte-bounded agent output.
        """
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
        full_prompt = f"{base_prompt}\n\n{execution_write_protocol(self.notes_rel)}"

        budgets = load_budgets(os.environ)
        self.stage_timer = StageTimer("execute", budgets.stage_seconds("execute"))
        self._supervise_authoring(base_prompt, full_prompt, budgets.turn)
        self._record_budget_failure()
        self._resolve_action_provenance()
        self._record_agent_action()
        self._capture_agent_output()

    def _supervise_authoring(self, base_prompt: str, full_prompt: str, turn_budgets: Any) -> None:
        """Run the authoring turn from temporary prompt and intent files, removed afterwards, and classify it."""
        prompt_file = os.path.join(tempfile.gettempdir(), f"exec_prompt-{self.exec_seq}.md")
        intent_file = os.path.join(tempfile.gettempdir(), f"exec_intent-{self.exec_seq}.json")
        runner, model_name = self.runner, self.args.model_name
        try:
            with open(prompt_file, "w") as f:
                f.write(full_prompt)
            with open(intent_file, "w") as f:
                json.dump(self.intent_data, f)

            outcome = supervise_authoring_turn(
                lambda text: runner.build_cmd(model_name, prompt_file, intent_file, text),
                full_prompt,
                artefact_path=self.notes_path,
                artefact_rel=self.notes_rel,
                spec=EXECUTION_SPEC,
                stage="execute",
                turn_budgets=turn_budgets,
                stage_timer=self.stage_timer,
                run=run_turn,
                modify_code=False,
                context=base_prompt,
                watch_tree=self.repo_dir,
                cwd=self.repo_dir,
                stdin_file=prompt_file if getattr(runner, "uses_stdin", False) else None,
                prompt_file=prompt_file,
            )
            self.exec_status, self.summary, self.budget_breach, self.budget_reason = classify_execution_outcome(outcome)
            # The implementation turn is the one classify_execution_outcome judges; follow-ups only touch the
            # notes, so output size and error context describe the implementation turn.
            self.agent_result = outcome.first
            self.notes_report = outcome.report
        finally:
            for tf in (prompt_file, intent_file):
                if os.path.exists(tf):
                    with contextlib.suppress(Exception):
                        os.remove(tf)

    def _record_budget_failure(self) -> None:
        """Append the ``act_budget`` failure row of an unrecovered budget breach; a write failure is only a warning."""
        if self.budget_breach is None:
            return
        # Written unconditionally (not gated on action logging): an unrecovered budget failure must always be
        # auditable, and this is the only write site, so there is exactly one record per stage failure.
        try:
            os.makedirs(self.ledger_dir, exist_ok=True)
            record_budget_failure(
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

    def _resolve_action_provenance(self) -> None:
        """Resolve the action origin and review provenance: environment, then intent row, else HEAD's trailers."""
        intent_data = self.intent_data
        self.action_origin = (
            os.getenv("HOLON_ACTION_ORIGIN")
            or (intent_data.get("action_origin") or intent_data.get("origin") if intent_data else None)
            or "initial_implementation"
        )
        self.review_severity = os.getenv("HOLON_REVIEW_SEVERITY") or (
            intent_data.get("review_severity") if intent_data else None
        )
        self.review_iteration = _parse_review_iteration(
            os.getenv("HOLON_REVIEW_ITERATION") or (intent_data.get("review_iteration") if intent_data else None)
        )
        self.review_finding_ids = _parse_review_finding_ids(
            os.getenv("HOLON_REVIEW_FINDING_IDS") or (intent_data.get("review_finding_ids") if intent_data else None)
        )

        if self.action_origin == "initial_implementation":
            extracted_prov = extract_action_provenance_from_commit(repo_dir=self.repo_dir, commit_ref="HEAD")
            if extracted_prov.get("action_origin") != "initial_implementation":
                self.action_origin = extracted_prov["action_origin"]
                self.review_severity = extracted_prov.get("review_severity") or self.review_severity
                self.review_iteration = (
                    extracted_prov.get("review_iteration") if self.review_iteration is None else self.review_iteration
                )
                self.review_finding_ids = extracted_prov.get("review_finding_ids") or self.review_finding_ids

    def _record_agent_action(self) -> None:
        """Append the ``command_execution`` action record of the agent run, when action logging is on."""
        if not is_action_logging_enabled(self.intent_data):
            return
        res = self.agent_result
        agent_name, model_name = self.args.agent_name, self.args.model_name
        with suppress_ledger_failures():
            os.makedirs(self.ledger_dir, exist_ok=True)
            append_action_record(
                self.executions_ledger,
                ActionRecord(
                    action_id=generate_action_id(),
                    execution_id=self.exec_id,
                    event_type="command_execution",
                    action=f"Executed agent {agent_name} with model {model_name}",
                    category="code",
                    labels=["agent", agent_name, "execution"],
                    target=f"{agent_name}:{model_name}",
                    outcome="success" if self.exec_status == "success" else "failure",
                    error_context=(redact_then_truncate(res.stderr, 500) if res.returncode != 0 else None),
                    action_origin=self.action_origin,
                    review_severity=self.review_severity,
                    review_iteration=self.review_iteration,
                    review_finding_ids=self.review_finding_ids,
                ),
            )

    def _capture_agent_output(self) -> None:
        """Keep the implementation turn's combined output, redacted and byte-bounded; a failure keeps nothing."""
        res = self.agent_result
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
            self.agent_output_text, self.agent_output_truncated, _dropped = prepare_agent_output_block(
                combined, max_bytes
            )
            self.agent_output_bytes = len(self.agent_output_text.encode("utf-8"))
        except Exception as e:
            print(f"Warning: Failed to capture agent output: {redact_agent_secrets(str(e))}", file=sys.stderr)
            self.agent_output_text = ""
            self.agent_output_truncated = False
            self.agent_output_bytes = 0

    def check_work(self) -> None:
        """Check the agent's finished work before anything is staged.

        Validates the execution notes, requires a usable git repository (raises when it cannot be repaired), applies the
        empty-diff guard (which can turn a success into a failure), measures the project's tests and converges prettier
        on changed markdown.
        """
        self.notes_valid = finalise_execution_notes(self.notes_path, self.notes_report)
        self._require_healthy_repo()
        self._guard_empty_diff()
        # Measure the project's tests on the agent's finished work, before anything is staged or committed.
        self.test_fields = measure_executor_tests(self.repo_dir, self.repo, self.exec_status)
        self._converge_markdown()

    def _require_healthy_repo(self) -> None:
        """Repair benign git damage the agent left, or raise: the mounted worktree's git directory is never rebuilt."""
        is_healthy, probe_err = _probe_git_repo(self.repo_dir)
        if is_healthy:
            return
        repaired = _repair_git_repo(
            self.repo_dir, probe_err, default_branch=self.exec_branch, plan_branch=self.plan_branch
        )
        if not repaired:
            # Its .git pointer is read-only and the git directory is the host's: never re-initialise it.
            raise RuntimeError(
                f"the mounted worktree's git repository is unusable after the agent ran ({probe_err}); its "
                "files are kept in the host worktree"
            )

    def _guard_empty_diff(self) -> None:
        """Fail a successful run whose diff against the plan tip is empty or cannot be computed.

        Empty-diff guard (shared with holon flow and the review loop): an agent run that changes nothing outside
        holon-knowledge/ledger/, plans/ and executions/ is never recorded as success, and whatever it did change is
        committed only as failed partial work. Fail closed when the diff cannot be computed.
        """
        if self.exec_status != "success":
            return
        from sandbox_executor.git import (
            EMPTY_DIFF_FAILURE_REASON,
            EMPTY_DIFF_STATUS,
            empty_diff_summary,
            has_effective_diff,
        )

        diff_base = self.plan_tip
        try:
            if not diff_base:
                raise RuntimeError(f"tip of {self.plan_branch} is unknown")
            effective = has_effective_diff(self.repo_dir, diff_base, git_runner=_entrypoint_git_runner(self.repo_dir))
        except Exception as e:
            self.exec_status = EMPTY_DIFF_STATUS
            self.failure_reason = "diff_unverified"
            self.summary = (
                f"Could not verify the execution diff against {self.plan_branch}; treating it as a failure: "
                f"{redact_agent_secrets(str(e))}"
            )
        else:
            if not effective:
                self.exec_status = EMPTY_DIFF_STATUS
                self.failure_reason = EMPTY_DIFF_FAILURE_REASON
                self.summary = empty_diff_summary(self.plan_branch)
        if self.failure_reason:
            print(f"Error: {self.summary}", file=sys.stderr)
            report_empty_diff_output(self.failure_reason, self.agent_output_text, self.agent_output_bytes)

    def _converge_markdown(self) -> None:
        """Format modified and untracked markdown files with prettier; a formatting failure is only a warning."""
        try:
            if md_files := changed_markdown_files(self.repo):
                converge_prettier(md_files, repo_dir=self.repo_dir)
                if is_action_logging_enabled(self.intent_data):
                    with suppress_ledger_failures():
                        append_action_record(
                            self.executions_ledger,
                            ActionRecord(
                                action_id=generate_action_id(),
                                execution_id=self.exec_id,
                                event_type="doc_update",
                                action=f"Formatted {len(md_files)} markdown files with prettier",
                                category="doc",
                                labels=["formatting", "prettier"],
                                target=", ".join(md_files[:3]),
                                outcome="success",
                                action_origin=self.action_origin,
                            ),
                        )
        except LedgerRewriteError:
            raise
        except Exception as e:
            print(f"Warning: Prettier formatting failed: {redact_agent_secrets(str(e))}", file=sys.stderr)

    def salvage(self) -> None:
        """Commit a failed run's code changes as ``partial work (failed)``, recording the partial head.

        Salvage on failure, in two commits: first the agent's code changes ("partial work (failed)"), then the notes and
        the ledger row ("failed for plan"), so a failed execution never loses its code.
        """
        if self.exec_status != "success" and self.plan_tip:
            self.salvaged_head = _salvage_partial_work(
                self.repo_dir, self.exec_id, self.plan_branch, self.notes_rel, self.plan_tip
            )

    def record_ledger(self) -> None:
        """Append the execution's summary row to ``executions.jsonl`` and choose the commit message.

        A write failure other than `LedgerRewriteError` is only a warning. Also appends the ``git_mutation`` action
        record when action logging is on.
        """
        self.exec_entry = self._summary_row()
        try:
            os.makedirs(self.ledger_dir, exist_ok=True)
            with atomic_ledger_append(self.executions_ledger) as ef:
                ef.write(json.dumps(self.exec_entry) + "\n")
        except LedgerRewriteError:
            raise
        except Exception as e:
            print(f"Warning: Failed to write execution ledger row: {redact_agent_secrets(str(e))}", file=sys.stderr)

        self.commit_msg = self._execution_commit_message()
        self._record_commit_action()

    def _summary_row(self) -> dict[str, Any]:
        """The execution's ``executions.jsonl`` summary row."""
        stage_timer = self.stage_timer
        exec_entry = {
            "entry_type": "summary",
            "execution_id": self.exec_id,
            "plan_branch": self.plan_branch,
            "agent": self.args.agent_name,
            "agent_version": self.runner.get_version(),
            "model": self.args.model_name,
            "status": self.exec_status,
            "summary": self.summary,
            "created_at": self.timestamp_str,
            "agent_output_truncated": self.agent_output_truncated,
            "agent_output_bytes": self.agent_output_bytes,
            "ledger_revision": 1,
            "execution_notes": self.notes_rel,
            "execution_notes_valid": self.notes_valid,
            "elapsed_seconds": round(stage_timer.elapsed(), 1),
            **stage_timer.ledger_fields(),
        }
        exec_entry.update(self.test_fields)
        if self.failure_reason:
            exec_entry["failure_reason"] = self.failure_reason
        if self.salvaged_head:
            exec_entry["partial_head"] = self.salvaged_head
        return exec_entry

    def _execution_commit_message(self) -> str:
        """The ``completed`` or ``failed`` commit message, with provenance trailers on a review-driven execution."""
        if self.exec_status == "success":
            commit_msg = f"execute: {self.exec_id} completed for plan {self.plan_branch}"
        else:
            # The failure commit (ledger row and notes, after the partial-work commit) never reads as completed.
            commit_msg = f"execute: {self.exec_id} failed for plan {self.plan_branch}"
        trailers = format_commit_trailers(
            action_origin=self.action_origin,
            review_severity=self.review_severity,
            review_iteration=self.review_iteration,
            review_finding_ids=self.review_finding_ids,
        )
        if (
            self.action_origin != "initial_implementation"
            or self.review_severity
            or self.review_iteration
            or self.review_finding_ids
        ):
            commit_msg = f"{commit_msg}\n\n{trailers}"
        return commit_msg

    def _record_commit_action(self) -> None:
        """Append the ``git_mutation`` action record of the execution commit, when action logging is on."""
        if not is_action_logging_enabled(self.intent_data):
            return
        with suppress_ledger_failures():
            append_action_record(
                self.executions_ledger,
                ActionRecord(
                    action_id=generate_action_id(),
                    execution_id=self.exec_id,
                    event_type="git_mutation",
                    action=f"Committed execution changes: {self.commit_msg.splitlines()[0]}",
                    category="code",
                    labels=["commit", "git"],
                    target="HEAD",
                    outcome="success",
                    action_origin=self.action_origin,
                    review_severity=self.review_severity,
                    review_iteration=self.review_iteration,
                    review_finding_ids=self.review_finding_ids,
                ),
            )

    def stage_changes(self) -> None:
        """Stage the ledger, plus the notes on a failure or every change on a success, and print the status.

        On a failure the code changes are already in the partial-work commit, so only the ledger and notes are staged.
        """
        repo_dir, repo = self.repo_dir, self.repo
        add_targets = [
            f for f in ("holon-knowledge/ledger/executions.jsonl",) if os.path.exists(os.path.join(repo_dir, f))
        ]
        if self.exec_status != "success" and os.path.exists(self.notes_path):
            # The notes (partial or not) explain the failure; the code changes are already in the partial commit.
            add_targets.append(self.notes_rel)
        if add_targets:
            repo.run(["add", *add_targets], check=False)
        if self.exec_status == "success":
            repo.add(all_files=True)
        # Log staged changes to provide visibility
        status_output = repo.run(["status", "--short"], check=False)
        print("Current git repository status:")
        print(redact_agent_secrets(status_output.stdout))

    def commit_steps(self) -> bool:
        """Commit whatever is staged; returns whether the execution branch has a commit to record.

        A salvaged partial-work commit counts even when nothing else is staged; with neither, prints that nothing was
        committed.
        """
        staged_check = self.repo.diff(cached=True, quiet=True)
        if staged_check.returncode == 0 and not self.salvaged_head:
            print("No staged changes to commit.")
            return False
        if staged_check.returncode != 0:
            _configure_executor_identity(self.repo_dir)
            self.repo.commit(self.commit_msg)
        return True

    def pre_push_sync(self) -> bool:
        """Merge the fetched target branch and reconcile ledger conflicts; returns whether the branch can be recorded.

        A failed sync records a superseding failure row (see `record_sync_failure`), which decides the result. The host
        fetched the target branch into the worktree before dispatch; no remote is read here.
        """
        intent_data = self.intent_data
        target_base = (
            os.getenv("HOLON_TARGET_BRANCH") or (intent_data.get("target_branch") if intent_data else None) or "main"
        )
        sync_ok, sync_msg = sync_and_reconcile_pre_push(self.repo_dir, target_branch=target_base)
        if sync_ok:
            return True
        return record_sync_failure(self.repo, self.exec_entry, self.exec_id, self.ledger_dir, sync_msg)

    def record_branch(self) -> None:
        """Record the committed execution branch for the host to push, after the safety and sync checks.

        Invariant: a branch with no parent, or one the attribution guard rejects, is never published, whatever the
        execution status. Sets `branch_recorded`; the container itself never pushes.
        """
        can_record = self.repo.rev_parse("HEAD^", verify=True).returncode == 0
        can_record = attribution_allows_publish(self.plan_tip, _entrypoint_git_runner(self.repo_dir)) and can_record
        if can_record:
            can_record = self.pre_push_sync()

        if can_record:
            self.branch_recorded = True
            print(f"Execution branch '{self.exec_branch}' successfully committed locally; the host pushes it.")
        else:
            print("Refusing to publish execution branch: safety or sync verification failed.", file=sys.stderr)

    def _exit_with_status(self, skip_agent: bool) -> None:
        """Print the stage summary and exit 1 on a budget breach; exit `OVER_BUDGET_EXIT_CODE` on a recorded refusal.

        An over-budget refusal whose row no execution branch carries is a failed run and exits 1. Otherwise returns.
        """
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
            # No execution branch carries the over_budget row: a failed run, not a refusal.
            print(f"{refusal} Its over_budget row was not recorded on an execution branch.", file=sys.stderr)
            sys.exit(1)
        print(
            f"{refusal} Re-plan it within the budget, or add an explicit sub-intent list.",
            file=sys.stderr,
        )
        sys.exit(OVER_BUDGET_EXIT_CODE)


def main() -> None:
    """Executor entry point: ``executor.py <plan_branch> [agent_name] [model_name] [partial_execution_branch]``.

    Without a plan branch prints the usage line and exits 1; otherwise runs one `ExecutionRun`, whose exit codes it
    passes through.
    """
    if len(sys.argv) < 2:
        print("Usage: executor.py <plan_branch> [agent_name] [model_name] [partial_execution_branch]")
        sys.exit(1)
    ExecutionRun(ExecutionArgs.from_argv(sys.argv)).run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
