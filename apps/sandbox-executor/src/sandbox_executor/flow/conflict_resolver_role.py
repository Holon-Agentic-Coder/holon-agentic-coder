"""The code resolver of the target sync: the ``conflict-resolver`` role container, cheapest model tier first.

``conflict_code_resolver(context)`` returns a ``CodeResolver`` for ``SyncRequest.code_resolver``. Each call walks the
runner's tier ladder: dispatch one attempt, check it with the host guard (``conflict_resolver_attempt``), and stage it
only when the container verdict is ``resolved`` and the guard finds nothing wrong. Failed lint, failed tests,
cannot-decide, an agent failure, a launcher abort or a rejected edit saves the attempt's diff, resets the merge and
moves up exactly one tier. Tampering (a git write by the agent; a moved ``HEAD`` is reset to the pre-attempt head), a
failed reset or a failed staging stops at once. The top tier failing returns ``resolved=False``, so ``merge_target``
aborts the merge, records the blocked ``target_sync`` row and pushes nothing. A conflict set holding the frozen module
budget file is blocked before any dispatch unless the intent carries ``module_budget_change``.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass, replace
from typing import Any

from sandbox_executor.agent_runner import CONFLICT_RESOLVER_ROLE
from sandbox_executor.flow.conflict_resolver_attempt import (
    MergeSnapshot,
    check_attempt,
    discard_tampered,
    reset_attempt,
    save_attempt_diff,
    snapshot_merge,
    stage_resolution,
)
from sandbox_executor.flow.conflict_resolver_tiers import next_tier, tier_ladder
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.dispatch import _dispatch_role
from sandbox_executor.flow.target_probe import TargetProbe
from sandbox_executor.flow.target_sync_result import CodeResolution, CodeResolver
from sandbox_executor.module_budget_guard import MODULE_BUDGET_CONFIG, module_budget_change_authorised
from sandbox_executor.plan_paths import plan_file_for_branch
from sandbox_executor.redaction import redact_agent_secrets

# The ledger row carries the test result, so it is redacted and capped.
TEST_RESULT_MAX_CHARS = 500
# Where the launcher mounts the result file in the container; the host's input travels in the same file.
_CONTAINER_TRANSPORT = "/tmp/review_result.json"
_VERDICTS = frozenset({"resolved", "cannot_decide", "lint_failed", "tests_failed", "agent_failed"})


def _bounded(text: str) -> str:
    redacted = redact_agent_secrets(text).strip()
    return redacted if len(redacted) <= TEST_RESULT_MAX_CHARS else redacted[: TEST_RESULT_MAX_CHARS - 1] + "…"


def _transport_file(payload: dict[str, Any]) -> str:
    """A host temp file outside the worktree (never swept into the merge) holding the role input, writable by the
    container's unprivileged user."""
    tmp_dir = os.path.expanduser("~/.holon/tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="holon-conflict-resolver-", suffix=".json", dir=tmp_dir)
    try:
        os.fchmod(fd, 0o666)
        os.write(fd, json.dumps(payload, indent=2).encode("utf-8"))
    finally:
        os.close(fd)
    return path


def _read_verdict(path: str, exit_code: int) -> dict[str, Any]:
    """The container's verdict; a non-zero exit or a file still holding the host input is ``agent_failed``."""
    data: Any = None
    with contextlib.suppress(OSError, ValueError), open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if exit_code != 0 or not isinstance(data, dict) or data.get("verdict") not in _VERDICTS:
        return {"verdict": "agent_failed", "reason": f"the container left no verdict (exit code {exit_code})"}
    return data


def _describe(verdict: dict[str, Any], reason: str = "") -> str:
    failing = [str(test) for test in verdict.get("failing_tests") or []]
    parts = [str(verdict.get("verdict")), reason or str(verdict.get("reason") or "")]
    return "; ".join(part for part in (*parts, f"failed: {', '.join(failing)}" if failing else "") if part)


@dataclass(frozen=True)
class _Try:
    """One attempt: the worktree and paths, the probe, the tier, its 1-based number and the pre-attempt snapshot."""

    worktree: str
    paths: list[str]
    probe: TargetProbe
    tier: str
    number: int
    before: MergeSnapshot


@dataclass(frozen=True)
class _ConflictCodeResolver:
    """The ``CodeResolver`` that dispatches the ``conflict-resolver`` role for the flow in ``context``."""

    context: FlowContext

    def __call__(self, worktree: str, code_paths: list[str], probe: TargetProbe) -> CodeResolution:
        """Resolve ``code_paths`` tier by tier; stage and return ``resolved=True`` only for a verified attempt."""
        if MODULE_BUDGET_CONFIG in code_paths and not module_budget_change_authorised(self.context.intent_data):
            return CodeResolution(resolved=False, tier="none", test_result="module budget file in conflict set")
        ladder = tier_ladder(self.context.agent, self.context.model)
        before = snapshot_merge(worktree)
        tier: str | None = ladder[0]
        number = 0
        resolution = CodeResolution(resolved=False, tier=ladder[0])
        while tier is not None:
            number += 1
            resolution, escalate = self._attempt(_Try(worktree, list(code_paths), probe, tier, number, before))
            if not escalate:
                return resolution
            tier = next_tier(ladder, tier)
        return resolution

    def _dispatch(self, attempt: _Try) -> dict[str, Any]:
        payload = {
            "paths": attempt.paths,
            "merge_base": attempt.probe.merge_base,
            "ours_sha": attempt.probe.head_sha,
            "theirs_sha": attempt.probe.target_sha,
            "target_ref": attempt.probe.target_ref,
            "intent": self.context.intent_data,
            "plan_path": plan_file_for_branch(self.context.plan_branch or ""),
            "tier": attempt.tier,
        }
        path = _transport_file(payload)
        args = [_CONTAINER_TRANSPORT, self.context.agent, attempt.tier, f"--result-file={_CONTAINER_TRANSPORT}"]
        try:
            tier_context = replace(self.context, model=attempt.tier)
            code = _dispatch_role(
                tier_context, CONFLICT_RESOLVER_ROLE, args, result_file=path, target_worktree=attempt.worktree
            )
            return _read_verdict(path, code)
        except SystemExit as exc:  # A launcher abort is an environment failure, never the end of the flow.
            return {"verdict": "agent_failed", "reason": f"dispatch failed: launcher aborted with exit code {exc.code}"}
        except Exception as exc:
            return {"verdict": "agent_failed", "reason": f"dispatch failed: {exc}"}
        finally:
            with contextlib.suppress(OSError):
                os.remove(path)

    def _attempt(self, attempt: _Try) -> tuple[CodeResolution, bool]:
        """Run one attempt; return its resolution and whether to escalate to the next tier."""
        verdict = self._dispatch(attempt)
        check = check_attempt(attempt.worktree, attempt.paths, attempt.before)
        if check.tampered:
            restore_error = discard_tampered(attempt.worktree, attempt.before)
            detail = "; ".join(part for part in (f"tampered: {check.reason}", restore_error) if part)
            return CodeResolution(False, attempt.tier, attempt.number, _bounded(detail)), False
        if verdict["verdict"] == "resolved" and check.ok:
            error = stage_resolution(attempt.worktree, attempt.paths, check.test_edits)
            if error:
                return CodeResolution(False, attempt.tier, attempt.number, _bounded(f"staging failed: {error}")), False
            return CodeResolution(True, attempt.tier, attempt.number, "passed", check.test_edits), False
        detail = _describe(verdict, f"rejected edits: {check.reason}" if not check.ok else "")
        label = f"{attempt.probe.target_sha[:12]}-attempt-{attempt.number}-{attempt.tier}"
        save_attempt_diff(attempt.worktree, [*attempt.paths, *check.test_edits], label)
        reset_error = reset_attempt(attempt.worktree, attempt.paths, check)
        self.context.log(f"Conflict resolver tier {attempt.tier} failed: {_bounded(detail)}")
        if reset_error:
            return CodeResolution(False, attempt.tier, attempt.number, _bounded(f"{detail}; {reset_error}")), False
        return CodeResolution(False, attempt.tier, attempt.number, _bounded(detail)), True


def conflict_code_resolver(context: FlowContext) -> CodeResolver:
    """The ``CodeResolver`` for ``SyncRequest.code_resolver`` that resolves with the flow in ``context``."""
    return _ConflictCodeResolver(context)
