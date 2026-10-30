"""Action outcome logging state machine, credential redactor, and late verdict reconciliation (Bean 0052 Goal 3)."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, ClassVar

from sandbox_executor.knowledge.index import DEFAULT_PROJECT_DB_PATH, sync_ledger_incremental
from sandbox_executor.ledger import atomic_ledger_append, generate_action_id

logger = logging.getLogger(__name__)

DEFAULT_ACTIONS_LEDGER = "holon-knowledge/ledger/executions.jsonl"


class Verdict(StrEnum):
    """Discrete verdicts for action outcomes in Holon."""

    ATTEMPTED = "attempted"
    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"
    INCONCLUSIVE = "inconclusive"
    ABANDONED = "abandoned"
    SUPERSEDED = "superseded"
    UNRESOLVED = "unresolved"

    @classmethod
    def from_str(cls, val: str | Verdict) -> Verdict:
        """Parse string to Verdict enum case-insensitively."""
        if isinstance(val, Verdict):
            return val
        clean = str(val).strip().lower()
        if clean in ("failed", "fail"):
            return cls.FAILURE
        for v in cls:
            if v.value == clean:
                return v
        raise ValueError(f"Unknown Verdict '{val}'. Valid verdicts: {[v.value for v in cls]}")


VALID_VERDICT_TRANSITIONS: dict[Verdict, set[Verdict]] = {
    Verdict.ATTEMPTED: {
        Verdict.SUCCESS,
        Verdict.FAILURE,
        Verdict.UNKNOWN,
        Verdict.INCONCLUSIVE,
        Verdict.ABANDONED,
    },
    Verdict.UNKNOWN: {
        Verdict.SUCCESS,
        Verdict.FAILURE,
        Verdict.SUPERSEDED,
        Verdict.UNRESOLVED,
    },
    Verdict.INCONCLUSIVE: {
        Verdict.SUCCESS,
        Verdict.FAILURE,
        Verdict.ABANDONED,
        Verdict.SUPERSEDED,
    },
    Verdict.ABANDONED: {Verdict.SUPERSEDED},
    Verdict.SUCCESS: {Verdict.SUPERSEDED},
    Verdict.FAILURE: {Verdict.SUPERSEDED},
    Verdict.UNRESOLVED: {Verdict.SUCCESS, Verdict.FAILURE, Verdict.SUPERSEDED},
    Verdict.SUPERSEDED: set(),
}


def validate_verdict_transition(old_verdict: Verdict | str, new_verdict: Verdict | str) -> None:
    """Validate that transitioning from old_verdict to new_verdict is legal."""
    v_old = Verdict.from_str(old_verdict)
    v_new = Verdict.from_str(new_verdict)
    if v_new not in VALID_VERDICT_TRANSITIONS.get(v_old, set()):
        raise ValueError(f"Illegal verdict transition from '{v_old.value}' to '{v_new.value}'")


class CredentialRedactor:
    """Multi-pattern secret and API key redactor to prevent credential leaks into the ledger."""

    _PATTERNS: ClassVar[list[tuple[re.Pattern, str]]] = [
        # OpenAI API Keys
        (re.compile(r"\b(sk-[a-zA-Z0-9_\-]{20,})\b"), "[REDACTED_SECRET]"),
        # Anthropic API Keys
        (re.compile(r"\b(sk-ant-[a-zA-Z0-9_\-]{20,})\b"), "[REDACTED_SECRET]"),
        # Google AI / Gemini API Keys
        (re.compile(r"\b(AIza[0-9A-Za-z\-_]{30,40})\b"), "[REDACTED_SECRET]"),
        # GitHub Personal Access Tokens (Classic and Fine-Grained)
        (re.compile(r"\b(ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{82})\b"), "[REDACTED_SECRET]"),
        # Generic Bearer Tokens
        (re.compile(r"(?i)\b(Bearer\s+)[a-zA-Z0-9_\-\.]{15,}"), r"\1[REDACTED_SECRET]"),
        # Private Keys
        (
            re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----"),
            "[REDACTED_PRIVATE_KEY]",
        ),
        # Sensitive URL Parameters
        (
            re.compile(r"([?&](?:token|api_key|access_token|secret|password|auth|bearer)[^=]*=)[^\s&]+", re.IGNORECASE),
            r"\1[REDACTED_SECRET]",
        ),
        # Generic token or secret assignment
        (
            re.compile(
                r'(["\']?(?:api_key|token|access_token|secret|password)["\']?\s*[:=]\s*["\'])([^"\'\s]{8,})(["\'])',
                re.IGNORECASE,
            ),
            r"\1[REDACTED_SECRET]\3",
        ),
    ]

    @classmethod
    def redact_text(cls, text: str) -> str:
        """Apply all credential redaction regexes to a string."""
        if not text:
            return text
        result = text
        for pattern, replacement in cls._PATTERNS:
            result = pattern.sub(replacement, result)
        return result

    @classmethod
    def redact_structure(cls, data: Any) -> Any:
        """Recursively redact dictionary or list structures."""
        if isinstance(data, str):
            return cls.redact_text(data)
        if isinstance(data, dict):
            return {cls.redact_text(str(k)): cls.redact_structure(v) for k, v in data.items()}
        if isinstance(data, list):
            return [cls.redact_structure(item) for item in data]
        return data


def append_action_event(
    record: dict[str, Any],
    ledger_path: str = DEFAULT_ACTIONS_LEDGER,
    db_path: str | None = None,
    project_db_path: str | None = None,
) -> dict[str, Any]:
    """Scrub credentials and append discrete action event to actions.jsonl with atomic locking."""
    rec = dict(record)

    if "action_id" not in rec or not rec["action_id"]:
        rec["action_id"] = generate_action_id()

    raw_verdict = rec.get("verdict") or rec.get("outcome") or Verdict.ATTEMPTED.value
    verdict_enum = Verdict.from_str(raw_verdict)
    rec["verdict"] = verdict_enum.value
    rec["outcome"] = verdict_enum.value

    if "created_at" not in rec or not rec["created_at"]:
        rec["created_at"] = datetime.now(UTC).isoformat()

    # Redact credentials before serialising to disk
    redacted_rec = CredentialRedactor.redact_structure(rec)

    with atomic_ledger_append(ledger_path) as f:
        f.write(json.dumps(redacted_rec) + "\n")

    # Incrementally update index
    target_db = project_db_path or db_path or DEFAULT_PROJECT_DB_PATH
    try:
        sync_ledger_incremental(target_db, ledger_path)
    except Exception as e:
        logger.debug("Failed incremental sync during append_action_event: %s", e)

    return redacted_rec


def reconcile_records(  # noqa: C901, PLR0912, PLR0913
    ledger_path: str = DEFAULT_ACTIONS_LEDGER,
    max_age_days: int = 14,
    target_action_id: str | None = None,
    verdict: str | None = None,
    reason: str | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Reconcile pending actions by attaching terminal verdicts or expiring stale unknown records."""
    if not os.path.exists(ledger_path):
        return {"reconciled": 0, "expired": 0, "total_scanned": 0}

    scanned_records: list[dict[str, Any]] = []
    with open(ledger_path, encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                scanned_records.append(json.loads(line_str))
            except Exception:
                continue

    reconciled_count = 0
    expired_count = 0
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=max_age_days)

    # 1. Reconcile explicit target action ID
    if target_action_id and verdict:
        target_verdict = Verdict.from_str(verdict)
        matched_target = None
        for r in scanned_records:
            if r.get("action_id") == target_action_id:
                matched_target = r
                break

        if matched_target:
            curr_verdict = Verdict.from_str(matched_target.get("verdict", Verdict.UNKNOWN.value))
            validate_verdict_transition(curr_verdict, target_verdict)

        event = {
            "action_id": generate_action_id(),
            "parent_action_id": target_action_id,
            "event_type": "reconciliation",
            "verdict": target_verdict.value,
            "outcome": target_verdict.value,
            "reason": reason or f"Reconciled verdict to {target_verdict.value}",
            "created_at": now.isoformat(),
        }
        append_action_event(event, ledger_path=ledger_path, db_path=db_path)
        reconciled_count += 1

    # 2. Expire actions with 'unknown' status older than max_age_days
    for r in scanned_records:
        r_verdict = str(r.get("verdict") or "").lower()
        if r_verdict != Verdict.UNKNOWN.value:
            continue

        r_id = r.get("action_id")
        created_at_str = r.get("created_at")
        if not r_id or not created_at_str:
            continue

        try:
            created_dt = datetime.fromisoformat(created_at_str)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=UTC)
        except Exception:
            continue

        if created_dt < cutoff:
            expire_event = {
                "action_id": generate_action_id(),
                "parent_action_id": r_id,
                "event_type": "ttl_expiration",
                "verdict": Verdict.UNRESOLVED.value,
                "outcome": Verdict.UNRESOLVED.value,
                "reason": f"TTL expired ({max_age_days} days)",
                "created_at": now.isoformat(),
            }
            append_action_event(expire_event, ledger_path=ledger_path, db_path=db_path)
            expired_count += 1

    return {
        "reconciled": reconciled_count,
        "expired": expired_count,
        "total_scanned": len(scanned_records),
    }
