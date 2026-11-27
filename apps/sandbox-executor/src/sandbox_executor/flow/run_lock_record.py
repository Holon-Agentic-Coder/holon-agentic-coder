from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict, dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class RunLockHolder:
    pid: int
    host: str
    started_at: str
    slug: str
    worktree: str
    run_id: str

    @classmethod
    def current(cls, slug: str, worktree: str, run_id: str) -> RunLockHolder:
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
        # why: a clean release leaves the file empty, which fails to parse and so reads as no holder
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
        return json.dumps(asdict(self), sort_keys=True)

    def describe(self) -> str:
        return f"pid {self.pid} on {self.host} since {self.started_at}"

    def is_dead_here(self) -> bool:
        if self.host != socket.gethostname() or self.pid <= 1:
            return False
        # why: a recycled pid looks alive, so a reused pid keeps the lock held rather than risk a double holder
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False
