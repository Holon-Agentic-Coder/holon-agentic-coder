from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sandbox_executor.entrypoint.review_failure_detail import failed_test_ids
from sandbox_executor.ledger.rows import try_append_action_record
from sandbox_executor.redaction import redact_agent_secrets

STAGE_3 = "stage_3"
STAGE_4 = "stage_4"
STAGE_5 = "stage_5"
MAX_RECORDED_IDS = 50
EXCERPT_CHARS_PER_TEST = 600
SIGNATURE_HEX_CHARS = 16
BLOCKED_CLASSIFICATIONS = frozenset({"environment", "pre-existing"})
IDENTITY_PREFIXES = ("GIT_AUTHOR_", "GIT_COMMITTER_")
SUMMARISED_PREFIXES = ("GIT_", "PYTEST_", "UV_", "PYTHON", "HOLON_")
SAFE_VALUE_NAMES = frozenset({"PYTEST_ADDOPTS", "UV_FROZEN", "PYTHONHASHSEED"})
LEDGER_RELPATH = ("holon-knowledge", "ledger", "executions.jsonl")

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_SECTION_HEADER = re.compile(r"^_{3,}\s+(?P<name>.+?)\s+_{3,}$")
_SECTION_END = re.compile(r"^(?:={3,}|_{3,}\s)")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]+")
_TEMP_PATH = re.compile(r"(?:/private)?/(?:tmp|var/folders)/\S*")
_DIGITS = re.compile(r"\d+")

AppendFn = Callable[..., "dict[str, Any] | None"]


@dataclass(frozen=True)
class TestFailureEvidence:
    __test__ = False

    run_id: str
    stage: str
    head_sha: str
    failing_ids: tuple[str, ...]
    excerpts: Mapping[str, str] = field(default_factory=dict)
    environment: Mapping[str, Any] = field(default_factory=dict)
    classification: str | None = None
    iteration: int = 0
    model_tier: str | None = None
    base_check: str = "not_run"
    outcome: str = "failure"


def failing_ids_from_output(output: str) -> tuple[str, ...]:
    return tuple(sorted(set(failed_test_ids(output))))[:MAX_RECORDED_IDS]


def _summary_line(lines: list[str], test_id: str) -> str:
    return next((line for line in lines if test_id in line and ("FAILED" in line or "ERROR" in line)), "")


def _traceback_section(lines: list[str], test_id: str) -> list[str]:
    test_name = test_id.rsplit("::", 1)[-1]
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if (match := _SECTION_HEADER.match(line.strip())) and match.group("name").endswith(test_name)
        ),
        None,
    )
    if start is None:
        return []
    end = next((index for index in range(start + 1, len(lines)) if _SECTION_END.match(lines[index])), len(lines))
    return lines[start:end]


def excerpts_by_test(output: str, ids: tuple[str, ...]) -> dict[str, str]:
    lines = _ANSI.sub("", output).splitlines()
    excerpts: dict[str, str] = {}
    for test_id in ids:
        parts = [_summary_line(lines, test_id), *_traceback_section(lines, test_id)]
        text = "\n".join(part for part in parts if part).strip()
        excerpts[test_id] = redact_agent_secrets(text)[:EXCERPT_CHARS_PER_TEST]
    return excerpts


def environment_summary(env: Mapping[str, str]) -> dict[str, Any]:
    names = sorted(name for name in env if name.startswith(SUMMARISED_PREFIXES))
    return {
        "variables": names,
        "values": {name: redact_agent_secrets(env[name]) for name in names if name in SAFE_VALUE_NAMES},
        "identity_overrides": [name for name in names if name.startswith(IDENTITY_PREFIXES)],
        "HOME_set": bool(env.get("HOME")),
        "TMPDIR_set": bool(env.get("TMPDIR")),
    }


def _first_error_line(excerpt: str) -> str:
    lines = excerpt.splitlines()
    return next((line for line in lines if line.lstrip().startswith("E ")), lines[0] if lines else "")


def _normalised(line: str) -> str:
    # why: line numbers, object addresses and temp paths change between runs of the same defect.
    return _DIGITS.sub("N", _TEMP_PATH.sub("<tmp>", _ADDRESS.sub("0x", line))).strip()


def failure_signature(ids: tuple[str, ...], excerpts: Mapping[str, str]) -> str:
    parts = [f"{test_id}|{_normalised(_first_error_line(excerpts.get(test_id, '')))}" for test_id in sorted(ids)]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:SIGNATURE_HEX_CHARS]


def _row(evidence: TestFailureEvidence, branch: str, execution_id: str) -> dict[str, Any]:
    first_excerpt = next(iter(evidence.excerpts.values()), "") if evidence.excerpts else ""
    return {
        "execution_id": execution_id,
        "action": "test_failure",
        "category": "test",
        "target": evidence.stage,
        "outcome": "blocked" if evidence.classification in BLOCKED_CLASSIFICATIONS else evidence.outcome,
        "action_origin": "ci_fix" if evidence.stage == STAGE_4 else "initial_implementation",
        "event_type": "test_run",
        "stage": evidence.stage,
        "branch": branch,
        "run_id": evidence.run_id,
        "head_sha": evidence.head_sha,
        "failing_test_ids": list(evidence.failing_ids),
        "test_excerpts": dict(evidence.excerpts),
        "environment": dict(evidence.environment),
        "classification": evidence.classification,
        "failure_signature": failure_signature(evidence.failing_ids, evidence.excerpts),
        "iteration": evidence.iteration,
        "model_tier": evidence.model_tier,
        "base_check": evidence.base_check,
        "error_context": first_excerpt or None,
    }


def record_test_failure(
    ledger_path: str,
    evidence: TestFailureEvidence,
    *,
    branch: str,
    execution_id: str,
    append: AppendFn = try_append_action_record,
) -> dict[str, Any] | None:
    try:
        row = _row(evidence, branch, execution_id or "E-unknown")
        return append(ledger_path, row, label="test failure record", preserve_shape=True)
    except Exception:
        return None
