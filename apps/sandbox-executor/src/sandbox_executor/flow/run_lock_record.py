"""The holder record stored in a flow run lock file: who holds the lock for an intent slug, where and since when."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict, dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class RunLockHolder:
    """One ``holon flow`` process holding the run lock of ``slug``."""

    pid: int
    host: str
    started_at: str
    slug: str
    worktree: str
    run_id: str

    @classmethod
    def current(cls, slug: str, worktree: str, run_id: str) -> RunLockHolder:
        """The record of this process, started now, running ``slug`` in ``worktree``."""
        return cls(
            pid=os.getpid(),
            host=socket.gethostname(),
            started_at=datetime.now(UTC).isoformat(timespec="seconds"),
            slug=slug,
            worktree=os.path.abspath(worktree),
            run_id=run_id,
        )

    @classmethod
    def from_text(cls, text: str) -> RunLockHolder | None:
        """Parse a stored record; None for an empty file (a clean release) or unreadable content."""
        try:
            data = json.loads(text)
            return cls(
                pid=int(data["pid"]),
                host=str(data["host"]),
                started_at=str(data["started_at"]),
                slug=str(data["slug"]),
                worktree=str(data["worktree"]),
                run_id=str(data.get("run_id", "")),
            )
        except (ValueError, TypeError, KeyError):
            return None

    def to_json(self) -> str:
        """The record as one JSON object."""
        return json.dumps(asdict(self), sort_keys=True)

    def describe(self) -> str:
        """``pid <pid> on <host> since <time>``, for log and error messages."""
        return f"pid {self.pid} on {self.host} since {self.started_at}"

    def is_dead_here(self) -> bool:
        """True when the holder ran on this host and its pid no longer exists (a recycled pid looks alive)."""
        if self.host != socket.gethostname() or self.pid <= 1:
            return False
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False
