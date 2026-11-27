from __future__ import annotations

import contextlib
import hashlib
import itertools
import json
import logging
import os
import re
import subprocess
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from sandbox_executor.credential_redactor import CredentialRedactor
from sandbox_executor.ledger.lock import LedgerRewriteError
from sandbox_executor.ledger.order import utc_now_z
from sandbox_executor.ledger.route import atomic_ledger_append
from sandbox_executor.naming import distinct_stage_id, stage_id
from sandbox_executor.ref_names import ensure_stage_segment_fits

logger = logging.getLogger(__name__)

VALID_CATEGORIES: set[str] = {"code", "doc", "config", "test", "build", "dependency", "budget"}
VALID_OUTCOMES: set[str] = {"success", "failure", "reverted", "blocked"}
VALID_ORIGINS: set[str] = {"initial_implementation", "pr_review", "ci_fix", "calibration", "operator"}
VALID_SEVERITIES: set[str] = {"critical", "important", "nit"}
VALID_DISPOSITIONS: set[str] = {"applied", "rejected"}
VALID_ENTRY_TYPES: set[str] = {"action", "summary"}
_REPEAT_CHECKED_ID_KEYS: frozenset[str] = frozenset({"plan_id"})

OPTIONAL_OMITTED_FIELDS: tuple[str, ...] = ("stage", "budget_name", "budget_seconds", "elapsed_seconds")

_ACTION_ID_HEX_LEN = 8
_DISAMBIGUATOR_HEX_LEN = 8
_MAX_SANITIZED_LEN = 64
_DEFAULT_ACTION_ORIGIN = "initial_implementation"

_ACTION_KNOWN_KEYS: frozenset[str] = frozenset(
    {
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
)

_SUMMARY_KNOWN_KEYS: frozenset[str] = frozenset(
    {
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
)

_COMMIT_TRAILER_PATTERN = re.compile(
    r"^(action-origin|review-severity|review-iteration|review-finding-ids|review-disposition|rejection-reason|introduced-by-action-id|consulted-knowledge-ids):\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)


def _split_csv_labels(raw: str) -> list[str]:
    return [lbl.strip() for lbl in raw.split(",") if lbl.strip()]


def generate_action_id(prefix: str = "ACT") -> str:
    ts = int(time.time())
    suffix = uuid.uuid4().hex[:_ACTION_ID_HEX_LEN]
    return f"{prefix}-{ts}-{suffix}"


_disambiguator_counter = itertools.count()


def _sanitize_string(name: str, fallback: str = "unknown") -> str:
    sanitized = re.sub(r"\s+", "-", name.replace("/", "_"))
    sanitized = re.sub(r"[^a-zA-Z0-9_.-]", "", sanitized)
    sanitized = re.sub(r"-+", "-", sanitized)
    sanitized = re.sub(r"\.+", ".", sanitized)
    sanitized = sanitized[:_MAX_SANITIZED_LEN].strip(".-")
    return sanitized or fallback


def _sanitize_model_name(model_name: str) -> str:
    return _sanitize_string(model_name, fallback="unknown-model")


def build_stage_disambiguator(parent_ref: str) -> str:
    clean_ref = parent_ref.removesuffix("/_").rstrip("/")
    seed = f"{clean_ref}:{time.time_ns()}:{time.perf_counter_ns()}:{os.getpid()}:{next(_disambiguator_counter)}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:_DISAMBIGUATOR_HEX_LEN]


def build_plan_id(
    plan_seq: int,
    parent_branch: str,
    agent: str,
    model: str,
    taken: Iterable[str] = (),
) -> str:
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
    exec_id = distinct_stage_id(stage_id("E", exec_seq, agent, model), set(taken))
    ensure_stage_segment_fits(parent_branch, exec_id)
    return exec_id


@dataclass
class ActionRecord:
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
    action_origin: str = _DEFAULT_ACTION_ORIGIN
    review_severity: str | None = None
    review_iteration: int | None = None
    review_finding_ids: list[str] | None = None
    review_disposition: str | None = None
    rejection_reason: str | None = None
    introduced_by_action_id: str | None = None
    created_at: str = field(default_factory=utc_now_z)
    stage: str | None = None
    budget_name: str | None = None
    budget_seconds: float | None = None
    elapsed_seconds: float | None = None

    def _validate_enums(self) -> None:
        if self.category not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category '{self.category}'. Must be one of: {sorted(VALID_CATEGORIES)}")
        if self.outcome not in VALID_OUTCOMES:
            raise ValueError(f"Invalid outcome '{self.outcome}'. Must be one of: {sorted(VALID_OUTCOMES)}")
        if self.action_origin not in VALID_ORIGINS:
            raise ValueError(f"Invalid action_origin '{self.action_origin}'. Must be one of: {sorted(VALID_ORIGINS)}")
        if self.review_severity is not None and self.review_severity not in VALID_SEVERITIES:
            raise ValueError(
                f"Invalid review_severity '{self.review_severity}'. Must be one of: {sorted(VALID_SEVERITIES)}"
            )
        if self.review_disposition is not None and self.review_disposition not in VALID_DISPOSITIONS:
            raise ValueError(
                f"Invalid review_disposition '{self.review_disposition}'. Must be one of: {sorted(VALID_DISPOSITIONS)}"
            )

    def validate(self) -> None:
        if not self.action_id:
            raise ValueError("action_id must not be empty")
        if not self.execution_id:
            raise ValueError("execution_id must not be empty")
        if not self.action:
            raise ValueError("action must not be empty")
        self._validate_enums()
        if self.category == "budget" and not (self.stage and self.budget_name):
            raise ValueError("budget actions must name the stage and the budget that fired")
        if self.review_iteration is not None and self.review_iteration < 1:
            raise ValueError(f"review_iteration must be a positive integer >= 1, got {self.review_iteration}")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data = asdict(self)
        data["entry_type"] = "action"
        for name in OPTIONAL_OMITTED_FIELDS:
            if data.get(name) is None:
                data.pop(name, None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionRecord:
        filtered = {k: v for k, v in data.items() if k in _ACTION_KNOWN_KEYS}
        if "entry_type" in filtered and filtered["entry_type"] != "action":
            filtered["entry_type"] = "action"
        if "labels" in filtered and isinstance(filtered["labels"], str):
            filtered["labels"] = _split_csv_labels(filtered["labels"])
        return cls(**filtered)


@dataclass
class SummaryRecord:
    execution_id: str
    plan_branch: str
    agent: str
    model: str
    status: str
    entry_type: Literal["summary"] = "summary"
    agent_version: str | None = None
    summary: str | None = None
    created_at: str = field(default_factory=utc_now_z)
    agent_output_truncated: bool = False
    agent_output_bytes: int = 0
    ledger_revision: int = 1
    sub_intents: list[dict[str, Any]] | None = None
    execution_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["entry_type"] = "summary"
        if data.get("sub_intents") is None:
            data.pop("sub_intents", None)
        if data.get("execution_file") is None:
            data.pop("execution_file", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SummaryRecord:
        filtered = {k: v for k, v in data.items() if k in _SUMMARY_KNOWN_KEYS}
        if "entry_type" in filtered and filtered["entry_type"] != "summary":
            filtered["entry_type"] = "summary"
        return cls(**filtered)


def _prepare_shape_preserving_action(record: dict[str, Any]) -> dict[str, Any]:
    copy_rec = dict(record)
    copy_rec["entry_type"] = "action"
    if not copy_rec.get("action_id"):
        copy_rec["action_id"] = generate_action_id()
    if "action_origin" not in copy_rec:
        copy_rec["action_origin"] = _DEFAULT_ACTION_ORIGIN
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
            copy_rec["action_origin"] = _DEFAULT_ACTION_ORIGIN
        if "labels" in copy_rec and isinstance(copy_rec["labels"], str):
            copy_rec["labels"] = _split_csv_labels(copy_rec["labels"])
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
    from sandbox_executor.redaction import redact_agent_secrets

    detail = redact_agent_secrets(str(exc))
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
    record_id = record.action_id if isinstance(record, ActionRecord) else str(record.get("action_id") or "?")
    try:
        if isinstance(record, ActionRecord):
            record = ActionRecord.from_dict(CredentialRedactor.redact_structure(record.to_dict()))
        else:
            record = CredentialRedactor.redact_structure(dict(record))
        return append_action_record(ledger_path, record, preserve_shape=preserve_shape)
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
    from sandbox_executor.ledger.query import validate_ledger_file_unique_ids

    if isinstance(record, SummaryRecord):
        data = record.to_dict()
    else:
        data = dict(record)
        data["entry_type"] = "summary"

    with atomic_ledger_append(ledger_path) as f:
        f.write(json.dumps(data) + "\n")

    validate_ledger_file_unique_ids(ledger_path, id_key="execution_id", parent_key="plan_branch")

    return data


append_action_ledger = append_action_record
append_execution_record = append_summary_record


def _handle_review_trailer(key: str, val: str, trailers: dict[str, Any]) -> None:
    if key == "review-severity":
        clean_val = val.lower()
        if clean_val in VALID_SEVERITIES:
            trailers["review_severity"] = clean_val
    elif key == "review-iteration":
        try:
            parsed_iter = int(val)
            if parsed_iter >= 1:
                trailers["review_iteration"] = parsed_iter
        except ValueError:
            trailers["review_iteration"] = None
    elif key == "review-finding-ids":
        parts = [p.strip() for p in re.split(r"[,;\s]+", val) if p.strip()]
        trailers["review_finding_ids"] = parts if parts else None
    elif key == "review-disposition":
        clean_val = val.lower()
        if clean_val in VALID_DISPOSITIONS:
            trailers["review_disposition"] = clean_val


def _handle_trailer(key: str, val: str, trailers: dict[str, Any]) -> None:
    if key.startswith("review-"):
        _handle_review_trailer(key, val, trailers)
    elif key == "action-origin":
        clean_val = val.lower()
        trailers["action_origin"] = clean_val if clean_val in VALID_ORIGINS else _DEFAULT_ACTION_ORIGIN
    elif key == "consulted-knowledge-ids":
        parts = [p.strip() for p in re.split(r"[,;\s]+", val) if p.strip()]
        trailers["consulted_knowledge_ids"] = parts if parts else None
    elif key in ("rejection-reason", "introduced-by-action-id"):
        out_key = "rejection_reason" if key == "rejection-reason" else "introduced_by_action_id"
        trailers[out_key] = val if val else None


def parse_commit_trailers(commit_message: str) -> dict[str, Any]:
    trailers: dict[str, Any] = {
        "action_origin": _DEFAULT_ACTION_ORIGIN,
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

    for match in _COMMIT_TRAILER_PATTERN.finditer(commit_message):
        _handle_trailer(match.group(1).lower(), match.group(2).strip(), trailers)

    return trailers


def extract_action_provenance_from_commit(repo_dir: str = ".", commit_ref: str = "HEAD") -> dict[str, Any]:
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


@dataclass(frozen=True, kw_only=True)
class CommitTrailers:
    review_severity: str | None = None
    review_iteration: int | None = None
    review_finding_ids: list[str] | None = None
    review_disposition: str | None = None
    rejection_reason: str | None = None
    introduced_by_action_id: str | None = None
    consulted_knowledge_ids: list[str] | None = None


def format_commit_trailers(action_origin: str = _DEFAULT_ACTION_ORIGIN, **trailers: Any) -> str:
    spec = CommitTrailers(**trailers)
    lines: list[str] = [f"Action-Origin: {action_origin}"]
    if spec.review_severity:
        lines.append(f"Review-Severity: {spec.review_severity}")
    if spec.review_iteration is not None:
        lines.append(f"Review-Iteration: {spec.review_iteration}")
    if spec.review_finding_ids:
        lines.append(f"Review-Finding-Ids: {', '.join(spec.review_finding_ids)}")
    if spec.review_disposition:
        lines.append(f"Review-Disposition: {spec.review_disposition}")
    if spec.rejection_reason:
        lines.append(f"Rejection-Reason: {spec.rejection_reason}")
    if spec.introduced_by_action_id:
        lines.append(f"Introduced-By-Action-Id: {spec.introduced_by_action_id}")
    if spec.consulted_knowledge_ids:
        lines.append(f"Consulted-Knowledge-IDs: {', '.join(spec.consulted_knowledge_ids)}")
    return "\n".join(lines)
