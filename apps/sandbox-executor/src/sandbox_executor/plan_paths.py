"""Plan and execution naming: model codes, three-generation ID parsing and nested plan-file paths (pure).

New-generation (gen 3) plan and execution identifiers are ``P-{epoch}-{ticker}-{model code}`` and
``E-{epoch}-{ticker}-{model code}``: no hexadecimal disambiguator, because a plan file is keyed by its intent
(``plans/{intent}/{plan id}.md``) and every lookup carries the intent, so two intents planned in the same second
never share a key. Both older generations stay parseable and are never renamed:

* gen 1: ``X-{epoch}-{agent}-{model}``
* gen 2: ``X-{epoch}-{hex}-{agent}-{model}`` (8-character lowercase hexadecimal disambiguator)
* gen 3: ``X-{epoch}-{ticker}-{model code}`` (three-uppercase-letter ticker)

The generation is decided from the field after the epoch: 8-character lowercase hex marks gen 2, three uppercase
letters mark gen 3, anything else is gen 1. The parse result carries the generation so callers never re-guess.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

PLANS_DIR = "plans"
CALIBRATION_REPORT_SUFFIX = "_calibration.md"

_GEN2_HEX = re.compile(r"[0-9a-f]{8}")
_GEN3_TICKER = re.compile(r"[A-Z]{3}")
_ID_SHAPE = re.compile(r"(?P<kind>[PE])-(?P<epoch>\d+)-(?P<rest>.+)")
# Characters kept in a model code: anything else is invalid in a git ref component or reserved by it.
_CODE_INVALID = re.compile(r"[^A-Za-z0-9.]")


def model_code(model: str) -> str:
    """Derive the short model code from a model name, with no lookup table.

    Everything before a ``/`` is dropped; the rest is split into ``-``-separated words. An alphabetic word
    contributes its first letter upper-cased; a word that begins with a digit is a version part, kept whole, and
    consecutive version parts are joined with a dot. Every character invalid in a git ref component is removed.

    Examples: ``claude-sonnet-5-5 -> CS5.5``, ``gemini-3.8-flash-medium -> G3.8FM``,
    ``anthropic/claude-opus-5-5 -> CO5.5``.
    """
    name = model.strip().rsplit("/", 1)[-1]
    parts: list[str] = []
    previous_was_version = False
    for word in filter(None, name.split("-")):
        if word[0].isdigit():
            if previous_was_version:
                parts[-1] = f"{parts[-1]}.{word}"
            else:
                parts.append(word)
            previous_was_version = True
        else:
            parts.append(word[0].upper())
            previous_was_version = False
    code = _CODE_INVALID.sub("", "".join(parts))
    code = re.sub(r"\.+", ".", code).strip(".")
    return code or "M"


@dataclass(frozen=True)
class ParsedFlowId:
    """A parsed plan or execution identifier and the name generation it belongs to.

    Attributes:
        kind: ``"P"`` for a plan, ``"E"`` for an execution.
        epoch: The creation epoch seconds carried in the name.
        generation: 1 (``X-{epoch}-{agent}-{model}``), 2 (``X-{epoch}-{hex}-{agent}-{model}``) or
            3 (``X-{epoch}-{ticker}-{model code}``).
        label: The text after the epoch (and after the hex for gen 2): the agent-and-model label for gens 1 and
            2, ``{ticker}-{model code}`` for gen 3.
        disambiguator: The 8-character hexadecimal field of a gen-2 name, otherwise ``""``.
    """

    kind: str
    epoch: int
    generation: int
    label: str
    disambiguator: str = ""


def parse_flow_id(identifier: str) -> ParsedFlowId:
    """Parse a plan or execution identifier of any of the three generations.

    Raises:
        ValueError: If ``identifier`` does not match any generation's shape.
    """
    match = _ID_SHAPE.fullmatch(identifier.strip())
    if match is None:
        raise ValueError(f"not a plan or execution identifier: '{identifier}'")
    kind, epoch, rest = match.group("kind"), int(match.group("epoch")), match.group("rest")
    head = rest.split("-", 1)[0]
    if _GEN2_HEX.fullmatch(head) and "-" in rest:
        return ParsedFlowId(kind, epoch, 2, rest.split("-", 1)[1], disambiguator=head)
    if _GEN3_TICKER.fullmatch(head):
        return ParsedFlowId(kind, epoch, 3, rest)
    return ParsedFlowId(kind, epoch, 1, rest)


def plan_segment(epoch: int, ticker: str, code: str) -> str:
    """Return the gen-3 plan branch segment ``P-{epoch}-{ticker}-{model code}``."""
    return f"P-{epoch}-{ticker}-{code}"


def execution_segment(epoch: int, ticker: str, code: str) -> str:
    """Return the gen-3 execution branch segment ``E-{epoch}-{ticker}-{model code}``."""
    return f"E-{epoch}-{ticker}-{code}"


def _branch_segments(branch: str) -> list[str]:
    """The path segments of a bare branch, with ref prefixes and the ``/_`` or ``/calibrated`` suffix removed."""
    bare = branch.strip()
    for prefix in ("refs/remotes/origin/", "refs/heads/", "origin/"):
        bare = bare.removeprefix(prefix)
    bare = bare.removesuffix("/_").removesuffix("/calibrated").strip("/")
    return [segment for segment in bare.split("/") if segment]


def plan_file_paths(plan_branch: str) -> tuple[str, str]:
    """The plan file and sibling calibration report paths that mirror ``plan_branch``'s nesting.

    A plan on ``I-x/P-y/_`` lives at ``plans/I-x/P-y.md`` with ``plans/I-x/P-y_calibration.md`` beside it; child
    intents (``{parent}--{index}-{slug}``) and plans nested under plans keep their full branch nesting as
    directories.

    Raises:
        ValueError: If ``plan_branch`` has no parent segment above the plan identifier.
    """
    segments = _branch_segments(plan_branch)
    if len(segments) < 2:
        raise ValueError(f"not a nested plan branch: '{plan_branch}'")
    plan_rel = posixpath.join(PLANS_DIR, *segments[:-1], f"{segments[-1]}.md")
    return plan_rel, plan_rel.removesuffix(".md") + CALIBRATION_REPORT_SUFFIX


def legacy_plan_file(plan_id: str) -> str:
    """The flat old-generation plan file path ``plans/{plan id}.md`` (reads only; new writes nest by intent)."""
    return posixpath.join(PLANS_DIR, f"{plan_id}.md")


def legacy_calibration_report(plan_id: str) -> str:
    """The flat old-generation report path ``plans/{plan id}_calibration.md`` (reads only)."""
    return posixpath.join(PLANS_DIR, f"{plan_id}{CALIBRATION_REPORT_SUFFIX}")


def is_calibration_report_path(path: str) -> bool:
    """True for a calibration report under ``plans/``, flat (old generation) or nested by intent."""
    return path.startswith(f"{PLANS_DIR}/") and path.endswith(CALIBRATION_REPORT_SUFFIX)
