"""Ledger models and operations for recording actions, summaries, and commit trailers."""

import json
import logging
import os
import re
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

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
    """Standardized discrete action record for the execution ledger."""

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
    """Standardized summary record for plan executions."""

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


def append_action_record(ledger_path: str, record: ActionRecord | dict[str, Any]) -> dict[str, Any]:
    """Append a discrete action record to the execution ledger.

    Args:
        ledger_path: Path to the executions.jsonl ledger file.
        record: ActionRecord or dictionary with action fields.

    Returns:
        The written action record dictionary.
    """
    if isinstance(record, ActionRecord):
        data = record.to_dict()
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

    dirname = os.path.dirname(ledger_path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)

    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data) + "\n")
        f.flush()

    return data


def append_summary_record(ledger_path: str, record: SummaryRecord | dict[str, Any]) -> dict[str, Any]:
    """Append a summary record to the execution ledger.

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

    dirname = os.path.dirname(ledger_path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)

    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data) + "\n")
        f.flush()

    return data


def parse_commit_trailers(commit_message: str) -> dict[str, Any]:
    """Parse standardized action provenance trailers from commit message.

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

    if not commit_message:
        return {
            "action_origin": action_origin,
            "review_severity": review_severity,
            "review_iteration": review_iteration,
            "review_finding_ids": review_finding_ids,
        }

    pattern = re.compile(
        r"^(action-origin|review-severity|review-iteration|review-finding-ids):\s*(.+)$",
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
                review_iteration = int(val)
            except ValueError:
                review_iteration = None
        elif trailer_key == "review-finding-ids":
            parts = [p.strip() for p in re.split(r"[,;\s]+", val) if p.strip()]
            review_finding_ids = parts if parts else None

    return {
        "action_origin": action_origin,
        "review_severity": review_severity,
        "review_iteration": review_iteration,
        "review_finding_ids": review_finding_ids,
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
) -> str:
    """Format structured commit trailers block for git commit messages."""
    lines: list[str] = [f"Action-Origin: {action_origin}"]
    if review_severity:
        lines.append(f"Review-Severity: {review_severity}")
    if review_iteration is not None:
        lines.append(f"Review-Iteration: {review_iteration}")
    if review_finding_ids:
        lines.append(f"Review-Finding-Ids: {', '.join(review_finding_ids)}")
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
                if not isinstance(rec_labels, list):
                    continue
                clean_rec_labels = [str(lbl).strip().lower() for lbl in rec_labels]
                if not any(tl in clean_rec_labels for tl in target_labels):
                    continue

            # 4. Filter by target (substring match)
            if target:
                rec_target = str(rec.get("target") or rec.get("plan_branch") or "")
                if target.lower() not in rec_target.lower():
                    continue

            # 5. Filter by outcome (matches outcome or status)
            if outcome:
                rec_outcome = str(rec.get("outcome") or rec.get("status") or "")
                if rec_outcome.lower() != outcome.lower():
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
