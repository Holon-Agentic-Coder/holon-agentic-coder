"""Short plan and execution names, their three-generation grammar, and intent-keyed plan file paths.

A new (generation 3) plan or execution identifier is ``P-{epoch}-{ticker}-{model code}`` or
``E-{epoch}-{ticker}-{model code}``: the runner's three-uppercase-letter ticker (declared in
``agent_defaults.AGENT_TICKERS``) and a model code derived by :func:`model_code`, with no hexadecimal
disambiguator. The hex existed only because a plan identifier was used on its own as a flat key; plan files are now
keyed by their intent (:func:`plan_file_rel`), so two intents planned in the same second by the same runner and model
no longer collide. Within one parent, a same-second duplicate gets the next second's epoch or fails clearly
(:func:`uncollided_stage_id`); it never overwrites.

Both older generations stay valid and parseable (:func:`parse_stage_id`), because existing identifiers match the head
branch names of the pull requests that carried them and are never renamed:

* generation 1: ``X-{epoch}-{agent}-{model}``
* generation 2: ``X-{epoch}-{hex8}-{agent}-{model}``
* generation 3: ``X-{epoch}-{ticker}-{model code}``

Disambiguation: an 8-character lowercase-hex second field marks generation 2, a three-uppercase-letter second field
marks generation 3, anything else is generation 1.

Pure except for the ticker lookup; no I/O and no git.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from sandbox_executor.agent_defaults import ticker_for_agent

INTENT_SUFFIX = "/_"
TICKER_LENGTH = 3
# Conservative upper bound on a model code's length, used only for room-below measurements at intent time; the real
# code of every known model is far shorter (``CS5.5``, ``G3.8FM``).
MODEL_CODE_BOUND = 12

_HEX_FIELD = re.compile(r"[0-9a-f]{8}")
_TICKER_FIELD = re.compile(r"[A-Z]{3}")
_STAGE_ID = re.compile(r"([PE])-(\d+)-(.+)")
_INVALID_CODE_CHARS = re.compile(r"[^0-9A-Za-z.]")

# How many one-second epoch bumps a creation attempts before failing on a same-parent duplicate name.
MAX_EPOCH_BUMPS = 5


class DuplicateStageNameError(ValueError):
    """A plan or execution name already exists under the same parent and bumping the epoch did not free one."""


def _version_group(parts: list[str]) -> str:
    """Join consecutive version words with a dot (``["5", "5"]`` gives ``5.5``; ``["3.8"]`` stays ``3.8``)."""
    return ".".join(p for p in parts if p)


def _tokenise(name: str) -> list[str]:
    """Split a model name into code tokens: one initial per alphabetic word, version words kept whole."""
    tokens: list[str] = []
    versions: list[str] = []
    for word in name.split("-"):
        cleaned = _INVALID_CODE_CHARS.sub("", word)
        if not cleaned:
            continue
        if cleaned[0].isdigit():
            versions.append(cleaned)
            continue
        if versions:
            tokens.append(_version_group(versions))
            versions = []
        tokens.append(cleaned[0].upper())
    if versions:
        tokens.append(_version_group(versions))
    return tokens


def model_code(model: str) -> str:
    """Derive the short model code from a model name, with no lookup table.

    Everything before a ``/`` is dropped, each alphabetic word contributes its upper-cased first letter, version
    numbers are kept whole and consecutive ones joined with a dot, and every character invalid in a git ref component
    is removed. ``claude-sonnet-5-5`` gives ``CS5.5``; ``anthropic/claude-opus-5-5`` gives ``CO5.5``;
    ``gemini-3.8-flash-medium`` gives ``G3.8FM``. The result is never empty and always a valid ref component.
    """
    name = model.strip().rsplit("/", 1)[-1]
    code = "".join(_tokenise(name))
    code = re.sub(r"\.{2,}", ".", code).strip(".")
    return code or "M"


@dataclass(frozen=True)
class StageIdParts:
    """A parsed plan or execution identifier; ``generation`` is exposed so callers never re-guess the grammar.

    Attributes:
        kind: ``"P"`` or ``"E"``.
        epoch: The creation epoch seconds.
        generation: 1, 2 or 3 (see the module docstring).
        label: The agent-and-model label text of a generation 1 or 2 identifier, None for generation 3.
        disambiguator: The 8-character hex field of a generation 2 identifier, None otherwise.
        ticker: The runner ticker of a generation 3 identifier, None otherwise.
        code: The model code of a generation 3 identifier, None otherwise.
        raw: The identifier as given.
    """

    kind: str
    epoch: int
    generation: int
    label: str | None
    disambiguator: str | None
    ticker: str | None
    code: str | None
    raw: str


def parse_stage_id(identifier: str) -> StageIdParts:
    """Parse a plan or execution identifier of any generation.

    Raises:
        ValueError: ``identifier`` is not a plan or execution identifier of any generation.
    """
    match = _STAGE_ID.fullmatch(identifier.strip())
    if match is None:
        raise ValueError(f"not a plan or execution identifier: '{identifier}'")
    kind, epoch, rest = match.group(1), int(match.group(2)), match.group(3)
    first, _, tail = rest.partition("-")
    if _HEX_FIELD.fullmatch(first) and tail:
        return StageIdParts(kind, epoch, 2, label=tail, disambiguator=first, ticker=None, code=None, raw=identifier)
    if _TICKER_FIELD.fullmatch(first) and tail:
        return StageIdParts(kind, epoch, 3, label=None, disambiguator=None, ticker=first, code=tail, raw=identifier)
    return StageIdParts(kind, epoch, 1, label=rest, disambiguator=None, ticker=None, code=None, raw=identifier)


def new_stage_id(kind: str, epoch: int, agent: str, model: str) -> str:
    """Build a generation 3 identifier ``{kind}-{epoch}-{ticker}-{model code}`` for a runner and model.

    Raises:
        MissingAgentTickerError: The runner declares no ticker.
    """
    return f"{kind}-{epoch}-{ticker_for_agent(agent)}-{model_code(model)}"


def uncollided_stage_id(
    kind: str,
    epoch: int,
    agent: str,
    model: str,
    exists: Callable[[str], bool] | None = None,
) -> str:
    """Build a generation 3 identifier whose name is free under its parent, bumping the epoch on a collision.

    ``exists`` answers whether a candidate name is already taken under the same parent (a file, a ledger row for that
    parent, or a ref). The epoch is bumped by one second at a time, deterministically, at most
    :data:`MAX_EPOCH_BUMPS` times; an existing name is never overwritten.

    Raises:
        DuplicateStageNameError: Every candidate name up to the bump limit is taken.
    """
    for bump in range(MAX_EPOCH_BUMPS + 1):
        name = new_stage_id(kind, epoch + bump, agent, model)
        if exists is None or not exists(name):
            return name
    taken = new_stage_id(kind, epoch, agent, model)
    raise DuplicateStageNameError(
        f"duplicate {'plan' if kind == 'P' else 'execution'} name: '{taken}' and the next {MAX_EPOCH_BUMPS} "
        f"epochs are already taken under the same parent; refusing to overwrite"
    )


def _bare_branch(branch: str) -> str:
    return branch.strip().removesuffix(INTENT_SUFFIX).strip("/")


def plan_file_rel(plan_branch: str) -> str:
    """Repo-relative plan markdown path mirroring the plan's branch: ``plans/{intent}/{plan id}.md``.

    ``plan_branch`` is the bare plan branch (with or without its ``/_`` suffix), including every ancestor segment, so
    child intents (``{parent}--{index}-{slug}``) and plans nested under plans mirror their full branch nesting.
    """
    return f"plans/{_bare_branch(plan_branch)}.md"


def calibration_report_rel(plan_branch: str) -> str:
    """Repo-relative calibration report path beside the plan file: ``plans/{intent}/{plan id}_calibration.md``."""
    return f"plans/{_bare_branch(plan_branch)}_calibration.md"


def execution_notes_rel(execution_branch: str) -> str:
    """Repo-relative execution notes path mirroring the execution's branch: ``executions/{...}/{exec id}.md``.

    Keyed on the full path (intent, plan and execution), never on the bare execution identifier, so two same-second
    executions under different plans write different notes files.
    """
    return f"executions/{_bare_branch(execution_branch)}.md"


def legacy_plan_file_rel(plan_id: str) -> str:
    """The flat pre-migration plan path ``plans/{plan id}.md``, kept readable for old-generation lookups."""
    return f"plans/{plan_id}.md"


def legacy_calibration_report_rel(plan_id: str) -> str:
    """The flat pre-migration report path ``plans/{plan id}_calibration.md``, kept readable for old generations."""
    return f"plans/{plan_id}_calibration.md"
