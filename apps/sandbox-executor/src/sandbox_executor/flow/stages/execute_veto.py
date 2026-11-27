from __future__ import annotations

import logging
import os

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.knowledge import ActionIndexDatabase, compute_context_fingerprint

logger = logging.getLogger(__name__)


def execution_veto_error(context: FlowContext) -> str | None:
    exec_fp = compute_context_fingerprint(
        target=context.plan_branch or "",
        tool=context.agent,
        command_prefix="pytest",
    )
    db_path = os.path.join(context.target_worktree, "holon-knowledge/.index/action_index.db")
    try:
        if os.path.exists(db_path):
            index_db = ActionIndexDatabase(db_path=db_path)
            conn = index_db.get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT is_shadow, state FROM active_vetoes WHERE context_fingerprint = ? AND state != 'lifted';",
                (exec_fp,),
            )
            veto_match = cur.fetchone()
            conn.close()
            if veto_match:
                is_shadow = bool(veto_match["is_shadow"])
                if is_shadow:
                    context.log(f"[VETO SHADOW WARNING] Known failure pattern matches context {exec_fp}")
                else:
                    err = f"Execution blocked by deterministic failure veto for context fingerprint: {exec_fp}"
                    context.log(f"[VETO BLOCKED] {err}")
                    return err
    except Exception as e:
        logger.debug("Non-fatal veto check exception: %s", e)
    return None
