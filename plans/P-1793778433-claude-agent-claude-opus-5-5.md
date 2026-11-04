# Plan for I-1793778409-render-review-consensus-comment-from-reviewer-data

- **Plan ID:** P-1793778433-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-04T07:47:14.321Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None. One design decision is made explicit instead of a reframe: the reviewer
  trailer parser in `review_ensemble.py` is outside the touch set and requires the trailer finding objects to carry
  exactly `severity`, `file`, `line` and `summary`. Roles, per-role verdicts, recommendations and verified evidence
  therefore travel in a second, separately marked JSON block inside the same reviewer report (`report_text`, which
  `ReviewerResult` already keeps in memory). `review_report.py` parses that block. The trailer contract stays unchanged.
- **Exploration stance:** balanced. The work is mostly exploitation of a clear written contract, but the seam design
  (second report block, finding id reuse, de-duplication key) is new. Step 3 is run as a bounded probe that fails fast
  if the extra block conflicts with the trailer parser.
- **Safety priority level:** elevated
- **Priority Justification:** The posted body goes to GitHub and is built from untrusted reviewer text
  (`docs/safety.md`, human review is the final gate; `holon-config/world/constraints.md` §3 ledger immutability). The
  human-only merge notice and the APPROVED heading must never be fabricated. Two earlier attempts failed on exactly
  these points.

## Exploration

- **Proportion of steps that are exploratory:** 0.125 (1 of 8 steps, Step 3)
- **Justification:** The contract is fully specified, so most steps exploit it. The one real unknown is whether a second
  report block can coexist with the strict trailer parser and the reviewer contract text that `review_ensemble.py`
  appends to the prompt. That gets one cheap probe before the builder is rewritten on top of it.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.70  |
| entropy_pred        | 5.0   |
| impact_pred         | 80.0  |
| cost_pred           | 20.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 37.0  |

## Plan Description & Strategy

The current tree renders a mechanical `## Holon Review: ...` comment (`review_report.py`). The prompt
(`holon-config/prompts/review_loop.template.md`, 309 lines) carries a 16-role persona registry, a 16-row activation
matrix, a severity legend and an output schema that disagrees with both the generator and the in-code twin
(`scaffold.REVIEW_LOOP_TEMPLATE`). Reviewer findings (`ReviewFinding`) carry no id, role or recommendation, and nothing
carries activated roles. The verdict line also falls back to `consensus_data["status"]`, which promotes a status string
to the heading when `ensemble_verdict` is missing.

The strategy is contract first: write the single posted-review shape once, as two fenced blocks (approved and changes
requested) inside a `## Posted review shape` section of the prompt. Mirror the whole prompt byte for byte in
`REVIEW_LOOP_TEMPLATE`, so there is one text and a parity test. Then build the seam and the generator against that text.
The tests derive their expectations from the template the agent receives rather than from string literals copied out of
the generator. Prompt drift and generator drift then both fail the suite.

Fixed design decisions the executor must follow:

1. **Posted shape (order is the contract):** heading → one evaluation paragraph → `**Role reviews:**` + table
   `| Role (activated) | Verdict | Review |` → `**Verified:**` bullets (approval) or the findings table
   `| ID | Severity | File:Line | Reviewer | Finding | Recommendation |` (otherwise) → one `**Checks:**` line →
   `### 🗳️ Ensemble Review Breakdown` with one bullet per reviewer id and verdict, the consensus verdict and the
   `> 🤖 **Reviewed by**: ... · **Model**: ...` line → the verbatim notice
   `Stage 5 calibration will be added to this PR before merge. Merging remains with the human maintainer.`
2. **Reviewer details block:** each reviewer report may contain, before the trailer, one block between
   `<!-- holon-review-details -->` and `<!-- /holon-review-details -->`. The block holds a JSON object with the keys
   `roles` (a list of `{role, verdict: PASS|CHANGES, review}`), `recommendations` (a list of strings aligned by position
   with the trailer `findings`) and `verified` (a list of `{claim, evidence}`). A missing or malformed block yields
   empty details. The reviewer still counts as a vote, because its trailer is valid, but contributes no roles, no
   recommendations (the cell renders `not supplied`) and no verified bullets. Nothing is ever invented.
3. **Activated roles:** the union of roles across completed reviewers. One row per role. The verdict is `CHANGES` if any
   reviewer marked that role `CHANGES`, else `PASS`. The Review cell is the first non-empty review text, sanitised with
   `_inline` and bounded to about 20 words (160 characters). Roles nobody reported are absent. When no reviewer supplied
   roles, a single row `| none reported | — | No reviewer supplied role data. |` is rendered rather than a fabricated
   PASS.
4. **Finding ids** use the formula `flow._reviewer_finding_ids` already writes to the ledger
   (`FIND-R{index}-{iteration:03d}-{n:02d}`). The formula moves into `review_report.finding_id()`, and flow calls it, so
   posted ids equal ledger `ACT-FIND` ids. **De-duplication key:** `(severity, normalised path, line)` when the line is
   set, else `(severity, normalised path, casefolded summary)`. The first occurrence keeps its id. The Finding cell ends
   with `(raised by Reviewer 1, Reviewer 3)` when more than one reviewer contributed.
5. **Verified bullets** come only from `verified` entries whose `evidence` is non-empty and looks like a path, module or
   test (it contains `/`, `.py`, `.md`, `test_` or `::`). Entries without evidence are dropped. The five fixed
   assertions do not exist in this tree, but no constant bullet text of any kind may be added. An approval with no
   evidenced entry renders `- No reviewer supplied evidenced verification.`
6. **Verdict heading** follows `consensus_data["ensemble_verdict"]` alone. `APPROVED` gives the ✅ heading with
   `({n}/{n})`, where `n` is the number of completed reviewers. Anything else, including `None`, `FAILED` and a `status`
   or `approved` flag, gives the ❌ heading with `(Iteration {iteration})`. The `or consensus_data.get("status")`
   fallback is deleted. In `flow.post_pr_review_consensus`, the `--approve` flag keeps its current
   `ensemble_verdict == APPROVED and approved` gate. That is a stricter gate, not a coercion, and it never changes the
   heading.
7. **Checks line:** mergeability renders `CLEAN`, `DIRTY`, `CONFLICTING` or `MISSING_REF` from the probe. `mergeable`
   `CONFLICTING` takes priority over `merge_state_status`. Any other state, or an absent probe, renders `unmeasured`. CI
   renders `{passed}/{total} passed, {failed} failing, {pending} pending`, or `unmeasured` when `measured` is `False` or
   the probe is empty. The `mergeability_note` (pinned by
   `test_flow.py::test_consensus_report_renders_mergeability_note`) is appended to the Checks line, sanitised.
8. **No trailer in posted bodies:** the builder never emits `VERDICT:`/`CI:`/`REPORT:`/`CRITICAL:`/`IMPORTANT:`/`NIT:`/
   `FINDINGS:` lines or the `holon-review-trailer` markers. A test asserts their absence.
9. **Compatibility:** keep the
   `build_review_consensus_report(repo_dir, target, consensus_data, reviewers=(), title=None)` signature and the
   `build_pr_review_consensus_report` alias. `title` and `_pr_link` survive inside the evaluation paragraph. Keep the
   `render_reviewer_line`, `render_finding_lines` and `resolve_report_title` exports, or keep `__all__` consistent if
   one of them is retired. `flow.py` imports only `build_pr_review_consensus_report` and `resolve_report_title`.

---

## Step 1: Baseline the affected tests and map every consumer of the report and the template

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only and cheap. It prevents the out-of-scope breakage that sank earlier attempts.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Record a green baseline and the list of assertions outside the touch set that pin the template or
  report text.
- **Git branch:**
  `I-1793778409-render-review-consensus-comment-from-reviewer-data/P-1793778433-claude-agent-claude-opus-5-5/E-{timestamp}-execute/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run
   `uv run pytest apps/sandbox-executor/tests/test_review_report.py apps/sandbox-executor/tests/test_prompt_templates.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_review_ensemble.py apps/sandbox-executor/tests/test_init.py`
   and note the pass count.
2. Grep the tests for `Holon Review`, `Bean 0034`, `### Reviewers`, `### Findings`, `Mergeability:`, `**CI:**`,
   `review_loop.template`, `REVIEW_LOOP_TEMPLATE` and `{reviewer_1_id}`. Known constraints outside the touch set:
   - `test_review_ensemble.py::test_prompt_carries_diff_description_report_path_and_contract` reads the shipped
     template. It requires exactly one `{diff}` and one `{pr_description}` placeholder and requires the literal
     `{reviewer_1_id}` to survive in the prompt. The breakdown footer of the posted shape must therefore keep
     `{reviewer_1_id}`.
   - `test_init.py` checks that scaffolding writes `holon-config/prompts/review_loop.template.md`.
3. List every assertion in in-scope tests that pins the old mechanical format. These are rewritten in Step 7, never
   deleted without a contract replacement.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (pytest only, test changes declared in the plan).
- **Potential failure modes for this step:** The baseline is already red.
- **Guardrails and early‑abort checks:** If the baseline has failures unrelated to this intent, record them in the
  execution notes and do not fix them.

### Success & Discard Criteria

- **Success:** The baseline counts and the constraint list are recorded in the execution notes.
- **Discard:** Not applicable (read-only).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.2   |
| impact_pred         | 5.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 5.29  |

### Step Metrics Rationale

`EV = 0.97 * 5.0 + 0.5 * 3.0 - 0.3 * 0.2 - 1.0 = 4.85 + 1.50 - 0.06 - 1.0 = 5.29`. Low impact on its own, but it is
cheap insurance against the out-of-scope `{reviewer_1_id}` assertion.

---

## Step 2: Rewrite the review prompt around the one posted shape and mirror it in REVIEW_LOOP_TEMPLATE

- **Sub‑intent recommendation:** NO
- **Reasoning:** Two files, and the text is fully dictated by the acceptance criteria. Splitting would separate the
  contract from its twin and invite drift.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Produce a prompt shorter than the current file. It has no persona registry, no activation matrix, no
  severity legend and no duplicated rule lists, and it states the posted-review contract once.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Restructure `holon-config/prompts/review_loop.template.md` into these sections (target 120 to 160 lines):
   - Title and a one-paragraph role statement using `{repo_dir}`, `{target}` and `{target_branch}`.
   - `## Loop invariants`: one numbered list, each invariant stated once:
     - unanimity across `{ensemble_size}` independent reviewers;
     - zero Critical, zero Important and zero open Nit findings (every Nit adjudicated as `applied` or `rejected` with a
       reason);
     - green CI;
     - mergeability checked before posting or calibration;
     - increment review (re-review only what changed since the last iteration, within `{max_iterations}`);
     - fresh context per pass and never pausing;
     - ledger appends only through `sandbox_executor.flow.record_review_action()` or
       `sandbox_executor.ledger.try_append_action_record(..., preserve_shape=True)`;
     - never `gh pr merge`, auto-merge or a merge queue, because merging is human-only;
     - namespaced `.subagent/` artefacts with `{sha}`.
   - `## Commit trailers`: keep the four-line trailer block (`{iteration}`, `{finding_ids}`, `{severity}`).
   - `## Reviewer details block`: the `holon-review-details` JSON contract from decision 2 of the strategy, with one
     short example. Say that it sits before the review trailer and never replaces it. A role is listed only when the
     diff touched files it owns. Every `verified` entry names the file, module or test that evidences it.
     `recommendations` align by position with trailer `findings`.
   - `## Posted review shape`: exactly two fenced `markdown` blocks titled `Approved` and `Changes requested`, each
     written in the order and literal wording of acceptance criterion 1. They use placeholders `{n}`, `{iteration}`,
     `{target_commit}`, `{target_branch}`, `{reviewer_1_id}` and similar. Each block contains one example role row, one
     example `**Verified:**` bullet or findings row, the `**Checks:**` line, the breakdown footer and the verbatim
     closing notice. One sentence states that roles nobody activated are omitted, that duplicate findings appear once
     with their contributors named, and that the trailer never appears in a posted body.
   - `## On-disk trailer`: the seven-line machine-readable block (VERDICT, CI, REPORT, CRITICAL, IMPORTANT, NIT,
     FINDINGS), described as on-disk only.
   - `## Reviewer turn contract`: keep the existing nine rules in compressed form, plus the trailer example. Keep the
     exact-keys wording, because the parser in `review_ensemble.py` is unchanged.
   - `## Evaluation context`: keep `{target}`, `{target_branch}`, `{ensemble_size}`, `{max_iterations}`, `{iteration}`,
     `{pr_title}`, one `{pr_description}` and one `{diff}`.
2. Write in Australian English. Put formulas in code spans. Use no Bean number in the notice: the new closing notice
   replaces the old `Bean 0034 Notice`. The human-only merge invariant stays in the invariants list.
3. Set `scaffold.REVIEW_LOOP_TEMPLATE` to the byte-identical text of the new markdown file. Escape nothing, because the
   template uses single braces only, and check that no line breaks ruff limits (the file is prettier-wrapped at the repo
   width). This makes the twin parity a simple equality test.
4. Run `npx prettier --check holon-config/prompts/review_loop.template.md` through `uv run task lint-docs` after
   editing. If prettier rewraps, re-copy the formatted text into `scaffold.py`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (the tests and the generator are written against this text)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §5 (human review is the final gate); `holon-config/world/constraints.md` §3
  (ledger append-only).
- **Potential failure modes for this step:**
  - An invariant is silently dropped while shrinking.
  - `{reviewer_1_id}` or a second `{diff}` breaks `test_review_ensemble.py`.
  - The prettier and scaffold copies diverge.
- **Guardrails and early‑abort checks:** Before moving on, check `wc -l` below 309 (and below 288). Grep for "Persona",
  "Activation Matrix", "CRITICAL / BLOCKER" and the severity emoji legend, which must all be absent. Grep for every
  invariant phrase, which must all be present.

### Success & Discard Criteria

- **Success:** The prompt is under 288 lines, prettier is clean, the scaffold twin is identical, and
  `test_review_ensemble.py` and `test_init.py` pass.
- **Discard:** If the prompt cannot keep every invariant under 288 lines after two rewrites, stop and report. This is
  not expected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.0   |
| impact_pred         | 20.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 15.70 |

### Step Metrics Rationale

`EV = 0.85 * 20.0 + 0.5 * 4.0 - 0.3 * 1.0 - 3.0 = 17.00 + 2.00 - 0.30 - 3.0 = 15.70`. Entropy is moderate because two
files are fully rewritten. The text is dictated by the criteria, so novelty is low.

---

## Step 3: Probe and build the reviewer-details seam in review_report.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** A small parser of about 60 lines. The intent forbids splitting, and the probe is bounded.
- **Step Type:** EXPLORATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** A second, separately marked JSON block placed before the review trailer is ignored by
  `parse_review_trailer`, so reviewer reports stay valid while carrying roles, recommendations and verified evidence.
- **Learning target:** Whether `_extract_trailer_block` tolerates preceding JSON blocks. Also whether real reviewer
  reports keep `report_text` populated through `run_ensemble` up to the deciding iteration.
- **Maximum acceptable cost for this learning:** 2 cost units. If the trailer parser rejects such reports, put the block
  after the trailer's closing marker instead. If that also fails, fall back to Markdown sections parsed by heading.
  Record the outcome in the execution notes.

### Intent & Git Integration

- **Step Intent:** Create the typed carrier for reviewer-supplied roles, recommendations and verified evidence, parsed
  defensively from `ReviewerResult.report_text`.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Probe: in a scratch pytest (later kept in `test_review_report.py`), build a report text with a details block followed
   by a valid trailer and call `parse_review_trailer`. It must return the parsed trailer unchanged.
2. Add to `review_report.py`:
   - `DETAILS_OPEN` and `DETAILS_CLOSE` marker constants.
   - Frozen dataclasses `RoleReview(role, verdict, review)`, `VerifiedItem(claim, evidence)` and
     `ReviewerDetails(roles, recommendations, verified)`.
   - `parse_reviewer_details(report_text)`. It finds the last details block, uses `json.loads` with a size cap (64 KiB)
     and returns an empty `ReviewerDetails` on any error. The function is short, with one try block and per-field
     helpers.
   - Per-field helpers. Each drops malformed entries instead of raising. A verdict outside `PASS`/`CHANGES` drops the
     row. Every string goes through `_inline` with bounds: role 60, review 160, recommendation `MAX_SUMMARY_CHARS`,
     claim 200, evidence `MAX_PATH_CHARS`.
   - `finding_id(index, iteration, n)` returning `FIND-R{index}-{iteration:03d}-{n:02d}`.
3. Failed reviewers (`ReviewerStatus.FAILED`) are never parsed and contribute no details.
4. Keep every function within ruff's mccabe limit of 10 and `PLR0912`, without adding `noqa`.

### Dependencies & Criticality

- **Depends on:** Step 2 (block name and shape)
- **Is Bottleneck:** YES (without the seam the generator has no real data, which is the failure of attempt one)

### Safety & Constraint Considerations

- **Relevant rules:** The intent requires every reviewer-supplied string to be sanitised and bounded by the existing
  helpers. `redaction.redact_agent_secrets` is applied through `_inline`.
- **Potential failure modes for this step:**
  - JSON bombs or huge reports.
  - Markdown or HTML injection through the role and review text.
  - Pipe characters breaking table cells.
- **Guardrails and early‑abort checks:** Cap the input size. Replace `|` with `\|` in table cells, which needs a small
  `_cell()` wrapper over `_inline`. Add the fuzz-style cases to the Step 7 tests.

### Success & Discard Criteria

- **Success:** The probe passes and the parser returns empty details for missing, malformed or oversized blocks.
- **Discard:** Abandon the in-report block only if both placements break trailer parsing (expected p below 0.1).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.6   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 13.07 |

### Step Metrics Rationale

`EV = 0.85 * 15.0 + 0.5 * 5.0 - 0.3 * 0.6 - 2.0 = 12.75 + 2.50 - 0.18 - 2.0 = 13.07`. The highest learning value of the
plan: it settles how structured reviewer data can grow without touching the trailer parser.

---

## Step 4: Rewrite build_review_consensus_report() to render the contract shape from real data

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the core change. It is tightly coupled to Steps 2 and 3, and the intent forbids splitting.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The builder emits sections (1) to (7) of the contract in order. Every element comes from the
  reviewers, their details, the probes or the consensus data. It has no fixed assertions, no status-driven APPROVED and
  no trailer.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Replace the body with a short orchestrator that concatenates the outputs of these helpers:
   - `_heading(verdict, completed, iteration)`
   - `_evaluation_paragraph(...)`: one paragraph naming the title or PR link, the head commit, the base branch, the
     Critical, Important and Nit totals, the CI state and the mergeability verdict.
   - `_role_section(details)`
   - `_verified_section(details)` or `_findings_section(rows)`, chosen by the verdict
   - `_checks_line(consensus_data)`
   - `_breakdown(reviewers, verdict, consensus_data)`
   - `CLOSING_NOTICE`
2. The verdict comes from `consensus_data.get("ensemble_verdict")` alone (strategy decision 6). Delete the `status`
   fallback.
3. `_breakdown` has one bullet per configured reviewer:
   - a completed reviewer shows its agent and model id, its verdict and its counts;
   - a failed reviewer shows `FAILED` with the redacted reason and never a vote;
   - the bullets are followed by `**Ensemble Consensus Verdict**`, then the `> 🤖 **Reviewed by**: ... · **Model**: ...`
     line built from the distinct agents and models.
4. Findings rows: iterate completed reviewers in index order and findings in report order, assigning
   `finding_id(index, iteration, n)`. Merge rows on the de-duplication key and record contributors. Render the
   Recommendation from the aligned `recommendations` entry, else `not supplied`. Severity renders as `Critical`,
   `Important` or `Nit`. `File:Line` renders `path:line` or `path`.
5. Replace `_ci_line` and `_mergeability_line` with one `_checks_line` that follows decision 7, so an unmeasured probe
   renders `unmeasured`, never `CLEAN` or `passed`.
6. Delete `HUMAN_MERGE_NOTICE` (the Bean 0034 notice) and the `### Reviewers` and `### Findings` sections. Keep
   `_inline`, `_pr_link`, `_target_commit` and `resolve_report_title`. Retire `render_reviewer_line` and
   `render_finding_lines`, or adapt them to the new bullets and rows, and update `__all__` either way. Grep confirms
   that only `review_report.py` and its test use them.
7. Update the module docstring to describe the new shape and the details block.

### Dependencies & Criticality

- **Depends on:** Steps 2 and 3
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** The no-fabrication requirement; `docs/safety.md` §5.
- **Potential failure modes for this step:**
  - Complexity-lint failures in table helpers.
  - The heading `n` is computed from the configured size instead of the completed reviewers.
  - The pipe or newline escaping misses a cell.
- **Guardrails and early‑abort checks:** Run `uv run task lint` after this step. Keep every helper under about 25 lines.

### Success & Discard Criteria

- **Success:** `uv run pytest apps/sandbox-executor/tests/test_review_report.py` passes against the new contract tests
  from Step 7 (written test-first for this file), and lint is clean.
- **Discard:** Re-plan if the cost exceeds `1.5 * 4.0 = 6.0` units or if the contract order cannot be met without `noqa`
  suppressions.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.0   |
| impact_pred         | 25.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 17.70 |

### Step Metrics Rationale

`EV = 0.80 * 25.0 + 0.5 * 4.0 - 0.3 * 1.0 - 4.0 = 20.00 + 2.00 - 0.30 - 4.0 = 17.70`. This step carries the largest
impact share. The main risk is lint and complexity limits, not correctness.

---

## Step 5: Wire the seam through flow.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** A few targeted edits in a 5165-line hot file. A narrow diff keeps conflict risk low.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The deciding iteration's reviewers, with their report text, iteration and probe data, reach the
  builder. Ledger finding ids and posted finding ids come from one function, and the role data is visible in the
  serialised loop outcome.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Make `_reviewer_finding_ids` call `review_report.finding_id`. Import it alongside `build_pr_review_consensus_report`.
   There is no circular import, because `review_report` imports nothing from `flow`.
2. In `run_review_loop`, check that the `consensus_data` passed to `post_pr_review_consensus` carries `iterations` (the
   deciding iteration), `ensemble_size`, `ensemble_verdict`, `ci_checks`, `mergeability`, `mergeability_note`,
   `target_commit`, `base_branch` and the agent and model. Add any that are missing from the values already computed.
   Check also that `reviewers=` is the deciding iteration's list, and that `report_text` is still populated at post
   time, because scratch files are removed but the in-memory text stays.
3. Extend `_reviewer_summary` with a `details` key: the serialised `ReviewerDetails` (roles, recommendations, verified)
   for completed reviewers. This carries the seam into the JSON outcome the entrypoint prints, without changing the
   ledger row shape. The ledger rows keep their historical keys.
4. In `post_pr_review_consensus`, leave the `--approve` gate as is. It already requires `ensemble_verdict == APPROVED`
   and is never used for the heading. Add a comment that the heading comes from `ensemble_verdict` in the builder.
5. Make no other behavioural change to the loop.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** YES (without the wiring the seam is dead code)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (ledger rows are append-only and their shape is kept);
  human-only merge.
- **Potential failure modes for this step:**
  - The ledger row shape changes and breaks `test_ledger_*`.
  - The posting tests in `test_flow.py` assert the old body.
- **Guardrails and early‑abort checks:** Run
  `uv run pytest apps/sandbox-executor/tests/test_flow.py -k "review or consensus or post"` after the edit, then all of
  `test_flow.py`.

### Success & Discard Criteria

- **Success:** The posted body in the flow tests (captured from a faked `gh`) contains roles and recommendations from a
  fixture reviewer report, and the ledger finding ids equal the posted ids.
- **Discard:** If the wiring needs a change to `review_ensemble.py`, which is out of scope, stop and record a follow-up
  intent instead of touching it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 0.8   |
| impact_pred         | 15.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 10.26 |

### Step Metrics Rationale

`EV = 0.80 * 15.0 + 0.5 * 3.0 - 0.3 * 0.8 - 3.0 = 12.00 + 1.50 - 0.24 - 3.0 = 10.26`. Entropy comes mainly from the
conflict likelihood of the hot `flow.py` file.

---

## Step 6: Align the reviewer entrypoint with the seam

- **Sub‑intent recommendation:** NO
- **Reasoning:** A tiny change, and possibly only a verification.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The container entrypoint passes the loop outcome through unchanged, including the per-reviewer
  `details`. Its failure-path consensus never implies a vote or an approval.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Read `entrypoint/review_loop.py` around the failure summary (the `"ensemble_verdict": "FAILED"` dict near line 209)
   and the summary printing.
2. Check that the printed JSON outcome includes `reviewers[*].details` from Step 5. Add it only if the entrypoint
   filters keys.
3. Check that no path in the entrypoint posts a body itself. Posting stays in `flow.post_pr_review_consensus`. If no
   change is needed, record "verified, unchanged" in the execution notes rather than making a cosmetic edit.

### Dependencies & Criticality

- **Depends on:** Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Human-only merging; no `gh` writes beyond the existing posting path.
- **Potential failure modes for this step:** An unnecessary edit adds churn.
- **Guardrails and early‑abort checks:** Run `uv run pytest apps/sandbox-executor/tests/test_review_loop.py`.

### Success & Discard Criteria

- **Success:** The entrypoint tests pass and the details are visible in the entrypoint output test.
- **Discard:** Not applicable.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.3   |
| impact_pred         | 4.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 3.51  |

### Step Metrics Rationale

`EV = 0.90 * 4.0 + 0.5 * 2.0 - 0.3 * 0.3 - 1.0 = 3.60 + 1.00 - 0.09 - 1.0 = 3.51`.

---

## Step 7: Pin the contract in tests (template-derived, not implementation-derived)

- **Sub‑intent recommendation:** NO
- **Reasoning:** The tests are the contract's enforcement and must land with the code. All four test files are in scope
  and declared here, as `ruleset.md` §3 requires.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make prompt and generator drift fail the suite. Cover the seam, the verdict rules, unmeasured probes,
  failed reviewers, de-duplication, trailer absence and the alias.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. **`test_prompt_templates.py`:**
   - The shipped review prompt equals `scaffold.REVIEW_LOOP_TEMPLATE` byte for byte.
   - It is under 288 lines.
   - It contains none of "Persona Registry", "Activation Matrix", "CRITICAL / BLOCKER" or the severity legend.
   - It contains each invariant phrase: unanimity, zero Critical, Important and open Nit findings, green CI,
     mergeability before posting or calibration, `record_review_action`, increment review and never `gh pr merge`.
   - It contains exactly two fenced blocks under `## Posted review shape`.
   - The existing unmatched-placeholder lint still passes.
2. **`test_review_report.py`:** one shared fixture dataset of three reviewers. Reviewer 1 and Reviewer 3 raise the same
   finding, and the reports carry details blocks. The tests are:
   - **Shape parity test (the drift guard):** extract the matching fenced block from the shipped template. Classify its
     lines as literal (no `{placeholder}`, not an example row or bullet) or patterned. Convert patterned lines to
     regexes with `re.escape` and placeholders replaced by `.+?`. Assert that every literal and patterned structural
     line appears in the rendered report in the same relative order. Parametrise over approved and changes-requested.
     This is what "asserted against the template text the agent is given" means.
   - Unactivated roles are absent from the table, and no PASS row appears for them.
   - A duplicate finding renders once, with both reviewers named in the Finding cell. The ID, Severity, File:Line,
     Reviewer, Finding and Recommendation columns are all present, with ids following the `FIND-R...` formula.
   - A Verified entry without evidence is dropped. An approval with no evidenced entry renders the
     no-evidenced-verification bullet, and no other constant claims appear.
   - The heading follows `ensemble_verdict` only: `status="approved"` or `approved=True` with a non-APPROVED
     `ensemble_verdict` renders CHANGES REQUESTED.
   - Unmeasured CI and mergeability render `unmeasured`. MISSING_REF, CONFLICTING, DIRTY and CLEAN render verbatim.
   - A failed reviewer renders FAILED in the breakdown with its redacted reason and is not counted in `{n}`.
   - A malformed, oversized or missing details block yields no roles and `not supplied` recommendations.
   - Injection text (backticks, `<script>`, `|`, newlines, a fake token) is sanitised in every cell.
   - No posted body contains any trailer key line or trailer marker.
   - `build_pr_review_consensus_report is build_review_consensus_report`, and the keyword parameters still work.
   - The trailer parser still accepts a report that carries a details block (the probe from Step 3).
   - Update the existing tests (title resolution, link without an origin, untrusted text) to the new shape.
3. **`test_flow.py`:**
   - Update `test_consensus_report_renders_mergeability_note` to the Checks line.
   - Add a seam test: `run_review_loop` with a fake ensemble returning `ReviewerResult`s whose `report_text` carries
     details, and a faked `gh` capturing `--body`. Assert that the posted body has the role rows and recommendations,
     and that the ledger `ACT-FIND` ids match the posted ids.
   - Assert that `--approve` is used only for an APPROVED `ensemble_verdict`.
   - Use only the existing fakes (`review_fakes.py`, `gh_write_guard.py`, `hermetic_fixtures.py`), with no real Docker,
     gh, remotes or LLM calls.
4. **`test_review_loop.py`:** update any assertion on the old body. Add one assertion that the entrypoint's printed
   outcome carries `reviewers[*].details`.

### Dependencies & Criticality

- **Depends on:** Steps 2 to 6 (the report tests may be written first, test-first, alongside Step 4)
- **Is Bottleneck:** YES (acceptance criterion 8)

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` §3 (pytest only, declared test changes); hermetic testing
  (`apps/sandbox-executor/docs/hermetic_testing.md`).
- **Potential failure modes for this step:**
  - The parity test is too brittle, for example because of prettier rewrapping.
  - The parity test is too loose, for example because it only checks headings.
- **Guardrails and early‑abort checks:**
  - Compare structural lines after whitespace normalisation, using the `normalised()` helper pattern already in
    `test_prompt_templates.py`.
  - Mutation check by hand: temporarily reorder two sections in the builder and confirm the parity test fails, then
    revert. Record this in the notes.

### Success & Discard Criteria

- **Success:** All four test files pass, and the mutation check confirms the drift guard bites.
- **Discard:** If the parity test needs more than one special-case exclusion per block, simplify the template example
  rather than the test.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 0.8   |
| impact_pred         | 15.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 8.76  |

### Step Metrics Rationale

`EV = 0.80 * 15.0 + 0.5 * 4.0 - 0.3 * 0.8 - 5.0 = 12.00 + 2.00 - 0.24 - 5.0 = 8.76`. This is the most expensive step
because of the large `test_flow.py`. Its impact is the anti-drift guarantee that the previous attempts lacked.

---

## Step 8: Verify the whole suite, lint and docs, and record the execution

- **Sub‑intent recommendation:** NO
- **Reasoning:** A standard closing gate.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove acceptance criterion 9 and leave a complete execution record.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run the affected tests with
   `uv run pytest <the four in-scope test files plus test_review_ensemble.py and test_init.py>`.
2. Run the full suite once with `uv run task test` (it already uses `-n auto --dist worksteal`). No repeated runs are
   needed, because this intent measures no timing.
3. Run `uv run task lint` and `uv run task lint-docs`.
4. Run `git diff --name-only main...HEAD` and confirm that only scope files, `plans/`, `executions/` and the three
   ledgers changed, and that no harness paths or tracker ids appear.
5. Write the execution notes with the actual metrics, the steps executed and the tests run (pass counts and
   pytest-reported times).

### Dependencies & Criticality

- **Depends on:** Steps 1 to 7
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §1 (commit scope), §3 (ledgers append-only); the touch set.
- **Potential failure modes for this step:** An out-of-scope test fails (for example `test_review_ensemble.py`), or
  prettier rewraps the plan or notes.
- **Guardrails and early‑abort checks:** Fix an out-of-scope test failure by adjusting in-scope code or the template
  only, never the out-of-scope test.

### Success & Discard Criteria

- **Success:** The suite, lint and lint-docs are all green, and the diff stays inside the touch set.
- **Discard:** If an out-of-scope file has to change, stop and report a follow-up intent.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.3   |
| impact_pred         | 5.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 4.16  |

### Step Metrics Rationale

`EV = 0.85 * 5.0 + 0.5 * 2.0 - 0.3 * 0.3 - 1.0 = 4.25 + 1.00 - 0.09 - 1.0 = 4.16`.

---

### Strategy Rationale

The intent is accepted as is. The contract is explicit, the touch set is sufficient, and the user has ruled out
splitting. The two earlier failures had three causes:

- the prompt invented its own schema;
- the generator had no data;
- the tests pinned the implementation.

This plan inverts each of them in order: contract text first (Step 2), the data seam second (Steps 3, 5 and 6), the
generator third (Step 4), and template-derived tests (Step 7). The only piece outside the intent's description is the
`holon-review-details` block. The trailer parser that would otherwise have to carry roles and recommendations lives in
`review_ensemble.py`, which is outside the touch set. A second block keeps that parser and its strict exact-keys
contract untouched while still delivering real per-role and per-finding data.

Aggregation:

- **Entropy** is the sum of the step entropies: `0.2 + 1.0 + 0.6 + 1.0 + 0.8 + 0.3 + 0.8 + 0.3 = 5.0`.
- **Cost** is the sum of the step costs: `1 + 3 + 2 + 4 + 3 + 1 + 5 + 1 = 20.0`.
- **Impact** is a qualitative whole-intent value of 80. It is not the sum of the step impacts (107), because the steps
  only deliver value together.
- **p_success** is set below the bottleneck chain product. The chain is Steps 2, 3, 4, 5 and 7, and its product is
  `0.85 * 0.85 * 0.80 * 0.80 * 0.80 = 0.37`. That product treats the risks as independent, but they are strongly
  correlated (one executor, one contract, and retries within a step), so the overall value is set qualitatively at 0.70.
  This reflects one prior run that made no source changes and one that was unmergeable.
- **Learning value** of 5.0 is close to the Step 3 maximum.

Overall EV: `EV = 0.70 * 80.0 + 0.5 * 5.0 - 0.3 * 5.0 - 20.0 = 56.00 + 2.50 - 1.50 - 20.0 = 37.00`.

Strong assumptions:

- `ReviewerResult.report_text` is still populated at post time.
- Prettier keeps the template within the width ruff accepts for the scaffold twin.
- `test_review_ensemble.py` tolerates the rewritten template as long as `{reviewer_1_id}`, one `{diff}` and one
  `{pr_description}` survive.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1: commits scoped to the execution branch; no commits to `main`.
  - `constraints.md` §3: append-only ledgers; rows only through the ledger API; ledger row shape unchanged.
  - `holon-config/world/ruleset.md` §2: typed, documented public functions.
  - `ruleset.md` §3: pytest only; test changes declared (Step 7).
  - `docs/safety.md` §5: human review is the final gate; merging remains human-only.
  - Intent constraints: Australian English, no new dependencies, short low-branching functions, the touch set, and no
    harness paths or tracker ids.
- **Potential violations or edge cases:**
  - The `--approve` path being exercised for a non-APPROVED verdict.
  - Reviewer text injecting markdown or HTML or leaking secrets into the public comment.
  - Silently dropping the human-only merge invariant while shrinking the prompt.
  - Editing `review_ensemble.py` or its tests, which are out of scope.
  - A trailer leaking into a posted body.
- **Mitigations built into the plan:**
  - The heading and `--approve` both depend on `ensemble_verdict`, and both have tests.
  - Every reviewer string passes `_inline` (redaction, single line, no backticks or angle brackets, bounded) plus pipe
    escaping.
  - Invariant-phrase tests protect the shrunk prompt.
  - An explicit discard rule applies if an out-of-scope change becomes necessary.
  - A trailer-absence test protects posted bodies.
  - All tests are hermetic and use the existing fakes.
- **Residual risk accepted (and why):** Reviewers may ignore the details block at first, so early comments show
  `none reported` and `not supplied`. That is accepted because it is truthful. The contract forbids fabrication, and the
  prompt now asks for the block.
- **Allocated Entropy Budget:** 6.0
- **Predicted Plan Entropy:** 5.0
- **Budget Compliance:** The strategy fits within budget (`5.0 <= 6.0`), so it runs as one plan with no sub-intent list,
  as the intent's answer to "Is this one intent or should it split?" requires.
