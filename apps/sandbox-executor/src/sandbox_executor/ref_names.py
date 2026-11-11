"""Branch-name limit, measurement and deterministic shortening (pure: no I/O, no git).

GitHub rejects a push whose full ref name is longer than ``MAX_REF_LENGTH`` characters. Measured against the real
service: a full ref of 255 characters, counting the ``refs/heads/`` prefix, is accepted and 256 is rejected, so a branch
name without the prefix can be at most ``MAX_BRANCH_LENGTH`` (244) characters.

The calibration branch ends in ``/calibrated`` where every other branch ends in ``/_`` (9 characters more), so every
bare name (a name without its ``/_`` suffix) is measured as if it ended in ``/calibrated``: ``measured_length`` is the
length of ``refs/heads/{bare}/calibrated``. A name that fits is returned untouched. A longer one is shortened, in the
order the caller lists its parts, by replacing the tail of a part with a 6-character hexadecimal digest of the part's
original text, so two different long inputs never collide and the same input always gives the same name. Timestamps,
8-character identifiers and index markers are never part of a shortenable part. A name that cannot fit even with every
shortenable part at its minimum raises ``RefTooLongError``.

An intent branch is measured with room left for one plan and one execution segment below it whose labels are at their
shortest, because those labels are only known, and shortened, when the plan and execution are created.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from typing import Any

from sandbox_executor.budgets import MAX_SUB_INTENT_DEPTH

MAX_REF_LENGTH = 255
REF_PREFIX = "refs/heads/"
INTENT_SUFFIX = "/_"
CALIBRATION_SUFFIX = "/calibrated"
MAX_BRANCH_LENGTH = MAX_REF_LENGTH - len(REF_PREFIX)
DIGEST_LENGTH = 6

_TIMESTAMP_LENGTH = 10
_DISAMBIGUATOR_LENGTH = 8


class RefTooLongError(ValueError):
    """A branch name cannot be made to fit the ref limit even after shortening."""

    def __init__(self, limit: int, length: int, segment: str) -> None:
        self.limit = limit
        self.length = length
        self.segment = segment
        super().__init__(
            f"ref too long: the branch name measures {length} characters with the '{REF_PREFIX}' prefix and the "
            f"'{CALIBRATION_SUFFIX}' suffix, over the limit of {limit}; the segment that cannot be shortened is "
            f"'{segment}'"
        )


@dataclass(frozen=True)
class Part:
    """One piece of a name. ``weight`` counts the part again for each copy that will nest below it later."""

    key: str
    text: str
    shortenable: bool = False
    weight: int = 1


@dataclass(frozen=True)
class FitResult:
    """A fitted bare name and the original text of every part that was shortened, keyed by part name."""

    name: str
    originals: dict[str, str] = field(default_factory=dict)


def measured_length(bare_name: str, extra: int = 0) -> int:
    """Length of the full ref ``refs/heads/{bare}/calibrated``, plus ``extra`` characters yet to be nested."""
    return len(REF_PREFIX) + len(bare_name) + len(CALIBRATION_SUFFIX) + extra


def digest(text: str) -> str:
    """Stable 6-character hexadecimal digest of the original text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


def shorten_text(text: str, max_length: int) -> str:
    """Return ``text`` cut to at most ``max_length`` characters plus its digest; never below digest-only length."""
    if len(text) <= max_length:
        return text
    keep = max_length - DIGEST_LENGTH - 1
    head = text[:keep].rstrip("-") if keep > 0 else ""
    return f"{head}-{digest(text)}" if head else digest(text)


def _total_length(parts: list[Part], limit_extra: int) -> int:
    rendered = sum(len(p.text) * p.weight for p in parts)
    return len(REF_PREFIX) + rendered + len(CALIBRATION_SUFFIX) + limit_extra


def _shorten_part(part: Part, excess: int) -> Part:
    cut = -(-excess // part.weight)
    target = max(DIGEST_LENGTH, len(part.text) - cut)
    shorter = shorten_text(part.text, target)
    return part if len(shorter) >= len(part.text) else replace(part, text=shorter)


def _unshortenable_segment(parts: list[Part]) -> str:
    fixed = [p for p in parts if not p.shortenable or len(p.text) <= DIGEST_LENGTH]
    return max(fixed or parts, key=lambda p: len(p.text) * p.weight).key


def fit_parts(parts: list[Part], extra: int = 0, limit: int = MAX_REF_LENGTH) -> FitResult:
    """Join ``parts`` into a bare name that fits ``limit``, shortening shortenable parts in the order listed."""
    originals: dict[str, str] = {}
    current = list(parts)
    for index, part in enumerate(current):
        excess = _total_length(current, extra) - limit
        if excess <= 0:
            break
        if part.shortenable:
            current[index] = _shorten_part(part, excess)
            if current[index].text != part.text:
                originals[part.key] = part.text
    length = _total_length(current, extra)
    if length > limit:
        raise RefTooLongError(limit, length, _unshortenable_segment(current))
    return FitResult("".join(p.text for p in current), originals)


def _segment_parts(parent: str, head: str, agent: str, model: str, weight: int) -> list[Part]:
    return [
        Part("parent", parent.removesuffix(INTENT_SUFFIX).rstrip("/")),
        Part("segment", head),
        Part("agent", agent, shortenable=True, weight=weight),
        Part("separator", "-"),
        Part("model", model, shortenable=True, weight=weight),
    ]


_SEGMENT_FIXED_LENGTH = len("/E-") + _TIMESTAMP_LENGTH + 1 + _DISAMBIGUATOR_LENGTH + 1 + 1


_MIN_SEGMENT_LENGTH = _SEGMENT_FIXED_LENGTH + 2 * DIGEST_LENGTH


# A child level below an intent: its plan segment, the ``--{n}-`` marker and a digest-only slug, labels shortest.
_MIN_CHILD_LEVEL_LENGTH = _MIN_SEGMENT_LENGTH + len("--1-") + DIGEST_LENGTH


def downstream_reserve() -> int:
    """Characters one plan and one execution segment add below an intent branch, labels at their shortest."""
    return 2 * _MIN_SEGMENT_LENGTH


def fit_plan_id(parent: str, seq: int, dis: str, agent: str, model: str) -> FitResult:
    """Fit ``P-{seq}-{dis}-{agent}-{model}`` below ``parent``, leaving room for the execution segment under it."""
    parts = _segment_parts(parent, f"/P-{seq}-{dis}-", agent, model, weight=2)
    result = fit_parts(parts, extra=_SEGMENT_FIXED_LENGTH)
    return _segment_result(result, parent)


def fit_execution_id(parent: str, seq: int, dis: str, agent: str, model: str) -> FitResult:
    """Fit ``E-{seq}-{dis}-{agent}-{model}`` below the plan branch ``parent``."""
    result = fit_parts(_segment_parts(parent, f"/E-{seq}-{dis}-", agent, model, weight=1))
    return _segment_result(result, parent)


def _segment_result(result: FitResult, parent: str) -> FitResult:
    """Strip the parent off the joined name, leaving the bare plan or execution identifier."""
    parent_length = len(parent.removesuffix(INTENT_SUFFIX).rstrip("/")) + 1
    return FitResult(result.name[parent_length:], result.originals)


def fit_root_intent_branch(seq: int, slug: str) -> FitResult:
    """Fit ``I-{seq}-{slug}``, leaving room for one plan and one execution segment below it.

    The reserve means a name within ``downstream_reserve()`` of the limit may be shortened although it would fit alone.
    """
    parts = [Part("prefix", f"I-{seq}-"), Part("slug", slug, shortenable=True)]
    return fit_parts(parts, extra=downstream_reserve())


def _child_levels_below(parent: str) -> int:
    """Child levels the sub-intent depth limit still allows below a child of ``parent`` (``--{n}-`` marks a level)."""
    return max(0, MAX_SUB_INTENT_DEPTH - len(re.findall(r"--\d+-", parent)) - 1)


def fit_child_intent_branch(parent: str, index: int, slug: str) -> FitResult:
    """Fit ``{parent}--{index}-{slug}``, leaving room below it for its plan and execution segment.

    Room is also kept for every child level the depth limit still allows, so a grandchild can be named; when that
    would leave no room even for a digest-only slug, only the child's own plan and execution segment are kept.
    """
    parts = [
        Part("parent", parent.removesuffix(INTENT_SUFFIX)),
        Part("index", f"--{index}-"),
        Part("child slug", slug, shortenable=True),
    ]
    try:
        return fit_parts(parts, extra=downstream_reserve() + _child_levels_below(parent) * _MIN_CHILD_LEVEL_LENGTH)
    except RefTooLongError:
        return fit_parts(parts, extra=downstream_reserve())


def ensure_chain_fits(intent_bare: str, agent: str, model: str) -> None:
    """Raise ``RefTooLongError`` unless a plan and execution below ``intent_bare`` fit with every label shortened."""
    seq, dis = 10 ** (_TIMESTAMP_LENGTH - 1), "0" * _DISAMBIGUATOR_LENGTH
    plan = fit_plan_id(intent_bare, seq, dis, agent, model)
    fit_execution_id(f"{intent_bare}/{plan.name}", seq, dis, agent, model)


def resolve_root_intent_branch(intent_data: dict[str, Any], slug: str, seq: int, agent: str, model: str) -> str:
    """Return the bare root intent branch, recording the slug used (and the original when shortened) in the payload.

    A recorded ``branch`` is used as it is. Either way the plan and execution branches that will nest below it, with
    the configured agent and model labels, are measured first, so an impossible name fails before any branch exists.
    """
    recorded = intent_data.get("branch")
    if recorded:
        branch = str(recorded).removesuffix(INTENT_SUFFIX)
        intent_data["slug"] = slug
    else:
        fitted = fit_root_intent_branch(seq, slug)
        branch = fitted.name
        intent_data["slug"] = branch.split("-", 2)[2]
        if "slug" in fitted.originals:
            intent_data["original_slug"] = fitted.originals["slug"]
    ensure_chain_fits(branch, str(agent or ""), str(model or ""))
    return branch


def calibration_branch(execution_branch: str) -> str:
    """Return ``{execution branch}/calibrated`` for a ``.../_`` execution branch."""
    return execution_branch.strip().removesuffix(INTENT_SUFFIX).rstrip("/") + CALIBRATION_SUFFIX


def original_labels(identifier: str, agent: str, model: str) -> dict[str, str]:
    """Ledger fields recording the unshortened labels, or nothing when ``identifier`` carries them as they are."""
    if identifier.endswith(f"-{agent}-{model}"):
        return {}
    return {"original_agent": agent, "original_model": model}


def is_over_limit_rejection(output: str) -> bool:
    """True when push output reports GitHub's ``ref too long`` rejection."""
    return "ref too long" in output.lower()


def named_push_rejection(branch: str, output: str) -> str:
    """Name a ref-length rejection (``ref too long: branch <name> (<n> characters)``); other output passes through."""
    if not is_over_limit_rejection(output) and len(REF_PREFIX) + len(branch) <= MAX_REF_LENGTH:
        return output
    return f"ref too long: branch {branch} ({len(REF_PREFIX) + len(branch)} characters); {output}"
