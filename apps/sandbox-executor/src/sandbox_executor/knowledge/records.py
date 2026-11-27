from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sandbox_executor.credential_redactor import CredentialRedactor
from sandbox_executor.knowledge.index import DEFAULT_PROJECT_DB_PATH
from sandbox_executor.knowledge.ledger_fragment_sync import sync_ledger_and_fragments
from sandbox_executor.ledger import atomic_ledger_append, generate_action_id, read_merged_text
from sandbox_executor.ledger.order import utc_now_z

logger = logging.getLogger(__name__)

DEFAULT_ACTIONS_LEDGER = "holon-knowledge/ledger/executions.jsonl"


class Verdict(StrEnum):
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
    v_old = Verdict.from_str(old_verdict)
    v_new = Verdict.from_str(new_verdict)
    if v_new not in VALID_VERDICT_TRANSITIONS.get(v_old, set()):
        raise ValueError(f"Illegal verdict transition from '{v_old.value}' to '{v_new.value}'")


def append_action_event(
    record: dict[str, Any],
    ledger_path: str = DEFAULT_ACTIONS_LEDGER,
    db_path: str | None = None,
    project_db_path: str | None = None,
) -> dict[str, Any]:
    rec = dict(record)

    if "action_id" not in rec or not rec["action_id"]:
        rec["action_id"] = generate_action_id()

    raw_verdict = rec.get("verdict") or rec.get("outcome") or Verdict.ATTEMPTED.value
    verdict_enum = Verdict.from_str(raw_verdict)
    rec["verdict"] = verdict_enum.value
    rec["outcome"] = verdict_enum.value

    if "created_at" not in rec or not rec["created_at"]:
        rec["created_at"] = utc_now_z()

    redacted_rec = CredentialRedactor.redact_structure(rec)

    with atomic_ledger_append(ledger_path) as f:
        f.write(json.dumps(redacted_rec) + "\n")

    target_db = project_db_path or db_path or DEFAULT_PROJECT_DB_PATH
    sync_ledger_and_fragments(target_db, ledger_path)

    return redacted_rec


def reconcile_records(  # noqa: C901, PLR0912, PLR0913
    ledger_path: str = DEFAULT_ACTIONS_LEDGER,
    max_age_days: int = 14,
    target_action_id: str | None = None,
    verdict: str | None = None,
    reason: str | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    ledger_text = read_merged_text(ledger_path)
    if ledger_text is None:
        return {"reconciled": 0, "expired": 0, "total_scanned": 0}

    scanned_records: list[dict[str, Any]] = []
    for line in ledger_text.splitlines():
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

        reconciliation_event = {
            "action_id": generate_action_id(),
            "parent_action_id": target_action_id,
            "event_type": "reconciliation",
            "verdict": target_verdict.value,
            "outcome": target_verdict.value,
            "reason": reason or f"Reconciled verdict to {target_verdict.value}",
            "created_at": now.isoformat(),
        }
        append_action_event(reconciliation_event, ledger_path=ledger_path, db_path=db_path)
        reconciled_count += 1

    for r in scanned_records:
        record_verdict = str(r.get("verdict") or "").lower()
        if record_verdict != Verdict.UNKNOWN.value:
            continue

        record_action_id = r.get("action_id")
        created_at_str = r.get("created_at")
        if not record_action_id or not created_at_str:
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
                "parent_action_id": record_action_id,
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
