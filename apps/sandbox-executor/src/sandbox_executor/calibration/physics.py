from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


def load_metrics_physics(target_worktree: str = ".") -> tuple[float, float]:
    default_lambda = 0.3
    default_mu = 0.5
    ev_config_path = os.path.join(target_worktree, "holon-config/metrics/ev_config.json")
    if os.path.exists(ev_config_path):
        try:
            with open(ev_config_path, encoding="utf-8") as f:
                data = json.load(f)
                l_val = float(data.get("lambda", default_lambda))
                m_val = float(data.get("mu", default_mu))
                if 0 < l_val <= 1.0:
                    default_lambda = l_val
                if 0 < m_val <= 1.0:
                    default_mu = m_val
        except Exception as e:
            logger.debug("Failed loading ev_config.json: %s", e)
    return default_lambda, default_mu
