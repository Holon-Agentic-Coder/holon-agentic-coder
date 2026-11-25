from __future__ import annotations

import re
from collections.abc import Container
from dataclasses import dataclass

from sandbox_executor.agent_defaults import AGENT_TICKERS, canonical_agent_name, normalise_agent_id

TICKER_PATTERN = re.compile(r"[A-Z]{3}")
_LEGACY_DISAMBIGUATOR = re.compile(r"[0-9a-f]{8}")
_VERSION_WORD = re.compile(r"[0-9]+(?:\.[0-9]+)*")
_INVALID_REF_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_STAGE_ID = re.compile(r"(?P<kind>[PE])-(?P<epoch>\d+)-(?P<tail>.+)")

GENERATION_TICKER = "ticker"
GENERATION_LEGACY_HEX = "legacy-hex"
GENERATION_LEGACY_LABELS = "legacy-labels"

_FIRST_SAME_SECOND_VARIANT = 2
MAX_SAME_SECOND_VARIANTS = 9


class MissingTickerError(ValueError):
    def __init__(self, runner: str) -> None:
        self.runner = runner
        declared = ", ".join(f"{rid}-agent = {ticker}" for rid, ticker in sorted(AGENT_TICKERS.items()))
        super().__init__(
            f"Agent runner '{canonical_agent_name(runner)}' declares no ticker in agent_defaults.py, so it may not "
            f"run a flow. Declared tickers: {declared}. There is no fallback; add a ticker beside the runner "
            "definition or choose a runner that has one."
        )


class StageIdCollisionError(ValueError):
    pass


@dataclass(frozen=True)
class StageId:
    kind: str
    epoch: int
    generation: str
    ticker: str | None = None
    model_code: str | None = None
    variant: int | None = None
    disambiguator: str | None = None
    labels: str | None = None


def agent_ticker(agent: str) -> str | None:
    return AGENT_TICKERS.get(normalise_agent_id(agent))


def ticker_for(agent: str) -> str:
    ticker = agent_ticker(agent)
    if ticker is None:
        raise MissingTickerError(agent)
    return ticker


def model_code(model: str) -> str:
    model_without_provider = model.strip().rsplit("/", 1)[-1]
    parts: list[str] = []
    previous_was_version = False
    for word in (w for w in model_without_provider.split("-") if w):
        if _VERSION_WORD.fullmatch(word):
            parts.append(f".{word}" if previous_was_version else word)
            previous_was_version = True
        else:
            parts.append(word[0].upper())
            previous_was_version = False
    sanitised_code = _INVALID_REF_CHARS.sub("", "".join(parts))
    return re.sub(r"\.{2,}", ".", sanitised_code).strip(".")


def stage_id(kind: str, epoch: int, agent: str, model: str) -> str:
    if kind not in ("P", "E"):
        raise ValueError(f"stage id kind must be 'P' or 'E', not {kind!r}")
    code = model_code(model)
    if not code:
        raise ValueError(f"model name {model!r} yields an empty model code")
    return f"{kind}-{epoch}-{ticker_for(agent)}-{code}"


def _parse_ticker_tail(kind: str, epoch: int, fields: list[str]) -> StageId:
    code = fields[1] if len(fields) > 1 else ""
    variant = int(fields[2]) if len(fields) > 2 and fields[2].isdigit() else None
    return StageId(kind, epoch, GENERATION_TICKER, ticker=fields[0], model_code=code, variant=variant)


def parse_stage_id(identifier: str) -> StageId | None:
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
    parsed = parse_stage_id(identifier)
    return parsed is not None and parsed.generation == GENERATION_TICKER


def distinct_stage_id(candidate: str, taken: Container[str]) -> str:
    if candidate not in taken:
        return candidate
    for number in range(_FIRST_SAME_SECOND_VARIANT, MAX_SAME_SECOND_VARIANTS + 1):
        variant_candidate = f"{candidate}-{number}"
        if variant_candidate not in taken:
            return variant_candidate
    raise StageIdCollisionError(
        f"every same-second variant of '{candidate}' up to -{MAX_SAME_SECOND_VARIANTS} already exists under this "
        "parent; refusing to overwrite an existing plan or execution"
    )
