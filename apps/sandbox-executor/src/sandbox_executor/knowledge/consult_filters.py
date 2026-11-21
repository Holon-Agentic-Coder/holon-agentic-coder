"""The filters of a ``holon consult`` query and the SQL ``WHERE`` clause built from them, one builder per filter.

Each clause builder returns its SQL fragment together with its parameters, so the placeholders and the bound values
of a filter can never drift apart.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Clause = tuple[str, list[Any]]


@dataclass(frozen=True, kw_only=True)
class ConsultQuery:
    """The filters ``ConsultEngine.consult_records`` accepts; an unknown filter raises TypeError.

    Attributes:
        all_labels: Records must carry every one of these labels (conjunctive).
        any_labels: Records must carry at least one of these labels (disjunctive).
        not_labels: Records must carry none of these labels.
        query_text: Phrase matched against the full-text index.
        top_k: Maximum number of records returned (capped at 6).
        max_tokens: Token budget for the returned records; the first record is always admitted.
        session_seen_ids: Record ids already consulted this session, excluded from the results.
    """

    all_labels: list[str] | None = None
    any_labels: list[str] | None = None
    not_labels: list[str] | None = None
    query_text: str | None = None
    top_k: int = 6
    max_tokens: int = 1200
    session_seen_ids: set[str] | list[str] | None = None


def clean_labels(values: list[str] | None) -> list[str]:
    """Strip and lower-case labels, dropping empty ones."""
    return [lbl.strip().lower() for lbl in (values or []) if lbl.strip()]


def _placeholders(count: int) -> str:
    return ",".join(["?"] * count)


def all_labels_clause(query: ConsultQuery) -> Clause | None:
    """Records carrying every requested label: the distinct-label count must equal the distinct requested count."""
    labels = clean_labels(query.all_labels)
    if not labels:
        return None
    sql = f"""
    ar.record_id IN (
        SELECT record_id FROM record_labels
        WHERE label IN ({_placeholders(len(labels))})
        GROUP BY record_id
        HAVING COUNT(DISTINCT label) = ?
    )
    """
    return sql, [*labels, len(set(labels))]


def any_labels_clause(query: ConsultQuery) -> Clause | None:
    """Records carrying at least one requested label."""
    labels = clean_labels(query.any_labels)
    if not labels:
        return None
    sql = f"""
    ar.record_id IN (
        SELECT DISTINCT record_id FROM record_labels
        WHERE label IN ({_placeholders(len(labels))})
    )
    """
    return sql, list(labels)


def not_labels_clause(query: ConsultQuery) -> Clause | None:
    """Records carrying none of the excluded labels."""
    labels = clean_labels(query.not_labels)
    if not labels:
        return None
    sql = f"""
    ar.record_id NOT IN (
        SELECT DISTINCT record_id FROM record_labels
        WHERE label IN ({_placeholders(len(labels))})
    )
    """
    return sql, list(labels)


def text_clause(query: ConsultQuery) -> Clause | None:
    """Records whose full-text columns match the query as one quoted FTS5 phrase."""
    if not (query.query_text and query.query_text.strip()):
        return None
    clean_query = query.query_text.strip().replace('"', '""')
    sql = """
    ar.record_id IN (
        SELECT record_id FROM action_records_fts
        WHERE action_records_fts MATCH ?
    )
    """
    return sql, [f'"{clean_query}"']


def seen_ids_clause(query: ConsultQuery) -> Clause | None:
    """Records not already consulted in this session."""
    seen_ids = list(set(query.session_seen_ids or []))
    if not seen_ids:
        return None
    return f"ar.record_id NOT IN ({_placeholders(len(seen_ids))})", seen_ids


CLAUSE_BUILDERS: tuple[Callable[[ConsultQuery], Clause | None], ...] = (
    all_labels_clause,
    any_labels_clause,
    not_labels_clause,
    text_clause,
    seen_ids_clause,
)


def build_where(query: ConsultQuery) -> Clause:
    """Return the ``WHERE`` clause (empty without filters) and its parameters, clauses joined with ``AND``."""
    clauses = [clause for clause in (build(query) for build in CLAUSE_BUILDERS) if clause is not None]
    if not clauses:
        return "", []
    params = [param for _, clause_params in clauses for param in clause_params]
    return "WHERE " + " AND ".join(sql for sql, _ in clauses), params


_RECORD_COLUMNS = (
    "record_id",
    "action_id",
    "execution_id",
    "action",
    "category",
    "target",
    "outcome",
    "verdict",
    "context_fingerprint",
    "error_context",
    "created_at",
    "payload_json",
)


def row_to_record(row: Any) -> dict[str, Any]:
    """Map a selected ``action_records`` row to its record dictionary."""
    return {column: row[column] for column in _RECORD_COLUMNS}
