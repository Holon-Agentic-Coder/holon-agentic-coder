from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

ADMISSION_BLOCKED = "blocked"
ADMISSION_ADMITTED = "admitted"
BLOCKED_BY_PULL_REQUEST = "pull_request"
BLOCKED_BY_LOCAL_FLOW = "local_flow"
_STATES = frozenset({ADMISSION_BLOCKED, ADMISSION_ADMITTED})


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


@dataclass(frozen=True)
class BlockedBy:
    kind: str
    files: tuple[str, ...]
    number: int | None = None
    branch: str | None = None
    plan_branch: str | None = None
    worktree: str | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.kind == BLOCKED_BY_PULL_REQUEST:
            return {"kind": self.kind, "number": self.number, "branch": self.branch, "files": list(self.files)}
        reference = {"plan_branch": self.plan_branch, "worktree": self.worktree}
        return {"kind": self.kind, **reference, "files": list(self.files)}

    @classmethod
    def from_dict(cls, data: Any) -> BlockedBy | None:
        if not isinstance(data, dict) or data.get("kind") not in (BLOCKED_BY_PULL_REQUEST, BLOCKED_BY_LOCAL_FLOW):
            return None
        files = data.get("files")
        return cls(
            kind=str(data["kind"]),
            files=tuple(str(path) for path in files) if isinstance(files, list) else (),
            number=_int_or_none(data.get("number")),
            branch=_str_or_none(data.get("branch")),
            plan_branch=_str_or_none(data.get("plan_branch")),
            worktree=_str_or_none(data.get("worktree")),
        )


@dataclass(frozen=True)
class AdmissionRecord:
    state: str
    blocked_since: str | None = None
    blocked_by: tuple[BlockedBy, ...] = ()
    override: bool = False
    override_conflicts: tuple[dict[str, Any], ...] = ()
    waited_since: str | None = None
    refreshed_to: str | None = None
    refreshed_branch: str | None = None

    @property
    def blocked(self) -> bool:
        return self.state == ADMISSION_BLOCKED

    def refresh_due(self, plan_branch: str) -> bool:
        waited = self.state == ADMISSION_ADMITTED and bool(self.waited_since)
        # why: a re-plan cuts a new plan branch from the stale intent tip, so it needs its own refresh
        return waited and not (self.refreshed_to and self.refreshed_branch == plan_branch)

    def refreshed_on(self, plan_branch: str) -> str | None:
        return self.refreshed_to if self.refreshed_branch == plan_branch else None

    def with_refresh(self, refreshed_to: str, plan_branch: str) -> AdmissionRecord:
        return replace(self, refreshed_to=refreshed_to, refreshed_branch=plan_branch)

    def to_dict(self) -> dict[str, Any]:
        if self.blocked:
            entries = [entry.to_dict() for entry in self.blocked_by]
            return {"state": self.state, "blocked_since": self.blocked_since, "blocked_by": entries}
        data: dict[str, Any] = {"state": self.state}
        if self.override:
            data.update(override=True, override_conflicts=list(self.override_conflicts))
        data.update({key: value for key, value in self._refresh_fields().items() if value})
        return data

    def _refresh_fields(self) -> dict[str, str | None]:
        return {
            "waited_since": self.waited_since,
            "refreshed_to": self.refreshed_to,
            "refreshed_branch": self.refreshed_branch,
        }

    @classmethod
    def from_dict(cls, data: Any) -> AdmissionRecord | None:
        if not isinstance(data, dict) or data.get("state") not in _STATES:
            return None
        raw_entries = data.get("blocked_by")
        entries = (BlockedBy.from_dict(item) for item in raw_entries) if isinstance(raw_entries, list) else ()
        conflicts = data.get("override_conflicts")
        return cls(
            state=str(data["state"]),
            blocked_since=str(data["blocked_since"]) if data.get("blocked_since") else None,
            blocked_by=tuple(entry for entry in entries if entry is not None),
            override=bool(data.get("override", False)),
            override_conflicts=tuple(item for item in conflicts if isinstance(item, dict))
            if isinstance(conflicts, list)
            else (),
            waited_since=_str_or_none(data.get("waited_since")),
            refreshed_to=_str_or_none(data.get("refreshed_to")),
            refreshed_branch=_str_or_none(data.get("refreshed_branch")),
        )


def blocked_record(previous: AdmissionRecord | None, blocked_by: tuple[BlockedBy, ...], now: str) -> AdmissionRecord:
    since = previous.blocked_since if previous is not None and previous.blocked and previous.blocked_since else now
    return AdmissionRecord(ADMISSION_BLOCKED, blocked_since=since, blocked_by=blocked_by)


def admitted_record(
    override_conflicts: list[dict[str, Any]] | None = None, previous: AdmissionRecord | None = None
) -> AdmissionRecord:
    waited = previous.blocked_since if previous is not None and previous.blocked else None
    admitted = previous if previous is not None and not previous.blocked else None
    carried = {
        "waited_since": waited or (admitted.waited_since if admitted else None),
        "refreshed_to": admitted.refreshed_to if admitted else None,
        "refreshed_branch": admitted.refreshed_branch if admitted else None,
    }
    if override_conflicts is None:
        return AdmissionRecord(ADMISSION_ADMITTED, **carried)
    return AdmissionRecord(ADMISSION_ADMITTED, override=True, override_conflicts=tuple(override_conflicts), **carried)
