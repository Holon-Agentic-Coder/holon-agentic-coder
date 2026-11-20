"""Short deterministic plan and execution names (pure: no I/O, no git).

A new-generation identifier is ``P-{epoch}-{ticker}-{model code}`` or ``E-{epoch}-{ticker}-{model code}``: the ticker
is the runner's three-uppercase-letter code declared beside the runner definitions in
:data:`sandbox_executor.agent_defaults.AGENT_TICKERS`, and the model code is derived by :func:`model_code` with no
lookup table. There is no fallback ticker: a runner without one may not run a flow, and :func:`ticker_for` raises
:class:`MissingTickerError` naming the runner.

Both legacy generations stay parseable (:func:`parse_stage_id`): ``{kind}-{epoch}-{agent}-{model}`` and
``{kind}-{epoch}-{hex}-{agent}-{model}``. No existing identifier is ever renamed, because existing identifiers match
the head branch names of the pull requests that carried them. Generations are told apart by the shape of the field
after the epoch: exactly three uppercase letters is a ticker, exactly eight lowercase hexadecimal digits (with more
fields after it) is a legacy disambiguator, anything else is a legacy agent label.

Two plans or executions that would get the same name under the same parent in the same second never overwrite each
other: :func:`distinct_stage_id` gives the second writer a deterministic ``-2`` … ``-9`` variant and fails clearly
once the variants are exhausted.
"""

from __future__ import annotations

import re
from collections.abc import Container
from dataclasses import dataclass

from sandbox_executor.agent_defaults import AGENT_TICKERS, canonical_agent_name, normalise_agent_id

# A ticker is exactly three uppercase letters; a legacy disambiguator is exactly eight lowercase hex digits.
TICKER_PATTERN = re.compile(r"[A-Z]{3}")
_LEGACY_DISAMBIGUATOR = re.compile(r"[0-9a-f]{8}")
# A version-number word of a model name: digits, optionally already joined with dots (``5``, ``3.8``).
_VERSION_WORD = re.compile(r"[0-9]+(?:\.[0-9]+)*")
# Characters kept in a model code: the conservative subset always valid inside a git ref component.
_INVALID_REF_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_STAGE_ID = re.compile(r"(?P<kind>[PE])-(?P<epoch>\d+)-(?P<tail>.+)")

GENERATION_TICKER = "ticker"
GENERATION_LEGACY_HEX = "legacy-hex"
GENERATION_LEGACY_LABELS = "legacy-labels"

# Deterministic same-second variants: the first writer keeps the bare name, later ones get ``-2`` … ``-9``.
MAX_SAME_SECOND_VARIANTS = 9


class MissingTickerError(ValueError):
    """The resolved runner declares no ticker, so it may not name a plan or execution (there is no fallback)."""

    def __init__(self, runner: str) -> None:
        self.runner = runner
        declared = ", ".join(f"{rid}-agent = {ticker}" for rid, ticker in sorted(AGENT_TICKERS.items()))
        super().__init__(
            f"Agent runner '{canonical_agent_name(runner)}' declares no ticker in agent_defaults.py, so it may not "
            f"run a flow. Declared tickers: {declared}. There is no fallback; add a ticker beside the runner "
            "definition or choose a runner that has one."
        )


class StageIdCollisionError(ValueError):
    """Every same-second variant of a plan or execution name is already taken under this parent."""


@dataclass(frozen=True)
class StageId:
    """A parsed plan or execution identifier of any generation.

    Attributes:
        kind: ``"P"`` or ``"E"``.
        epoch: The creation epoch seconds recorded in the name.
        generation: :data:`GENERATION_TICKER`, :data:`GENERATION_LEGACY_HEX` or :data:`GENERATION_LEGACY_LABELS`.
        ticker: The runner ticker (new generation only).
        model_code: The derived model code (new generation only).
        variant: The same-second variant number (new generation only; ``None`` for the first writer).
        disambiguator: The 8-character hex field (legacy hex generation only).
        labels: The undivided ``{agent}-{model}`` tail (legacy generations only).
    """

    kind: str
    epoch: int
    generation: str
    ticker: str | None = None
    model_code: str | None = None
    variant: int | None = None
    disambiguator: str | None = None
    labels: str | None = None


def agent_ticker(agent: str) -> str | None:
    """Return the declared ticker for a runner name in any accepted form, or None when it declares none."""
    return AGENT_TICKERS.get(normalise_agent_id(agent))


def ticker_for(agent: str) -> str:
    """Return the runner's declared ticker.

    Raises:
        MissingTickerError: The runner declares no ticker; the message names the runner.
    """
    ticker = agent_ticker(agent)
    if ticker is None:
        raise MissingTickerError(agent)
    return ticker


def model_code(model: str) -> str:
    """Derive the short model code from a model name by a pure rule, with no lookup table.

    Everything before a ``/`` is dropped, the rest splits into words on hyphens, each non-version word contributes
    its first letter upper-cased, version-number words are kept and adjacent ones are joined with a dot, and every
    character invalid in a git ref component is removed. For example ``claude-sonnet-5-5`` gives ``CS5.5`` and
    ``anthropic/claude-opus-5-5`` gives ``CO5.5``.
    """
    bare = model.strip().rsplit("/", 1)[-1]
    parts: list[str] = []
    previous_was_version = False
    for word in (w for w in bare.split("-") if w):
        if _VERSION_WORD.fullmatch(word):
            parts.append(f".{word}" if previous_was_version else word)
            previous_was_version = True
        else:
            parts.append(word[0].upper())
            previous_was_version = False
    code = _INVALID_REF_CHARS.sub("", "".join(parts))
    return re.sub(r"\.{2,}", ".", code).strip(".")


def stage_id(kind: str, epoch: int, agent: str, model: str) -> str:
    """Build a new-generation identifier ``{kind}-{epoch}-{ticker}-{model code}``.

    Raises:
        MissingTickerError: The runner declares no ticker.
        ValueError: ``kind`` is not ``"P"`` or ``"E"``, or the model name yields an empty code.
    """
    if kind not in ("P", "E"):
        raise ValueError(f"stage id kind must be 'P' or 'E', not {kind!r}")
    code = model_code(model)
    if not code:
        raise ValueError(f"model name {model!r} yields an empty model code")
    return f"{kind}-{epoch}-{ticker_for(agent)}-{code}"


def _parse_ticker_tail(kind: str, epoch: int, fields: list[str]) -> StageId:
    """Parse the fields after a ticker: the model code and, when present, the same-second variant number."""
    code = fields[1] if len(fields) > 1 else ""
    variant = int(fields[2]) if len(fields) > 2 and fields[2].isdigit() else None
    return StageId(kind, epoch, GENERATION_TICKER, ticker=fields[0], model_code=code, variant=variant)


def parse_stage_id(identifier: str) -> StageId | None:
    """Parse a plan or execution identifier of any generation, or return None for a different shape.

    The generation keys on the field after the epoch: a ticker (three uppercase letters), a legacy 8-hex
    disambiguator followed by labels, or the start of a legacy ``{agent}-{model}`` tail.
    """
    match = _STAGE_ID.fullmatch(identifier.strip())
    if not match:
        return None
    kind, epoch, tail = match.group("kind"), int(match.group("epoch")), match.group("tail")
    fields = tail.split("-")
    if TICKER_PATTERN.fullmatch(fields[0]):
        return _parse_ticker_tail(kind, epoch, fields)
    if _LEGACY_DISAMBIGUATOR.fullmatch(fields[0]) and len(fields) > 1:
        return StageId(kind, epoch, GENERATION_LEGACY_HEX, disambiguator=fields[0], labels="-".join(fields[1:]))
    return StageId(kind, epoch, GENERATION_LEGACY_LABELS, labels=tail)


def is_new_generation(identifier: str) -> bool:
    """True when ``identifier`` is a new-generation ``{kind}-{epoch}-{ticker}-{model code}`` name."""
    parsed = parse_stage_id(identifier)
    return parsed is not None and parsed.generation == GENERATION_TICKER


def distinct_stage_id(candidate: str, taken: Container[str]) -> str:
    """Return ``candidate`` or its first free deterministic same-second variant under one parent.

    Args:
        candidate: The bare new-generation identifier the writer wants.
        taken: The sibling identifiers already present under the same parent (any container supporting ``in``).

    Raises:
        StageIdCollisionError: ``candidate`` and every ``-2`` … ``-9`` variant are taken; the second writer must
            fail clearly rather than overwrite.
    """
    if candidate not in taken:
        return candidate
    for number in range(2, MAX_SAME_SECOND_VARIANTS + 1):
        variant = f"{candidate}-{number}"
        if variant not in taken:
            return variant
    raise StageIdCollisionError(
        f"every same-second variant of '{candidate}' up to -{MAX_SAME_SECOND_VARIANTS} already exists under this "
        "parent; refusing to overwrite an existing plan or execution"
    )
