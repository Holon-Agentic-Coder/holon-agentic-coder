"""Operator-facing reporting of a non-fatal ledger failure, shared by the stages and the review loop."""

from __future__ import annotations

import logging

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.redaction import redact_agent_secrets

logger = logging.getLogger(__name__)


def _warn_ledger_failure(context: FlowContext, label: str, ledger_path: str, exc: BaseException) -> None:
    """Report a non-fatal ledger failure at WARNING level and in the operator-facing context log.

    The exception text is passed through the canonical ``redact_agent_secrets`` first, so a
    secret carried in the failure detail never reaches either log.
    """
    detail = redact_agent_secrets(str(exc))
    message = f"Ledger append failed ({label}) for {ledger_path}: {type(exc).__name__}: {detail}"
    logger.warning(message)
    context.log(f"Warning: {message}")
