from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Literal

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.knowledge import ConsultEngine


def _intent_labels(intent_data: dict[str, Any]) -> Any:
    labels = intent_data.get("labels", [])
    if isinstance(labels, str):
        return [label.strip() for label in labels.split(",") if label.strip()]
    return labels


def consult_knowledge(
    context: FlowContext,
    intent_data: dict[str, Any],
    *,
    match: Literal["all", "any"],
    purpose: str,
    log: logging.Logger,
) -> list[str]:
    consulted_ids: list[str] = []
    try:
        db_path = os.path.join(context.target_worktree, "holon-knowledge/.index/action_index.db")
        consult_engine = ConsultEngine(project_db_path=db_path)
        labels = {f"{match}_labels": _intent_labels(intent_data)}
        consulted_ids = consult_engine.consult_records(**labels, top_k=6, max_tokens=1200).consulted_ids
        if consulted_ids:
            context.log(f"Consulted {len(consulted_ids)} knowledge records for {purpose}: {consulted_ids}")
    except Exception as e:
        log.debug("Non-fatal consult failure during %s stage: %s", purpose, e)
    return consulted_ids


def stage_success(
    context: FlowContext, stage: FlowStage, key: str, start_time: str, payload: dict[str, Any]
) -> StageResult:
    result = StageResult(
        stage=stage,
        status=StageStatus.SUCCESS,
        start_time=start_time,
        end_time=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        payload=payload,
    )
    context.stage_results[key] = result
    return result


@contextlib.contextmanager
def intent_payload_file(intent_data: dict[str, Any]) -> Iterator[str]:
    tmp_dir = os.path.expanduser("~/.holon/tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    fd, intent_file = tempfile.mkstemp(prefix="holon-flow-intent-", suffix=".json", dir=tmp_dir)
    try:
        # why: mkstemp creates 0600, but the container's 'holon' user (uid 1000) reads the bind mount,
        # why: as it reads the 0644 file 'holon intent <file>' mounts; the payload is the intent JSON, not a secret.
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(intent_data, f)
        yield intent_file
    finally:
        with contextlib.suppress(OSError):
            os.remove(intent_file)
