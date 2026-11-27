from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

ParsedRow = tuple[int, str, dict[str, Any]]


# why: a fragment row promoted into the canonical ledger, or re-read from a recreated fragment, is never indexed twice
@dataclass(frozen=True)
class RowIdentity:
    scope: str
    of: Callable[[dict[str, Any]], str]
    record: bool = False


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fragment_row_identities (
            ledger_scope TEXT NOT NULL,
            identity TEXT NOT NULL,
            PRIMARY KEY (ledger_scope, identity)
        );
        """
    )


def drop_known_rows(
    conn: sqlite3.Connection, identity: RowIdentity, parsed_rows: list[ParsedRow]
) -> tuple[list[ParsedRow], list[str]]:
    _ensure_table(conn)
    known = {
        row[0]
        for row in conn.execute(
            "SELECT identity FROM fragment_row_identities WHERE ledger_scope = ?", (identity.scope,)
        ).fetchall()
    }
    kept: list[ParsedRow] = []
    kept_identities: list[str] = []
    for parsed in parsed_rows:
        key = identity.of(parsed[2])
        if key not in known:
            if identity.record:
                known.add(key)
            kept.append(parsed)
            kept_identities.append(key)
    return kept, kept_identities


def record_identities(conn: sqlite3.Connection, identity: RowIdentity, identities: list[str]) -> None:
    if identity.record and identities:
        conn.executemany(
            "INSERT OR IGNORE INTO fragment_row_identities (ledger_scope, identity) VALUES (?, ?);",
            [(identity.scope, key) for key in identities],
        )
