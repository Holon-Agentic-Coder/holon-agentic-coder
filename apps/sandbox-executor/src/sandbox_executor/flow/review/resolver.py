"""The Stage 4 resolver pass: dispatch the resolver agent and record finding dispositions.

Adjudication suppression lives in :mod:`sandbox_executor.flow.review.adjudication`.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.agent_defaults import FALLBACK_AGENT
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.dispatch import _dispatch_role
from sandbox_executor.flow.host_remote import push_commit, remote_error
from sandbox_executor.flow.review.ensemble import _ENGINE_PASSES_PER_DISPATCH
from sandbox_executor.flow.review.records import _bounded_error_context, record_review_action
from sandbox_executor.ledger import generate_action_id
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.resolver_evidence import OPEN
from sandbox_executor.resolver_result import (
    read_container_resolver_result,
    rejected_attempt_fields,
    resolver_error_text,
)
from sandbox_executor.review_report import (
    FindingDisposition,
    FindingRow,
    deserialize_finding_dispositions,
    finding_signature,
    serialize_finding_disposition,
    serialize_finding_for_resolver,
)
from sandbox_executor.review_target import ReviewTarget


@dataclass(frozen=True)
class ResolverOutcome:
    """Structured result of one resolver pass on an iteration's findings."""

    dispositions: tuple[FindingDisposition, ...] = ()
    head_sha: str = ""
    pushed: bool = False
    lint_passed: bool = True
    tests_passed: bool = True
    error: str | None = None
    rejected_patch: str | None = None
    failed_tests: tuple[str, ...] = ()
    test_summary: str | None = None


@dataclass(frozen=True)
class ResolverInput:
    """Input payload for a resolver pass."""

    findings: Sequence[FindingRow]
    action_ids: Mapping[str, str] = field(default_factory=dict)
    rejected_findings: Sequence[FindingDisposition] = ()


def _push_resolver_head(
    context: FlowContext, target: ReviewTarget, head_sha: str, container_error: str | None
) -> tuple[bool, str | None]:
    """Push the head the resolver committed in the container, from the host; return ``(pushed, error)``.

    Nothing is pushed after a failed pass, under ``skip_push`` or when the head did not move. The push is a
    fast-forward only and a failure becomes the named ``HostRemoteError`` message of the resolver pass.
    """
    if container_error or context.skip_push or not head_sha or head_sha == target.head_sha or not target.head_branch:
        return False, None
    error = remote_error(push_commit, context.target_worktree, head_sha, target.head_branch)
    return error is None, None if error is None else f"Resolver push failed: {error}"


def _dispatch_review_resolver(
    context: FlowContext,
    target: ReviewTarget,
    iteration: int,
    resolver_input: ResolverInput,
) -> ResolverOutcome:
    """Dispatch one resolver pass into the review-loop role container via ``_dispatch_role``."""
    tmp_dir = os.path.expanduser("~/.holon/tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    fd, result_file = tempfile.mkstemp(prefix="holon-resolve-result-", suffix=".json", dir=tmp_dir)
    try:
        os.fchmod(fd, 0o666)
    finally:
        os.close(fd)

    findings = resolver_input.findings
    lookup = dict(resolver_input.action_ids or {})
    input_payload = {
        "iteration": iteration,
        "target": target.raw,
        "head_branch": target.head_branch,
        "head_sha": target.head_sha,
        "findings": [
            serialize_finding_for_resolver(row, introduced_by_action_id=lookup.get(row.id)) for row in findings
        ],
        "rejected_findings": [serialize_finding_disposition(item) for item in resolver_input.rejected_findings],
    }

    target_ref = target.raw or target.head_branch or "HEAD"
    container_args = [
        target_ref,
        context.agent or FALLBACK_AGENT,
        context.model or "inherit",
        "--resolve",
        f"--max-iterations={_ENGINE_PASSES_PER_DISPATCH}",
        f"--iteration={iteration}",
        "--no-auto-pr",
        "--skip-post",
        "--findings-file=/tmp/review_result.json",
        "--result-file=/tmp/review_result.json",
        "--json",
    ]
    if context.dry_run:
        container_args.append("--dry-run")

    try:
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(input_payload, f, indent=2)

        exit_code = _dispatch_role(context, "review-loop", container_args, result_file=result_file)
        data: dict[str, Any] = {}
        if os.path.exists(result_file) and os.path.getsize(result_file) > 0:
            with contextlib.suppress(OSError, ValueError), open(result_file, encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded

        # Fails closed: a non-zero exit, or a file still holding the input payload, is a failed pass.
        parsed = read_container_resolver_result(data, exit_code, target.head_sha)
        pushed, push_error = _push_resolver_head(context, target, parsed.head_sha, parsed.error)
        return ResolverOutcome(
            dispositions=deserialize_finding_dispositions(parsed.raw_dispositions, findings, lookup),
            head_sha=parsed.head_sha,
            pushed=pushed,
            lint_passed=parsed.lint_passed,
            tests_passed=parsed.tests_passed,
            error=parsed.error or push_error,
            rejected_patch=parsed.rejected_patch,
            failed_tests=parsed.failed_tests,
            test_summary=parsed.test_summary,
        )
    except Exception as exc:
        dispositions = deserialize_finding_dispositions([], findings, lookup)
        return ResolverOutcome(
            dispositions=dispositions,
            head_sha=target.head_sha,
            pushed=False,
            lint_passed=False,
            tests_passed=False,
            error=f"Resolver container dispatch failed: {redact_agent_secrets(str(exc))}",
        )
    finally:
        with contextlib.suppress(OSError):
            os.remove(result_file)


def record_rejected_attempt(
    context: FlowContext, exec_file: str, review_target: str, iteration: int, outcome: ResolverOutcome
) -> None:
    """Append one failed action row pointing at the rejected patch and test summary of a failed pre-push run.

    Nothing is written when the pass kept no rejected attempt. The ledger writer redacts every string again.
    """
    if not (outcome.rejected_patch or outcome.failed_tests or outcome.test_summary):
        return
    row: dict[str, Any] = {
        "action_id": generate_action_id("ACT-RES"),
        "execution_id": f"REV-RES-{iteration}-{int(time.time())}",
        "action": f"Review Loop iteration {iteration} resolver change rejected: pre-push tests failed",
        "category": "code",
        "target": review_target,
        "outcome": "failure",
        "entry_type": "action",
        "action_origin": "pr_review",
        "review_iteration": iteration,
        "labels": ["review", "resolver", f"iteration-{iteration}", "rejected-attempt"],
        "error_context": _bounded_error_context(outcome.test_summary) if outcome.test_summary else None,
        "rejected_patch": outcome.rejected_patch,
        "failed_tests": list(outcome.failed_tests),
        "created_at": datetime.now(UTC).isoformat(),
    }
    record_review_action(context, exec_file, row)


def _lookup_introducing_action_id(row: FindingRow, action_ids: Mapping[str, str]) -> str | None:
    """Find the ACT-FIND action_id that first introduced ``row``."""
    sig_key = str(finding_signature(row.severity, row.path, row.line, row.summary))
    return action_ids.get(row.id) or action_ids.get(sig_key)


@dataclass
class DispositionRecorder:
    """Helper to record finding dispositions into executions.jsonl."""

    context: FlowContext
    exec_file: str
    review_target: str
    finding_action_ids: Mapping[str, str]
    recorded_keys: set[tuple[str, str, int | None, str]]
    all_dispositions: list[FindingDisposition]
    effective_dry_run: bool = False

    def record(self, iteration: int, dispositions: Sequence[FindingDisposition]) -> int:
        """Append one ACT-RES row per newly finalised finding disposition.

        Returns count of suppressed rows in dry run.
        """
        suppressed = 0
        for disp in dispositions:
            sig = finding_signature(disp.severity, disp.path, disp.line, disp.summary)
            if sig in self.recorded_keys:
                continue
            self.recorded_keys.add(sig)
            intro_id = (
                disp.introduced_by_action_id
                or self.finding_action_ids.get(disp.id)
                or self.finding_action_ids.get(str(sig))
            )
            final_disp = dataclasses.replace(disp, introduced_by_action_id=intro_id)
            self.all_dispositions.append(final_disp)
            if self.effective_dry_run:
                suppressed += 1
                continue
            # A finding left open has no ledger disposition value: the row records it with a null disposition.
            left_open = final_disp.disposition == OPEN
            outcome = "failure" if left_open or final_disp.rejection_reason == "no-convergence" else "success"
            detail = final_disp.rejection_reason or (final_disp.evidence if left_open else None)
            res_row: dict[str, Any] = {
                "action_id": generate_action_id("ACT-RES"),
                "execution_id": f"REV-RES-{iteration}-{int(time.time())}",
                "action": f"Review Loop iteration {iteration} finding {final_disp.id} "
                + ("left open (no evidenced adjudication)" if left_open else f"adjudicated: {final_disp.disposition}"),
                "category": "code",
                "target": self.review_target,
                "outcome": outcome,
                "entry_type": "action",
                "action_origin": "pr_review",
                "review_severity": (
                    final_disp.severity if final_disp.severity in ("critical", "important", "nit") else None
                ),
                "review_iteration": iteration,
                "review_finding_ids": [final_disp.id],
                "review_disposition": None if left_open else final_disp.disposition,
                "rejection_reason": final_disp.rejection_reason,
                "introduced_by_action_id": intro_id,
                "labels": ["review", "resolver", f"iteration-{iteration}"],
                "error_context": _bounded_error_context(detail) if detail else None,
                "created_at": datetime.now(UTC).isoformat(),
            }
            record_review_action(self.context, self.exec_file, res_row)
        return suppressed


def _invoke_resolver_pass(
    context: FlowContext,
    target: ReviewTarget,
    iteration: int,
    resolver_input: ResolverInput,
    run_resolver_fn: Callable[..., Any] | None,
) -> ResolverOutcome:
    """Invoke the injected resolver seam or dispatch the resolver role container."""
    findings = resolver_input.findings
    action_ids = resolver_input.action_ids
    rejected_findings = resolver_input.rejected_findings
    lookup = {
        **action_ids,
        **{row.id: _lookup_introducing_action_id(row, action_ids) or "" for row in findings},
    }
    lookup = {k: v for k, v in lookup.items() if v}
    resolved_input = dataclasses.replace(resolver_input, action_ids=lookup)
    if run_resolver_fn is None:
        return _dispatch_review_resolver(context, target, iteration, resolved_input)
    try:
        raw = run_resolver_fn(
            target=target,
            iteration=iteration,
            findings=list(findings),
            action_ids=lookup,
            rejected_findings=list(rejected_findings),
        )
    except TypeError:
        raw = run_resolver_fn(list(findings), iteration)
    except Exception as exc:
        dispositions = deserialize_finding_dispositions([], findings, lookup)
        return ResolverOutcome(
            dispositions=dispositions,
            head_sha=target.head_sha,
            pushed=False,
            error=f"Resolver failed: {redact_agent_secrets(str(exc))}",
        )
    if isinstance(raw, ResolverOutcome):
        dispositions = deserialize_finding_dispositions(
            [serialize_finding_disposition(d) for d in raw.dispositions], findings, lookup
        )
        return dataclasses.replace(raw, dispositions=dispositions, head_sha=raw.head_sha or target.head_sha)
    if isinstance(raw, Mapping):
        raw_disps = raw.get("dispositions")
        if isinstance(raw_disps, Sequence):
            raw_disps = [
                serialize_finding_disposition(d) if isinstance(d, FindingDisposition) else d for d in raw_disps
            ]
        dispositions = deserialize_finding_dispositions(raw_disps, findings, lookup)
        head_sha = str(raw.get("head_sha") or target.head_sha or "").strip()
        pushed = bool(raw.get("pushed", head_sha != target.head_sha))
        err = resolver_error_text(raw.get("error"), raw.get("exit_code"), raw.get("output_tail"))
        return ResolverOutcome(
            dispositions=dispositions,
            head_sha=head_sha,
            pushed=pushed,
            lint_passed=bool(raw.get("lint_passed", True)),
            tests_passed=bool(raw.get("tests_passed", True)),
            error=err,
            **rejected_attempt_fields(raw),
        )
    if isinstance(raw, Sequence):
        raw_list = [serialize_finding_disposition(d) if isinstance(d, FindingDisposition) else d for d in raw]
        dispositions = deserialize_finding_dispositions(raw_list, findings, lookup)
        pushed = any(d.disposition == "applied" for d in dispositions)
        new_sha = f"{target.head_sha or '0' * 32}-r{iteration}" if pushed else target.head_sha
        return ResolverOutcome(dispositions=dispositions, head_sha=new_sha, pushed=pushed)
    dispositions = deserialize_finding_dispositions([], findings, lookup)
    return ResolverOutcome(dispositions=dispositions, head_sha=target.head_sha, pushed=False)
