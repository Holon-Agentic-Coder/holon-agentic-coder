# Plan for I-1793200635-review-ensemble-module

- **Plan ID:** P-1793200653-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-28T15:17:34.674Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. The intent is already scoped as the second of three sequential changes, the
  operator answered that it must not be split, and the main design question (can the planner's turn supervision be
  reused for a review report?) resolves favourably on inspection (see Step 1).
- **Exploration stance:** balanced, leaning conservative. The module is new and isolated, so the blast radius is small,
  but one design point (reusing `supervise_authoring_turn` with a report-shaped `ArtefactSpec` and `modify_code=False`)
  is verified first and has a documented fallback (direct `run_turn`).
- **Safety priority level:** elevated
- **Priority Justification:** The module decides whether a PR can be called `APPROVED`. The incident in the intent
  (fabricated reviewer ids and votes copied from CI, posted to PR #97) shows that a fail-open consensus is a provenance
  and human-review-boundary risk (`docs/safety.md` "Human review is the final gate"; `holon-config/world/constraints.md`
  §1, only humans merge to `main`). The consensus must fail closed, and reviewer turns must never be able to push, merge
  or enable auto-merge.

## Exploration

- **Proportion of steps that are exploratory:** 0.125
- **Justification:** One of eight steps (Step 1) is information gathering that validates the reuse of the existing turn
  supervision before code is written. Everything else is exploit work against well-specified acceptance criteria; higher
  exploration would add cost without changing the outcome of a tightly specified, fake-tested module.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 6.0   |
| impact_pred         | 85.0  |
| cost_pred           | 16.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 52.70 |

## Plan Description & Strategy

Deliver one execution branch that adds `apps/sandbox-executor/src/sandbox_executor/review_ensemble.py`, a
`Reviewer turn contract` section in `holon-config/prompts/review_loop.template.md`, a fake-driven pytest module
`apps/sandbox-executor/tests/test_review_ensemble.py`, and documentation in `apps/sandbox-executor/README.md` and the
Stage 4 description under `docs/` (`docs/architecture.md`, plus `docs/sandbox/execute_plan.md` or `docs/git_flow.md`
only if they already describe Stage 4 review in detail). Nothing calls the module yet; `flow.py`, `cli.py`,
`run_review_loop()`, `turns.py`, `entrypoint/review_loop.py` and the consensus report are not touched.

Findings from inspection that shape the plan:

- `supervise_authoring_turn(build_cmd, prompt, *, artefact_path, artefact_rel, spec, stage, turn_budgets, stage_timer, run, modify_code, context, watch_tree, cwd, env)`
  in `turns.py` already accepts an injectable `run` (the fake seam), an `ArtefactSpec`, and `modify_code=False` (which
  makes the single follow-up re-prompt say "do not modify any code"). It validates via `validate_artefact`, whose
  `usable` needs only existence, a size floor and the required metrics, so a `REVIEW_REPORT_SPEC` with
  `required_metrics=()`, a small size floor and one section whose pattern matches the closing
  `<!-- /holon-review-trailer -->` line (`_find_heading` matches any stripped line against the regex) fits cleanly. No
  `turns.py` change is expected.
- The planner entrypoint (`entrypoint/planner.py`) shows the pattern to copy: `runner = get_runner(agent)`, a
  `build_agent_cmd(prompt_text)` closure calling `runner.build_cmd(model, prompt_file, intent_file, prompt_text)`,
  `load_budgets(os.environ)` and `StageTimer`, and `runner.cleanup_ephemeral_credentials()` in a `finally`, with
  `redact_agent_secrets` on exception text.
- `get_runner` calls `sys.exit(1)` on an unknown agent, so the module takes a `runner_factory` seam (default
  `get_runner`) and treats `SystemExit` from it as a FAILED reviewer rather than letting it end the process.
- `holon-config/prompts/review_loop.template.md` contains literal braces in code blocks (`{finding_ids}`,
  `{reviewer_1_id}`, ...), so `str.format` would raise. Placeholders must be filled by literal per-key replacement of a
  fixed allow-list (`repo_dir`, `target`, `target_branch`, `ensemble_size`, `max_iterations`, `iteration`, `pr_title`,
  `pr_description`, `diff`, `sha`), leaving unknown placeholders intact.
- The existing template has an older line-based on-disk trailer (`VERDICT:` ... `FINDINGS:`). It is kept (other callers
  and `scaffold.py`'s embedded copy may rely on it); the new contract section states that, for reviewer turns, the JSON
  trailer block supersedes it.
- `.holon/` is already git-ignored at the repository root, so `.holon/review/iter-<n>/reviewer-<i>/` needs no ignore
  change.

Ordering: verify seams (Step 1), then build bottom-up: types and parser (Step 2), prompt builder and template (Step 3),
reviewer and ensemble runners (Step 4), consensus (Step 5), tests (Step 6), docs (Step 7), validation (Step 8). Steps 2
to 5 land in one module file; Step 6 may be written alongside each but is run as a whole.

---

## Step 1: Verify the reuse seams before writing code

- **Sub‑intent recommendation:** NO
- **Reasoning:** A short read-only check that de-risks the main design choice; it has no artefacts of its own and is far
  too small to justify a branch.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** `supervise_authoring_turn` can supervise a reviewer turn unchanged, using a report
  `ArtefactSpec` (no required metrics, one section matching the closing trailer marker), `modify_code=False`, and an
  injected `run` callable.
- **Learning target:** Confirm the exact `run(...)` keyword signature the fake must accept (`turn_budgets`,
  `stage_timer`, `stage`, `watch_path`, `watch_tree`, `cwd`, `env`), how a breach is represented (`TurnResult.breach` as
  a `BudgetBreach`), what `AuthoringOutcome.budget_failure` and `report.usable` mean for a report, and that no
  `turns.py` parameter is needed.
- **Maximum acceptable cost for this learning:** about 1.0 cost unit (a few file reads); if reuse turns out not to be
  clean, switch to calling `run_turn` directly with the same `TurnBudgets` and `StageTimer` and document why in the
  module docstring.

### Intent & Git Integration

- **Step Intent:** Pin down the reused APIs (`turns.py`, `artefacts.py`, `budgets.py`, `agent_runner.py`,
  `redaction.py`, `entrypoint/planner.py`) so the module code is written once.
- **Git branch:**
  `I-1793200635-review-ensemble-module/P-1793200653-claude-agent-claude-opus-5-5/E-<ts>-claude-agent-claude-opus-5-5/_`
  (single execution branch for all steps)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Read `TurnResult`, `run_turn` keyword arguments, `AuthoringOutcome` and `supervise_authoring_turn` in `turns.py`.
2. Read `ArtefactSpec`, `ArtefactSection`, `validate_artefact` and `build_reprompt` in `artefacts.py`; confirm that a
   spec with `required_metrics=()` and `metrics_section=None` is `usable` once the file exists above the floor.
3. Read `TurnBudgets`, `StageTimer`, `load_budgets` and the stage-name handling in `budgets.py` to choose the stage
   label (reuse an existing review stage name if `budgets.stage_seconds` knows one, otherwise pass `"review"` only if it
   resolves to a sane default; never add a stage to `budgets.py` in this change).
4. Read `AgentRunner.build_cmd(model_name, prompt_file, intent_file, full_prompt)` and `cleanup_ephemeral_credentials`
   to decide what the reviewer passes for `prompt_file` and `intent_file` (the prompt file written inside the reviewer
   scratch directory; an intent file holding a small JSON description of the review request).
5. Read `redact_agent_secrets` in `redaction.py` for failure reasons.
6. Record the decision (reuse versus direct `run_turn`) for the module docstring.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 (commit boundaries); intent constraint "do not change
  `turns.py` beyond at most one new optional parameter".
- **Potential failure modes for this step:** misreading the budgets stage names, leading to a stage timer with zero
  seconds.
- **Guardrails and early‑abort checks:** read-only; no edits in this step.

### Success & Discard Criteria

- **Success:** A written decision on reuse, the fake `run` signature, and the stage name.
- **Discard:** If reuse would require more than one new optional `turns.py` parameter, discard reuse and take the
  documented direct `run_turn` fallback.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.3   |
| impact_pred         | 5.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 5.26  |

### Step Metrics Rationale

Pure reading of code already inspected during planning. `EV = 0.97 * 5.0 + 0.5 * 3.0 - 0.3 * 0.3 - 1.0 = 5.26`.

---

## Step 2: Add the public types and the strict trailer parser

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure functions and dataclasses in a new file with no side effects; easily tested and reverted.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Create `review_ensemble.py` with the typed public API and a fail-closed `parse_review_trailer`.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Module docstring (Australian English) stating purpose, that Stage 4 does not call it yet, the trailer contract, the
   unanimity rule and the reuse decision from Step 1.
2. `ReviewerVerdict(StrEnum)`: `APPROVED`, `CHANGES_REQUESTED`. `ReviewerStatus(StrEnum)`: `COMPLETED`, `FAILED`.
   `ConsensusVerdict(StrEnum)`: `APPROVED`, `CHANGES_REQUESTED`, `FAILED` (return type of `decide_consensus`).
3. `ReviewFinding` frozen dataclass: `severity` (one of `critical`, `important`, `nit`, held in a module constant
   `SEVERITIES`), `path`, `line` (int or None), `summary`. The trailer key is `file`; it maps to `path`.
4. `ReviewCounts` frozen dataclass (`critical`, `important`, `nit`, with a `total` property) so results carry counts as
   one typed value.
5. `ParsedTrailer` frozen dataclass: `verdict`, `counts`, `findings` (tuple).
6. `ReviewerResult` dataclass: `index`, `agent`, `model`, `status`, `verdict` (None when FAILED), `counts`, `findings`,
   `report_path`, `report_text` (kept in memory after the file is removed), `failure_reason`. Two small constructors,
   `completed(...)` and `failed(...)`, guarantee that a FAILED result never carries a verdict.
7. `ReviewRequest` frozen dataclass: `repo_dir`, `head_sha`, `base_ref`, `pr_title`, `pr_body`, `diff_text`,
   `iteration`.
8. `TrailerError(ValueError)`.
9. `parse_review_trailer(text)`, split into short helpers, each at most two levels of branching:
   - `_extract_trailer_block(text)`: strip trailing whitespace; the last non-empty line must be exactly the closing
     marker `<!-- /holon-review-trailer -->`; find the last opening marker line `<!-- holon-review-trailer -->` before
     it; error if either is missing, if the order is wrong, or if more than one closing marker appears after the opening
     one. Returns the inner text (optionally tolerating one surrounding ` ```json ` fence).
   - `_load_trailer_object(inner)`: `json.loads`; must be a dict with exactly the keys `verdict`, `critical`,
     `important`, `nit`, `findings` (unknown or missing keys rejected).
   - `_parse_counts(obj)`: each count must be an `int` that is not a `bool` and is at least zero.
   - `_parse_findings(raw)`: a list of dicts each with `severity` in `SEVERITIES`, non-empty string `file`, `line` as a
     non-negative int or null, and non-empty string `summary`.
   - `_check_consistency(verdict, counts, findings)`: per-severity counts must equal the tally of findings; an
     `APPROVED` verdict with any non-zero count raises `TrailerError` (fails closed).
   - Unknown verdict strings raise `TrailerError`; `json.JSONDecodeError` is re-raised as `TrailerError`.
10. A `render_review_trailer(parsed)` helper (used by the prompt example and the round-trip test) that emits the
    canonical block, so the format is defined once.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (every later step consumes these types)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (typing, docstrings); intent constraint "standard-library JSON
  only", "no new dependencies".
- **Potential failure modes for this step:** accepting `true` as a count (bool is an int subclass); accepting a trailer
  that is not the final block; lenient parsing that lets an inconsistent report count as approval.
- **Guardrails and early‑abort checks:** every ambiguity raises `TrailerError`; tests in Step 6 cover each rejection.

### Success & Discard Criteria

- **Success:** `render_review_trailer` then `parse_review_trailer` round-trips; each rejection case raises
  `TrailerError`; `ruff check` clean.
- **Discard:** If any parser helper cannot stay near 25 lines with two levels of branching, split it further rather than
  relaxing validation.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.0   |
| impact_pred         | 20.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 16.70 |

### Step Metrics Rationale

Well-specified, pure code. Main risk is edge-case strictness. `EV = 0.90 * 20.0 + 0.5 * 3.0 - 0.3 * 1.0 - 2.5 = 16.70`.

---

## Step 3: Add the prompt builder and the reviewer turn contract in the template

- **Sub‑intent recommendation:** NO
- **Reasoning:** One function plus an additive markdown section; low risk, but it is the reviewer's only instructions so
  its wording matters.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make every reviewer prompt self-contained (diff, PR description, report path, trailer contract) and
  free of any other reviewer's output.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Append a `## Reviewer turn contract` section to `holon-config/prompts/review_loop.template.md` (before
   `## 📋 Evaluation Context`, so the context stays last), saying: review only the supplied diff and description; write
   the full report to the path given at the end of the prompt; end the report with the JSON trailer block as its final
   lines (show the markers, the keys, the severities and a zero-finding example); counts must equal the findings list
   and `APPROVED` requires all counts zero; never modify code, commit, push, call `gh pr merge` or enable auto-merge;
   never read other files under `.holon/review/`; for reviewer turns this trailer supersedes the line-based on-disk
   trailer described above. Nothing existing is removed.
2. Keep any example braces in the new section free of names that the builder replaces, and format with prettier using
   the repository configuration.
3. `build_reviewer_prompt(template_text, request, report_rel)`:
   - build a mapping from the allow-listed placeholder names to values from the request (`repo_dir`, `target` as
     `head_sha`, `target_branch` as `base_ref`, `iteration`, `pr_title`, `pr_description` as `pr_body`, `diff` as
     `diff_text`, `sha` as `head_sha`, `ensemble_size` and `max_iterations` as fixed neutral text because a reviewer
     turn is a single pass);
   - replace each `{name}` literally (no `str.format`), leaving unknown placeholders untouched;
   - append a closing block with the exact report path (`report_rel`) and a restatement of the trailer contract (built
     from `render_review_trailer` so it cannot drift from the parser).
4. The function takes only the request, so it is structurally impossible for it to include another reviewer's report.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent item 2 ("remove nothing other callers rely on"); `docs/safety.md` human merge gate;
  Australian English.
- **Potential failure modes for this step:** a diff containing `{diff}` text being re-expanded (avoid by replacing the
  `diff` key last, or by a single-pass regex substitution); prettier reflowing the template and failing `lint-docs`.
- **Guardrails and early‑abort checks:** use a single-pass `re.sub` over `\{(name1|name2|...)\}` so substituted values
  are never re-scanned; run `npx prettier --check` on the template in Step 8.

### Success & Discard Criteria

- **Success:** A built prompt contains the diff, the PR description, the exact report path and both trailer markers, and
  contains no `{diff}` or `{pr_description}` placeholders.
- **Discard:** If the template cannot be extended without breaking `lint-docs`, place the contract text in a module
  constant appended by the builder and leave a one-line pointer in the template.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.8   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 8.46  |

### Step Metrics Rationale

Additive text and a short pure function. `EV = 0.92 * 10.0 + 0.5 * 2.0 - 0.3 * 0.8 - 1.5 = 8.46`.

---

## Step 4: Add run_reviewer and run_ensemble on the existing turn supervision

- **Sub‑intent recommendation:** NO
- **Reasoning:** The riskiest step (process supervision, credentials, file isolation), but it reuses tested
  infrastructure through injectable seams and is fully exercised by fakes in Step 6; splitting it would only add branch
  overhead, and the operator asked for a single plan.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Run N independent reviewer turns sequentially, each in a fresh scratch directory, converting every
  failure into a FAILED result with a redacted reason and never leaking one reviewer's report to the next.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `REVIEW_REPORT_SPEC = ArtefactSpec(name="review report", sections=(ArtefactSection("closing trailer marker", pattern matching the closing marker line),), size_floor=<small, for example 80>)`
   with no required metrics.
2. `reviewer_dir_rel(iteration, index)` returns `.holon/review/iter-<n>/reviewer-<i>` (target-native path);
   `_prepare_scratch(repo_dir, rel)` removes any stale directory with that name and creates it fresh.
3. `_write_turn_inputs(scratch, prompt, request)` writes `prompt.md` and a small `request.json` (head SHA, base ref,
   iteration; no diff duplication needed beyond the prompt) for runners that need `prompt_file` and `intent_file`.
4. `run_reviewer(request, index, agent, model, *, template_text, turn_runner=run_turn, runner_factory=get_runner, budgets=None, stage_timer=None)`:
   - default `budgets` to `load_budgets(os.environ)` and `stage_timer` to a `StageTimer` for the review stage chosen in
     Step 1;
   - prepare scratch, build the prompt with `build_reviewer_prompt`, write inputs;
   - obtain the runner via `runner_factory(agent)` inside the guarded block (catch `SystemExit` and `Exception`);
   - call
     `supervise_authoring_turn(build_cmd, prompt, artefact_path=..., artefact_rel=..., spec=REVIEW_REPORT_SPEC, stage=..., turn_budgets=budgets.turn, stage_timer=stage_timer, run=turn_runner, modify_code=False, context=prompt, cwd=request.repo_dir)`;
   - `finally`: call `cleanup_ephemeral_credentials()` when the runner was created, as the planner does;
   - hand the outcome to `_result_from_outcome(...)`, which returns FAILED when `outcome.budget_failure` is set or the
     first turn breached, when the report file is missing, or when `parse_review_trailer` raises; otherwise COMPLETED
     with verdict, counts, findings and the report text read into memory.
   - All failure reasons pass through `redact_agent_secrets` and are bounded in length.
   - Split into helpers so each function stays near 25 lines with at most two levels of branching.
5. A breach on the first turn always fails the reviewer even if the follow-up salvaged a parseable report (the review
   work itself was cut short), documented in the docstring.
6. `run_ensemble(request, size, agent, model, **seams)`:
   - `size < 1` returns an empty list (so `decide_consensus` reports FAILED for zero dispatched);
   - loop `index` from 1 to `size`: before starting reviewer `index`, call `_collect_and_remove_report(previous)`, which
     reads the previous report into `ReviewerResult.report_text` (already done in step 4) and deletes the previous
     reviewer's scratch directory, then assert (log and fail closed) that no earlier report path still exists;
   - after the final reviewer, remove its scratch directory too, so no report remains in the workspace;
   - return the list of results in dispatch order.
7. Logging (Australian English) records reviewer index, agent, status and counts only; never prompt or report content.

### Dependencies & Criticality

- **Depends on:** Steps 1, 2, 3
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` sandboxing and human review gate; intent item 1 (credential cleanup in a
  `finally`, redacted reasons, no `turns.py` change beyond one optional parameter); `constraints.md` §2 (no
  unwhitelisted subprocesses: the module only calls the injected `turn_runner`).
- **Potential failure modes for this step:** `get_runner` exiting the process on an unknown agent; a fake or real runner
  raising after creating credentials; scratch deletion escaping the `.holon/review/` root through a crafted iteration
  value; stale report from an earlier run being parsed as fresh.
- **Guardrails and early‑abort checks:** catch `SystemExit` around the factory; `iteration` and `index` are ints and the
  resolved scratch path is checked to be inside `<repo_dir>/.holon/review/` before any `shutil.rmtree`; scratch is
  recreated empty before each turn.

### Success & Discard Criteria

- **Success:** With fakes, a written valid report yields COMPLETED; no report, a raise, a breach or a bad trailer yields
  FAILED with a redacted reason; credentials cleanup is called exactly once per reviewer whose runner was created.
- **Discard:** If reuse of `supervise_authoring_turn` forces more than one optional `turns.py` parameter, switch to a
  direct `run_turn` call with the same `TurnBudgets` and `StageTimer` and document why; if cost passes `1.5 * 4.0`, stop
  and reassess.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 1.5   |
| impact_pred         | 25.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 19.05 |

### Step Metrics Rationale

Highest logic density and the only step touching process supervision and credentials, so it is the bottleneck with the
lowest p. Learning value is highest here: it tells the third change how cleanly the authoring supervision fits a review
artefact. `EV = 0.82 * 25.0 + 0.5 * 6.0 - 0.3 * 1.5 - 4.0 = 19.05`.

---

## Step 5: Add the fail-closed unanimity consensus

- **Sub‑intent recommendation:** NO
- **Reasoning:** A short pure function with an exact specification.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** `decide_consensus(results, configured_size, mergeable, ci_green)` returns `APPROVED` only for full
  unanimity of completed, clean reviewers plus mergeable and green CI.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `_ensemble_failed(results, configured_size)`: true when `configured_size < 1`, `len(results) == 0`,
   `len(results) != configured_size`, or any result is not COMPLETED.
2. `_any_reviewer_objects(results)`: true when any result's verdict is not APPROVED or its counts total is non-zero or
   its findings list is non-empty.
3. `decide_consensus`: FAILED if `_ensemble_failed`; CHANGES_REQUESTED if `_any_reviewer_objects`, or `mergeable` is not
   exactly `True`, or `ci_green` is not exactly `True`; otherwise APPROVED.
4. A final defensive guard: never return APPROVED when the count of COMPLETED results is zero (redundant by
   construction, kept as an explicit invariant matching the intent wording).
5. Default ensemble size constant `DEFAULT_ENSEMBLE_SIZE = 3`, exported for the next change.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent item 1 consensus rule; operator answer "unanimity, no majority".
- **Potential failure modes for this step:** treating `mergeable=None` (unknown) as clean.
- **Guardrails and early‑abort checks:** identity comparison with `True`, so unknown is never clean.

### Success & Discard Criteria

- **Success:** Every consensus test in Step 6 passes.
- **Discard:** None expected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.4   |
| impact_pred         | 12.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 11.28 |

### Step Metrics Rationale

Trivial logic with a precise specification. `EV = 0.95 * 12.0 + 0.5 * 2.0 - 0.3 * 0.4 - 1.0 = 11.28`.

---

## Step 6: Add the fake-driven pytest module

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests are the acceptance evidence for the module; they belong on the same branch as the code.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Cover every behaviour listed in intent item 3 using fakes only, in
  `apps/sandbox-executor/tests/test_review_ensemble.py`.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Fakes:
   - `FakeRunner` (returned by a fake `runner_factory`) whose `build_cmd` returns a sentinel argv embedding the prompt
     text and whose `cleanup_ephemeral_credentials` counts calls;
   - `FakeTurnRunner` configured per reviewer with an action: write chosen report text to the watched path, write
     nothing, raise, or return a `TurnResult` with a `BudgetBreach`. It records the prompt it received and a snapshot of
     which earlier report paths exist at call time.
   - Real `TurnBudgets` and a `StageTimer` with generous limits (constructed directly, not from the environment).
2. Trailer tests: round-trip through `render_review_trailer`; parametrised rejections for missing block, missing closing
   marker, trailer not final, invalid JSON, non-object JSON, unknown verdict, unknown or missing key, negative count,
   bool count, counts disagreeing with findings, unknown severity, and APPROVED with any non-zero count.
3. Reviewer tests: valid report gives COMPLETED; no report, raise, breach, unparseable report each give FAILED with no
   verdict and a non-empty reason; a secret-shaped string in an exception is redacted in the reason; credentials cleanup
   is called on success and on failure; an unknown agent (factory raising `SystemExit`) gives FAILED.
4. Ensemble isolation tests: with three reviewers writing distinctive report text, no recorded prompt contains another
   reviewer's text; at the start of reviewers 2 and 3 the earlier report files do not exist; after the run no report
   remains under `.holon/review/`; results keep the report text in memory.
5. Consensus tests: one FAILED blocks (FAILED); one unparseable reviewer blocks; one CHANGES_REQUESTED blocks; a single
   nit blocks; zero dispatched (size 0, empty results) is FAILED; zero completed is not APPROVED; length mismatch is
   FAILED; non-mergeable or red CI gives CHANGES_REQUESTED; three clean reviewers plus mergeable and green CI give
   APPROVED; an end-to-end `run_ensemble` with three clean fakes then `decide_consensus` gives APPROVED.
6. Use `tmp_path` as `repo_dir`; never call a real runner, gh, git remote or Docker (the existing autouse hermetic
   fixtures in `conftest.py` stay in force).
7. Run `uv run pytest apps/sandbox-executor/tests/test_review_ensemble.py` from the repository root first.

### Dependencies & Criticality

- **Depends on:** Steps 2 to 5
- **Is Bottleneck:** YES (acceptance evidence)

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` §3 (pytest, test location); intent item 3 (pytest only, fakes only); intent item 5
  (run from the root, never `PYTHONPATH`).
- **Potential failure modes for this step:** the fake `run` signature drifting from `run_turn`'s keywords; tests relying
  on wall-clock budgets.
- **Guardrails and early‑abort checks:** the fake accepts `**kwargs` but asserts the keys it needs (`watch_path`,
  `cwd`); no sleeps.

### Success & Discard Criteria

- **Success:** The new module passes on its first focused run, then once inside `uv run task test`.
- **Discard:** If a test needs a real process to pass, the design is wrong; fix the seam rather than the test.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.0   |
| impact_pred         | 18.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 14.04 |

### Step Metrics Rationale

Many cases but all deterministic. `EV = 0.88 * 18.0 + 0.5 * 4.0 - 0.3 * 1.0 - 3.5 = 14.04`.

---

## Step 7: Document the module, the trailer contract and the unanimity rule

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small additive docs; risk is only formatting.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Describe `review_ensemble.py` where Stage 4 is already described, stating plainly that Stage 4 does
  not call it yet.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `apps/sandbox-executor/README.md`: add a short subsection next to the existing Stage 4 / `review_target.py` text
   covering the public API, the scratch layout `.holon/review/iter-<n>/reviewer-<i>/`, the trailer block, the unanimity
   rule and "not yet called by Stage 4; the wiring is the next change".
2. `docs/architecture.md` (and `docs/sandbox/execute_plan.md` or `docs/git_flow.md` only where they describe Stage 4
   review behaviour): add the same facts briefly, linking to the README section rather than duplicating it.
3. No harness paths, bean ids or review ids; Australian English; run `npx prettier --write` on touched markdown with the
   repository configuration (only on the files changed).

### Dependencies & Criticality

- **Depends on:** Steps 2 to 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent items 4 and 5; markdown hygiene rules.
- **Potential failure modes for this step:** docs implying the ensemble is live; prettier changes to unrelated files.
- **Guardrails and early‑abort checks:** prettier only on touched files; explicit "not yet wired" sentence.

### Success & Discard Criteria

- **Success:** `uv run task lint-docs` passes; docs state the not-yet-wired status.
- **Discard:** None expected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.6   |
| impact_pred         | 6.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 4.72  |

### Step Metrics Rationale

`EV = 0.90 * 6.0 + 0.5 * 2.0 - 0.3 * 0.6 - 1.5 = 4.72`.

---

## Step 8: Validate with lint, lint-docs and one full test run

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mandatory gate; no new artefacts.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the change meets the repository gates and leaves forbidden files untouched.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. From the repository root: `uv run task lint`, then `uv run task lint-docs`, fixing with `ruff format` / prettier on
   touched files only.
2. Run `uv run task test` exactly once (no repeated runs; not a performance change).
3. `git diff --name-only` against the plan branch must list only: `review_ensemble.py`, `test_review_ensemble.py`,
   `review_loop.template.md`, the README and the touched docs, plus the execution notes and ledger appends the flow
   itself produces. Confirm `flow.py`, `cli.py`, `turns.py`, `entrypoint/review_loop.py` and `uv.lock` are unchanged.
4. Grep the diff for harness paths, bean ids and review ids; there must be none.

### Dependencies & Criticality

- **Depends on:** Steps 2 to 7
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** intent item 5; `constraints.md` §3 (append-only ledgers).
- **Potential failure modes for this step:** an unrelated flaky test in the full run; prettier drift in untouched files.
- **Guardrails and early‑abort checks:** if the full suite fails only in tests unrelated to the diff, record the failure
  output verbatim in the execution notes rather than re-running repeatedly.

### Success & Discard Criteria

- **Success:** lint, lint-docs and the single full test run all pass; diff scope as listed.
- **Discard:** A failure caused by the change that cannot be fixed within `1.5 * 1.0` cost units.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.4   |
| impact_pred         | 4.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 3.90  |

### Step Metrics Rationale

`EV = 0.88 * 4.0 + 0.5 * 3.0 - 0.3 * 0.4 - 1.0 = 3.90`.

---

### Strategy Rationale

The intent is accepted as is: it is well scoped, isolated (a new module nothing calls), operator-confirmed as a single
plan, and its riskiest assumption (reuse of `supervise_authoring_turn`) checks out on inspection because that function
already exposes an injectable `run`, an `ArtefactSpec` and `modify_code=False`. Building bottom-up (types, parser,
prompt, runners, consensus) lets each layer be tested before the next depends on it, and the fake `turn_runner` keeps
the whole module hermetic.

Aggregation:

- `entropy_pred` is the sum of step entropies: `0.3 + 1.0 + 0.8 + 1.5 + 0.4 + 1.0 + 0.6 + 0.4 = 6.0`. A factor check
  agrees: about five files, all additive (SSA about 4), IRR about 0.5, CL about 1 (no hot files touched), SER about 1,
  NOV about 4, giving `0.30 * 4 + 0.25 * 0.5 + 0.20 * 1 + 0.15 * 1 + 0.10 * 4 = 2.1` as a floor; 6.0 is kept as a
  conservative estimate that covers prettier and fixture surprises, well inside the 12.0 budget.
- `cost_pred` is the sum of step costs: `1.0 + 2.5 + 1.5 + 4.0 + 1.0 + 3.5 + 1.5 + 1.0 = 16.0`.
- `p_success_pred` is a qualitative bottleneck judgement: Step 4 (0.82) dominates, with Steps 6 and 8 (0.88) correlated
  with it, so the overall value is set at 0.80 rather than the much lower independent product.
- `impact_pred` is 85.0: this is the first real reviewer in Stage 4 and removes the fabricated-vote failure mode once
  wired, but its value is only realised by the third change, so it is not scored at the full step sum of 100.
- `learning_value_pred` is 5.0: moderate, mostly from Step 4 (how the authoring supervision fits review artefacts).
- `EV = 0.80 * 85.0 + 0.5 * 5.0 - 0.3 * 6.0 - 16.0 = 68.0 + 2.5 - 1.8 - 16.0 = 52.70`.

Strong assumptions: the existing autouse fixtures in `conftest.py` do not block writes under `tmp_path/.holon/`; the
budgets module can produce a review stage timer without changes; prettier accepts the new template section.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1: work only on the execution branch; never commit to `main`.
  - `holon-config/world/constraints.md` §3: ledgers are append-only; this change writes none directly.
  - `holon-config/world/ruleset.md` §2 and §3: typed, docstringed public API; pytest; tests under the matching `tests/`
    directory.
  - `docs/safety.md` "Human review is the final gate": reviewer turns must never merge, enable auto-merge or push.
  - Intent constraints: no `flow.py`, `cli.py` or `run_review_loop()` changes; no new dependencies; Australian English;
    tests only via `uv run task test` / `uv run pytest` from the root.
- **Potential violations or edge cases:**
  - A real agent CLI accidentally invoked by a test through the default `get_runner` / `run_turn`.
  - `get_runner` calling `sys.exit` inside a library function.
  - Scratch deletion with `shutil.rmtree` targeting a path outside `.holon/review/`.
  - Secrets from a runner exception reaching `failure_reason` or logs.
  - An approval derived from zero or partial reviewers.
  - The template's older line-based trailer confusing reviewers.
- **Mitigations built into the plan:**
  - Every test passes both `turn_runner` and `runner_factory` fakes; hermetic autouse fixtures stay active.
  - `SystemExit` from the factory is caught and becomes a FAILED reviewer.
  - Scratch paths are built from ints and checked to resolve inside `<repo_dir>/.holon/review/` before deletion.
  - Reasons are passed through `redact_agent_secrets` and length-bounded; logs carry no prompt or report content.
  - `decide_consensus` fails closed on zero dispatched, any FAILED, size mismatch, and never approves with zero
    completed; unknown mergeability or CI counts as not clean.
  - The contract section explicitly supersedes the older trailer for reviewer turns.
- **Residual risk accepted (and why):** The real agent CLIs are not exercised, so prompt wording and the trailer's
  practical parse rate are untested against live models. That is deliberate: the intent forbids real runners here, and
  the third change (wiring) is the right place to observe them, behind the fail-closed consensus built now.
- **Allocated Entropy Budget:** 12.0
- **Predicted Plan Entropy:** 6.0
- **Budget Compliance:** The strategy fits within budget (`6.0 <= 12.0`), so it runs as one plan with no sub-intent
  list, as the operator requested.
