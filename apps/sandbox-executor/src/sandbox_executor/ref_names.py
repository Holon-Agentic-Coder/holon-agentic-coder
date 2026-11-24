from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from typing import Any

from sandbox_executor.naming import model_code, ticker_for

MAX_REF_LENGTH = 255
REF_PREFIX = "refs/heads/"
INTENT_SUFFIX = "/_"
CALIBRATION_SUFFIX = "/calibrated"
MAX_BRANCH_LENGTH = MAX_REF_LENGTH - len(REF_PREFIX)
DIGEST_LENGTH = 6
MAX_SLUG_LENGTH = 32

_TIMESTAMP_LENGTH = 10
_TICKER_LENGTH = 3
_RESERVED_CODE_LENGTH = 12
_STAGE_SEGMENTS_BELOW_INTENT = 2
_REF_TOO_LONG_MARKER = "ref too long"


class RefTooLongError(ValueError):
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
    key: str
    text: str
    shortenable: bool = False
    weight: int = 1


@dataclass(frozen=True)
class FitResult:
    name: str
    originals: dict[str, str] = field(default_factory=dict)


def measured_length(bare_name: str, extra: int = 0) -> int:
    return len(REF_PREFIX) + len(bare_name) + len(CALIBRATION_SUFFIX) + extra


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


def shorten_text(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    keep = max_length - DIGEST_LENGTH - 1
    head = text[:keep].rstrip("-") if keep > 0 else ""
    return f"{head}-{digest(text)}" if head else digest(text)


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def _total_length(parts: list[Part], limit_extra: int) -> int:
    rendered = sum(len(p.text) * p.weight for p in parts)
    return len(REF_PREFIX) + rendered + len(CALIBRATION_SUFFIX) + limit_extra


def _shorten_part(part: Part, excess: int) -> Part:
    cut = _ceil_div(excess, part.weight)
    target = max(DIGEST_LENGTH, len(part.text) - cut)
    shorter = shorten_text(part.text, target)
    return part if len(shorter) >= len(part.text) else replace(part, text=shorter)


def _unshortenable_segment(parts: list[Part]) -> str:
    fixed = [p for p in parts if not p.shortenable or len(p.text) <= DIGEST_LENGTH]
    return max(fixed or parts, key=lambda p: len(p.text) * p.weight).key


def fit_parts(parts: list[Part], extra: int = 0, limit: int = MAX_REF_LENGTH) -> FitResult:
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
    return len("/P-") + _TIMESTAMP_LENGTH + 1 + _TICKER_LENGTH + 1 + code_length


_MIN_SEGMENT_LENGTH = stage_segment_length(_RESERVED_CODE_LENGTH)


def ensure_stage_segment_fits(parent: str, segment: str, extra: int = 0) -> None:
    bare = f"{parent.removesuffix(INTENT_SUFFIX).rstrip('/')}/{segment}"
    length = measured_length(bare, extra)
    if length > MAX_REF_LENGTH:
        raise RefTooLongError(MAX_REF_LENGTH, length, "parent")


def _fit_intent_parts(parts: list[Part]) -> FitResult:
    result = fit_parts(parts)
    reserved_below_intent = _STAGE_SEGMENTS_BELOW_INTENT * _MIN_SEGMENT_LENGTH
    if measured_length(result.name, reserved_below_intent) > MAX_REF_LENGTH:
        return fit_parts(parts, extra=reserved_below_intent)
    return result


def fit_root_intent_branch(seq: int, slug: str) -> FitResult:
    return _fit_intent_parts([Part("prefix", f"I-{seq}-"), Part("slug", slug, shortenable=True)])


def fit_child_intent_branch(parent: str, index: int, slug: str) -> FitResult:
    return _fit_intent_parts(
        [
            Part("parent", parent.removesuffix(INTENT_SUFFIX)),
            Part("index", f"--{index}-"),
            Part("child slug", slug, shortenable=True),
        ]
    )


def ensure_chain_fits(intent_bare: str, agent: str, model: str) -> None:
    if str(agent or "").strip():
        ticker_for(agent)
    code_length = len(model_code(model)) if str(model or "").strip() else _RESERVED_CODE_LENGTH
    needed = _STAGE_SEGMENTS_BELOW_INTENT * stage_segment_length(max(code_length, 1))
    length = measured_length(intent_bare, needed)
    if length > MAX_REF_LENGTH:
        raise RefTooLongError(MAX_REF_LENGTH, length, "intent")


def resolve_root_intent_branch(intent_data: dict[str, Any], slug: str, seq: int, agent: str, model: str) -> str:
    if str(agent or "").strip():
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
    return execution_branch.strip().removesuffix(INTENT_SUFFIX).rstrip("/") + CALIBRATION_SUFFIX


class SlugTooLongError(ValueError):
    def __init__(self, slug: str) -> None:
        self.slug = slug
        super().__init__(
            f"slug '{slug}' is {len(slug)} characters long; a slug is at most {MAX_SLUG_LENGTH} characters"
        )


def validate_supplied_slug(slug: str) -> str:
    if len(slug) > MAX_SLUG_LENGTH:
        raise SlugTooLongError(slug)
    return slug


def truncate_derived_slug(slug: str, limit: int = MAX_SLUG_LENGTH) -> str:
    if len(slug) <= limit:
        return slug
    head = slug[: limit + 1]
    cut = head[:limit] if "-" not in head[1:] else head[: head.rindex("-")]
    return cut[:limit].rstrip("-") or slug[:limit]


def _sanitise_slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()


def root_intent_slug(intent_data: dict[str, Any], derive_from: str) -> str:
    supplied = str(intent_data.get("slug") or "").strip()
    if not supplied:
        return truncate_derived_slug(_sanitise_slug(derive_from))
    if not intent_data.get("branch"):
        validate_supplied_slug(supplied)
    return _sanitise_slug(supplied)


def is_over_limit_rejection(output: str) -> bool:
    return _REF_TOO_LONG_MARKER in output.lower()


def named_push_rejection(branch: str, output: str) -> str:
    if not is_over_limit_rejection(output) and len(REF_PREFIX) + len(branch) <= MAX_REF_LENGTH:
        return output
    return f"{_REF_TOO_LONG_MARKER}: branch {branch} ({len(REF_PREFIX) + len(branch)} characters); {output}"
