from __future__ import annotations

import logging

from sandbox_executor.knowledge.fragment_identities import RowIdentity
from sandbox_executor.knowledge.ledger_sync import sync_ledger_incremental
from sandbox_executor.ledger.merged import fragment_sync_plan, ledger_row_identity

logger = logging.getLogger(__name__)


def sync_ledger_and_fragments(db_path: str, ledger: str) -> None:
    identity_of = ledger_row_identity(ledger)
    syncs = [(ledger, None, RowIdentity(ledger, identity_of))]
    fragment_identity = RowIdentity(ledger, identity_of, record=True)
    syncs += [(path, skip_row, fragment_identity) for path, skip_row in fragment_sync_plan(ledger)]
    for ledger_path, skip_row, identity in syncs:
        try:
            sync_ledger_incremental(db_path, ledger_path, skip_row, identity)
        except Exception as e:
            logger.debug("Failed incremental sync for %s: %s", ledger_path, e)
