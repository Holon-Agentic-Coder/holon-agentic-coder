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

An intent branch is shortened only when it exceeds the limit on its own or leaves no room for a plan and an execution
segment below it; ``ensure_chain_fits`` measures a generation 3 segment (``/P-{epoch}-{ticker}-{model code}``, see
``sandbox_executor.plan_paths``) with the model code at its documented bound. Slugs are capped at
``MAX_SLUG_LENGTH`` (32) characters: a supplied root slug over the cap is rejected (``SlugTooLongError``), an
engine-derived slug is cut at a word boundary (``truncate_slug``). With those names a root intent and two levels of
nested child intents, each with a 32-character slug, fit the ref limit with no shortening; deeper nesting falls back
to the slug shortening below.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Any

MAX_REF_LENGTH = 255
REF_PREFIX = "refs/heads/"
INTENT_SUFFIX = "/_"
CALIBRATION_SUFFIX = "/calibrated"
MAX_BRANCH_LENGTH = MAX_REF_LENGTH - len(REF_PREFIX)
DIGEST_LENGTH = 6
MAX_SLUG_LENGTH = 32

_TIMESTAMP_LENGTH = 10
_TICKER_LENGTH = 3
# Upper bound on a model code's length for room-below measurements (plan_paths.MODEL_CODE_BOUND; the real codes are
# far shorter, e.g. ``CS5.5``). plan_paths is not imported here to keep this module free of the ticker registry.
_MODEL_CODE_BOUND = 12


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


# A generation 3 stage segment at its longest: ``/P-{epoch}-{ticker}-{model code}`` with the code at its bound.
MAX_STAGE_SEGMENT_LENGTH = len("/P-") + _TIMESTAMP_LENGTH + 1 + _TICKER_LENGTH + 1 + _MODEL_CODE_BOUND


class SlugTooLongError(ValueError):
    """A supplied root or child intent slug exceeds :data:`MAX_SLUG_LENGTH` characters."""

    def __init__(self, slug: str, length: int | None = None) -> None:
        self.slug = slug
        self.limit = MAX_SLUG_LENGTH
        self.length = len(slug) if length is None else length
        super().__init__(
            f"slug too long: '{slug}' is {self.length} characters, over the limit of {self.limit}; "
            f"supply a slug of at most {self.limit} characters"
        )


def validate_slug_length(slug: str) -> str:
    """Return ``slug`` unchanged, raising :class:`SlugTooLongError` when it exceeds the 32-character limit."""
    if len(slug) > MAX_SLUG_LENGTH:
        raise SlugTooLongError(slug)
    return slug


def truncate_slug(slug: str, limit: int = MAX_SLUG_LENGTH) -> str:
    """Cut an engine-derived slug to at most ``limit`` characters at a word boundary.

    The cut lands on the last ``-`` at or before ``limit``; with no boundary the slug is hard-cut at ``limit``. The
    result is never empty for a non-empty input.
    """
    if len(slug) <= limit:
        return slug
    cut = slug[:limit]
    if slug[limit] == "-":
        # The cut already lands on a boundary: the limit-th character starts a new word.
        return cut.strip("-") or cut
    head, boundary, _ = cut.rpartition("-")
    truncated = head if boundary and head.strip("-") else cut
    return truncated.strip("-") or cut.strip("-") or cut


def _fit_intent_parts(parts: list[Part]) -> FitResult:
    """Fit an intent name alone; shorten further only when no plan and execution could fit below the fitted name."""
    result = fit_parts(parts)
    try:
        ensure_chain_fits(result.name)
    except RefTooLongError:
        return fit_parts(parts, extra=2 * MAX_STAGE_SEGMENT_LENGTH)
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


def ensure_chain_fits(intent_bare: str) -> None:
    """Raise ``RefTooLongError`` unless a generation 3 plan and execution segment fit below ``intent_bare``."""
    length = measured_length(intent_bare, extra=2 * MAX_STAGE_SEGMENT_LENGTH)
    if length > MAX_REF_LENGTH:
        raise RefTooLongError(MAX_REF_LENGTH, length, "parent")


def resolve_root_intent_branch(intent_data: dict[str, Any], slug: str, seq: int) -> str:
    """Return the bare root intent branch, recording the slug used (and the original when shortened) in the payload.

    A supplied slug over :data:`MAX_SLUG_LENGTH` characters is rejected here, before any branch exists; derive and
    truncate engine slugs with :func:`truncate_slug` before calling. A recorded ``branch`` is used as it is, but is
    still measured, so the plan and execution branches that will nest below it fit; an impossible name fails early.
    """
    recorded = intent_data.get("branch")
    if recorded:
        branch = str(recorded).removesuffix(INTENT_SUFFIX)
        intent_data["slug"] = slug
    else:
        validate_slug_length(slug)
        fitted = fit_root_intent_branch(seq, slug)
        branch = fitted.name
        intent_data["slug"] = branch.split("-", 2)[2]
        if "slug" in fitted.originals:
            intent_data["original_slug"] = fitted.originals["slug"]
    ensure_chain_fits(branch)
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
