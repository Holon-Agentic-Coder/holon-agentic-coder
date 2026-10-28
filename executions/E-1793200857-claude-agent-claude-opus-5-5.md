# Execution Record: E-1793200857-claude-agent-claude-opus-5-5

## Summary

Implementation of plan `P-1793200653-claude-agent-claude-opus-5-5` for intent `I-1793200635-review-ensemble-module`: add
the reviewer ensemble as an isolated, fake-tested module. All eight steps were carried out on this execution branch. The
new `review_ensemble.py` reuses `supervise_authoring_turn` unchanged (no `turns.py` change), parses a strict JSON review
trailer that fails closed, isolates reviewers in per-reviewer scratch directories under `.holon/review/`, and decides
consensus by unanimity. Nothing calls it yet: `flow.py`, `cli.py`, `turns.py`, `entrypoint/review_loop.py` and `uv.lock`
are unchanged. The new test module (57 tests), `uv run task test` (2,597 passed), `uv run task lint` and
`uv run task lint-docs` all pass.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.95  |
| entropy_actual        | 4.5   |
| impact_actual         | 80.0  |
| cost_actual           | 12.0  |
| learning_value_actual | 4.5   |

## Steps Executed

1. **Step 1 (verify reuse seams): reuse confirmed.** `supervise_authoring_turn` accepts an injectable `run`, an
   `ArtefactSpec` and `modify_code=False`; `validate_artefact` marks a file `usable` once it exists above the size floor
   when `required_metrics=()`. The fake `run` receives `turn_budgets`, `stage_timer`, `stage`, `watch_path`,
   `watch_tree`, `cwd` and `env` as keywords. `budgets.stage_seconds("review")` resolves through the existing alias to
   `review_iteration` (900 s default), so the stage label is `review` and `budgets.py` is unchanged. `get_runner` and
   `runner.build_cmd` (via `validate()`) can both call `sys.exit(1)`, so the whole reviewer turn guards `SystemExit`. No
   `turns.py` change is needed. `uv` was not installed on the host; installed with `pip install --user uv` (0.13.6) and
   ran `uv sync --locked`.
2. **Step 2 (types and trailer parser).** Added `apps/sandbox-executor/src/sandbox_executor/review_ensemble.py` with
   `ReviewerVerdict`, `ReviewerStatus`, `ConsensusVerdict`, `ReviewFinding`, `ReviewCounts`, `ParsedTrailer`,
   `ReviewerResult` (constructors `completed` and `failed`; a FAILED result never carries a verdict and its reason is
   redacted and capped at 500 characters), `ReviewRequest` and `TrailerError`. `parse_review_trailer` is split into
   `_extract_trailer_block` (closing marker must be the last non-empty line, one closing marker only, optional `json`
   fence), `_load_trailer_object` (exact keys), `_parse_verdict`, `_parse_counts` (non-negative ints, bools rejected),
   `_parse_findings` / `_parse_finding` (exact keys, known severity, non-empty `file` and `summary`, `line` int or null)
   and `_check_consistency` (per-severity counts equal the tally; `APPROVED` with any count is rejected).
   `render_review_trailer` is the single definition of the canonical block.
3. **Step 3 (prompt builder and template).** `build_reviewer_prompt` fills an allow-list of ten placeholders with one
   regex pass, so a diff containing `{diff}` is never re-expanded and unknown placeholders (`{reviewer_1_id}`) stay
   intact; it appends the diff or description when a template lacks them, then the report path and the contract with
   examples rendered by `render_review_trailer`. The template is read from the repository, falling back to the
   scaffolded copy. Added a `## Reviewer turn contract` section to `holon-config/prompts/review_loop.template.md` before
   the evaluation context; nothing was removed and the scaffold's embedded copy is unchanged.
4. **Step 4 (reviewer and ensemble runners).** `run_reviewer` prepares `.holon/review/iter-<n>/reviewer-<i>/` (paths are
   built from validated non-negative ints and checked to resolve inside `.holon/review/` before any `rmtree`), writes
   `prompt.md` and `request.json`, and runs `supervise_authoring_turn` with `REVIEW_REPORT_SPEC` (one section matching
   the closing marker, size floor 80, no metrics), `modify_code=False` and stage `review`. Credentials are cleaned in a
   `finally` whenever a runner was created. `SystemExit` and any exception become FAILED; a budget failure or any breach
   of the first turn, a missing report or a `TrailerError` also become FAILED. `run_ensemble` resolves budgets, one
   shared stage timer and the template once, clears the iteration directory, removes each reviewer's scratch directory
   before the next starts (if removal fails, the remaining reviewers are recorded FAILED and not dispatched), and
   removes the iteration directory at the end. Logs carry index, agent, status and counts only.
5. **Step 5 (consensus).** `decide_consensus` returns FAILED for size below one, no results, a length mismatch or any
   non-COMPLETED reviewer; CHANGES_REQUESTED when any reviewer did not approve cleanly or `mergeable` / `ci_green` is
   not exactly `True`; APPROVED otherwise, with an explicit guard against zero completed reviewers.
   `DEFAULT_ENSEMBLE_SIZE = 3`.
6. **Step 6 (tests).** Added `apps/sandbox-executor/tests/test_review_ensemble.py` (57 tests) with a fake runner
   factory, a fake runner counting credential cleanups and a fake turn runner that writes a report, writes nothing,
   raises or reports a breach and records each prompt and which reports existed when it started. Covered: round trips;
   22 rejected trailer shapes; prompt contents; COMPLETED and six FAILED paths (including a breach salvaged by the
   follow-up); redaction; unknown agent; isolation (no prompt contains another report, no earlier report exists when a
   reviewer starts, stale reports are cleared, nothing remains afterwards); all consensus cases and an end-to-end
   approval. All passed on the first focused run.
7. **Step 7 (docs).** Added a `Reviewer ensemble (not yet wired)` subsection and a submodule entry to
   `apps/sandbox-executor/README.md`, a component-map entry to `docs/architecture.md` and a short paragraph after the
   Stage 4 limits list in `docs/git_flow.md`, each stating that Stage 4 does not call the module yet.
   `docs/sandbox/execute_plan.md` does not describe Stage 4 review in detail and was left alone.
8. **Step 8 (validation).** Lint, lint-docs and a single full test run passed. `git diff` touches only the module, its
   tests, the template, the README, `docs/architecture.md`, `docs/git_flow.md` and these notes; `flow.py`, `cli.py`,
   `turns.py`, `entrypoint/review_loop.py` and `uv.lock` are unchanged. No harness paths or review ids were added.

## Tests Run

| command                                                             | result                          |
| ------------------------------------------------------------------- | ------------------------------- |
| `uv run pytest apps/sandbox-executor/tests/test_review_ensemble.py` | 57 passed                       |
| `uv run task lint`                                                  | passed (ruff check and format)  |
| `uv run task lint-docs`                                             | passed after formatting notes   |
| `uv run task test` (run once)                                       | 2,597 passed, 8 warnings, 29.5s |
