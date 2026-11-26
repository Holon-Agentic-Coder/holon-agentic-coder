"""The parity oracle of ``tests/ledger_query_reference.py`` over rows parsed once, for the real-ledger parity test.

``reference_query_ledger`` reads and parses the whole ledger file on every call. The parity test asks it about the same
pinned file with 23 filter sets, so the file is parsed here once (:func:`reference_rows`) and every filter set is
applied to those rows (:func:`reference_query_rows`). The filtering is the oracle's own: the same ``_match_*`` and
``_parse_target_labels`` helpers, called per row in the same order, with the same ``limit`` stop, so a row that makes
the oracle raise makes this raise at the same point. ``test_row_reference_matches_file_reference`` checks the two on
the real ledgers.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sandbox_executor.ledger import format_query_results_table

from tests.ledger_query_reference import (
    ReferenceLedgerQuery,
    _match_category_and_labels,
    _match_outcome_and_origin,
    _match_query_text,
    _parse_target_labels,
)


def reference_rows(path: str) -> tuple[Any, ...]:
    """Every record of the ledger at ``path`` the oracle would look at: blank and undecodable lines are skipped."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                rows.append(json.loads(line_str))
            except (json.JSONDecodeError, ValueError):
                continue
    return tuple(rows)


def _entry_type_matches(rec: Any, entry_type: str | None) -> bool:
    if not entry_type or entry_type.lower() == "all":
        return True
    return rec.get("entry_type", "summary").lower() == entry_type.lower()


def reference_query_rows(rows: tuple[Any, ...], **filters: Any) -> list[Any]:
    """The records ``reference_query_ledger`` returns for ``filters``, taken from rows already parsed."""
    spec = ReferenceLedgerQuery(**filters)
    target_labels = _parse_target_labels(spec.labels)
    results: list[Any] = []
    for rec in rows:
        # The oracle reads the entry type before it looks at the filter, so a record without `get` raises here too.
        rec.get("entry_type", "summary")
        if not _entry_type_matches(rec, spec.entry_type):
            continue
        if not _match_category_and_labels(rec, spec.category, target_labels):
            continue
        if not _match_outcome_and_origin(rec, spec.outcome, spec.origin, spec.review_severity):
            continue
        if not _match_query_text(rec, spec.query, spec.target):
            continue
        results.append(rec)
        if spec.limit is not None and len(results) >= spec.limit:
            break
    return results


Outcome = tuple[str, Any]


class ReferenceResults:
    """The oracle's answer for each pinned ledger and filter set, computed on first use from rows parsed once.

    An answer is ``(status, records, table)``: the status and value ``run`` gives (an error is captured as its type and
    message) and the table the records format to. The records are only ever compared, never handed to the code under
    test, so no case can change another's expected value.
    """

    def __init__(self, pinned_path: Callable[[str], str], run: Callable[..., Outcome]) -> None:
        self._pinned_path = pinned_path
        self._run = run
        self._rows: dict[str, tuple[Any, ...]] = {}
        self._answers: dict[tuple[str, str], tuple[str, Any, str | None]] = {}

    def answer(self, ledger_name: str, filter_id: str, filters: dict[str, Any]) -> tuple[str, Any, str | None]:
        """The answer for ``filters``, named ``filter_id``, on the pinned copy of ``ledger_name``."""
        key = (ledger_name, filter_id)
        if key not in self._answers:
            path = self._pinned_path(ledger_name)
            if ledger_name not in self._rows:
                self._rows[ledger_name] = reference_rows(path)
            rows = self._rows[ledger_name]
            status, records = self._run(lambda _path, **f: reference_query_rows(rows, **f), path, filters)
            table = format_query_results_table(records) if status == "ok" else None
            self._answers[key] = (status, records, table)
        return self._answers[key]
