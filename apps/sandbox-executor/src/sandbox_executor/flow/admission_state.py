"""Stage 3 admission state recorded in a flow checkpoint: ``blocked`` (and by what) or ``admitted``.

A refusal for a touch-set overlap records ``{state: "blocked", blocked_since, blocked_by}``; ``blocked_since`` is kept
from the first refusal across retries and ``blocked_by`` is replaced on each one. Each ``blocked_by`` entry names an
open pull request (``{kind: "pull_request", number, branch, files}``) or a local flow
(``{kind: "local_flow", plan_branch, worktree, files}``). An admission records ``{state: "admitted"}``, with
``override: true`` and the overridden conflicts under ``--allow-overlap``. An admission after a refusal also carries
``waited_since`` (the refusal's ``blocked_since``, kept across resumes) and, once Stage 3 merged the target branch into
the plan branch (:mod:`flow.plan_refresh`), ``refreshed_to``: a refresh is due while only the
first is set. The checkpoint's top-level ``admission`` object is the operator interface: a waiting runner reads
``blocked_by`` to know which pull requests or flows to watch.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

ADMISSION_BLOCKED = "blocked"
ADMISSION_ADMITTED = "admitted"
#: ``kind`` of a ``blocked_by`` entry naming an open flow pull request.
BLOCKED_BY_PULL_REQUEST = "pull_request"
#: ``kind`` of a ``blocked_by`` entry naming a flow checkpoint in another worktree.
BLOCKED_BY_LOCAL_FLOW = "local_flow"
_STATES = frozenset({ADMISSION_BLOCKED, ADMISSION_ADMITTED})


def utc_now() -> str:
    """The current UTC time as the sortable ISO string the flow records (millisecond precision)."""
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
    """One pull request or local flow a blocked flow waits on, with the overlapping files."""

    kind: str
    files: tuple[str, ...]
    number: int | None = None
    branch: str | None = None
    plan_branch: str | None = None
    worktree: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Checkpoint shape: only the reference fields of this entry's kind."""
        if self.kind == BLOCKED_BY_PULL_REQUEST:
            return {"kind": self.kind, "number": self.number, "branch": self.branch, "files": list(self.files)}
        reference = {"plan_branch": self.plan_branch, "worktree": self.worktree}
        return {"kind": self.kind, **reference, "files": list(self.files)}

    @classmethod
    def from_dict(cls, data: Any) -> BlockedBy | None:
        """Parse one entry; ``None`` for a value that is not an entry of a known kind."""
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
    """The admission object of a checkpoint."""

    state: str
    blocked_since: str | None = None
    blocked_by: tuple[BlockedBy, ...] = ()
    override: bool = False
    override_conflicts: tuple[dict[str, Any], ...] = ()
    waited_since: str | None = None
    refreshed_to: str | None = None

    @property
    def blocked(self) -> bool:
        """Whether Stage 3 refused this flow for an overlap (a blocked flow is not in flight)."""
        return self.state == ADMISSION_BLOCKED

    @property
    def refresh_due(self) -> bool:
        """Whether this flow was admitted after a wait and its plan branch is not yet refreshed onto the target."""
        return self.state == ADMISSION_ADMITTED and bool(self.waited_since) and not self.refreshed_to

    def with_refresh(self, refreshed_to: str) -> AdmissionRecord:
        """A copy recording the target-branch commit the plan branch was refreshed to."""
        return replace(self, refreshed_to=refreshed_to)

    def to_dict(self) -> dict[str, Any]:
        """Checkpoint shape of the record."""
        if self.blocked:
            entries = [entry.to_dict() for entry in self.blocked_by]
            return {"state": self.state, "blocked_since": self.blocked_since, "blocked_by": entries}
        data: dict[str, Any] = {"state": self.state}
        if self.override:
            data.update(override=True, override_conflicts=list(self.override_conflicts))
        data.update({key: value for key, value in self._refresh_fields().items() if value})
        return data

    def _refresh_fields(self) -> dict[str, str | None]:
        return {"waited_since": self.waited_since, "refreshed_to": self.refreshed_to}

    @classmethod
    def from_dict(cls, data: Any) -> AdmissionRecord | None:
        """Parse a checkpoint's admission object; ``None`` (never an error) when missing, malformed or unknown."""
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
        )


def blocked_record(previous: AdmissionRecord | None, blocked_by: tuple[BlockedBy, ...], now: str) -> AdmissionRecord:
    """A blocked record: ``blocked_since`` kept from a previous refusal (else ``now``), ``blocked_by`` replaced."""
    since = previous.blocked_since if previous is not None and previous.blocked and previous.blocked_since else now
    return AdmissionRecord(ADMISSION_BLOCKED, blocked_since=since, blocked_by=blocked_by)


def admitted_record(
    override_conflicts: list[dict[str, Any]] | None = None, previous: AdmissionRecord | None = None
) -> AdmissionRecord:
    """An admitted record; ``override_conflicts`` (even empty) notes an ``--allow-overlap`` admission.

    The wait survives: ``waited_since`` is a previous refusal's ``blocked_since`` or a previous admission's
    ``waited_since``, and ``refreshed_to`` is kept from a previous admission only.
    """
    waited = previous.blocked_since if previous is not None and previous.blocked else None
    admitted = previous if previous is not None and not previous.blocked else None
    carried = {
        "waited_since": waited or (admitted.waited_since if admitted else None),
        "refreshed_to": admitted.refreshed_to if admitted else None,
    }
    if override_conflicts is None:
        return AdmissionRecord(ADMISSION_ADMITTED, **carried)
    return AdmissionRecord(ADMISSION_ADMITTED, override=True, override_conflicts=tuple(override_conflicts), **carried)
