"""Short stage names (generation 3), the three-generation ID grammar and nested plan-file paths.

A new plan or execution ID is ``P-{epoch}-{ticker}-{model code}`` / ``E-{epoch}-{ticker}-{model code}``: the ticker is
the runner's three-uppercase-letter registration (:data:`sandbox_executor.agent_defaults.AGENT_TICKERS`) and the model
code is derived from the model name by :func:`model_code`, with no lookup table. The hex disambiguator of the previous
generation is gone because a plan file is no longer a flat key: it lives at ``plans/{intent}/{plan id}.md`` beside its
calibration report, mirroring the full branch nesting (child intents and plans nested under plans included).

Both older generations stay valid and parseable (:func:`parse_stage_id`): ``X-{epoch}-{agent}-{model}`` (generation 1)
and ``X-{epoch}-{hex}-{agent}-{model}`` (generation 2). No existing ID is ever renamed, because existing IDs match the
head branch names of the pull requests that carried them.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from sandbox_executor.agent_defaults import ticker_for

PLANS_DIR = "plans"
LEGACY_PLANS_SUBDIR = "legacy"
CALIBRATION_FILE_SUFFIX = "_calibration.md"

_REF_COMPONENT_UNSAFE = re.compile(r"[^A-Za-z0-9.]")
_STAGE_ID_PATTERN = re.compile(r"(?P<kind>[PE])-(?P<epoch>\d+)-(?P<rest>.+)\Z")
_GEN2_DISAMBIGUATOR = re.compile(r"[0-9a-f]{8}\Z")
_GEN3_TICKER = re.compile(r"[A-Z]{3}\Z")


def _model_words(model: str) -> list[str]:
    """Split a model name into its hyphen-separated words, dropping anything before a ``/``."""
    tail = model.rsplit("/", 1)[-1]
    return [word for word in tail.split("-") if word]


def model_code(model: str) -> str:
    """Derive the short model code used in stage names, with no lookup table.

    The first letter of each word upper-cased; a word that begins with a digit is a version part, kept whole, with
    consecutive version parts joined by a dot; anything before a ``/`` is dropped; every character invalid in a git
    ref component is removed. For example ``claude-sonnet-5-5`` gives ``CS5.5`` and ``gemini-3.8-flash-medium``
    gives ``G3.8FM``.

    Raises:
        ValueError: If no usable character survives (the model name has no letters or digits).
    """
    pieces: list[str] = []
    previous_numeric = False
    for word in _model_words(model):
        cleaned = _REF_COMPONENT_UNSAFE.sub("", word).strip(".")
        if not cleaned:
            continue
        if cleaned[0].isdigit():
            pieces.append(("." if previous_numeric else "") + cleaned)
            previous_numeric = True
        else:
            pieces.append(cleaned[0].upper())
            previous_numeric = False
    code = re.sub(r"\.{2,}", ".", "".join(pieces)).strip(".")
    if not code:
        raise ValueError(f"model name '{model}' yields no usable model-code character")
    return code


def stage_label(agent: str, model: str) -> str:
    """Return the ``{ticker}-{model code}`` label that names a runner and model in generation-3 stage IDs.

    Raises:
        MissingAgentTickerError: If the runner declares no ticker.
        ValueError: If the model name yields no model code.
    """
    return f"{ticker_for(agent)}-{model_code(model)}"


@dataclass(frozen=True)
class StageIdParts:
    """A parsed plan or execution ID; ``generation`` says which grammar matched, so callers never re-guess.

    ``label`` is everything after the epoch: ``{agent}-{model}`` (generation 1), ``{hex}-{agent}-{model}``
    (generation 2) or ``{ticker}-{model code}`` (generation 3).
    """

    kind: str
    epoch: int
    generation: int
    label: str
    ticker: str | None = None
    model_code: str | None = None
    disambiguator: str | None = None


def parse_stage_id(identifier: str) -> StageIdParts:
    """Parse a plan or execution ID of any of the three generations.

    Disambiguation on the field after the epoch: an 8-character lowercase-hex field marks generation 2, a
    three-uppercase-letter field (with a model code after it) marks generation 3, anything else is generation 1.

    Raises:
        ValueError: If ``identifier`` is not a ``P-`` or ``E-`` stage ID.
    """
    match = _STAGE_ID_PATTERN.fullmatch(identifier.strip())
    if match is None:
        raise ValueError(f"not a plan or execution ID: '{identifier}'")
    kind, epoch, rest = match.group("kind"), int(match.group("epoch")), match.group("rest")
    head, _, tail = rest.partition("-")
    if _GEN2_DISAMBIGUATOR.fullmatch(head) and tail:
        return StageIdParts(kind, epoch, 2, rest, disambiguator=head)
    if _GEN3_TICKER.fullmatch(head) and tail:
        return StageIdParts(kind, epoch, 3, rest, ticker=head, model_code=tail)
    return StageIdParts(kind, epoch, 1, rest)


def build_stage_name(kind: str, epoch: int, agent: str, model: str) -> str:
    """Compose a generation-3 stage name ``{kind}-{epoch}-{ticker}-{model code}`` (no hex)."""
    return f"{kind}-{epoch}-{stage_label(agent, model)}"


def branch_dir(branch: str) -> str:
    """Normalise a branch reference to its bare directory form: no ref prefix, no ``/_`` suffix."""
    clean = branch.strip()
    for prefix in ("refs/remotes/origin/", "refs/heads/", "origin/"):
        clean = clean.removeprefix(prefix)
    return clean.removesuffix("/_").strip("/")


def plan_file_rel(parent_branch: str, plan_id: str) -> str:
    """Repo-relative path of a plan file: ``plans/{parent branch}/{plan id}.md``, mirroring the branch nesting."""
    return f"{PLANS_DIR}/{branch_dir(parent_branch)}/{plan_id}.md"


def calibration_report_rel(parent_branch: str, plan_id: str) -> str:
    """Repo-relative path of a plan's calibration report, beside the plan file."""
    return f"{PLANS_DIR}/{branch_dir(parent_branch)}/{plan_id}{CALIBRATION_FILE_SUFFIX}"


def legacy_plan_file_rel(plan_id: str) -> str:
    """The flat pre-migration plan-file path, kept readable for old-generation plans."""
    return f"{PLANS_DIR}/{plan_id}.md"


def legacy_calibration_report_rel(plan_id: str) -> str:
    """The flat pre-migration calibration-report path, kept readable for old-generation plans."""
    return f"{PLANS_DIR}/{plan_id}{CALIBRATION_FILE_SUFFIX}"


def plan_parent_dir(plan_branch: str) -> str:
    """The parent branch directory of a plan branch (everything above the plan's own segment), or ``""``."""
    clean = branch_dir(plan_branch)
    return clean.rsplit("/", 1)[0] if "/" in clean else ""


def _existing_rel(target_worktree: str, candidates: list[str]) -> str | None:
    for rel in candidates:
        if os.path.exists(os.path.join(target_worktree, rel)):
            return rel
    return None


def find_plan_file(target_worktree: str, plan_id: str, parent_branch: str = "") -> str | None:
    """Locate a plan file by (parent branch, plan ID): the nested path first, then the flat old-generation path."""
    candidates = [plan_file_rel(parent_branch, plan_id)] if branch_dir(parent_branch) else []
    candidates.append(legacy_plan_file_rel(plan_id))
    return _existing_rel(target_worktree, candidates)


def find_calibration_report(target_worktree: str, plan_id: str, parent_branch: str = "") -> str | None:
    """Locate a calibration report by (parent branch, plan ID): nested first, then the flat old-generation path."""
    candidates = [calibration_report_rel(parent_branch, plan_id)] if branch_dir(parent_branch) else []
    candidates.append(legacy_calibration_report_rel(plan_id))
    return _existing_rel(target_worktree, candidates)


def resolve_calibration_report_rel(target_worktree: str, plan_branch: str, plan_id: str) -> str:
    """The path a calibration report should be written to: beside the plan file it calibrates.

    Prefers the directory of an existing plan file (nested or flat), then the nested path mirroring the plan branch,
    and falls back to the flat old-generation path only when no parent branch is known.
    """
    parent = plan_parent_dir(plan_branch)
    plan_rel = find_plan_file(target_worktree, plan_id, parent)
    if plan_rel is not None:
        return plan_rel.removesuffix(".md") + CALIBRATION_FILE_SUFFIX
    if parent:
        return calibration_report_rel(parent, plan_id)
    return legacy_calibration_report_rel(plan_id)
