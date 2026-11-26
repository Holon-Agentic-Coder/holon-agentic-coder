"""The record type, sections and shared wording of the ``HOLON_*`` environment-variable reference (``env_registry``)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

ReadSide = Literal["host", "container", "both", "ci", "ci and container"]
Forwarding = Literal["env-file", "argv", "not forwarded"]

BOOLEAN = "boolean: `1`, `true`, `yes`, `on` (any case)"
OTHER_IS_OFF = "any other value is off"
SECONDS = "seconds, a positive number"
BUDGETS_DOC = "`docs/budgets.md`"
CREDENTIALS_DOC = "`docs/executor/agent_credentials_requirements.md`"
LOCAL_DOC = "host-local models (`local_llm.py`)"
PROXY_DOC = "`docs/optimisation/token_reduction_architecture.md`"
AGENTS_DOC = "`docs/agents.md`"
PASSED_THROUGH = "passed through unvalidated; the runner CLI rejects it"
LOCAL_CONFIG_ERROR = "configuration error naming the variable, never the value"
STAGE_MINUTES = "minutes, a positive number up to 7 days, or `unlimited`"
STAGE_INVALID = "warning naming the variable; the default is kept"


class Area(StrEnum):
    """The sections of the reference, in document order."""

    RUNNER_AND_AGENT = "runner and agent"
    BUDGETS = "budgets"
    TOKEN_REDUCTION_AND_PROXY = "token reduction and proxy"
    GIT = "git"
    LEDGER_AND_ACTIONS = "ledger and actions"
    REVIEW_LOOP = "review loop"
    LOGGING = "logging"
    CI_ONLY = "CI-only"


@dataclass(frozen=True, slots=True)
class EnvironmentVariable:
    """One ``HOLON_*`` variable: what it does, what it accepts and where it is read and forwarded.

    ``forwarding`` describes how the value reaches a role or sidecar container: ``env-file`` is the 0600 docker
    env-file every forwarded ``HOLON_*`` value goes through (a value no env-file line can carry goes as a passthrough
    ``-e NAME`` instead), ``argv`` is an explicit ``-e`` item on the sidecar command line. ``internal`` marks a variable
    the host or a maintainer sets, not an operator setting.
    """

    name: str
    area: Area
    purpose: str
    value_type: str
    default: str
    when_unset: str
    when_invalid: str
    read_side: ReadSide
    since: str
    forwarding: Forwarding = "env-file"
    secret: bool = False
    internal: bool = False
