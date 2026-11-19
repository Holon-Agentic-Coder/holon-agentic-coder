# Plan for I-1793840798-render-review-consensus-comment-from-reviewer-data

- **Plan ID:** P-1793840814-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-05T01:06:55.298Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None. One clarification is recorded as a design decision rather than a reframe:
  `review_ensemble.py` (the strict trailer parser) is outside the touch set, so the richer reviewer data (roles, finding
  ids, recommendations, evidenced verified claims) travels in a second, optional block of the reviewer report that
  `review_report.py` parses from the `ReviewerResult.report_text` the ensemble already keeps in memory.
- **Exploration stance:** conservative. Two attempts have already failed, one by inventing a persona registry and fixed
  assertions and one by changing nothing; this plan pins one written shape first and builds strictly against it.
- **Safety priority level:** elevated
- **Priority Justification:** The change alters what is posted to GitHub (an outward-facing write) and the text of the
  Stage 4 review prompt, whose invariants (unanimity, human-only merging per `docs/safety.md` "Human review is the final
  gate", ledger append-only per `holon-config/world/constraints.md` section 3) must survive the shrink. Reviewer text is
  untrusted and must stay redacted and bounded.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The code paths are already mapped (see Plan Description); the risk is drift and scope creep, not
  unknowns. Every step is EXPLOIT; learning value comes from validating the contract-pinning test technique.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.65  |
| entropy_pred        | 5.4   |
| impact_pred         | 85.0  |
| cost_pred           | 25.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 31.63 |

### Strategy Rationale

The intent is accepted as one plan (the intent's own Q&A forbids splitting, and the predicted entropy `5.4` sits under
the `6.0` budget). The strategy is "contract first": Step 1 writes the single posted-review shape and the reviewer data
block into the prompt and its in-code twin; Steps 2 and 3 implement the parser seam and the renderer against that text;
Step 4 wires flow and entrypoint and removes the verdict coercion; Step 5 pins the contract in tests by extracting the
shape from the template the agent receives; Step 6 runs lint, docs lint and the suite; Step 7 records the execution.

Aggregation: entropy is the sum of step entropies (`1.0 + 1.0 + 1.2 + 0.9 + 0.8 + 0.4 + 0.1 = 5.4`). `p_success` is
below the weakest step (Step 3 and Step 4 at `0.80`) because the steps are serial and partly correlated, so a pure
product (`~0.38`) is too pessimistic and the bottleneck (`0.80`) too optimistic; `0.65` is the qualitative midpoint,
lowered by the history of two failed attempts. Impact `85` reflects that every Stage 4 PR comment in the system is
rendered by this code. Cost `25` is the sum of step costs. Learning value `6.0` is the sum rounded down.

`EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`

`EV = 0.65 * 85.0 + 0.5 * 6.0 - 0.3 * 5.4 - 25.0 = 55.25 + 3.00 - 1.62 - 25.0 = 31.63`

Strong assumptions: (a) the strict trailer parser in `review_ensemble.py` takes the last opening marker and requires the
closing marker on the final line, so a separate, differently marked block placed before the trailer is ignored by it
(verified by reading `_extract_trailer_block`); (b) `ReviewerResult.report_text` is retained for completed reviewers
(verified in `ReviewerResult.completed`); (c) no on-disk consensus report is currently written by the flow, so the
machine-readable `VERDICT/CI/REPORT/...` trailer is produced by a separate renderer used only for on-disk text.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 1: commits only on the current execution branch; never `main`.
  - `holon-config/world/constraints.md` section 3 and intent goal: exactly three append-only ledgers; append through the
    ledger API only.
  - `holon-config/world/ruleset.md` section 2: type annotations and docstrings on every public function; section 3:
    pytest only; test changes are declared here (all four test files are in scope).
  - `docs/safety.md` "Human review is the final gate": merging stays human-only; the posted body must say so.
  - `pyproject.toml`: `ruff` with `max-complexity = 10`; `lint-docs` runs Prettier over `**/*.md`.
  - Intent touch set: only the nine scoped files plus `plans/`, `executions/` and the ledgers.
- **Potential violations or edge cases:**
  - Editing `review_ensemble.py`, `tests/review_fakes.py` or docs to make the seam easier would break the touch set.
  - The prompt shrink could silently drop an invariant (unanimity, zero Nits, mergeability before posting, ledger API,
    increment review, human-only merging).
  - Reviewer-supplied role names, review text, ids, recommendations and evidence paths are untrusted and could inject
    markdown, HTML or secrets into a public comment.
  - A Verified bullet without evidence, or a role row for an un-activated role, would repeat the first failure.
  - `flow.py` is 5 000+ lines; broad edits there raise regression risk in `test_flow.py`.
- **Mitigations built into the plan:**
  - Seam parsing lives in `review_report.py`; `review_ensemble.py` is read, never edited.
  - Step 1 keeps an explicit invariant checklist and Step 5 asserts each invariant phrase in the shipped template.
  - Every reviewer string passes through the existing `_inline()` helper (redaction, single line, no backticks or angle
    brackets, length bound); table cells additionally escape `|`.
  - Verified bullets are rendered only from reviewer entries carrying a non-empty evidence path; role rows only from
    reviewer-declared roles.
  - Flow edits are limited to `post_pr_review_consensus` (coercion), `_reviewer_summary` and the posting call.
- **Residual risk accepted (and why):** Reviewers may omit the optional data block; the report then renders fewer rows
  and states that no role data was supplied rather than inventing it. This is the honest failure mode the intent asks
  for.
- **Allocated Entropy Budget:** 6.0
- **Predicted Plan Entropy:** 5.4
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

Current state (read during planning):

- `review_report.py` (218 lines) renders `## Holon Review: {title} (iteration n)` with a reviewer list, a findings list,
  `### Checks` and a Bean 0034 notice. Its verdict comes from `ensemble_verdict or status`, so a non-verdict `status`
  can leak into the heading. It has no roles, no finding ids, no recommendations and no Verified section. The fixed
  assertions of the first failed attempt are not on this branch, so "delete them" becomes "never introduce them, and
  test that an approval with no evidenced data renders no Verified bullet".
- `review_ensemble.py` (not in scope) parses a strict JSON trailer whose findings have exactly `severity`, `file`,
  `line` and `summary`, keeps `report_text` on completed results, and falls back to `scaffold.REVIEW_LOOP_TEMPLATE` when
  the repository has no prompt file. Its `reviewer_contract()` is appended after the template.
- `flow.post_pr_review_consensus` computes `approved` from `ensemble_verdict` plus the `approved` flag and passes
  `reviewers` and `title` to `build_pr_review_consensus_report`. `run_review_loop` builds `consensus_data` with
  `mergeability`, `mergeability_note` (MISSING_REF), `ci_checks` (with `measured: False` when unmeasured),
  `target_commit`, `base_branch`, `agent`, `model`, `ensemble_size`, `iterations`.
- `holon-config/prompts/review_loop.template.md` is 288 lines with a 16-persona registry, an activation matrix, a
  severity legend and an output schema that does not match the generator. `scaffold.REVIEW_LOOP_TEMPLATE` is a
  different, shorter text with a five-persona registry.

The single posted shape (the written contract every step builds against), in order:

1. `## ✅ Ensemble Consensus Review — APPROVED ({n}/{n})` or
   `## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration {iteration})`.
2. One evaluation paragraph: title (if any), head commit, base branch, finding counts (critical, important, nit), CI
   state and mergeability verdict.
3. `**Role reviews:**` then a table `| Role (activated) | Verdict | Review |`, one row per role at least one completed
   reviewer declared activated; verdict `PASS` or `CHANGES`; Review cell bounded to about twenty words.
4. Approval: `**Verified:**` bullets, each `claim — evidence` where evidence is a file, module or test path supplied by
   a reviewer. Otherwise a table `| ID | Severity | File:Line | Reviewer | Finding | Recommendation |`.
5. `**Checks:** Mergeability {CLEAN|DIRTY|CONFLICTING|MISSING_REF|UNMEASURED} · CI {passed}/{total} passed, {failed} failing, {pending} pending`
   (or `CI unmeasured`).
6. `### 🗳️ Ensemble Review Breakdown` with one bullet per reviewer (id, agent, model, verdict or `FAILED` with reason),
   the consensus verdict and `Reviewed by {agent} ({n}-Agent Ensemble) · Model {model}`.
7. Verbatim: `Stage 5 calibration will be added to this PR before merge. Merging remains with the human maintainer.`

The reviewer data block (new, optional, placed immediately before the existing JSON trailer in each reviewer report):
`<!-- holon-review-roles -->` ... `<!-- /holon-review-roles -->` holding one JSON object with `roles` (list of `role`,
`verdict` PASS or CHANGES, `review`), `findings` (list parallel to the trailer findings, each `id` and `recommendation`)
and `verified` (list of `claim` and `evidence`). It is parsed tolerantly: a missing or malformed block yields no roles
and no verified claims for that reviewer, never invented ones; the reviewer's vote still comes only from the strict
trailer, and a FAILED reviewer contributes nothing but a FAILED line in the breakdown.

---

## Step 1: Rewrite the review prompt and its in-code twin around the one posted shape

- **Sub‑intent recommendation:** NO
- **Reasoning:** Text-only change to two scoped files; small, reversible, and it is the contract the rest of the plan
  pins, so splitting it off would only add coordination cost (and the intent forbids splitting).
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make `holon-config/prompts/review_loop.template.md` shorter than 288 lines and make
  `scaffold.REVIEW_LOOP_TEMPLATE` carry the same posted-review shape block and reviewer data block, with every existing
  invariant kept.
- **Git branch:** current execution branch
  (`I-1793840798-render-review-consensus-comment-from-reviewer-data/P-1793840814-claude-agent-claude-opus-5-5/E-{timestamp}-{action}/_`)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Delete from the markdown prompt: the "Comprehensive Dynamic Persona Registry", the "Dynamic Role Activation Matrix
   Guide", the severity legend ("Review Guidelines & Severity Levels" bullet list) and the old "Standard Review Output
   Schema" block; merge the duplicated ensemble and ledger rule lists into the principles list.
2. Keep a compact "Loop invariants" numbered list that states, once each: iterate to unanimous approval or
   `{max_iterations}`; fresh context per pass; approval needs every reviewer approving with zero Critical, Important and
   open Nit findings plus green CI; Nits are `applied` or `rejected` with justification; never pause; check mergeability
   before posting a review or adding calibration; review only the increment since the last reviewed commit after the
   first pass; namespaced `.subagent/` artefacts; append to `holon-knowledge/ledger/executions.jsonl` only through
   `sandbox_executor.flow.record_review_action()` or
   `sandbox_executor.ledger.try_append_action_record(..., preserve_shape=True)`; never `gh pr merge`, auto-merge or
   merge queue, merging stays with the human maintainer.
3. Replace roles guidance with one short paragraph: activate only the roles the changed files call for, name them in the
   data block, never list an inactive role. No persona list.
4. Add a "Posted review shape" section with one fenced `markdown` block containing exactly the seven parts in the order
   given in the Plan Description, using placeholders that the existing prompt-template lint accepts (complete `{name}`
   braces only, or prose braces closed on the same line). Include one example role row, one example Verified bullet with
   an evidence path and the findings table header with one example row.
5. Keep the "Machine-readable on-disk trailer block" section (VERDICT, CI, REPORT, CRITICAL, IMPORTANT, NIT, FINDINGS)
   and state that it is written to on-disk reports only and never posted.
6. Rewrite the "Reviewer turn contract" to add the optional data block: its markers, keys (`roles`, `findings`,
   `verified`), that it sits immediately before the JSON trailer, that `findings` entries align by position with the
   trailer findings and carry `id` and `recommendation`, and that every `verified` entry must name a file, module or
   test as `evidence`. Keep all existing reviewer prohibitions.
7. Keep the "Evaluation Context" section and its placeholders (`{target}`, `{target_branch}`, `{ensemble_size}`,
   `{max_iterations}`, `{iteration}`, `{pr_title}`, `{pr_description}`, `{diff}`) so `build_reviewer_prompt` still
   substitutes them.
8. In `scaffold.py`, replace `REVIEW_LOOP_TEMPLATE` with text whose "Posted review shape" fenced block and reviewer data
   block section are character-identical to the markdown file (the remaining prose may stay shorter). Keep the string
   free of `str.format` hazards (it is substituted by regex, not `format`).
9. Use Australian English throughout; run Prettier on the markdown file and confirm the fenced block survives unchanged.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (every later step and test derives from this text)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` "Human review is the final gate"; `holon-config/world/constraints.md` section 3;
  intent acceptance criterion 7 (prompt shrinks, invariants survive).
- **Potential failure modes for this step:**
  - An invariant disappears in the merge of duplicated lists.
  - Prettier rewraps the fenced block or a table, so the md and scaffold copies diverge.
  - A new placeholder name trips `test_shipped_templates_have_no_unmatched_placeholders`.
- **Guardrails and early‑abort checks:** Before moving on, run
  `uv run pytest apps/sandbox-executor/tests/test_prompt_templates.py` and
  `npx prettier --check holon-config/prompts/review_loop.template.md`; check `wc -l` is below 288; grep the file for
  "Persona", "Activation Matrix" and "CRITICAL / BLOCKER" and expect no hits.

### Success & Discard Criteria

- **Success:** Markdown prompt under 288 lines (target about 150), no registry, matrix or legend, every invariant
  present once, the shape block identical in both surfaces, prompt lint and Prettier green.
- **Discard:** If the shape block cannot be kept identical across Prettier formatting after two attempts, stop and
  restructure the block (for example, drop column alignment) rather than editing tests to tolerate drift.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.0   |
| impact_pred         | 20.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 13.45 |

### Step Metrics Rationale

Text editing with a clear target; the main risk is Prettier formatting drift.
`EV = 0.85 * 20.0 + 0.5 * 1.5 - 0.3 * 1.0 - 4.0 = 17.0 + 0.75 - 0.30 - 4.0 = 13.45`.

---

## Step 2: Build the reviewer-data seam in review_report.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** One module, pure functions over in-memory strings; it is the missing piece the first attempt never
  built, but it is small enough to execute and test in one plan.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Turn each completed `ReviewerResult` into typed role, finding and verified data without touching the
  strict trailer parser.
- **Git branch:** current execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add frozen dataclasses in `review_report.py`: `RoleReview` (`role`, `verdict` PASS or CHANGES, `review`),
   `FindingRow` (`id`, `severity`, `path`, `line`, `reviewers` tuple of indices, `summary`, `recommendation`),
   `VerifiedClaim` (`claim`, `evidence`) and `ReviewData` (activated roles, finding rows, verified claims).
2. Add `ROLES_OPEN` and `ROLES_CLOSE` marker constants matching the template text from Step 1.
3. Add `parse_reviewer_detail(result)` that returns empty data for FAILED results or missing `report_text`; otherwise
   extracts the last roles block before the trailer, decodes JSON with `json.loads` in a `try`, and keeps only
   well-typed entries. Unknown keys are ignored; malformed entries are dropped. Verdict values other than PASS or
   CHANGES drop the role entry.
4. Pair the reviewer's trailer findings (`result.findings`, the authoritative list) with the block's `findings` entries
   by position: id from the block when it is a short token, else `R{index}-F{n}`; recommendation from the block, else
   `not supplied`. Never create a finding that is not in the trailer.
5. Add `collect_review_data(reviewers)`: merge roles across completed reviewers in first-seen order (case-insensitive
   name match; verdict CHANGES if any reviewer said CHANGES; review text from the first reviewer that gave one); merge
   findings that share severity, path and line into one row whose `reviewers` lists every contributor; keep verified
   claims only when `evidence` is non-empty, deduplicated by claim and evidence.
6. Bound every string at parse time with the existing `_inline()` helper and the existing `MAX_SUMMARY_CHARS` and
   `MAX_PATH_CHARS`; bound the review cell to about twenty words (cut at 25 words with an ellipsis); escape `|` for
   table cells in one small helper.
7. Keep each function under ruff's complexity limit of 10 and add docstrings and type annotations; export the new public
   names in `__all__`.

### Dependencies & Criticality

- **Depends on:** Step 1 (marker strings and keys)
- **Is Bottleneck:** YES (without the seam the report has no real data to render)

### Safety & Constraint Considerations

- **Relevant rules:** intent acceptance criterion 5 (sanitised, length-bounded by existing helpers); goal item 2 (never
  fabricate a role, verdict or finding; failed reviewers are not votes).
- **Potential failure modes for this step:**
  - The roles block is found inside the trailer or in quoted diff text in the report.
  - Position alignment silently attaches a recommendation to the wrong finding when a reviewer reorders.
  - Large malicious JSON slows parsing.
- **Guardrails and early‑abort checks:** Search only the text before the last trailer opening marker; cap the block at a
  fixed character length before decoding; when the block's findings list length differs from the trailer's, use no block
  ids or recommendations for that reviewer (fall back to generated ids and `not supplied`).

### Success & Discard Criteria

- **Success:** Unit-level calls show roles, ids, recommendations and verified claims extracted from a fixture report;
  FAILED and block-less reviewers contribute no roles; duplicates merge with all contributors listed.
- **Discard:** If the seam needs a change in `review_ensemble.py` to work, stop and record it in the execution notes as
  a follow-up rather than widening the touch set.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.0   |
| impact_pred         | 22.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 15.40 |

### Step Metrics Rationale

New, self-contained pure code with clear inputs; learning value comes from proving that the seam works without editing
the strict parser. `EV = 0.85 * 22.0 + 0.5 * 2.0 - 0.3 * 1.0 - 4.0 = 18.70 + 1.00 - 0.30 - 4.0 = 15.40`.

---

## Step 3: Render the consensus comment from the collected data

- **Sub‑intent recommendation:** NO
- **Reasoning:** Replaces the body of one builder function with small section helpers; the risk is shape mismatch, which
  Step 5 catches, so it does not justify a separate branch.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make `build_review_consensus_report()` emit exactly the seven-part posted shape, with the heading
  driven by the ensemble verdict alone and unmeasured probes rendered as unmeasured.
- **Git branch:** current execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Keep the signature `build_review_consensus_report(repo_dir, target, consensus_data, reviewers=(), title=None)` and
   add one optional keyword `review_data: ReviewData | None = None`; when it is `None`, call
   `collect_review_data(reviewers)` so callers of the `build_pr_review_consensus_report` alias keep working unchanged.
2. Heading helper: APPROVED only when `consensus_data["ensemble_verdict"]` equals `ConsensusVerdict.APPROVED`; `n` is
   the configured ensemble size; every other value (including missing, `status`, or an `approved` flag) renders CHANGES
   REQUESTED with the iteration number. Remove the `or consensus_data.get("status")` fallback.
3. Evaluation paragraph helper: one sentence group naming the title (when resolved), short head commit (existing
   `_target_commit`), base branch, finding counts summed from the merged rows by severity, the CI state word and the
   mergeability word.
4. Mergeability word helper: `MISSING_REF` when the note or probe says so; `DIRTY` or `CONFLICTING` from `is_dirty`,
   `mergeable` and `merge_state_status`; `CLEAN` only when the probe says CLEAN; anything else (missing probe, UNKNOWN,
   BLOCKED) renders `UNMEASURED`.
5. CI helper: `measured is False`, a missing probe or zero checks renders `CI unmeasured`; otherwise
   `{passed}/{total} passed, {failed} failing, {pending} pending`.
6. Role table helper: header plus one row per activated role; when no reviewer supplied roles, a single sentence saying
   no reviewer reported activated roles (no invented rows).
7. Approval body helper: `**Verified:**` and one bullet per evidenced claim
   (`claim — \`evidence\``); with no evidenced claims, one sentence saying no evidenced claims were supplied (no bullet, no fixed assertions). Changes body helper: the findings table; the Finding cell is prefixed with `[Flagged
   by Reviewer 1 & 2]` when several reviewers raised it and the Reviewer cell lists the reviewer numbers.
8. Breakdown helper: reuse `render_reviewer_line` (adjusted to the breakdown wording) for every configured reviewer
   result, then the consensus verdict line and the reviewed-by and model line from `consensus_data["agent"]` and
   `["model"]`.
9. Replace `HUMAN_MERGE_NOTICE` with `CLOSING_NOTICE` holding the verbatim sentence; keep `HUMAN_MERGE_NOTICE` as an
   alias name only if something outside the touch set imports it (grep first; otherwise delete).
10. Add `render_on_disk_trailer(consensus_data, review_data, report_path)` returning the seven `KEY: value` lines, and
    `build_on_disk_review_report(...)` that appends it to the posted body; the posted builder never calls it.
11. Remove `_reviewer_section`, the `### Reviewers` and `### Findings` sections and the `## Holon Review:` heading.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** intent acceptance criteria 1 to 4 and 6; ruff `max-complexity = 10`.
- **Potential failure modes for this step:**
  - The heading reverts to APPROVED for a missing verdict through some other default.
  - A table cell breaks because of an unescaped pipe or newline in reviewer text.
  - The on-disk trailer leaks into the posted body.
- **Guardrails and early‑abort checks:** Run `uv run pytest apps/sandbox-executor/tests/test_review_report.py` after the
  rewrite (expected failures are the old-shape assertions only, which Step 5 replaces); `uv run ruff check` on the file.

### Success & Discard Criteria

- **Success:** A fixture approval and a fixture changes-requested dataset both render the seven parts in order; no
  string from `consensus_data["status"]` or the `approved` flag influences the heading.
- **Discard:** If the builder cannot stay under the complexity limit without `noqa`, split helpers further; stop if the
  cost exceeds `1.5 * 5.0` units.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.2   |
| impact_pred         | 22.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 12.99 |

### Step Metrics Rationale

Most lines of the change land here; the shape is fully specified, so success depends on discipline, not discovery.
`EV = 0.80 * 22.0 + 0.5 * 1.5 - 0.3 * 1.2 - 5.0 = 17.60 + 0.75 - 0.36 - 5.0 = 12.99`.

---

## Step 4: Wire the seam through flow and the entrypoint and remove the verdict coercion

- **Sub‑intent recommendation:** NO
- **Reasoning:** Surgical edits to three functions in `flow.py` and one summary line in the entrypoint; the regression
  risk is in the large existing `test_flow.py`, handled in Step 5.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Carry the collected review data from the reviewer results of the deciding iteration into the posted
  report, the loop result and the entrypoint summary, and make APPROVED depend on the ensemble verdict alone.
- **Git branch:** current execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `flow.run_review_loop`, after the deciding iteration's reviewers are known, call `collect_review_data(reviewers)`
   once and add a JSON-safe summary to `consensus_data` (activated role names and verdicts, finding ids with severity
   and reviewers) so the loop result and ledger rows can see it without raw reviewer text.
2. Extend `_reviewer_summary` with the reviewer's activated role names and finding ids (bounded strings only).
3. In `post_pr_review_consensus`, accept an optional `review_data` keyword and pass it to the builder; derive the GitHub
   `--approve` flag from `ensemble_verdict == APPROVED` and at least one completed reviewer only, dropping the
   `consensus_data.get("approved")` promotion; keep every existing parameter and return key.
4. In `entrypoint/review_loop.py`, add one summary line listing activated roles and the open finding count through
   `review_summary_lines` (or directly if that helper is in `flow.py`), keeping the exit-code rule unchanged
   (`review_loop_approved`).
5. Do not add new ledger files or write paths; any new fields go only into existing action records via the existing
   ledger API.

### Dependencies & Criticality

- **Depends on:** Step 2, Step 3
- **Is Bottleneck:** YES (criterion 5 requires the seam to travel through the flow)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 3 (append-only ledgers); `docs/safety.md` human-only
  merge; Bean 0034 halt in the entrypoint stays.
- **Potential failure modes for this step:**
  - Removing the flag check changes `--approve` behaviour for a case existing tests rely on.
  - A new `consensus_data` key breaks the ledger record schema validation.
  - Untrusted reviewer text reaches the ledger unbounded.
- **Guardrails and early‑abort checks:** Run
  `uv run pytest apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_review_loop.py -n auto --dist worksteal`
  immediately; if a ledger validation test fails, put the summary in `consensus_data` only (not in action records).

### Success & Discard Criteria

- **Success:** A fake ensemble whose reports carry the data block yields a posted body (captured from the fake `gh`)
  with the roles, finding ids and recommendations; the `approved` flag alone can no longer produce an APPROVED heading
  or `--approve`.
- **Discard:** If more than three unrelated `test_flow.py` tests fail for reasons outside the report shape, revert the
  `run_review_loop` change and pass data only through `post_pr_review_consensus` (computing it there from `reviewers`).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 0.9   |
| impact_pred         | 15.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 8.48  |

### Step Metrics Rationale

Small edits in a very large module with many tests; the fallback (compute inside the poster) keeps p high.
`EV = 0.80 * 15.0 + 0.5 * 1.5 - 0.3 * 0.9 - 4.0 = 12.00 + 0.75 - 0.27 - 4.0 = 8.48`.

---

## Step 5: Pin the contract in tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** The tests are the guard against a third failed attempt; they live in the four scoped test files and
  need no new fixtures module.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make prompt and generator drift fail the suite and cover the seam, the verdict rules, failed
  reviewers, unmeasured probes, the trailer separation and the alias.
- **Git branch:** current execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `test_prompt_templates.py`: a helper extracts the fenced "Posted review shape" block from the shipped markdown
   template and from `scaffold.REVIEW_LOOP_TEMPLATE`; assert they are identical. Assert the markdown file has fewer than
   288 lines and contains none of "Persona Registry", "Activation Matrix", "CRITICAL / BLOCKER". Assert each invariant
   phrase (unanimous, zero Critical/Important/Nit, CI, mergeability before posting, `record_review_action`, increment,
   human maintainer, never `gh pr merge`) appears in the normalised text. Keep the placeholder lint test green.
2. In `test_review_report.py`: build one fixture dataset in the test file (completed reviewers whose `report_text`
   carries the data block before a trailer rendered by `render_review_trailer`, plus one FAILED reviewer). Derive the
   ordered structural markers from the template block (heading prefix, `**Role reviews:**`, role table header,
   `**Verified:**` or findings table header, `**Checks:**`, the breakdown heading, the closing sentence) and assert the
   rendered approval and changes-requested reports contain them in that order and contain the closing sentence verbatim.
3. Seam tests: roles absent from every reviewer's block never appear; a role marked CHANGES by one reviewer renders
   CHANGES; a finding raised by two reviewers renders once with both named; block ids and recommendations appear; a
   reviewer without a block gets generated ids and `not supplied`; a misaligned block is ignored; a FAILED reviewer
   appears only as FAILED in the breakdown and adds no role or vote.
4. Verdict and probe tests: `ensemble_verdict` missing with `approved=True` and `status="approved"` renders CHANGES
   REQUESTED; an APPROVED approval with no evidenced claims has no bullet under Verified; unmeasured CI renders
   `CI unmeasured`; missing mergeability renders `UNMEASURED`; MISSING_REF, DIRTY and CLEAN render as such.
5. Sanitisation tests: a role review or recommendation containing a pipe, backticks, `<script>` and a fake secret is
   escaped and redacted, and long text is bounded.
6. Trailer test: the posted body contains none of `VERDICT:`, `REPORT:`, `FINDINGS:`; `build_on_disk_review_report` ends
   with the seven trailer lines.
7. Alias test: `build_pr_review_consensus_report` with the old positional and keyword parameters still renders.
8. Replace the old-shape assertions (`## Holon Review:`, `- **CI:**`, `agent ..., model ...`) in `test_review_report.py`
   and the `**Mergeability:**` assertion in `test_flow.py` with the new shape equivalents; update any posted-body
   assertions in `test_review_loop.py` and `test_flow.py` and add one flow test that a fake ensemble's data block
   reaches the body passed to the fake `gh` and that the `approved` flag alone no longer yields `--approve`.
9. No real Docker, `gh`, git remotes or LLM calls; reuse existing fakes and `monkeypatch`.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 4
- **Is Bottleneck:** NO (failures here point to fixes in earlier steps rather than sinking the plan)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 3 (pytest only; test changes declared here); intent goal
  item 3.
- **Potential failure modes for this step:**
  - Tests copy the expected strings instead of reading the template, so drift is not caught.
  - Tests become brittle to Prettier rewrapping.
- **Guardrails and early‑abort checks:** Markers are read from the template file at test time; comparisons of prose use
  the existing `normalised()` helper; the fenced-block comparison is exact because both copies must be identical.

### Success & Discard Criteria

- **Success:**
  `uv run pytest apps/sandbox-executor/tests/test_review_report.py apps/sandbox-executor/tests/test_prompt_templates.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_flow.py`
  passes; deliberately editing one marker in the template makes the shape test fail (checked once by hand, then
  reverted).
- **Discard:** Never weaken an assertion to green the suite; if a contract test cannot pass, fix the source step.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 0.8   |
| impact_pred         | 14.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 7.71  |

### Step Metrics Rationale

Test-writing is well understood; learning value comes from validating "assert against the template text" as a reusable
anti-drift pattern. `EV = 0.80 * 14.0 + 0.5 * 1.5 - 0.3 * 0.8 - 4.0 = 11.20 + 0.75 - 0.24 - 4.0 = 7.71`.

---

## Step 6: Run lint, docs lint and the full suite once

- **Sub‑intent recommendation:** NO
- **Reasoning:** Verification only.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the acceptance gate: `uv run task lint`, `uv run task lint-docs` and the full suite pass.
- **Git branch:** current execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. From the repository root, run `uv run task lint` (ruff check, ruff format check, lock check); fix findings in scoped
   files only.
2. Run `uv run task lint-docs`; if the markdown prompt or the plan or execution notes fail, format them with Prettier
   and re-check that the shape block is still identical to the scaffold copy (rerun the prompt-template tests).
3. Run the full suite once with `uv run task test` (it already uses `-n auto --dist worksteal`); no repeated runs, since
   the intent measures no timing behaviour. Record the pass/fail counts and pytest-reported time.
4. Never set `PYTHONPATH`; never name another test runner.

### Dependencies & Criticality

- **Depends on:** Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent acceptance criterion 9; Test-Run Discipline.
- **Potential failure modes for this step:** unrelated pre-existing failures; `npx` unavailable offline.
- **Guardrails and early‑abort checks:** If a failure is in a file outside the touch set and also fails on the parent
  branch, record it in the execution notes instead of fixing it.

### Success & Discard Criteria

- **Success:** All three commands exit 0.
- **Discard:** If failures persist outside the touch set, stop and report rather than editing out-of-scope files.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.4   |
| impact_pred         | 6.0   |
| cost_pred           | 3.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 2.48  |

### Step Metrics Rationale

Mechanical verification; cost is the suite runtime.
`EV = 0.85 * 6.0 + 0.5 * 1.0 - 0.3 * 0.4 - 3.0 = 5.10 + 0.50 - 0.12 - 3.0 = 2.48`.

---

## Step 7: Record the execution notes and ledger row

- **Sub‑intent recommendation:** NO
- **Reasoning:** Bookkeeping required by the flow.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Write `executions/E-{timestamp}-claude-agent-claude-opus-5-5.md` and append the execution row through
  the ledger API, noting the design decision about the roles block and any follow-up for `review_ensemble.py`.
- **Git branch:** current execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Summarise per step what changed, the test and lint results with counts and time, and the prompt line count before and
   after.
2. Note the follow-up: folding `id`, `recommendation` and `role` into the strict trailer in `review_ensemble.py` would
   remove the position-alignment rule; it is out of this intent's touch set.
3. Append only through the existing ledger API; never edit historical lines; keep exactly the three ledgers.

### Dependencies & Criticality

- **Depends on:** Step 6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 3.
- **Potential failure modes for this step:** hand-editing a `.jsonl` file.
- **Guardrails and early‑abort checks:** `git diff holon-knowledge/ledger/` must show additions only.

### Success & Discard Criteria

- **Success:** Notes file passes Prettier; ledger diff is append-only.
- **Discard:** Not applicable.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.1   |
| impact_pred         | 1.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 0.17  |

### Step Metrics Rationale

Routine. `EV = 0.95 * 1.0 + 0.5 * 0.5 - 0.3 * 0.1 - 1.0 = 0.95 + 0.25 - 0.03 - 1.0 = 0.17`.

---

## Sub-intent list

- None. The intent forbids splitting and the plan fits the entropy budget.
