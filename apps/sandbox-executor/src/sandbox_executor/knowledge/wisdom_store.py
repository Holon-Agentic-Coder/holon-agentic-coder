from __future__ import annotations

import glob
import json
import logging
import os
from typing import Any

from sandbox_executor.knowledge.stable_envelope import (
    build_kb_envelope,
    build_wb_envelope,
    write_envelope_once,
)

logger = logging.getLogger(__name__)

DEFAULT_KB_DIR = "holon-knowledge/kb"
DEFAULT_WB_DIR = "holon-knowledge/wisdom"

_INDEX_FIELDS: dict[str, tuple[str, ...]] = {
    "kb_id": ("kb_id", "kb_type", "status", "created_ts", "tags"),
    "wb_id": ("wb_id", "wb_type", "status", "created_ts"),
}


def _index_entry(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as f:
            envelope = json.load(f)
    except (OSError, ValueError) as exc:
        logger.warning("Skipping unreadable knowledge entry %s: %s", path, exc)
        return None
    id_key = next((k for k in _INDEX_FIELDS if isinstance(envelope, dict) and k in envelope), None)
    if id_key is None:
        logger.warning("Skipping knowledge entry without kb_id or wb_id: %s", path)
        return None
    entry: dict[str, Any] = {field: envelope.get(field) for field in _INDEX_FIELDS[id_key]}
    entry["file"] = os.path.basename(path)
    if id_key == "kb_id" and entry["tags"] is None:
        entry["tags"] = []
    return entry


def load_index(subfolder: str) -> list[dict[str, Any]]:
    if not os.path.isdir(subfolder):
        return []
    paths = sorted(glob.glob(os.path.join(subfolder, "KB-*.json")) + glob.glob(os.path.join(subfolder, "WB-*.json")))
    entries = [entry for entry in map(_index_entry, paths) if entry is not None]
    return sorted(entries, key=lambda e: (str(e.get("created_ts") or ""), str(e.get("kb_id") or e.get("wb_id"))))


def persist_kb_fact(fact_data: dict[str, Any], kb_dir: str = DEFAULT_KB_DIR) -> str:
    data = dict(fact_data)
    kb_type = str(data.get("kb_type") or "pattern").lower()
    kb_id = str(data.get("kb_id") or f"KB-{kb_type}-{data.get('context_fingerprint', 'item')[:8]}")

    subfolder = "patterns"
    if "failure" in kb_type:
        subfolder = "failure_modes"
    elif "tactic" in kb_type:
        subfolder = "tactics"

    target_dir = os.path.join(kb_dir, subfolder)
    out_file = os.path.join(target_dir, f"{kb_id}.json")
    if os.path.exists(out_file):
        return out_file
    return write_envelope_once(out_file, build_kb_envelope(data, kb_id, kb_type))


def persist_wb_rule(rule_data: dict[str, Any], wb_dir: str = DEFAULT_WB_DIR) -> str:
    data = dict(rule_data)
    wb_type = str(data.get("wb_type") or "heuristic").lower()
    wb_id = str(data.get("wb_id") or f"WB-{wb_type}-{data.get('context_fingerprint', 'rule')[:8]}")

    subfolder = "heuristics"
    if "invariant" in wb_type:
        subfolder = "invariants"

    target_dir = os.path.join(wb_dir, subfolder)
    out_file = os.path.join(target_dir, f"{wb_id}.json")
    if os.path.exists(out_file):
        return out_file
    return write_envelope_once(out_file, build_wb_envelope(data, wb_id, wb_type))
