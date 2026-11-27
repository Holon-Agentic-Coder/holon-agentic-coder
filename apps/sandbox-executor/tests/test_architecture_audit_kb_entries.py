from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.harness_reference_patterns import find_references
from sandbox_executor.knowledge.wisdom import load_index

REPO = Path(__file__).resolve().parents[3]
KB_DIR = REPO / "holon-knowledge" / "kb"
AUDIT_CREATOR = "architecture-audit-2026-11"

PATTERN_IDS: tuple[str, ...] = (
    "KB-pattern-worktree-git-hardening",
    "KB-pattern-hermetic-pytest-conftest",
    "KB-pattern-three-ledger-fragment-routing",
)

FAILURE_MODE_IDS: tuple[str, ...] = (
    "KB-failure-bidirectional-flow-entrypoint-cycle",
    "KB-failure-monolithic-review-loop-complexity",
    "KB-failure-env-registry-runtime-drift",
    "KB-failure-container-resource-and-secret-perm-gaps",
)

ALL_ENTRIES: tuple[tuple[str, str, str], ...] = tuple(
    [("patterns", kb_id, "pattern") for kb_id in PATTERN_IDS]
    + [("failure_modes", kb_id, "failure_mode") for kb_id in FAILURE_MODE_IDS]
)


def _entry_path(subfolder: str, kb_id: str) -> Path:
    return KB_DIR / subfolder / f"{kb_id}.json"


def _load_entry(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _assert_iso_timestamp(value: Any) -> None:
    assert isinstance(value, str) and value
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None


def _assert_string_list(value: Any, *, allow_empty: bool = False) -> None:
    assert isinstance(value, list)
    assert allow_empty or len(value) > 0
    assert all(isinstance(item, str) and item for item in value)


def _assert_non_bool_int(value: Any) -> None:
    assert isinstance(value, int) and not isinstance(value, bool)


def _assert_common_envelope(entry: dict[str, Any], expected_id: str, expected_type: str) -> None:
    assert entry.get("kb_id") == expected_id
    assert entry.get("kb_type") == expected_type
    assert entry.get("version") == "1.0"
    _assert_iso_timestamp(entry.get("created_ts"))
    _assert_iso_timestamp(entry.get("updated_ts"))
    assert entry.get("created_by") == AUDIT_CREATOR
    assert entry.get("status") == "active"
    assert entry.get("human_approved") is True
    evidence = entry.get("evidence")
    assert isinstance(evidence, dict)
    _assert_string_list(evidence.get("ledger_refs"), allow_empty=True)
    _assert_non_bool_int(evidence.get("success_count"))
    _assert_non_bool_int(evidence.get("failure_count"))
    _assert_string_list(entry.get("tags"))
    assert isinstance(entry.get("payload"), dict)


def _assert_pattern_payload(payload: dict[str, Any]) -> None:
    assert isinstance(payload.get("name"), str) and payload["name"]
    assert isinstance(payload.get("description"), str) and payload["description"]
    assert isinstance(payload.get("applicability"), dict)
    structure = payload.get("structure")
    assert isinstance(structure, dict)
    _assert_string_list(structure.get("steps"))
    _assert_string_list(payload.get("known_pitfalls"))


def _assert_failure_mode_payload(payload: dict[str, Any]) -> None:
    assert isinstance(payload.get("name"), str) and payload["name"]
    assert isinstance(payload.get("description"), str) and payload["description"]
    _assert_string_list(payload.get("symptoms"))
    _assert_string_list(payload.get("root_causes"))
    mitigations = payload.get("mitigations")
    assert isinstance(mitigations, list) and len(mitigations) > 0
    for mitigation in mitigations:
        assert isinstance(mitigation, dict)
        assert isinstance(mitigation.get("strategy"), str) and mitigation["strategy"]
        assert isinstance(mitigation.get("description"), str) and mitigation["description"]
        assert isinstance(mitigation.get("cost"), str) and mitigation["cost"]
        assert isinstance(mitigation.get("effectiveness"), (int, float))
    heuristic = payload.get("detection_heuristic")
    assert isinstance(heuristic, dict)
    assert isinstance(heuristic.get("condition"), str) and heuristic["condition"]
    assert isinstance(heuristic.get("confidence"), (int, float))


def _assert_indexed_entries(subfolder: str, expected_ids: tuple[str, ...], expected_type: str) -> None:
    indexed = {item["kb_id"]: item for item in load_index(str(KB_DIR / subfolder))}
    for kb_id in expected_ids:
        assert kb_id in indexed
        entry = indexed[kb_id]
        assert entry["file"] == f"{kb_id}.json"
        assert entry["kb_type"] == expected_type
        assert entry["status"] == "active"
        assert isinstance(entry["created_ts"], str) and entry["created_ts"]
        _assert_string_list(entry["tags"])


def test_load_index_loads_all_architecture_audit_entries() -> None:
    _assert_indexed_entries("patterns", PATTERN_IDS, "pattern")
    _assert_indexed_entries("failure_modes", FAILURE_MODE_IDS, "failure_mode")


@pytest.mark.parametrize(("subfolder", "kb_id", "kb_type"), ALL_ENTRIES)
def test_architecture_audit_kb_entry_conforms_to_schema(subfolder: str, kb_id: str, kb_type: str) -> None:
    entry = _load_entry(_entry_path(subfolder, kb_id))
    _assert_common_envelope(entry, kb_id, kb_type)
    payload = entry["payload"]
    if kb_type == "pattern":
        _assert_pattern_payload(payload)
    else:
        _assert_failure_mode_payload(payload)


def test_architecture_audit_kb_entries_have_no_harness_references() -> None:
    paths = [_entry_path(subfolder, kb_id) for subfolder, kb_id, _ in ALL_ENTRIES] + [Path(__file__).resolve()]
    findings = {path.relative_to(REPO).as_posix(): find_references(path.read_text(encoding="utf-8")) for path in paths}
    assert findings == {path.relative_to(REPO).as_posix(): [] for path in paths}
