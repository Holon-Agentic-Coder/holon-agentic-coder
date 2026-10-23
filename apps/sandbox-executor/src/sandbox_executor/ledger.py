"""Ledger models and operations for recording actions, summaries, and commit trailers."""

import contextlib
import errno
import io
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, TextIO

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

VALID_CATEGORIES: set[str] = {"code", "doc", "config", "test", "build", "dependency"}
VALID_OUTCOMES: set[str] = {"success", "failure", "reverted", "blocked"}
VALID_ORIGINS: set[str] = {"initial_implementation", "pr_review", "ci_fix", "calibration", "operator"}
VALID_SEVERITIES: set[str] = {"critical", "important", "nit"}
VALID_DISPOSITIONS: set[str] = {"applied", "rejected"}
VALID_ENTRY_TYPES: set[str] = {"action", "summary"}


def generate_action_id(prefix: str = "ACT") -> str:
    """Generate a unique action identifier."""
    ts = int(time.time())
    suffix = uuid.uuid4().hex[:8]
    return f"{prefix}-{ts}-{suffix}"


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

    def validate(self) -> None:
        """Validate constraints and enum values for action record."""
        if not self.action_id:
            raise ValueError("action_id must not be empty")
        if not self.execution_id:
            raise ValueError("execution_id must not be empty")
        if not self.action:
            raise ValueError("action must not be empty")
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
        if self.review_iteration is not None and self.review_iteration < 1:
            raise ValueError(f"review_iteration must be a positive integer >= 1, got {self.review_iteration}")

    def to_dict(self) -> dict[str, Any]:
        """Convert record to dictionary, validating constraints."""
        self.validate()
        data = asdict(self)
        data["entry_type"] = "action"
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActionRecord":
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
    def from_dict(cls, data: dict[str, Any]) -> "SummaryRecord":
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


class LedgerLock:
    """Reusable advisory file locking context manager for ledger mutation safety (Bean 0127).

    Guarantees atomic append semantics across concurrent processes and threads
    using POSIX advisory locking (fcntl.flock with LOCK_EX | LOCK_NB) with bounded
    exponential backoff, randomised jitter, and configurable timeout.

    Flushes and fsyncs the file descriptor on lock release, ensuring durability.
    Gracefully falls back on platforms or filesystems without flock support.
    """

    def __init__(
        self,
        filepath: str,
        timeout: float = 10.0,
        initial_delay: float = 0.02,
        max_delay: float = 0.5,
    ) -> None:
        self.filepath = filepath
        self.timeout = timeout
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self._file: TextIO | None = None
        self._fd: int | None = None
        self._locked: bool = False

    def __enter__(self) -> TextIO:
        dirname = os.path.dirname(os.path.abspath(self.filepath))
        if dirname:
            os.makedirs(dirname, exist_ok=True)

        use_posix_lock = sys.platform != "win32" and fcntl is not None and hasattr(fcntl, "flock")
        self._file = open(self.filepath, "a", encoding="utf-8")

        if not use_posix_lock:
            logger.warning(
                "Advisory file locking (fcntl.flock) unavailable on platform %s; "
                "falling back to uncoordinated append for %s",
                sys.platform,
                self.filepath,
            )
            return self._file

        try:
            self._fd = self._file.fileno()
        except (AttributeError, io.UnsupportedOperation, OSError):
            self._fd = None

        if not isinstance(self._fd, int):
            return self._file

        start_time = time.monotonic()
        delay = self.initial_delay
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._locked = True
                break
            except (BlockingIOError, OSError) as e:
                if isinstance(e, OSError) and e.errno in (errno.ENOSYS, getattr(errno, "EOPNOTSUPP", 45)):
                    logger.warning(
                        "Advisory file locking not supported on filesystem for %s; "
                        "falling back to uncoordinated append",
                        self.filepath,
                    )
                    break
                if (
                    isinstance(e, OSError)
                    and e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK)
                    and not isinstance(e, BlockingIOError)
                ):
                    self._file.close()
                    raise
                if (time.monotonic() - start_time) >= self.timeout:
                    self._file.close()
                    raise TimeoutError(f"Timed out waiting for exclusive lock on ledger file: {self.filepath}") from e
                jitter = random.uniform(0.5, 1.5)
                sleep_time = min(delay * jitter, self.max_delay)
                time.sleep(sleep_time)
                delay = min(delay * 2, self.max_delay)

        return self._file

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._file is not None and not self._file.closed:
            try:
                self._file.flush()
                if self._fd is not None:
                    with contextlib.suppress(OSError):
                        os.fsync(self._fd)
                if self._locked and self._fd is not None:
                    with contextlib.suppress(OSError):
                        fcntl.flock(self._fd, fcntl.LOCK_UN)
                    self._locked = False
            finally:
                self._file.close()


@contextlib.contextmanager
def atomic_ledger_append(
    filepath: str,
    timeout: float = 10.0,
    initial_delay: float = 0.02,
    max_delay: float = 0.5,
) -> Iterator[TextIO]:
    """Atomic context manager for appending records to a ledger file with advisory locking."""
    with LedgerLock(
        filepath=filepath,
        timeout=timeout,
        initial_delay=initial_delay,
        max_delay=max_delay,
    ) as f:
        yield f


def _prepare_shape_preserving_action(record: dict[str, Any]) -> dict[str, Any]:
    """Validate an action dictionary while keeping its key order and any extra keys.

    Only the minimum normalisation is applied: ``entry_type`` is forced to ``"action"``
    (keeping its existing position when present), and ``action_id`` / ``action_origin``
    are filled in only when absent. Unknown keys are retained verbatim.

    Args:
        record: Action record dictionary to validate.

    Returns:
        A shallow copy of the record, ready to serialise.

    Raises:
        ValueError: If the record is missing required fields or carries invalid enum values.
    """
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
    """Append a discrete action record to the execution ledger under LedgerLock.

    Args:
        ledger_path: Path to the executions.jsonl ledger file.
        record: ActionRecord or dictionary with action fields.
        preserve_shape: When True and ``record`` is a dictionary, validate it against
            ``ActionRecord`` but serialise it verbatim, keeping key order and extra keys
            (for example calibration metrics). Defaults to False, which normalises the
            record through ``ActionRecord.to_dict()``.

    Returns:
        The written action record dictionary.

    Raises:
        ValueError: If the record fails validation. Nothing is written in that case.
    """
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
    with LedgerLock(ledger_path) as f:
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
    message = f"Ledger append failed ({label}, record {record_id}) for {ledger_path}: {type(exc).__name__}: {exc}"
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
    """Append an action record under LedgerLock, reporting rather than raising on failure.

    A failed append is never silent: it is logged at WARNING level with the label, record
    identifier, ledger path and exception detail, and mirrored to ``log_fn`` when supplied.
    No alternative ledger file is ever written.

    Args:
        ledger_path: Path to the executions.jsonl ledger file.
        record: ActionRecord or dictionary with action fields.
        label: Short description of the writer, used in the warning message.
        preserve_shape: Passed through to ``append_action_record``. Defaults to True so
            existing record shapes stay byte-compatible.
        log_fn: Optional callable (for example ``FlowContext.log``) that receives the warning.

    Returns:
        The written record dictionary, or None if the append failed.
    """
    try:
        return append_action_record(ledger_path, record, preserve_shape=preserve_shape)
    except Exception as exc:
        record_id = record.action_id if isinstance(record, ActionRecord) else str(record.get("action_id") or "?")
        _report_ledger_failure(ledger_path, label, record_id, exc, log_fn)
        return None


def try_append_ledger_line(
    ledger_path: str,
    record: dict[str, Any],
    *,
    label: str,
    log_fn: Callable[[str], None] | None = None,
) -> bool:
    """Append an arbitrary JSON ledger row verbatim under LedgerLock, reporting failures.

    Intended for summary, intent and plan rows whose shape must stay unchanged. Follows the
    same fail-loud contract as ``try_append_action_record``.

    Args:
        ledger_path: Path to the JSONL ledger file.
        record: Row to serialise with ``json.dumps``.
        label: Short description of the writer, used in the warning message.
        log_fn: Optional callable (for example ``FlowContext.log``) that receives the warning.

    Returns:
        True if the row was written, False otherwise.
    """
    try:
        line = json.dumps(record) + "\n"
        with atomic_ledger_append(ledger_path) as f:
            f.write(line)
        return True
    except Exception as exc:
        record_id = str(record.get("action_id") or record.get("execution_id") or record.get("plan_id") or "?")
        _report_ledger_failure(ledger_path, label, record_id, exc, log_fn)
        return False


def append_summary_record(ledger_path: str, record: SummaryRecord | dict[str, Any]) -> dict[str, Any]:
    """Append a summary record to the execution ledger under LedgerLock.

    Args:
        ledger_path: Path to the executions.jsonl ledger file.
        record: SummaryRecord or dictionary with summary fields.

    Returns:
        The written summary record dictionary.
    """
    if isinstance(record, SummaryRecord):
        data = record.to_dict()
    else:
        data = dict(record)
        data["entry_type"] = "summary"

    with LedgerLock(ledger_path) as f:
        f.write(json.dumps(data) + "\n")

    return data


# Canonical Aliases (Bean 0127)
append_action_ledger = append_action_record
append_execution_record = append_summary_record


def parse_commit_trailers(commit_message: str) -> dict[str, Any]:
    """Parse standardised action provenance trailers from commit message.

    Trailers parsed (case-insensitive):
    - Action-Origin: initial_implementation, pr_review, ci_fix, calibration, operator
    - Review-Severity: critical, important, nit
    - Review-Iteration: integer
    - Review-Finding-Ids: comma or space separated string

    Returns:
        Dictionary containing extracted metadata fields with default fallbacks.
    """
    action_origin = "initial_implementation"
    review_severity: str | None = None
    review_iteration: int | None = None
    review_finding_ids: list[str] | None = None
    review_disposition: str | None = None
    rejection_reason: str | None = None
    introduced_by_action_id: str | None = None
    consulted_knowledge_ids: list[str] | None = None

    if not commit_message:
        return {
            "action_origin": action_origin,
            "review_severity": review_severity,
            "review_iteration": review_iteration,
            "review_finding_ids": review_finding_ids,
            "review_disposition": review_disposition,
            "rejection_reason": rejection_reason,
            "introduced_by_action_id": introduced_by_action_id,
            "consulted_knowledge_ids": consulted_knowledge_ids,
        }

    pattern = re.compile(
        r"^(action-origin|review-severity|review-iteration|review-finding-ids|review-disposition|rejection-reason|introduced-by-action-id|consulted-knowledge-ids):\s*(.+)$",
        re.MULTILINE | re.IGNORECASE,
    )

    for match in pattern.finditer(commit_message):
        trailer_key = match.group(1).lower()
        val = match.group(2).strip()

        if trailer_key == "action-origin":
            clean_val = val.lower()
            action_origin = clean_val if clean_val in VALID_ORIGINS else "initial_implementation"
        elif trailer_key == "review-severity":
            clean_val = val.lower()
            if clean_val in VALID_SEVERITIES:
                review_severity = clean_val
        elif trailer_key == "review-iteration":
            try:
                parsed_iter = int(val)
                if parsed_iter >= 1:
                    review_iteration = parsed_iter
            except ValueError:
                review_iteration = None
        elif trailer_key == "review-finding-ids":
            parts = [p.strip() for p in re.split(r"[,;\s]+", val) if p.strip()]
            review_finding_ids = parts if parts else None
        elif trailer_key == "review-disposition":
            clean_val = val.lower()
            if clean_val in VALID_DISPOSITIONS:
                review_disposition = clean_val
        elif trailer_key == "rejection-reason":
            rejection_reason = val if val else None
        elif trailer_key == "introduced-by-action-id":
            introduced_by_action_id = val if val else None
        elif trailer_key == "consulted-knowledge-ids":
            parts = [p.strip() for p in re.split(r"[,;\s]+", val) if p.strip()]
            consulted_knowledge_ids = parts if parts else None

    return {
        "action_origin": action_origin,
        "review_severity": review_severity,
        "review_iteration": review_iteration,
        "review_finding_ids": review_finding_ids,
        "review_disposition": review_disposition,
        "rejection_reason": rejection_reason,
        "introduced_by_action_id": introduced_by_action_id,
        "consulted_knowledge_ids": consulted_knowledge_ids,
    }


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
    review_disposition: str | None = None,
    rejection_reason: str | None = None,
    introduced_by_action_id: str | None = None,
    consulted_knowledge_ids: list[str] | None = None,
) -> str:
    """Format structured commit trailers block for git commit messages."""
    lines: list[str] = [f"Action-Origin: {action_origin}"]
    if review_severity:
        lines.append(f"Review-Severity: {review_severity}")
    if review_iteration is not None:
        lines.append(f"Review-Iteration: {review_iteration}")
    if review_finding_ids:
        lines.append(f"Review-Finding-Ids: {', '.join(review_finding_ids)}")
    if review_disposition:
        lines.append(f"Review-Disposition: {review_disposition}")
    if rejection_reason:
        lines.append(f"Rejection-Reason: {rejection_reason}")
    if introduced_by_action_id:
        lines.append(f"Introduced-By-Action-Id: {introduced_by_action_id}")
    if consulted_knowledge_ids:
        lines.append(f"Consulted-Knowledge-IDs: {', '.join(consulted_knowledge_ids)}")
    return "\n".join(lines)


def query_ledger(
    ledger_path: str,
    *,
    entry_type: str | None = None,
    category: str | None = None,
    labels: list[str] | str | None = None,
    target: str | None = None,
    outcome: str | None = None,
    origin: str | None = None,
    review_severity: str | None = None,
    query: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Stream and filter execution ledger records.

    Args:
        ledger_path: Path to the execution ledger file (e.g. executions.jsonl).
        entry_type: Filter by entry type ('action', 'summary', or 'all'/None).
        category: Filter by category (e.g. 'code', 'test', 'doc').
        labels: Filter by labels (matches if any label exists in record).
        target: Substring match on target or plan_branch.
        outcome: Match on outcome or status.
        origin: Match on action_origin.
        review_severity: Match on review_severity.
        query: Case-insensitive search across summary/action/target/error fields.
        limit: Maximum number of matching records to return.

    Returns:
        List of matching record dictionaries.
    """
    if not os.path.exists(ledger_path):
        return []

    target_labels: list[str] = []
    if labels:
        if isinstance(labels, str):
            target_labels = [lbl.strip().lower() for lbl in labels.split(",") if lbl.strip()]
        elif isinstance(labels, list):
            target_labels = [str(lbl).strip().lower() for lbl in labels if str(lbl).strip()]

    results: list[dict[str, Any]] = []

    with open(ledger_path, encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue

            try:
                rec = json.loads(line_str)
            except (json.JSONDecodeError, ValueError):
                continue

            # 1. Filter by entry_type
            rec_entry_type = rec.get("entry_type", "summary")
            if entry_type and entry_type.lower() != "all" and rec_entry_type.lower() != entry_type.lower():
                continue

            # 2. Filter by category
            if category and rec.get("category", "").lower() != category.lower():
                continue

            # 3. Filter by labels
            if target_labels:
                rec_labels = rec.get("labels")
                if isinstance(rec_labels, str):
                    clean_rec_labels = [lbl.strip().lower() for lbl in rec_labels.split(",") if lbl.strip()]
                elif isinstance(rec_labels, list):
                    clean_rec_labels = [str(lbl).strip().lower() for lbl in rec_labels if str(lbl).strip()]
                else:
                    continue
                if not any(tl in clean_rec_labels for tl in target_labels):
                    continue

            # 4. Filter by target (substring match)
            if target:
                rec_target = str(rec.get("target") or rec.get("plan_branch") or "")
                if target.lower() not in rec_target.lower():
                    continue

            # 5. Filter by outcome (matches outcome or status, normalizing failure/failed)
            if outcome:
                rec_outcome = str(rec.get("outcome") or rec.get("status") or "").lower()
                req_outcome = outcome.lower()
                outcome_matches = False
                if req_outcome in ("failure", "failed"):
                    outcome_matches = rec_outcome in ("failure", "failed")
                else:
                    outcome_matches = rec_outcome == req_outcome
                if not outcome_matches:
                    continue

            # 6. Filter by origin
            if origin and rec.get("action_origin", "").lower() != origin.lower():
                continue

            # 7. Filter by review_severity
            if review_severity and rec.get("review_severity", "").lower() != review_severity.lower():
                continue

            # 8. Filter by query / keyword text
            if query:
                q = query.lower()
                searchable = [
                    str(rec.get("action") or ""),
                    str(rec.get("summary") or ""),
                    str(rec.get("target") or ""),
                    str(rec.get("error_context") or ""),
                    str(rec.get("mistake_category") or ""),
                    str(rec.get("execution_id") or ""),
                    str(rec.get("action_id") or ""),
                    str(rec.get("event_type") or ""),
                    str(rec.get("plan_branch") or ""),
                ]
                if not any(q in item.lower() for item in searchable):
                    continue

            results.append(rec)
            if limit is not None and len(results) >= limit:
                break

    return results


def format_query_results_table(records: list[dict[str, Any]]) -> str:
    """Format matching records into a clean human-readable table."""
    if not records:
        return "No matching ledger records found."

    headers = ["Timestamp", "Type", "Category", "Action/Summary", "Target", "Outcome", "Origin"]

    rows: list[list[str]] = []
    for r in records:
        ts = str(r.get("created_at") or "")[:19]
        etype = str(r.get("entry_type", "summary"))
        cat = str(r.get("category") or "-")
        act = str(r.get("action") or r.get("summary") or "-")
        tgt = str(r.get("target") or r.get("plan_branch") or "-")
        outcome = str(r.get("outcome") or r.get("status") or "-")
        origin = str(r.get("action_origin") or "-")

        # Truncate overly long fields to keep table compact
        if len(act) > 40:
            act = act[:37] + "..."
        if len(tgt) > 30:
            tgt = tgt[:27] + "..."

        rows.append([ts, etype, cat, act, tgt, outcome, origin])

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    def format_row(cells: list[str]) -> str:
        padded = [cell.ljust(col_widths[i]) for i, cell in enumerate(cells)]
        return "| " + " | ".join(padded) + " |"

    separator = "|-" + "-|-".join(["-" * w for w in col_widths]) + "-|"

    output_lines = [format_row(headers), separator]
    for row in rows:
        output_lines.append(format_row(row))

    return "\n".join(output_lines)
