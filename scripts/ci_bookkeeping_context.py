from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class GateContext:
    event_name: str
    action: str
    label: str
    before: str
    head_sha: str
    branch: str
    run_number: int
    run_attempt: int
    workflow_file: str
    job_name: str
    os_name: str
    sha: str = ""
    ref: str = ""
    base_ref: str = ""


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _number(value: object) -> int:
    text = _text(value)
    return int(text) if text.isascii() and text.isdigit() else 0


def _child(mapping: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = mapping.get(key)
    return value if isinstance(value, dict) else {}


def load_event(path: str) -> dict[str, object]:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def context_from(environ: Mapping[str, str], payload: Mapping[str, object], job_name: str, os_name: str) -> GateContext:
    workflow_file = _text(environ.get("GITHUB_WORKFLOW_REF")).partition("@")[0].rpartition("/")[2]
    pull_request = _child(payload, "pull_request")
    head = _child(pull_request, "head")
    return GateContext(
        event_name=_text(environ.get("GITHUB_EVENT_NAME")),
        action=_text(payload.get("action")),
        label=_text(_child(payload, "label").get("name")),
        before=_text(payload.get("before")),
        head_sha=_text(head.get("sha")),
        branch=_text(head.get("ref")),
        run_number=_number(environ.get("GITHUB_RUN_NUMBER")),
        run_attempt=_number(environ.get("GITHUB_RUN_ATTEMPT")),
        workflow_file=workflow_file if workflow_file.endswith((".yml", ".yaml")) else "",
        job_name=job_name,
        os_name=os_name,
        sha=_text(environ.get("GITHUB_SHA")),
        ref=_text(environ.get("GITHUB_REF")),
        base_ref=_text(_child(pull_request, "base").get("ref")),
    )
