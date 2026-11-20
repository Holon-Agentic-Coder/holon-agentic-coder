"""Ledger action and summary record models, persistence, and commit trailer parsing."""

from __future__ import annotations

import contextlib
import hashlib
import itertools
import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sandbox_executor.ledger.lock import (
    LedgerRewriteError,
    atomic_ledger_append,
)
from sandbox_executor.ledger.query import validate_ledger_file_unique_ids
from sandbox_executor.naming import distinct_stage_id, stage_id
from sandbox_executor.ref_names import ensure_stage_segment_fits

logger = logging.getLogger(__name__)

VALID_CATEGORIES: set[str] = {"code", "doc", "config", "test", "build", "dependency", "budget"}
VALID_OUTCOMES: set[str] = {"success", "failure", "reverted", "blocked"}
VALID_ORIGINS: set[str] = {"initial_implementation", "pr_review", "ci_fix", "calibration", "operator"}
VALID_SEVERITIES: set[str] = {"critical", "important", "nit"}
VALID_DISPOSITIONS: set[str] = {"applied", "rejected"}
VALID_ENTRY_TYPES: set[str] = {"action", "summary"}

OPTIONAL_OMITTED_FIELDS: tuple[str, ...] = ("stage", "budget_name", "budget_seconds", "elapsed_seconds")


def generate_action_id(prefix: str = "ACT") -> str:
    """Generate a unique action identifier."""
    ts = int(time.time())
    suffix = uuid.uuid4().hex[:8]
    return f"{prefix}-{ts}-{suffix}"


_disambiguator_counter = itertools.count()


def _sanitize_string(name: str, fallback: str = "unknown") -> str:
    """Sanitise an identifier component for safe use in branch names and filenames."""
    s = re.sub(r"\s+", "-", name.replace("/", "_"))
    s = re.sub(r"[^a-zA-Z0-9_.-]", "", s)
    s = re.sub(r"-+", "-", s)
    s = re.sub(r"\.+", ".", s)
    s = s[:64].strip(".-")
    return s or fallback


def _sanitize_model_name(model_name: str) -> str:
    """Sanitise a model name so it is safe for use in git branch names and filenames."""
    return _sanitize_string(model_name, fallback="unknown-model")


def build_stage_disambiguator(parent_ref: str) -> str:
    """Generate a deterministic 8-character lowercase hex disambiguator."""
    clean_ref = parent_ref.removesuffix("/_").rstrip("/")
    seed = f"{clean_ref}:{time.time_ns()}:{time.perf_counter_ns()}:{os.getpid()}:{next(_disambiguator_counter)}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]


def build_plan_id(
    plan_seq: int,
    parent_branch: str,
    agent: str,
    model: str,
    taken: Iterable[str] = (),
) -> str:
    """Build the plan id ``P-{plan_seq}-{ticker}-{model code}`` (no hex); a name in ``taken`` gets a free variant."""
    plan_id = distinct_stage_id(stage_id("P", plan_seq, agent, model), set(taken))
    ensure_stage_segment_fits(parent_branch, plan_id, extra=len(plan_id) + 1)
    return plan_id


def build_execution_id(
    exec_seq: int,
    parent_branch: str,
    agent: str,
    model: str,
    taken: Iterable[str] = (),
) -> str:
    """Build execution id ``E-{exec_seq}-{ticker}-{model code}``; a name in ``taken`` gets a free variant."""
    exec_id = distinct_stage_id(stage_id("E", exec_seq, agent, model), set(taken))
    ensure_stage_segment_fits(parent_branch, exec_id)
    return exec_id


def _validate_non_empty(val: str, name: str) -> None:
    if not val:
        raise ValueError(f"{name} must not be empty")


def _validate_enum(val: str, valid_set: set[str], name: str) -> None:
    if val not in valid_set:
        raise ValueError(f"Invalid {name} '{val}'. Must be one of: {sorted(valid_set)}")


@dataclass
class ActionRecord:
    """Standardised discrete action record for the execution ledger."""

    action_id: str
    execution_id: str
    action: str
    category: str
    target: str
    outcome: str
    entry_type: Literal["action"] = "action"
    parent_action_id: str | None = None
    event_type: str = "command_execution"
    labels: list[str] = field(default_factory=list)
    error_context: str | None = None
    mistake_category: str | None = None
    action_origin: str = "initial_implementation"
    review_severity: str | None = None
    review_iteration: int | None = None
    review_finding_ids: list[str] | None = None
    review_disposition: str | None = None
    rejection_reason: str | None = None
    introduced_by_action_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    stage: str | None = None
    budget_name: str | None = None
    budget_seconds: float | None = None
    elapsed_seconds: float | None = None

    def validate(self) -> None:
        """Validate constraints and enum values for action record."""
        _validate_non_empty(self.action_id, "action_id")
        _validate_non_empty(self.execution_id, "execution_id")
        _validate_non_empty(self.action, "action")
        _validate_enum(self.category, VALID_CATEGORIES, "category")
        _validate_enum(self.outcome, VALID_OUTCOMES, "outcome")
        _validate_enum(self.action_origin, VALID_ORIGINS, "action_origin")
        if self.review_severity is not None:
            _validate_enum(self.review_severity, VALID_SEVERITIES, "review_severity")
        if self.review_disposition is not None:
            _validate_enum(self.review_disposition, VALID_DISPOSITIONS, "review_disposition")
        if self.category == "budget" and not (self.stage and self.budget_name):
            raise ValueError("budget actions must name the stage and the budget that fired")
        if self.review_iteration is not None and self.review_iteration < 1:
            raise ValueError(f"review_iteration must be a positive integer >= 1, got {self.review_iteration}")

    def to_dict(self) -> dict[str, Any]:
        """Convert record to dictionary, validating constraints."""
        self.validate()
        data = asdict(self)
        data["entry_type"] = "action"
        for name in OPTIONAL_OMITTED_FIELDS:
            if data.get(name) is None:
                data.pop(name, None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionRecord:
        """Construct ActionRecord from dictionary."""
        known_keys = {
            "action_id",
            "execution_id",
            "action",
            "category",
            "target",
            "outcome",
            "entry_type",
            "parent_action_id",
            "event_type",
            "labels",
            "error_context",
            "mistake_category",
            "action_origin",
            "review_severity",
            "review_iteration",
            "review_finding_ids",
            "review_disposition",
            "rejection_reason",
            "introduced_by_action_id",
            "created_at",
            *OPTIONAL_OMITTED_FIELDS,
        }
        filtered = {k: v for k, v in data.items() if k in known_keys}
        if "entry_type" in filtered and filtered["entry_type"] != "action":
            filtered["entry_type"] = "action"
        if "labels" in filtered and isinstance(filtered["labels"], str):
            filtered["labels"] = [lbl.strip() for lbl in filtered["labels"].split(",") if lbl.strip()]
        return cls(**filtered)


@dataclass
class SummaryRecord:
    """Standardised summary record for plan executions."""

    execution_id: str
    plan_branch: str
    agent: str
    model: str
    status: str
    entry_type: Literal["summary"] = "summary"
    agent_version: str | None = None
    summary: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    agent_output_truncated: bool = False
    agent_output_bytes: int = 0
    ledger_revision: int = 1
    sub_intents: list[dict[str, Any]] | None = None
    execution_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert record to dictionary."""
        data = asdict(self)
        data["entry_type"] = "summary"
        if data.get("sub_intents") is None:
            data.pop("sub_intents", None)
        if data.get("execution_file") is None:
            data.pop("execution_file", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SummaryRecord:
        """Construct SummaryRecord from dictionary."""
        known_keys = {
            "execution_id",
            "plan_branch",
            "agent",
            "model",
            "status",
            "entry_type",
            "agent_version",
            "summary",
            "created_at",
            "agent_output_truncated",
            "agent_output_bytes",
            "ledger_revision",
            "sub_intents",
            "execution_file",
        }
        filtered = {k: v for k, v in data.items() if k in known_keys}
        if "entry_type" in filtered and filtered["entry_type"] != "summary":
            filtered["entry_type"] = "summary"
        return cls(**filtered)


def _prepare_shape_preserving_action(record: dict[str, Any]) -> dict[str, Any]:
    """Validate an action dictionary while keeping its key order and any extra keys."""
    copy_rec = dict(record)
    copy_rec["entry_type"] = "action"
    if not copy_rec.get("action_id"):
        copy_rec["action_id"] = generate_action_id()
    if "action_origin" not in copy_rec:
        copy_rec["action_origin"] = "initial_implementation"
    try:
        ActionRecord.from_dict(copy_rec).validate()
    except TypeError as exc:
        raise ValueError(f"Malformed action record: {exc}") from exc
    return copy_rec


def append_action_record(
    ledger_path: str,
    record: ActionRecord | dict[str, Any],
    *,
    preserve_shape: bool = False,
) -> dict[str, Any]:
    """Append a discrete action record to the execution ledger under LedgerLock."""
    if isinstance(record, ActionRecord):
        data = record.to_dict()
    elif preserve_shape:
        data = _prepare_shape_preserving_action(record)
    else:
        copy_rec = dict(record)
        if "action_id" not in copy_rec:
            copy_rec["action_id"] = generate_action_id()
        if "entry_type" not in copy_rec or copy_rec["entry_type"] != "action":
            copy_rec["entry_type"] = "action"
        if "action_origin" not in copy_rec:
            copy_rec["action_origin"] = "initial_implementation"
        if "labels" in copy_rec and isinstance(copy_rec["labels"], str):
            copy_rec["labels"] = [lbl.strip() for lbl in copy_rec["labels"].split(",") if lbl.strip()]
        data = ActionRecord.from_dict(copy_rec).to_dict()

    line = json.dumps(data) + "\n"
    with atomic_ledger_append(ledger_path) as f:
        f.write(line)

    return data


def _report_ledger_failure(
    ledger_path: str,
    label: str,
    record_id: str,
    exc: BaseException,
    log_fn: Callable[[str], None] | None,
) -> None:
    """Log a ledger append failure at WARNING level and mirror it to an operator log."""
    from sandbox_executor.knowledge.records import CredentialRedactor

    detail = CredentialRedactor.redact_text(str(exc))
    message = f"Ledger append failed ({label}, record {record_id}) for {ledger_path}: {type(exc).__name__}: {detail}"
    logger.warning(message)
    if log_fn is not None:
        with contextlib.suppress(Exception):
            log_fn(f"Warning: {message}")


def try_append_action_record(
    ledger_path: str,
    record: ActionRecord | dict[str, Any],
    *,
    label: str,
    preserve_shape: bool = True,
    log_fn: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """Append an action record under LedgerLock, reporting rather than raising on failure."""
    from sandbox_executor.knowledge.records import CredentialRedactor

    record_id = record.action_id if isinstance(record, ActionRecord) else str(record.get("action_id") or "?")
    try:
        if isinstance(record, ActionRecord):
            record = ActionRecord.from_dict(CredentialRedactor.redact_structure(record.to_dict()))
        else:
            record = CredentialRedactor.redact_structure(dict(record))
        ledger_mod = sys.modules.get("sandbox_executor.ledger")
        appender = getattr(ledger_mod, "append_action_record", append_action_record)
        return appender(ledger_path, record, preserve_shape=preserve_shape)
    except LedgerRewriteError:
        raise
    except Exception as exc:
        _report_ledger_failure(ledger_path, label, record_id, exc, log_fn)
        return None


def try_append_ledger_line(
    ledger_path: str,
    record: dict[str, Any],
    *,
    label: str,
    log_fn: Callable[[str], None] | None = None,
) -> bool:
    """Append an arbitrary JSON ledger row verbatim under LedgerLock, reporting failures."""
    try:
        line = json.dumps(record) + "\n"
        with atomic_ledger_append(ledger_path) as f:
            f.write(line)
        return True
    except LedgerRewriteError:
        raise
    except Exception as exc:
        record_id = str(record.get("action_id") or record.get("execution_id") or record.get("plan_id") or "?")
        _report_ledger_failure(ledger_path, label, record_id, exc, log_fn)
        return False


def append_summary_record(ledger_path: str, record: SummaryRecord | dict[str, Any]) -> dict[str, Any]:
    """Append a summary record to the execution ledger under LedgerLock."""
    if isinstance(record, SummaryRecord):
        data = record.to_dict()
    else:
        data = dict(record)
        data["entry_type"] = "summary"

    with atomic_ledger_append(ledger_path) as f:
        f.write(json.dumps(data) + "\n")

    if os.path.exists(ledger_path):
        validate_ledger_file_unique_ids(ledger_path, id_key="execution_id", parent_key="plan_branch")

    return data


# Canonical Aliases
append_action_ledger = append_action_record
append_execution_record = append_summary_record


def _parse_list_trailer(val: str) -> list[str] | None:
    parts = [p.strip() for p in re.split(r"[,;\s]+", val) if p.strip()]
    return parts if parts else None


def _parse_single_trailer(trailer_key: str, val: str) -> tuple[str, Any] | None:
    res: tuple[str, Any] | None = None
    if trailer_key == "action-origin":
        clean = val.lower()
        res = ("action_origin", clean if clean in VALID_ORIGINS else "initial_implementation")
    elif trailer_key == "review-severity":
        clean = val.lower()
        res = ("review_severity", clean if clean in VALID_SEVERITIES else None)
    elif trailer_key == "review-iteration":
        try:
            parsed = int(val)
            res = ("review_iteration", parsed if parsed >= 1 else None)
        except ValueError:
            res = ("review_iteration", None)
    elif trailer_key == "review-disposition":
        clean = val.lower()
        res = ("review_disposition", clean if clean in VALID_DISPOSITIONS else None)
    elif trailer_key in ("review-finding-ids", "consulted-knowledge-ids"):
        field_name = "review_finding_ids" if trailer_key == "review-finding-ids" else "consulted_knowledge_ids"
        res = (field_name, _parse_list_trailer(val))
    elif trailer_key in ("rejection-reason", "introduced-by-action-id"):
        field_name = "rejection_reason" if trailer_key == "rejection-reason" else "introduced_by_action_id"
        res = (field_name, val or None)
    return res


def parse_commit_trailers(commit_message: str) -> dict[str, Any]:
    """Parse standardised action provenance trailers from commit message."""
    trailers: dict[str, Any] = {
        "action_origin": "initial_implementation",
        "review_severity": None,
        "review_iteration": None,
        "review_finding_ids": None,
        "review_disposition": None,
        "rejection_reason": None,
        "introduced_by_action_id": None,
        "consulted_knowledge_ids": None,
    }
    if not commit_message:
        return trailers

    pattern = re.compile(
        r"^(action-origin|review-severity|review-iteration|review-finding-ids|review-disposition|rejection-reason|introduced-by-action-id|consulted-knowledge-ids):\s*(.+)$",
        re.MULTILINE | re.IGNORECASE,
    )

    for match in pattern.finditer(commit_message):
        parsed = _parse_single_trailer(match.group(1).lower(), match.group(2).strip())
        if parsed is not None:
            trailers[parsed[0]] = parsed[1]

    return trailers


def extract_action_provenance_from_commit(repo_dir: str = ".", commit_ref: str = "HEAD") -> dict[str, Any]:
    """Extract action provenance metadata from git commit trailers."""
    try:
        proc = subprocess.run(
            ["git", "-C", repo_dir, "log", "-1", "--pretty=%B", commit_ref],
            capture_output=True,
            text=True,
            check=True,
        )
        msg = proc.stdout
    except Exception as e:
        logger.debug("Failed to extract commit message for ref %s: %s", commit_ref, e)
        msg = ""
    return parse_commit_trailers(msg)


def format_commit_trailers(
    action_origin: str = "initial_implementation",
    review_severity: str | None = None,
    review_iteration: int | None = None,
    review_finding_ids: list[str] | None = None,
    **extra_trailers: Any,
) -> str:
    """Format structured commit trailers block for git commit messages."""
    lines: list[str] = [f"Action-Origin: {action_origin}"]
    if review_severity:
        lines.append(f"Review-Severity: {review_severity}")
    if review_iteration is not None:
        lines.append(f"Review-Iteration: {review_iteration}")
    if review_finding_ids:
        lines.append(f"Review-Finding-Ids: {', '.join(review_finding_ids)}")
    review_disposition = extra_trailers.get("review_disposition")
    if review_disposition:
        lines.append(f"Review-Disposition: {review_disposition}")
    rejection_reason = extra_trailers.get("rejection_reason")
    if rejection_reason:
        lines.append(f"Rejection-Reason: {rejection_reason}")
    introduced_by_action_id = extra_trailers.get("introduced_by_action_id")
    if introduced_by_action_id:
        lines.append(f"Introduced-By-Action-Id: {introduced_by_action_id}")
    consulted_knowledge_ids = extra_trailers.get("consulted_knowledge_ids")
    if consulted_knowledge_ids:
        lines.append(f"Consulted-Knowledge-IDs: {', '.join(consulted_knowledge_ids)}")
    return "\n".join(lines)
