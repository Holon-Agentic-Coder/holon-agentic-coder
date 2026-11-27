from __future__ import annotations

import logging

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.redaction import redact_agent_secrets

logger = logging.getLogger(__name__)


def _warn_ledger_failure(context: FlowContext, label: str, ledger_path: str, exc: BaseException) -> None:
    # why: a secret carried in the failure detail must never reach the logger or the context log
    detail = redact_agent_secrets(str(exc))
    message = f"Ledger append failed ({label}) for {ledger_path}: {type(exc).__name__}: {detail}"
    logger.warning(message)
    context.log(f"Warning: {message}")
