"""Settings that start Claude Code with commit and pull request attribution disabled.

The maintainer's rule is that no commit carries an attribution trailer. The payload is delivered through the runner's
existing ``--settings`` route (``HOLON_AGENT_SETTINGS``); the user's own settings files are never edited and no
permission flag is involved.
"""

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

ATTRIBUTION_OFF: dict[str, Any] = {
    "includeCoAuthoredBy": False,
    "attribution": {"commit": "", "pr": ""},
}


def attribution_off_settings(existing: str | None = None) -> str:
    """Return the ``--settings`` value: ``existing`` (inline JSON or a readable file path) with attribution disabled.

    An unset value yields the attribution-off payload alone. An unparsable or unreadable value falls back to the
    payload alone after one warning that never includes the value, as it may carry a credential. A file is read, never
    written.
    """
    merged = dict(ATTRIBUTION_OFF)
    if existing:
        base = _load_settings(existing)
        if base is None:
            logger.warning("HOLON_AGENT_SETTINGS is not a JSON object or readable file; using attribution-off only.")
        else:
            merged = {**base, **ATTRIBUTION_OFF}
    return json.dumps(merged)


def _load_settings(value: str) -> dict[str, Any] | None:
    text = value
    if not value.lstrip().startswith("{") and os.path.isfile(value):
        try:
            with open(value, encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
