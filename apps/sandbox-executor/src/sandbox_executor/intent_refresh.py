from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

REFRESHED_INTENT_FIELDS = ("goal", "description", "rationale", "scope", "acceptance_criteria")
SELF_EXPLAINING_SENTENCE = (
    "Code is self-explaining: no docstrings and no comments except '# why:' comments that record a reason the code "
    "cannot show."
)
SELF_EXPLAINING_MARKER = "code is self-explaining"


class IntentContractError(ValueError):
    pass


def goal_states_self_explaining_code(goal: object) -> bool:
    if not isinstance(goal, str):
        return False
    return SELF_EXPLAINING_MARKER in re.sub(r"\s+", " ", goal).lower()


def read_intent_file(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        raise IntentContractError(f"Intent file '{path}' does not exist; the resume is refused.")
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise IntentContractError(f"Intent file '{path}' does not hold a JSON object; the resume is refused.")
    return payload


def refresh_intent_text(context: Any, intent_path: str) -> list[str]:
    intent_file = read_intent_file(intent_path)
    changed: list[str] = []
    # why: a field absent from the intent file keeps its stored text, so an older intent file never erases a field.
    for field in (name for name in REFRESHED_INTENT_FIELDS if name in intent_file):
        if intent_file[field] == context.intent_data.get(field):
            continue
        # why: stages write keys such as replan_feedback into this dict, so it is updated in place, never replaced.
        context.intent_data[field] = intent_file[field]
        changed.append(field)
        message = f"Resume refreshed intent {field} from {intent_path}: the checkpoint held an older text."
        logger.info(message)
        context.log(message)
    if not goal_states_self_explaining_code(context.intent_data.get("goal")):
        raise IntentContractError(
            f"The goal in intent file '{intent_path}' lacks the self-explaining-code sentence, so the resume is "
            f'refused. Add this sentence to the goal: "{SELF_EXPLAINING_SENTENCE}"'
        )
    return changed
