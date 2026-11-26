from __future__ import annotations

import json
import os
from typing import Any

STABLE_CREATED_TS = "1970-01-01T00:00:00+00:00"
KB_STABLE_EVIDENCE_KEYS: tuple[str, ...] = ()
WB_STABLE_EVIDENCE_KEYS: tuple[str, ...] = ("project_count", "provenance")

_COUNTS_NOTE = "Execution counts live in the action index and ledgers."


def stable_created_ts(data: dict[str, Any]) -> str:
    return str(data.get("created_ts") or STABLE_CREATED_TS)


def _stable_subset(evidence: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        return {}
    return {key: evidence[key] for key in keys if key in evidence}


def build_kb_envelope(data: dict[str, Any], kb_id: str, kb_type: str) -> dict[str, Any]:
    created_ts = stable_created_ts(data)
    return {
        "kb_id": kb_id,
        "kb_type": kb_type,
        "version": str(data.get("version") or "1.0"),
        "created_ts": created_ts,
        "updated_ts": created_ts,
        "created_by": str(data.get("created_by") or "holon-promotion-engine"),
        "status": str(data.get("status") or "active"),
        "evidence": _stable_subset(data.get("evidence", {}), KB_STABLE_EVIDENCE_KEYS),
        "tags": data.get("tags", []),
        "human_approved": bool(data.get("human_approved", False)),
        "payload": data.get("payload", {}),
    }


def build_wb_envelope(data: dict[str, Any], wb_id: str, wb_type: str) -> dict[str, Any]:
    return {
        "wb_id": wb_id,
        "wb_type": wb_type,
        "version": str(data.get("version") or "1.0"),
        "created_ts": stable_created_ts(data),
        "scope": "global",
        "status": str(data.get("status") or "active"),
        "ascension_evidence": _stable_subset(data.get("ascension_evidence", {}), WB_STABLE_EVIDENCE_KEYS),
        "human_approved": bool(data.get("human_approved", False)),
        "payload": data.get("payload", {}),
    }


def write_envelope_once(out_file: str, envelope: dict[str, Any]) -> str:
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    try:
        with open(out_file, "x", encoding="utf-8") as f:
            json.dump(envelope, f, indent=2)
    except FileExistsError:
        return out_file
    return out_file


def stable_advisory_text(kind: str, action: str) -> str:
    if kind == "failure":
        return f"Reproduced failure across executions with identical context. {_COUNTS_NOTE}"
    return (
        "Advisory: historical executions matching this context pattern succeeded consistently. "
        f"Consider evaluating proven approach: '{action}'. {_COUNTS_NOTE}"
    )
