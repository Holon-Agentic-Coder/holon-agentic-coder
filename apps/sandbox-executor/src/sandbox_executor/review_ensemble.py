"""Independent reviewer ensemble for the Stage 4 review loop, with a strict report trailer and a unanimity consensus.

This module runs ``N`` independent reviewer agent turns against one diff, parses each reviewer's report and decides
the ensemble verdict. ``run_review_loop()`` calls :func:`run_ensemble` and :func:`decide_consensus` once per iteration,
with a request built by :func:`build_review_request`, and ``review_report.py`` renders the posted report from the
results; the loop refuses to run reviewers outside a sandbox.

Reviewer turns
    Each reviewer gets a fresh scratch directory ``.holon/review/iter-<n>/reviewer-<i>/`` (``.holon/`` is git-ignored)
    and a prompt built from ``holon-config/prompts/review_loop.template.md`` that holds only the diff, the PR
    description, the exact report path and the trailer contract. Reviewers run sequentially; before the next one
    starts, the previous report has been read into memory and its scratch directory removed, so no reviewer can see
    another reviewer's output. The turn is supervised by :func:`~sandbox_executor.turns.supervise_authoring_turn`, the
    same supervision the planner uses, with :data:`REVIEW_REPORT_SPEC` as the artefact spec and ``modify_code=False``;
    it needed no change to ``turns.py``. A runner exception (including ``SystemExit`` from an unknown agent or missing
    credentials), a budget breach on any turn, a missing report or an invalid trailer makes the reviewer
    FAILED with a redacted reason. A FAILED reviewer carries no verdict and is never counted as a vote. The runner's
    exit code is not judged: the report on disk is the deliverable, as it is for plans.

Trailer contract
    A report ends with a block whose first line is ``<!-- holon-review-trailer -->`` and whose last line, the final
    line of the report, is ``<!-- /holon-review-trailer -->``. Between them sits one JSON object with exactly the keys
    ``verdict`` (``APPROVED`` or ``CHANGES_REQUESTED``), ``critical``, ``important``, ``nit`` (non-negative integers)
    and ``findings`` (objects with exactly ``severity``, ``file``, ``line`` and ``summary``). Counts must equal the
    findings tally per severity, and ``APPROVED`` with any non-zero count is rejected, so an inconsistent report fails
    closed. A key repeated in any object is rejected rather than resolved to its last value.
    :func:`render_review_trailer` is the single definition of the canonical block.

Consensus
    :func:`decide_consensus` approves only when every configured reviewer completed with ``APPROVED`` and zero
    findings of any severity, and mergeability and CI are both known to be clean. There is no majority vote, and zero
    completed reviewers can never approve.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sandbox_executor.agent_runner import AgentRunner, get_runner
from sandbox_executor.artefacts import ArtefactSection, ArtefactSpec
from sandbox_executor.budgets import Budgets, StageTimer, load_budgets
from sandbox_executor.git import EMPTY_DIFF_EXCLUDES, run_git
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.review_target import ReviewTarget
from sandbox_executor.scaffold import REVIEW_LOOP_TEMPLATE
from sandbox_executor.turns import AuthoringOutcome, TurnRunner, run_turn, supervise_authoring_turn

__all__ = [
    "DEFAULT_ENSEMBLE_SIZE",
    "ENSEMBLE_SIZE_HELP",
    "REVIEW_REPORT_SPEC",
    "REVIEW_STAGE",
    "SEVERITIES",
    "TRAILER_CLOSE",
    "TRAILER_OPEN",
    "ConsensusVerdict",
    "ParsedTrailer",
    "ReviewCounts",
    "ReviewFinding",
    "ReviewRequest",
    "ReviewerResult",
    "ReviewerStatus",
    "ReviewerVerdict",
    "TrailerError",
    "build_review_request",
    "build_reviewer_prompt",
    "decide_consensus",
    "parse_review_trailer",
    "render_review_trailer",
    "reviewer_report_rel",
    "run_ensemble",
    "run_reviewer",
]

logger = logging.getLogger(__name__)

DEFAULT_ENSEMBLE_SIZE = 3
# Help text for every ``--ensemble-size`` option (``holon flow``, ``holon review-loop`` and the container entrypoint).
ENSEMBLE_SIZE_HELP = (
    "Number of independent reviewer agent turns per iteration; approval needs every reviewer to approve with zero "
    "findings (default: 3)"
)
# Budget stage for reviewer turns; ``review`` resolves to the ``review_iteration`` ceiling in ``budgets.py``. One
# ensemble is one review iteration, so all of its reviewers share a single stage timer.
REVIEW_STAGE = "review"
SEVERITIES = ("critical", "important", "nit")
TRAILER_OPEN = "<!-- holon-review-trailer -->"
TRAILER_CLOSE = "<!-- /holon-review-trailer -->"
TEMPLATE_REL = "holon-config/prompts/review_loop.template.md"
REVIEW_ROOT_REL = ".holon/review"
REPORT_NAME = "report.md"
MAX_REASON_CHARS = 500

_TRAILER_KEYS = frozenset({"verdict", *SEVERITIES, "findings"})
_FINDING_KEYS = frozenset({"severity", "file", "line", "summary"})
# Reviewer turns are single passes, so the loop-level placeholders get neutral text.
_SINGLE_PASS = "n/a (single reviewer turn)"
_PLACEHOLDER_NAMES = (
    "repo_dir",
    "target",
    "target_branch",
    "ensemble_size",
    "max_iterations",
    "iteration",
    "pr_title",
    "pr_description",
    "diff",
    "sha",
)
# One pass over the template, so text substituted in (a diff that contains ``{diff}``) is never expanded again.
_PLACEHOLDER = re.compile(r"\{(" + "|".join(_PLACEHOLDER_NAMES) + r")\}")

REVIEW_REPORT_SPEC = ArtefactSpec(
    name="review report",
    sections=(
        ArtefactSection(
            f"the closing review trailer block (`{TRAILER_OPEN}` ... `{TRAILER_CLOSE}`) as the final lines",
            r"^<!--\s*/holon-review-trailer\s*-->$",
        ),
    ),
    size_floor=80,
)


class ReviewerVerdict(StrEnum):
    """Verdict a completed reviewer reports in its trailer."""

    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"


class ReviewerStatus(StrEnum):
    """Whether a reviewer turn produced a valid report."""

    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ConsensusVerdict(StrEnum):
    """Ensemble verdict returned by :func:`decide_consensus`."""

    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    FAILED = "FAILED"


class TrailerError(ValueError):
    """A review report has no valid trailer block; the reviewer is treated as FAILED."""


@dataclass(frozen=True)
class ReviewFinding:
    """One finding from a reviewer's trailer; ``path`` is the trailer's ``file`` key."""

    severity: str
    path: str
    line: int | None
    summary: str


@dataclass(frozen=True)
class ReviewCounts:
    """Findings per severity as declared in a trailer."""

    critical: int = 0
    important: int = 0
    nit: int = 0

    @property
    def total(self) -> int:
        """Findings of every severity."""
        return self.critical + self.important + self.nit


@dataclass(frozen=True)
class ParsedTrailer:
    """A validated trailer block."""

    verdict: ReviewerVerdict
    counts: ReviewCounts
    findings: tuple[ReviewFinding, ...] = ()


@dataclass(frozen=True)
class ReviewRequest:
    """What every reviewer is asked to judge; nothing in it comes from another reviewer."""

    repo_dir: str
    head_sha: str
    base_ref: str
    pr_title: str
    pr_body: str
    diff_text: str
    iteration: int


def build_review_request(
    target: ReviewTarget, base_ref: str, iteration: int, pr_title: str, pr_body: str, repo_dir: str
) -> ReviewRequest:
    """Build the request every reviewer judges: the real diff between ``base_ref`` and the resolved head SHA.

    The diff excludes the ledgers, plans and execution notes, as the empty-diff guard does. PR title and body come
    from the resolved target when it carries them, else from the caller.

    Raises:
        RuntimeError: If the head SHA is missing or ``git diff`` fails or times out (the iteration then fails closed).
    """
    if not target.head_sha:
        raise RuntimeError(f"'{target.head_branch}' has no resolved head commit to review")
    pathspec = ["--", ".", *(f":(exclude){prefix}" for prefix in EMPTY_DIFF_EXCLUDES)]
    try:
        proc = run_git(["diff", f"{base_ref}...{target.head_sha}", *pathspec], cwd=repo_dir, check=True, timeout=120)
    except TimeoutError as exc:
        raise RuntimeError(f"git diff timed out: {redact_agent_secrets(str(exc))}") from exc
    return ReviewRequest(
        repo_dir=repo_dir,
        head_sha=target.head_sha,
        base_ref=base_ref,
        pr_title=target.title or pr_title,
        pr_body=target.body or pr_body,
        diff_text=proc.stdout or "",
        iteration=iteration,
    )


@dataclass(frozen=True)
class ReviewerResult:
    """Outcome of one reviewer turn. Build it with :meth:`completed` or :meth:`failed`.

    ``report_path`` is repository-relative and the file is removed once the ensemble moves on; ``report_text`` keeps the
    report in memory. A FAILED result never carries a verdict.
    """

    index: int
    agent: str
    model: str
    status: ReviewerStatus
    verdict: ReviewerVerdict | None
    counts: ReviewCounts
    findings: tuple[ReviewFinding, ...]
    report_path: str
    report_text: str | None = None
    failure_reason: str | None = None

    @classmethod
    def completed(
        cls, index: int, agent: str, model: str, report_path: str, report_text: str, parsed: ParsedTrailer
    ) -> ReviewerResult:
        """A reviewer whose report carried a valid trailer."""
        return cls(
            index,
            agent,
            model,
            ReviewerStatus.COMPLETED,
            parsed.verdict,
            parsed.counts,
            parsed.findings,
            report_path,
            report_text,
        )

    @classmethod
    def failed(cls, index: int, agent: str, model: str, report_path: str, reason: str) -> ReviewerResult:
        """A reviewer that produced no usable report; ``reason`` is redacted and bounded here."""
        return cls(
            index,
            agent,
            model,
            ReviewerStatus.FAILED,
            None,
            ReviewCounts(),
            (),
            report_path,
            failure_reason=_bounded_reason(reason),
        )


def _bounded_reason(reason: str) -> str:
    """Redact secrets from a failure reason and cap its length."""
    text = redact_agent_secrets(reason) or "unknown failure"
    return text if len(text) <= MAX_REASON_CHARS else text[: MAX_REASON_CHARS - 3] + "..."


# --- Trailer -------------------------------------------------------------------------------------------------------


def render_review_trailer(parsed: ParsedTrailer) -> str:
    """The canonical trailer block for ``parsed``; :func:`parse_review_trailer` accepts it unchanged."""
    payload = {
        "verdict": str(parsed.verdict),
        "critical": parsed.counts.critical,
        "important": parsed.counts.important,
        "nit": parsed.counts.nit,
        "findings": [
            {"severity": item.severity, "file": item.path, "line": item.line, "summary": item.summary}
            for item in parsed.findings
        ],
    }
    return f"{TRAILER_OPEN}\n{json.dumps(payload, indent=2, ensure_ascii=False)}\n{TRAILER_CLOSE}"


def parse_review_trailer(text: str) -> ParsedTrailer:
    """Parse the trailer block that must end ``text``; raise :class:`TrailerError` on any ambiguity."""
    obj = _load_trailer_object(_extract_trailer_block(text))
    verdict = _parse_verdict(obj["verdict"])
    counts = _parse_counts(obj)
    findings = _parse_findings(obj["findings"])
    _check_consistency(verdict, counts, findings)
    return ParsedTrailer(verdict=verdict, counts=counts, findings=findings)


def _extract_trailer_block(text: str) -> str:
    """Text between the last opening marker and the closing marker on the report's final non-empty line."""
    lines = [line.strip() for line in (text or "").rstrip().splitlines()]
    if not lines or lines[-1] != TRAILER_CLOSE:
        raise TrailerError("the report does not end with the closing trailer marker")
    openings = [index for index, line in enumerate(lines) if line == TRAILER_OPEN]
    if not openings:
        raise TrailerError("the report has no opening trailer marker")
    inner = lines[openings[-1] + 1 : -1]
    if TRAILER_CLOSE in inner:
        raise TrailerError("the trailer block contains more than one closing marker")
    return _strip_json_fence("\n".join(inner).strip())


def _strip_json_fence(inner: str) -> str:
    """Tolerate one ```json fence around the trailer object."""
    lines = inner.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1] == "```":
        return "\n".join(lines[1:-1])
    return inner


def _load_trailer_object(inner: str) -> dict[str, Any]:
    """Decode the trailer JSON and require exactly the trailer keys."""
    try:
        obj = json.loads(inner, object_pairs_hook=_reject_repeated_keys)
    except TrailerError:
        raise
    except json.JSONDecodeError as error:
        raise TrailerError(f"the trailer is not valid JSON: {error.msg}") from error
    except (ValueError, RecursionError) as error:  # an oversized integer or a too-deeply nested value
        raise TrailerError(f"the trailer cannot be decoded ({type(error).__name__})") from error
    if not isinstance(obj, dict):
        raise TrailerError("the trailer must be one JSON object")
    if set(obj) != _TRAILER_KEYS:
        missing = sorted(_TRAILER_KEYS - set(obj))
        unknown = sorted(set(obj) - _TRAILER_KEYS)
        raise TrailerError(f"trailer keys are wrong (missing: {missing}, unknown: {unknown})")
    return obj


def _reject_repeated_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one decoded JSON object, refusing a repeated key (plain decoding silently keeps the last value)."""
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise TrailerError(f"the trailer repeats the key {key!r}")
        obj[key] = value
    return obj


def _parse_verdict(value: Any) -> ReviewerVerdict:
    """The trailer verdict; anything but the two known strings is rejected."""
    if not isinstance(value, str) or value not in tuple(ReviewerVerdict):
        raise TrailerError(f"unknown verdict {value!r}")
    return ReviewerVerdict(value)


def _is_count(value: Any) -> bool:
    """True for a non-negative int that is not a bool (``True`` is an ``int`` subclass)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _parse_counts(obj: Mapping[str, Any]) -> ReviewCounts:
    """Per-severity counts; each must be a non-negative integer."""
    for name in SEVERITIES:
        if not _is_count(obj[name]):
            raise TrailerError(f"count {name!r} must be a non-negative integer, got {obj[name]!r}")
    return ReviewCounts(critical=obj["critical"], important=obj["important"], nit=obj["nit"])


def _parse_findings(raw: Any) -> tuple[ReviewFinding, ...]:
    """The findings list; each entry must be a well-formed finding object."""
    if not isinstance(raw, list):
        raise TrailerError("findings must be a list")
    return tuple(_parse_finding(item) for item in raw)


def _parse_finding(item: Any) -> ReviewFinding:
    """One finding object with a known severity, a file, an optional line and a summary."""
    if not isinstance(item, dict) or set(item) != _FINDING_KEYS:
        raise TrailerError(f"each finding must be an object with exactly the keys {sorted(_FINDING_KEYS)}")
    if item["severity"] not in SEVERITIES:
        raise TrailerError(f"unknown finding severity {item['severity']!r}")
    if not all(isinstance(item[key], str) and item[key].strip() for key in ("file", "summary")):
        raise TrailerError("a finding needs a non-empty file and summary")
    if item["line"] is not None and not _is_count(item["line"]):
        raise TrailerError(f"finding line must be a non-negative integer or null, got {item['line']!r}")
    return ReviewFinding(severity=item["severity"], path=item["file"], line=item["line"], summary=item["summary"])


def _check_consistency(verdict: ReviewerVerdict, counts: ReviewCounts, findings: tuple[ReviewFinding, ...]) -> None:
    """Counts must match the findings tally, and an approval may not carry any finding."""
    for name in SEVERITIES:
        tally = sum(1 for item in findings if item.severity == name)
        if getattr(counts, name) != tally:
            raise TrailerError(f"count {name!r} is {getattr(counts, name)} but {tally} such findings are listed")
    if verdict is ReviewerVerdict.APPROVED and counts.total:
        raise TrailerError("an APPROVED verdict must report zero findings")


# --- Prompt --------------------------------------------------------------------------------------------------------


def load_review_template(repo_dir: str) -> str:
    """The repository's review template, or the scaffolded default when the repository has none."""
    path = os.path.join(repo_dir, TEMPLATE_REL)
    if not os.path.isfile(path):
        return REVIEW_LOOP_TEMPLATE
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def build_reviewer_prompt(template_text: str, request: ReviewRequest, report_rel: str) -> str:
    """Fill the template's allow-listed placeholders and append the report path and trailer contract.

    Placeholders are replaced literally in a single pass, so literal braces in the template stay intact and unknown
    placeholders are left as they are. The prompt is built from ``request`` alone, so it cannot contain another
    reviewer's report. When the template lacks ``{diff}`` or ``{pr_description}``, they are appended.
    """
    values = _placeholder_values(request)
    body = _PLACEHOLDER.sub(lambda match: values[match.group(1)], template_text)
    return f"{body.rstrip()}\n\n{_missing_inputs(template_text, request)}{reviewer_contract(report_rel)}"


def _placeholder_values(request: ReviewRequest) -> dict[str, str]:
    """Value for each allow-listed template placeholder."""
    return {
        "repo_dir": request.repo_dir,
        "target": request.head_sha,
        "target_branch": request.base_ref,
        "ensemble_size": _SINGLE_PASS,
        "max_iterations": _SINGLE_PASS,
        "iteration": str(request.iteration),
        "pr_title": request.pr_title,
        "pr_description": request.pr_body,
        "diff": request.diff_text,
        "sha": request.head_sha,
    }


def _missing_inputs(template_text: str, request: ReviewRequest) -> str:
    """The PR description and diff, for templates that do not already include them."""
    parts = []
    if "{pr_description}" not in template_text:
        parts.append(f"PR description:\n\n```markdown\n{request.pr_body}\n```\n\n")
    if "{diff}" not in template_text:
        parts.append(f"Git diff:\n\n```diff\n{request.diff_text}\n```\n\n")
    return "".join(parts)


_REVIEWER_PROHIBITIONS = (
    "Write no file other than the report path. Make no GitHub write of any kind: no `gh pr review`, "
    "`gh pr comment`, `gh pr edit` or `gh api` call, and no merge or auto-merge. Append to no ledger under "
    "`holon-knowledge/ledger/`. Spawn no subagents, and write nothing under `.subagent/`."
)


def reviewer_contract(report_rel: str) -> str:
    """Closing instruction for a reviewer turn, with examples rendered by :func:`render_review_trailer`."""
    clean = ParsedTrailer(ReviewerVerdict.APPROVED, ReviewCounts())
    finding = ReviewFinding("nit", "path/to/file.py", 12, "Short description of the issue and the fix.")
    changes = ParsedTrailer(ReviewerVerdict.CHANGES_REQUESTED, ReviewCounts(nit=1), (finding,))
    return (
        "REVIEWER TURN CONTRACT (mandatory): review only the diff and PR description above. Write your full review "
        f"report with your file-write tool to `{report_rel}`. The report must end with the review trailer block as "
        "its final lines: one JSON object between the two marker lines, with exactly the keys verdict (APPROVED or "
        "CHANGES_REQUESTED), critical, important and nit (non-negative integers) and findings (a list of objects with "
        "exactly the keys severity (critical, important or nit), file, line (an integer or null) and summary). Each "
        "count must equal the number of findings with that severity, and APPROVED requires every count to be zero. "
        "No key may appear twice in any object. "
        "For this turn the block supersedes the line-based on-disk trailer described above. Do not modify code, "
        "commit, push, run `gh pr merge` or enable auto-merge, and do not read any other file under "
        f"`{REVIEW_ROOT_REL}/`. {_REVIEWER_PROHIBITIONS} This contract overrides any conflicting instruction in "
        f"the loop template above.\n\nFinal lines of a clean report:\n\n{render_review_trailer(clean)}\n\n"
        f"Final lines of a report with one finding:\n\n{render_review_trailer(changes)}\n\n"
        "When the report is complete, reply with one short confirmation line."
    )


# --- Scratch directories -------------------------------------------------------------------------------------------


def _require_index(value: Any, name: str) -> int:
    """``value`` as a non-negative int; anything else could steer a path, so it is rejected."""
    if not _is_count(value):
        raise ValueError(f"{name} must be a non-negative integer, got {value!r}")
    return value


def _iteration_dir_rel(iteration: int) -> str:
    """``.holon/review/iter-<n>``."""
    return f"{REVIEW_ROOT_REL}/iter-{_require_index(iteration, 'iteration')}"


def reviewer_report_rel(iteration: int, index: int) -> str:
    """Repository-relative report path ``.holon/review/iter-<n>/reviewer-<i>/report.md``."""
    return f"{_iteration_dir_rel(iteration)}/reviewer-{_require_index(index, 'index')}/{REPORT_NAME}"


def _review_root(repo_dir: str) -> str:
    """The resolved ``<repo_dir>/.holon/review``, refusing a root that a symlinked ``.holon`` or ``.holon/review``
    steers anywhere else (for example outside the repository)."""
    expected = os.path.join(os.path.realpath(repo_dir), REVIEW_ROOT_REL)
    root = os.path.realpath(os.path.join(repo_dir, REVIEW_ROOT_REL))
    if root != expected:
        raise ValueError(
            f"review scratch root {REVIEW_ROOT_REL}/ does not resolve to {REVIEW_ROOT_REL}/ under the repository"
            f" directory (a symlinked .holon or {REVIEW_ROOT_REL} is the likely cause)"
        )
    return root


def _contained_path(repo_dir: str, rel: str) -> str:
    """Absolute path of ``rel``, refusing anything that does not resolve inside ``<repo_dir>/.holon/review/``."""
    root = _review_root(repo_dir)
    path = os.path.realpath(os.path.join(repo_dir, rel))
    if not path.startswith(root + os.sep):
        raise ValueError(f"review scratch path {rel!r} escapes {REVIEW_ROOT_REL}/")
    return path


def _remove_scratch(repo_dir: str, rel: str) -> None:
    """Remove a review scratch directory if it exists (contained to ``.holon/review/``)."""
    path = _contained_path(repo_dir, rel)
    if os.path.lexists(path):
        shutil.rmtree(path)


def _prepare_scratch(repo_dir: str, rel: str) -> str:
    """Create ``rel`` empty, removing anything stale first; return its absolute path."""
    _remove_scratch(repo_dir, rel)
    path = _contained_path(repo_dir, rel)
    os.makedirs(path)
    return path


def _write_turn_inputs(scratch: str, prompt: str, request: ReviewRequest) -> tuple[str, str]:
    """Write ``prompt.md`` and ``request.json`` for runners that take a prompt file and an intent file."""
    prompt_file = os.path.join(scratch, "prompt.md")
    intent_file = os.path.join(scratch, "request.json")
    with open(prompt_file, "w", encoding="utf-8") as handle:
        handle.write(prompt)
    with open(intent_file, "w", encoding="utf-8") as handle:
        json.dump({"head_sha": request.head_sha, "base_ref": request.base_ref, "iteration": request.iteration}, handle)
    return prompt_file, intent_file


def _read_report(path: str) -> str | None:
    """The report text, or None when it is missing or unreadable."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


# --- Reviewer turns ------------------------------------------------------------------------------------------------


RunnerFactory = Callable[[str], AgentRunner]


def run_reviewer(
    request: ReviewRequest,
    index: int,
    agent: str,
    model: str,
    *,
    template_text: str | None = None,
    turn_runner: TurnRunner = run_turn,
    runner_factory: RunnerFactory = get_runner,
    budgets: Budgets | None = None,
    stage_timer: StageTimer | None = None,
) -> ReviewerResult:
    """Run one supervised reviewer turn and turn its report into a :class:`ReviewerResult`.

    Every failure, including ``SystemExit`` from the runner factory or credential validation, becomes a FAILED result
    with a redacted reason. A budget breach on any turn fails the reviewer, even when the first turn or a follow-up left
    a parseable report, because a cut-short turn cannot count as a finished review. The scratch directory is left in
    place; :func:`run_ensemble` removes it before the next reviewer starts.
    """
    report_rel = reviewer_report_rel(request.iteration, index)
    budgets = budgets or load_budgets(os.environ)
    timer = stage_timer or StageTimer(REVIEW_STAGE, budgets.stage_seconds(REVIEW_STAGE))
    template = template_text if template_text is not None else load_review_template(request.repo_dir)
    try:
        outcome = _supervise_reviewer_turn(
            request, report_rel, agent, model, template, turn_runner, runner_factory, budgets, timer
        )
    except (Exception, SystemExit) as error:
        reason = f"reviewer turn raised {type(error).__name__}: {error}"
        return _logged(ReviewerResult.failed(index, agent, model, report_rel, reason))
    return _logged(_judged_report(outcome, request.repo_dir, report_rel, index, agent, model))


def _supervise_reviewer_turn(
    request: ReviewRequest,
    report_rel: str,
    agent: str,
    model: str,
    template: str,
    turn_runner: TurnRunner,
    runner_factory: RunnerFactory,
    budgets: Budgets,
    timer: StageTimer,
) -> AuthoringOutcome:
    """Prepare the scratch directory and run the turn; ephemeral credentials are always cleaned up."""
    scratch = _prepare_scratch(request.repo_dir, os.path.dirname(report_rel))
    prompt = build_reviewer_prompt(template, request, report_rel)
    prompt_file, intent_file = _write_turn_inputs(scratch, prompt, request)
    runner: AgentRunner | None = None
    try:
        runner = runner_factory(agent)
        build_cmd = _command_builder(runner, model, prompt_file, intent_file)
        return supervise_authoring_turn(
            build_cmd,
            prompt,
            artefact_path=os.path.join(scratch, REPORT_NAME),
            artefact_rel=report_rel,
            spec=REVIEW_REPORT_SPEC,
            stage=REVIEW_STAGE,
            turn_budgets=budgets.turn,
            stage_timer=timer,
            run=turn_runner,
            modify_code=False,
            context=prompt,
            cwd=request.repo_dir,
        )
    finally:
        if runner is not None:
            runner.cleanup_ephemeral_credentials()


def _command_builder(runner: AgentRunner, model: str, prompt_file: str, intent_file: str) -> Callable[[str], list[str]]:
    """``build_cmd`` callable for :func:`supervise_authoring_turn`."""

    def build_cmd(prompt_text: str) -> list[str]:
        return runner.build_cmd(model, prompt_file, intent_file, prompt_text)

    return build_cmd


def _turn_failure(outcome: AuthoringOutcome) -> str | None:
    """Why a supervised turn cannot count, or None when the report may be judged."""
    breach = outcome.breach  # the last breach of any turn; it covers every unrecovered budget failure too
    return f"review turn cut short: {breach.describe()}" if breach is not None else None


def _judged_report(
    outcome: AuthoringOutcome, repo_dir: str, report_rel: str, index: int, agent: str, model: str
) -> ReviewerResult:
    """:func:`_result_from_outcome`, but an unexpected error reading or parsing the report fails only this reviewer."""
    try:
        return _result_from_outcome(outcome, repo_dir, report_rel, index, agent, model)
    except Exception as error:  # KeyboardInterrupt and SystemExit still propagate
        reason = f"judging the review report raised {type(error).__name__}: {error}"
        return ReviewerResult.failed(index, agent, model, report_rel, reason)


def _result_from_outcome(
    outcome: AuthoringOutcome, repo_dir: str, report_rel: str, index: int, agent: str, model: str
) -> ReviewerResult:
    """COMPLETED when the turn ran within budget and the report carries a valid trailer, otherwise FAILED."""
    failure = _turn_failure(outcome)
    if failure is not None:
        return ReviewerResult.failed(index, agent, model, report_rel, failure)
    text = _read_report(os.path.join(repo_dir, report_rel))
    if text is None:
        return ReviewerResult.failed(index, agent, model, report_rel, "the reviewer wrote no report")
    try:
        parsed = parse_review_trailer(text)
    except TrailerError as error:
        return ReviewerResult.failed(index, agent, model, report_rel, f"invalid review trailer: {error}")
    return ReviewerResult.completed(index, agent, model, report_rel, text, parsed)


def _logged(result: ReviewerResult) -> ReviewerResult:
    """Log a reviewer outcome (index, agent, status and counts only; never prompt or report content)."""
    logger.info(
        "Reviewer %d (%s) %s: critical %d, important %d, nit %d",
        result.index,
        result.agent,
        result.status,
        result.counts.critical,
        result.counts.important,
        result.counts.nit,
    )
    return result


# --- Ensemble ------------------------------------------------------------------------------------------------------


def run_ensemble(
    request: ReviewRequest, *, agent: str, model: str, size: int = DEFAULT_ENSEMBLE_SIZE, **seams: Any
) -> list[ReviewerResult]:
    """Run ``size`` independent reviewers (three by default) one after another and return their results in order.

    ``seams`` are passed to :func:`run_reviewer`. Budgets, the stage timer and the template are resolved once, so the
    whole ensemble shares one review-iteration ceiling. The iteration directory is cleared first; each reviewer's
    report is held in memory and its scratch directory removed before the next reviewer starts, and the iteration
    directory is removed at the end. If the iteration directory cannot be cleared, no reviewer is dispatched; if a
    report cannot be removed, the remaining reviewers are not dispatched. Either way the undispatched reviewers are
    recorded as FAILED, so the consensus fails closed. ``size < 1`` dispatches nothing.
    """
    if size < 1:
        return []
    seams = _shared_seams(request, seams)
    iteration_rel = _iteration_dir_rel(request.iteration)
    clear_error = _clear_iteration(request.repo_dir, iteration_rel)
    if clear_error is not None:
        return [_undispatched(request, index, agent, model, clear_error) for index in range(1, size + 1)]
    results: list[ReviewerResult] = []
    for index in range(1, size + 1):
        isolation_error = _isolate(request.repo_dir, results, consequence="stopping the ensemble", level=logging.ERROR)
        if isolation_error is not None:
            results.append(_undispatched(request, index, agent, model, isolation_error))
            continue
        results.append(run_reviewer(request, index, agent, model, **seams))
    _remove_leftovers(request.repo_dir, results, iteration_rel)
    return results


def _shared_seams(request: ReviewRequest, seams: Mapping[str, Any]) -> dict[str, Any]:
    """``seams`` with budgets, one shared stage timer and the template filled in."""
    shared = dict(seams)
    budgets = shared.get("budgets") or load_budgets(os.environ)
    shared["budgets"] = budgets
    if shared.get("stage_timer") is None:
        shared["stage_timer"] = StageTimer(REVIEW_STAGE, budgets.stage_seconds(REVIEW_STAGE))
    if shared.get("template_text") is None:
        shared["template_text"] = load_review_template(request.repo_dir)
    return shared


def _clear_iteration(repo_dir: str, iteration_rel: str) -> str | None:
    """Remove stale reports from an earlier run; return why the iteration could not be cleared, or None."""
    try:
        _remove_scratch(repo_dir, iteration_rel)
    except (OSError, ValueError) as error:
        logger.error(
            "Could not clear %s before the ensemble (%s); no reviewer is dispatched.",
            iteration_rel,
            type(error).__name__,
        )
        return f"isolation from an earlier run could not be guaranteed: {error}"
    return None


def _undispatched(request: ReviewRequest, index: int, agent: str, model: str, reason: str) -> ReviewerResult:
    """A FAILED result for a reviewer that was never dispatched because isolation could not be guaranteed."""
    report_rel = reviewer_report_rel(request.iteration, index)
    return _logged(ReviewerResult.failed(index, agent, model, report_rel, reason))


def _remove_leftovers(repo_dir: str, results: Sequence[ReviewerResult], iteration_rel: str) -> None:
    """Remove the last reports and the iteration directory; a failure here is logged, the results stand.

    A path that cannot be removed, or that resolves outside ``.holon/review/`` (for example a symlinked iteration
    directory), is left in place rather than aborting the ensemble.
    """
    try:
        _remove_scratch(repo_dir, iteration_rel)
    except (OSError, ValueError) as error:
        logger.warning(
            "Could not remove %s after the ensemble (%s); earlier reports may remain.",
            iteration_rel,
            type(error).__name__,
        )
    _isolate(repo_dir, results, consequence="it may remain", level=logging.WARNING)


def _isolate(repo_dir: str, earlier: Sequence[ReviewerResult], *, consequence: str, level: int) -> str | None:
    """Remove every earlier reviewer's scratch directory; return why isolation failed, or None.

    A directory that cannot be removed is logged at ``level`` with the caller's ``consequence`` (no error detail).
    """
    for result in earlier:
        scratch_rel = os.path.dirname(result.report_path)
        try:
            _remove_scratch(repo_dir, scratch_rel)
        except (OSError, ValueError) as error:
            logger.log(level, "Could not remove reviewer %d's scratch directory; %s.", result.index, consequence)
            return f"isolation from earlier reviewers could not be guaranteed: {error}"
        if os.path.lexists(os.path.join(repo_dir, result.report_path)):
            return "isolation from earlier reviewers could not be guaranteed: an earlier report is still present"
    return None


# --- Consensus -----------------------------------------------------------------------------------------------------


def decide_consensus(
    results: Sequence[ReviewerResult], configured_size: int, mergeable: bool | None, ci_green: bool | None
) -> ConsensusVerdict:
    """Unanimity consensus that fails closed.

    FAILED when nothing was dispatched, the result count differs from ``configured_size`` or any reviewer FAILED.
    CHANGES_REQUESTED when any reviewer requested changes or reported any finding (a single nit blocks), or when
    ``mergeable`` or ``ci_green`` is not exactly ``True`` (unknown is never clean). APPROVED only otherwise.
    :func:`_ensemble_failed` guarantees APPROVED never comes from zero completed reviewers.
    """
    if _ensemble_failed(results, configured_size):
        return ConsensusVerdict.FAILED
    if _any_reviewer_objects(results) or mergeable is not True or ci_green is not True:
        return ConsensusVerdict.CHANGES_REQUESTED
    return ConsensusVerdict.APPROVED


def _ensemble_failed(results: Sequence[ReviewerResult], configured_size: int) -> bool:
    """True when the ensemble did not run in full with every reviewer completing.

    Empty results and any non-COMPLETED result are FAILED here, so past this check at least one reviewer completed.
    """
    if configured_size < 1 or not results or len(results) != configured_size:
        return True
    return any(result.status is not ReviewerStatus.COMPLETED for result in results)


def _any_reviewer_objects(results: Sequence[ReviewerResult]) -> bool:
    """True when any reviewer did not approve cleanly."""
    return any(
        result.verdict is not ReviewerVerdict.APPROVED or result.counts.total or result.findings for result in results
    )
