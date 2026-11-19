"""Branch-name limit, measurement and deterministic slug shortening (pure: no I/O, no git).

GitHub rejects a push whose full ref name is longer than ``MAX_REF_LENGTH`` characters. Measured against the real
service: a full ref of 255 characters, counting the ``refs/heads/`` prefix, is accepted and 256 is rejected, so a branch
name without the prefix can be at most ``MAX_BRANCH_LENGTH`` (244) characters.

The calibration branch ends in ``/calibrated`` where every other branch ends in ``/_`` (9 characters more), so every
bare name (a name without its ``/_`` suffix) is measured as if it ended in ``/calibrated``: ``measured_length`` is the
length of ``refs/heads/{bare}/calibrated``. A slug that fits is returned untouched. A longer one is shortened, in the
order the caller lists its parts, by replacing the tail of a part with a 6-character hexadecimal digest of the part's
original text, so two different long inputs never collide and the same input always gives the same name. Timestamps
and index markers are never part of a shortenable part. A name that cannot fit even with every shortenable part at
its minimum raises ``RefTooLongError``.

Plan and execution segments carry the short ``{P|E}-{epoch}-{ticker}-{model code}`` names
(:mod:`sandbox_executor.naming`) and are never shortened: ``ensure_stage_segment_fits`` measures them against the
limit and fails instead. An intent branch is shortened only when it exceeds the limit on its own or leaves no room
for a plan and an execution segment below it; ``ensure_chain_fits`` checks the real ticker and model code at
creation time. A slug is at most ``MAX_SLUG_LENGTH`` characters: a supplied root slug over the limit is rejected
(:func:`validate_supplied_slug`) and an engine-derived slug is cut at a word boundary (:func:`truncate_derived_slug`).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Any

from sandbox_executor.naming import model_code, ticker_for

MAX_REF_LENGTH = 255
REF_PREFIX = "refs/heads/"
INTENT_SUFFIX = "/_"
CALIBRATION_SUFFIX = "/calibrated"
MAX_BRANCH_LENGTH = MAX_REF_LENGTH - len(REF_PREFIX)
DIGEST_LENGTH = 6

# A slug (root or child intent) is at most this many characters; see validate_supplied_slug and truncate_derived_slug.
MAX_SLUG_LENGTH = 32

_TIMESTAMP_LENGTH = 10
_TICKER_LENGTH = 3
# Room reserved for a model code when the real one is not known yet (the longest practical code is shorter).
_RESERVED_CODE_LENGTH = 12


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


def stage_segment_length(code_length: int) -> int:
    """Length of one ``/{P|E}-{epoch}-{ticker}-{model code}`` segment for a model code of ``code_length``."""
    return len("/P-") + _TIMESTAMP_LENGTH + 1 + _TICKER_LENGTH + 1 + code_length


_MIN_SEGMENT_LENGTH = stage_segment_length(_RESERVED_CODE_LENGTH)


def ensure_stage_segment_fits(parent: str, segment: str, extra: int = 0) -> None:
    """Raise ``RefTooLongError`` when ``{parent}/{segment}`` (plus ``extra`` nested characters) exceeds the limit.

    Plan and execution segments are never shortened: a name over the limit fails here with the parent named, and
    the fallback is the intent-level slug shortening applied when the intent was created.
    """
    bare = f"{parent.removesuffix(INTENT_SUFFIX).rstrip('/')}/{segment}"
    length = measured_length(bare, extra)
    if length > MAX_REF_LENGTH:
        raise RefTooLongError(MAX_REF_LENGTH, length, "parent")


def _fit_intent_parts(parts: list[Part]) -> FitResult:
    """Fit an intent name alone; shorten further only when no plan and execution could fit below the fitted name."""
    result = fit_parts(parts)
    if measured_length(result.name, 2 * _MIN_SEGMENT_LENGTH) > MAX_REF_LENGTH:
        return fit_parts(parts, extra=2 * _MIN_SEGMENT_LENGTH)
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
    """Raise unless a new-format plan and execution segment fit below ``intent_bare``.

    The real ticker and model code are measured when ``agent`` and ``model`` are given; an empty label falls back to
    the reserved lengths. Stage segments are never shortened, so an intent that leaves no room fails here.

    Raises:
        RefTooLongError: The chain cannot fit the ref limit.
        MissingTickerError: ``agent`` is non-empty and declares no ticker.
    """
    if str(agent or "").strip():
        ticker_for(agent)
    code_length = len(model_code(model)) if str(model or "").strip() else _RESERVED_CODE_LENGTH
    needed = 2 * stage_segment_length(max(code_length, 1))
    length = measured_length(intent_bare, needed)
    if length > MAX_REF_LENGTH:
        raise RefTooLongError(MAX_REF_LENGTH, length, "intent")


def resolve_root_intent_branch(intent_data: dict[str, Any], slug: str, seq: int, agent: str, model: str) -> str:
    """Return the bare root intent branch, recording the slug used (and the original when shortened) in the payload.

    A recorded ``branch`` is used as it is, but is still measured against the current labels, so a resume whose labels
    changed may fail here, before any branch exists. Either way the plan and execution branches that will nest below
    it, with the configured agent and model labels, are measured first, so an impossible name fails early.

    Raises:
        MissingTickerError: The resolved runner declares no ticker; raised before any branch is created.
    """
    if str(agent or "").strip():
        # A runner with no declared ticker may not name the plan and execution branches below this intent.
        ticker_for(agent)
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


class SlugTooLongError(ValueError):
    """A supplied root slug exceeds :data:`MAX_SLUG_LENGTH`; the message states the limit and the actual length."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        super().__init__(
            f"slug '{slug}' is {len(slug)} characters long; a slug is at most {MAX_SLUG_LENGTH} characters"
        )


def validate_supplied_slug(slug: str) -> str:
    """Return a slug supplied in an intent file, rejecting one over :data:`MAX_SLUG_LENGTH` characters.

    Raises:
        SlugTooLongError: The slug is over the limit; raised when the intent is opened, before any branch exists.
    """
    if len(slug) > MAX_SLUG_LENGTH:
        raise SlugTooLongError(slug)
    return slug


def truncate_derived_slug(slug: str) -> str:
    """Cut an engine-derived slug (from a description or sub-intent title) to the limit at a word boundary."""
    if len(slug) <= MAX_SLUG_LENGTH:
        return slug
    head = slug[: MAX_SLUG_LENGTH + 1]
    cut = head[:MAX_SLUG_LENGTH] if "-" not in head[1:] else head[: head.rindex("-")]
    return cut[:MAX_SLUG_LENGTH].rstrip("-") or slug[:MAX_SLUG_LENGTH]


def is_over_limit_rejection(output: str) -> bool:
    """True when push output reports GitHub's ``ref too long`` rejection."""
    return "ref too long" in output.lower()


def named_push_rejection(branch: str, output: str) -> str:
    """Name a ref-length rejection (``ref too long: branch <name> (<n> characters)``); other output passes through."""
    if not is_over_limit_rejection(output) and len(REF_PREFIX) + len(branch) <= MAX_REF_LENGTH:
        return output
    return f"ref too long: branch {branch} ({len(REF_PREFIX) + len(branch)} characters); {output}"
