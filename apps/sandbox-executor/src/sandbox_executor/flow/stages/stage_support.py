"""Steps Stage 1 and Stage 2 share: the knowledge consult, the success result, and the intent payload file.

Nothing here dispatches a role, probes conflicts or reads a ledger: those calls stay in the stage modules, where tests
patch them.
"""

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
    """The intent's ``labels``; a comma-separated string becomes a list of its non-empty, stripped labels."""
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
    """Consult the worktree's knowledge index for records matching the intent's labels; return their ids.

    ``match`` selects whether a record must carry all of the labels (Stage 1) or any of them (Stage 2). The consult is
    non-fatal: any failure is logged at DEBUG level on the stage's own logger ``log`` and yields the ids found so far.
    """
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
    """Record and return the SUCCESS result of ``stage`` under ``context.stage_results[key]``."""
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
    """Write ``intent_data`` as JSON to a temporary file under ``~/.holon/tmp``, yield its path, then remove it."""
    tmp_dir = os.path.expanduser("~/.holon/tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    fd, intent_file = tempfile.mkstemp(prefix="holon-flow-intent-", suffix=".json", dir=tmp_dir)
    try:
        # mkstemp creates 0600, but the container's 'holon' user (uid 1000) reads the bind mount, as it
        # reads the 0644 file 'holon intent <file>' mounts. The payload is the intent JSON, not a secret.
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(intent_data, f)
        yield intent_file
    finally:
        with contextlib.suppress(OSError):
            os.remove(intent_file)
