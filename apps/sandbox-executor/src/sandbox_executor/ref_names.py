"""Branch-name limit, measurement and deterministic shortening (pure: no I/O, no git).

GitHub rejects a push whose full ref name is longer than ``MAX_REF_LENGTH`` characters. Measured against the real
service: a full ref of 255 characters, counting the ``refs/heads/`` prefix, is accepted and 256 is rejected, so a branch
name without the prefix can be at most ``MAX_BRANCH_LENGTH`` (244) characters.

The calibration branch ends in ``/calibrated`` where every other branch ends in ``/_`` (9 characters more), so every
bare name (a name without its ``/_`` suffix) is measured as if it ended in ``/calibrated``: ``measured_length`` is the
length of ``refs/heads/{bare}/calibrated``. A name that fits is returned untouched. A longer one is shortened, in the
order the caller lists its parts, by replacing the tail of a part with a 6-character hexadecimal digest of the part's
original text, so two different long inputs never collide and the same input always gives the same name. Timestamps,
tickers, model codes and index markers are never part of a shortenable part. A name that cannot fit even with every
shortenable part at its minimum raises ``RefTooLongError``.

Only slugs are shortenable: plan and execution segments carry a three-letter runner ticker and a short model code
(``P-{epoch}-{ticker}-{model code}``), so they are never shortened. An intent branch is shortened only when it
exceeds the limit on its own or leaves no room for a plan and an execution segment below it;
``ensure_chain_fits`` checks the real labels at creation time.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Any

from sandbox_executor.agent_defaults import AGENT_TICKERS, normalise_agent_id
from sandbox_executor.plan_paths import model_code

MAX_REF_LENGTH = 255
REF_PREFIX = "refs/heads/"
INTENT_SUFFIX = "/_"
CALIBRATION_SUFFIX = "/calibrated"
MAX_BRANCH_LENGTH = MAX_REF_LENGTH - len(REF_PREFIX)
DIGEST_LENGTH = 6

#: Root and child intent slugs are at most this many characters; longer supplied slugs are rejected at intent
#: time and longer engine-derived slugs are cut at a word boundary.
MAX_SLUG_LENGTH = 32

_TIMESTAMP_LENGTH = 10
_TICKER_LENGTH = 3
#: Room reserved for a model code before the model is known; the longest current code (``G3.8FM``) is 6.
_MAX_MODEL_CODE_LENGTH = 12


class SlugTooLongError(ValueError):
    """A supplied root or child intent slug exceeds :data:`MAX_SLUG_LENGTH`."""

    def __init__(self, limit: int, length: int, slug: str) -> None:
        self.limit = limit
        self.length = length
        self.slug = slug
        super().__init__(f"slug too long: '{slug}' is {length} characters, over the limit of {limit}")


def validate_slug_length(slug: str) -> None:
    """Reject a supplied slug longer than :data:`MAX_SLUG_LENGTH`, naming the limit and the actual length.

    Raises:
        SlugTooLongError: If ``slug`` exceeds the limit.
    """
    if len(slug) > MAX_SLUG_LENGTH:
        raise SlugTooLongError(MAX_SLUG_LENGTH, len(slug), slug)


def truncate_slug(slug: str, limit: int = MAX_SLUG_LENGTH) -> str:
    """Cut an engine-derived slug to at most ``limit`` characters at a word boundary.

    The cut lands on the last ``-`` at or before ``limit``; a slug without one is hard-cut at ``limit``. The
    result is never empty for a non-empty input.
    """
    if len(slug) <= limit:
        return slug
    if slug[limit] == "-":
        return slug[:limit]
    head = slug[:limit]
    boundary = head.rfind("-")
    if boundary <= 0:
        return head
    return head[:boundary].rstrip("-") or head


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


def _segment_label_length(agent: str, model: str) -> int:
    """Length of the gen-3 ``{ticker}-{model code}`` label, from the real labels when known, else a bound."""
    code_length = len(model_code(model)) if model else _MAX_MODEL_CODE_LENGTH
    ticker_length = len(AGENT_TICKERS.get(normalise_agent_id(agent), "")) or _TICKER_LENGTH
    return ticker_length + 1 + code_length


def _segment_reserve(label_length: int) -> int:
    """Characters one gen-3 plan or execution segment adds below a parent: ``/P-{epoch}-{label}``."""
    return len("/P-") + _TIMESTAMP_LENGTH + 1 + label_length


def ensure_segment_fits(parent: str, segment: str, reserve_below: int = 0) -> None:
    """Raise :class:`RefTooLongError` when ``{parent}/{segment}`` cannot fit, with ``reserve_below`` to spare."""
    bare = f"{parent.removesuffix(INTENT_SUFFIX).rstrip('/')}/{segment}"
    length = measured_length(bare, extra=reserve_below)
    if length > MAX_REF_LENGTH:
        raise RefTooLongError(MAX_REF_LENGTH, length, "parent")


_MAX_SEGMENT_RESERVE = len("/P-") + _TIMESTAMP_LENGTH + 1 + _TICKER_LENGTH + 1 + _MAX_MODEL_CODE_LENGTH


def _fit_intent_parts(parts: list[Part]) -> FitResult:
    """Fit an intent name alone; shorten further only when no plan and execution could fit below the fitted name."""
    result = fit_parts(parts)
    try:
        ensure_chain_fits(result.name, "", "")
    except RefTooLongError:
        return fit_parts(parts, extra=2 * _MAX_SEGMENT_RESERVE)
    return result


def fit_root_intent_branch(seq: int, slug: str) -> FitResult:
    """Fit ``I-{seq}-{slug}``; a name that fits, with room for a plan and execution below it, is unchanged."""
    return _fit_intent_parts([Part("prefix", f"I-{seq}-"), Part("slug", slug, shortenable=True)])


def fit_child_intent_branch(parent: str, index: int, slug: str) -> FitResult:
    """Fit ``{parent}--{index}-{slug}``; a name that fits, with room for a plan and execution below it, is unchanged."""
    return _fit_intent_parts(
        [
            Part("parent", parent.removesuffix(INTENT_SUFFIX)),
            Part("index", f"--{index}-"),
            Part("child slug", slug, shortenable=True),
        ]
    )


def ensure_chain_fits(intent_bare: str, agent: str, model: str) -> None:
    """Raise ``RefTooLongError`` unless a gen-3 plan and execution segment fit below ``intent_bare``.

    The segments are measured with the configured runner's ticker and the model's code when both are known,
    otherwise with the reserved bounds, so an impossible intent name fails before any branch exists.
    """
    reserve = _segment_reserve(_segment_label_length(agent, model))
    length = measured_length(intent_bare, extra=2 * reserve)
    if length > MAX_REF_LENGTH:
        raise RefTooLongError(MAX_REF_LENGTH, length, "parent")


def resolve_root_intent_branch(intent_data: dict[str, Any], slug: str, seq: int, agent: str, model: str) -> str:
    """Return the bare root intent branch, recording the slug used (and the original when shortened) in the payload.

    A recorded ``branch`` is used as it is, but is still measured against the current labels, so a resume whose labels
    changed may fail here, before any branch exists. Either way the plan and execution branches that will nest below
    it, with the configured agent and model labels, are measured first, so an impossible name fails early.
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


def is_over_limit_rejection(output: str) -> bool:
    """True when push output reports GitHub's ``ref too long`` rejection."""
    return "ref too long" in output.lower()


def named_push_rejection(branch: str, output: str) -> str:
    """Name a ref-length rejection (``ref too long: branch <name> (<n> characters)``); other output passes through."""
    if not is_over_limit_rejection(output) and len(REF_PREFIX) + len(branch) <= MAX_REF_LENGTH:
        return output
    return f"ref too long: branch {branch} ({len(REF_PREFIX) + len(branch)} characters); {output}"
