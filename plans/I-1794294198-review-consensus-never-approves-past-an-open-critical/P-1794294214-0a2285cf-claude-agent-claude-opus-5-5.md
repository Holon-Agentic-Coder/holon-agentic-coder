# Plan for I-1794294198-review-consensus-never-approves-past-an-open-critical

- **Plan ID:** P-1794294214-0a2285cf-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-10T07:03:34.954Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. The intent is accepted in full. One clarification is made explicit rather
  than reframed: the reviewer's prompt template `holon-config/prompts/review_loop.template.md` (line 26, "already
  adjudicated so they are not re-raised") is outside the intent's scope list, so it is not edited; the host-side
  behaviour it describes is changed in code and the template wording is listed as a follow-up for the maintainer.
- **Exploration stance:** balanced. Planning-time reading already pins the cause to specific lines (see Plan
  Description), so most steps are exploitation; Step 1 stays BALANCED because the intent explicitly says the cause is
  "not yet confirmed" and the reproduction must prove which of several contributing defects flipped the verdict.
- **Safety priority level:** critical
- **Priority Justification:** `docs/safety.md` "Human review is the final gate" (principle 5) and "Invariant 4: Human
  review" depend on the Stage 4 posted review being true; a false APPROVED let a real sandbox escape reach `main`
  (`holon-config/world/constraints.md` section 2, escape actions). The change touches the code that decides when the
  pipeline halts as approved, so a regression here silently weakens the only human gate.

## Exploration

- **Proportion of steps that are exploratory:** 0.125 (1 of 8 steps is BALANCED; none fully EXPLORATORY)
- **Justification:** The defect is already localised by reading the code, so exploration is limited to confirming the
  exact flip point with a red test before fixing it. Broad exploration would add entropy to a security-critical path
  with no matching learning gain.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 12.7  |
| impact_pred         | 90.0  |
| cost_pred           | 23.5  |
| learning_value_pred | 6.0   |
| ev_pred             | 43.19 |

## Plan Description & Strategy

### Findings from planning-time code reading (to be confirmed by Step 1)

Paths below are relative to `apps/sandbox-executor/src/sandbox_executor/`.

1. **The verdict flip (primary cause).** `flow/review/resolver.py` `_filter_adjudicated_findings` drops every finding
   whose signature `(severity, path, line, summary)` is in `rejected_history` **or whose row id is in `rejected_ids`**,
   then sets `verdict = ReviewerVerdict.APPROVED if not kept_findings else result.verdict` and recomputes `counts` from
   the kept findings. It keeps `detail.roles` unchanged. `flow/review/loop.py` (around line 370) replaces `reviewers`
   with this filtered list before `_any_reviewer_objects`, `decide_consensus`, `consensus_data["reviewers"]` and the
   posted review. This matches every symptom: reviewer 3 shown as APPROVED with zero findings, while the role table
   (taken from the untouched `detail.roles`) still shows Security as CHANGES.
2. **Identifier collision makes the match far too wide.** Finding ids default to positional `R{index}-F{number}`
   (`review_report.py` `_finding_id`). A rejection of `R3-F1` in iteration 1 therefore suppresses whatever reviewer 3
   reports first in every later iteration, at any severity and with any text. Severity is ignored on the id path, so a
   finding re-raised at a higher severity is silently dropped.
3. **"Out of scope" is a silent default.** `review_report.py` `_normalise_rejection_reason` maps any unrecognised or
   missing reason to `"out of scope"`, and `_disposition_from_mapping` defaults a missing disposition to `rejected`. A
   resolver that skipped a finding, or any failed or fallback path, therefore records `rejected: out of scope`, which
   explains "many findings rejected as out of scope, including important ones", and feeds the suppression above.
4. **The container's honest verdict is discarded.** `loop.py` unpacks `_dispatch_review_ensemble` as
   `reviewers, _container_verdict, infra_error`; the container said `CHANGES_REQUESTED`, and the host never compared.
5. **Role verdicts never block consensus.** `review_ensemble.py` `_any_reviewer_objects` checks only reviewer verdicts
   and counts; a role at `CHANGES` is ignored.
6. **The posted headline counts by verdict only.** `review_report.py` `_approvals` counts reviewers whose verdict is
   APPROVED regardless of open findings, and `_heading` uses only `ensemble_verdict` to choose the word.

### Strategy

Fix the cause at its source (stop the filter mutating verdicts and counts), then add independent defences so no single
future regression can produce a false approval: the verdict is computed from the returned reviewers, role verdicts and
the container's verdict cross-check it, the posted report refuses an APPROVED headline on inconsistent input, resolver
rejections of important or critical findings need evidence, and end-of-flow wording names each final verdict. New
behaviour goes into two focused new flow modules (`flow/review/adjudication.py`, `flow/review/verdict.py`) and one
package-level module (`resolver_evidence.py`), because `loop.py` (960 of 962), `resolver.py` (362 of 362) and
`stages/review.py` (322 of 322) are at their line budgets and `review_report.py` (827) and `review_ensemble.py` (1055)
are already far over the ~400-line guidance.

### Touch set (module granularity)

- New: `flow/review/adjudication.py`, `flow/review/verdict.py`, `resolver_evidence.py`.
- Changed: `flow/review/resolver.py` (shrinks: filter and PR-text augmentation move out), `flow/review/loop.py`,
  `flow/stages/review.py`, `review_report.py`, `entrypoint/review_loop.py` (resolver prompt asks for evidence; summary
  wording), `apps/sandbox-executor/flow_module_budgets.json`.
- Tests: new `tests/flow/review/test_consensus_integrity.py`, new `tests/test_resolver_evidence.py`, additions to
  `tests/test_review_report.py`, `tests/flow/review/test_loop.py` and `tests/flow/stages/` review tests; refreshed
  `tests/flow/fixtures/flow_py_definitions.json` (amended and added definitions, each with a reason).
- Docs: `docs/git_flow.md`, `docs/ledger_schema.md`.
- Not touched: `holon-knowledge/ledger/*.jsonl` (only appended by the flow itself), `holon-config/` (out of scope).

---

## Step 1: Reproduce the false consensus with a hermetic red test and pin the flip point

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, test-only, and every later step depends on it; a separate branch would only add merge overhead.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The verdict changes inside `_filter_adjudicated_findings` (verdict forced to APPROVED
  once every finding is dropped), reached through either the id path (`R3-F1` reused) or the signature path, and nowhere
  else between the reviewer's returned result and `decide_consensus`.
- **Learning target:** Which of the two match paths fires for the failing shape, whether `_normalise_rejection_reason`
  manufactured the "out of scope" row, and whether any other point (for example `_attach_transported_detail` or
  `deserialize_review_outcome`) also alters the verdict.
- **Maximum acceptable cost for this learning:** about 3 cost units; if the flip is not found within that, widen the
  probe to `review_ensemble.deserialize_review_outcome` before writing any fix.

### Intent & Git Integration

- **Step Intent:** Prove the defect exists with a test shaped exactly like the failing run before any fix.
- **Git branch:**
  `I-1794294198-review-consensus-never-approves-past-an-open-critical/P-1794294214-0a2285cf-claude-agent-claude-opus-5-5/E-{timestamp}-consensus-integrity/_`
  (one execution branch for the whole plan)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `tests/flow/review/test_consensus_integrity.py`, reusing `tests/review_fakes.py` and the existing seams
   (`run_ensemble_fn`, `run_resolver_fn`, the fake `gh` and local-test fixtures already used by
   `tests/flow/review/test_loop.py`), so no network, container or real agent is used.
2. Scenario A (id path): iteration 1 has reviewer 3 at CHANGES_REQUESTED with one important finding (id `R3-F1`); the
   fake resolver rejects it with `out of scope` and pushes nothing new. Iteration 2 returns APPROVED, APPROVED and
   CHANGES_REQUESTED with one critical finding (id `R3-F1`, different text, same or different path) and a reviewer
   detail block whose role list has Security at CHANGES.
3. Scenario B (signature path): same, but the iteration-2 critical repeats the rejected finding's path, line and text at
   the same severity, then at a higher severity.
4. Assertions describing the required behaviour (they fail on `main`): the loop result's `consensus.approved` is False,
   `halted_for_human` is False, `ensemble_verdict` is not `APPROVED`, no approving review was posted through the fake
   poster, the resolver seam was called for iteration 2 with the critical finding in its input, and the `reviewers`
   summary lists reviewer 3 as CHANGES_REQUESTED with critical 1.
5. Add a direct unit test of the filter on the failing shape that asserts the returned reviewer's verdict and counts are
   unchanged; run it on the unmodified code first and record the observed flipped values in the execution report as the
   confirmed cause.
6. Run only these tests with `uv run pytest apps/sandbox-executor/tests/flow/review/test_consensus_integrity.py` and
   record the red result (expected failures named) in the execution log.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (it establishes the cause the fix must target; p_success is high, so no sub-intent needed)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 3 (test changes declared in the plan: this step declares
  them), `docs/safety.md` "Sandboxing is mandatory for execution", `apps/sandbox-executor/docs/hermetic_testing.md`.
- **Potential failure modes for this step:** the fake seams cannot express a role table; the test passes on `main`
  because the failing run used the container dispatch path rather than the injected seam.
- **Guardrails and early‑abort checks:** if the test passes on `main`, repeat it through the `_dispatch_review_ensemble`
  path with a faked `_dispatch_role` writing a result file (as existing tests do) before concluding anything; never
  weaken an assertion to make it pass.

### Success & Discard Criteria

- **Success:** at least one scenario fails on unmodified code with `approved` True and reviewer 3 shown as APPROVED, and
  the flip point is named by function and line in the execution notes.
- **Discard:** neither path reproduces after both seam and container-path attempts, or cost exceeds `1.5 * 3`; then stop
  and report the reproduction gap rather than fixing blind.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.0   |
| impact_pred         | 70.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 7.0   |
| ev_pred             | 64.6  |

### Step Metrics Rationale

The planning-time reading already shows the flip line, so reproduction is very likely; the learning value is the highest
in the plan because it converts the intent's "likely cause" into a confirmed one.
`EV = 0.92 * 70 + 0.5 * 7 - 0.3 * 1.0 - 3.0 = 64.4 + 3.5 - 0.3 - 3.0 = 64.6`.

---

## Step 2: Move adjudication suppression into a nits-only module that never changes a reviewer's verdict

- **Sub‑intent recommendation:** NO
- **Reasoning:** It is the root-cause fix and is tightly coupled to Steps 1 and 3; splitting would leave a branch where
  the filter is fixed but the loop still reads its output, which is harder to review.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make suppression apply only to nit findings, never to critical, important or Security-role findings,
  and make it unable to alter the verdict or counts the loop decides on.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `flow/review/adjudication.py` and move `_filter_adjudicated_findings` and `_augment_pr_text_with_rejected`
   there from `flow/review/resolver.py` (re-export or update imports in `loop.py`; `resolver.py` shrinks, and its budget
   is lowered by hand to the new size).
2. Add a predicate (for example `is_suppressible(finding, reviewer_detail)`) that returns True only when the finding's
   severity is `nit` **and** the raising reviewer has no activated Security role at `CHANGES` (the role table is
   per-reviewer, so a reviewer whose Security role objects has none of its findings suppressed; if a finding carries an
   explicit role attribution in its detail block, that is used instead).
3. Match only on the full signature including severity. Drop the id-only match: ids are positional (`R{index}-F{n}`) and
   collide across iterations. A finding re-raised at a higher severity has a different signature and is a new finding by
   construction; add an explicit guard so a matching history entry at a lower severity never suppresses.
4. Change the filter's contract: it no longer returns rewritten `ReviewerResult`s for the verdict. It returns the
   suppressed finding rows (with their ids, for logging) and the actionable rows for the resolver. Reviewer verdicts,
   counts and role tables are left exactly as returned. Put a code comment at the old flip point describing the cause:
   "the filter used to set the verdict to APPROVED once all findings were dropped, and matched rejected findings by
   positional id, so a critical re-raised as `R3-F1` was dropped and the reviewer reported as approved".
5. Restrict the rejected list handed to reviewers in the PR text (`_augment_pr_text_with_rejected`) and to the resolver
   (`ResolverInput.rejected_findings`) to nit rejections only, so reviewers are never told to stay silent about an
   important or critical issue.
6. Unit tests in `tests/flow/review/test_consensus_integrity.py`: critical, important and Security-role findings are
   never suppressed whatever the history; a nit with a matching rejected signature is suppressed; a nit re-raised as
   important is not; an id collision with different text is not suppressed; verdict and counts are identical before and
   after the call.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` principle 5 and Invariant 4; flow module budgets
  (`apps/sandbox-executor/flow_module_budgets.json`); ruff complexity limits (`C901 <= 10`, `PLR0912 <= 12`).
- **Potential failure modes for this step:** moving definitions breaks `tests/flow/test_move_proof.py`; existing tests
  in `tests/flow/review/test_loop.py` assert the old flipping behaviour; a reviewer that keeps re-raising a rejected nit
  now prevents consensus (by design) and could lengthen loops.
- **Guardrails and early‑abort checks:** update the move-proof fixture only through Step 8's declared amendments; any
  existing test that asserts a flipped verdict is rewritten to the new rule and listed in the execution report, never
  deleted silently; repeated rejected nits are still bounded by the existing three-iteration no-convergence guard.

### Success & Discard Criteria

- **Success:** Step 1 Scenario A and B unit assertions on the filter pass; no function in the new module exceeds ruff
  limits; `resolver.py` is smaller than before.
- **Discard:** the role attribution needed for the Security rule is not available per reviewer; then fall back to the
  stricter rule "no suppression for any reviewer whose any role is CHANGES" and note it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.5   |
| impact_pred         | 90.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 73.75 |

### Step Metrics Rationale

Clear target and a small function, but it changes the contract of a function used in the loop and existing tests, so
entropy is moderate. `EV = 0.85 * 90 + 0.5 * 4 - 0.3 * 2.5 - 4.0 = 76.5 + 2.0 - 0.75 - 4.0 = 73.75`.

---

## Step 3: Compute the ensemble verdict from the returned reviewers, with role and container cross-checks

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core of acceptance criterion 1; it must land with Step 2 so the loop never reads filtered data.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The loop declares consensus only when every reviewer returned APPROVED with zero findings, no role is
  CHANGES, the pre-filter and post-filter verdicts agree, and the container (when used) did not report otherwise.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `flow/review/verdict.py` with:
   - a function returning the role objections across reviewers (reviewer index, role name) from
     `parse_reviewer_detail(result).roles` where the verdict is `CHANGES`;
   - `decide_returned_consensus(reviewers, ensemble_size, mergeable, ci_green, container_verdict)` that calls
     `decide_consensus` on the returned reviewers and downgrades APPROVED to CHANGES_REQUESTED when any role objects or
     when `container_verdict` is present and not APPROVED (five parameters, within `PLR0913`);
   - a helper that compares per-reviewer verdicts before and after adjudication and, when they differ, logs both
     verdicts with the suppressed finding ids and returns a flag that forbids consensus;
   - `render_final_verdicts(reviewers)` producing one line such as
     `Reviewer 1 APPROVED (0/0/0); Reviewer 2 APPROVED (0/0/0); Reviewer 3 CHANGES_REQUESTED (1/0/0)` plus role
     objections, used by Steps 4 and 6.
2. In `flow/review/loop.py`: keep the returned reviewers as the single source for `_any_reviewer_objects`, all three
   `decide_consensus` call sites (replaced by `decide_returned_consensus`), `consensus_data["reviewers"]` and the posted
   review; keep `_container_verdict` instead of discarding it; feed only the adjudication module's actionable rows to
   the resolver input, so a reviewer at CHANGES_REQUESTED proceeds to the resolver.
3. When the role check or the container cross-check is the only thing blocking approval, there may be zero actionable
   rows; in that case the loop does not approve and halts with an explicit reason ("reviewer 3 Security role CHANGES
   with no actionable finding") instead of the generic "no resolver pass applicable".
4. Keep `loop.py` within its 962-line budget by extracting the three near-identical local-tests CI-status blocks into
   one helper in `verdict.py` or a small sibling module; if the budget still must rise, record the new value and the
   reason (in `flow_module_budgets.json` description text or the documented reasons location used by
   `scripts/check_flow_module_budgets.py`, plus `docs/budgets.md`).
5. Tests: the Step 1 loop scenarios now pass (no consensus, no approving post, resolver called); add cases for a role
   CHANGES with all reviewers APPROVED and zero findings (no consensus), a container verdict CHANGES_REQUESTED with a
   clean host view (no consensus, both logged), and a pre/post mismatch log line naming the finding ids.
6. Run `uv run pytest apps/sandbox-executor/tests/flow/review apps/sandbox-executor/tests/test_review_ensemble.py`.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` Invariant 4; constraints "no new imports from entrypoints into the host flow
  engine"; flow module budgets; ruff limits (`_run_review_loop_iterations` already carries `noqa` codes and must not
  gain new ones).
- **Potential failure modes for this step:** `loop.py` grows past budget; legacy tests rely on a clean consensus where a
  fake reviewer's detail block declares a role at CHANGES; the container's verdict is missing on older result files.
- **Guardrails and early‑abort checks:** a missing container verdict is treated as "no cross-check", never as approval;
  fail closed on any parse error in role detail (treat as an objection); run the module budget script after each edit.

### Success & Discard Criteria

- **Success:** acceptance criterion 1 test passes; all existing `tests/flow/review` tests pass or are updated with a
  stated reason; budgets pass.
- **Discard:** if `loop.py` cannot be kept within budget plus a modest recorded raise (more than about 30 lines), stop
  and split the CI-status handling into its own sub-intent.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 2.5   |
| impact_pred         | 92.0  |
| cost_pred           | 4.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 68.51 |

### Step Metrics Rationale

The most invasive edit in a 960-line function with three decision sites; the budget squeeze is the main risk.
`EV = 0.78 * 92 + 0.5 * 4 - 0.3 * 2.5 - 4.5 = 71.76 + 2.0 - 0.75 - 4.5 = 68.51`.

---

## Step 4: Make the posted consensus review report the returned verdicts exactly

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, local change to the report renderer with a direct test; low risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The posted review never reads APPROVED while any reviewer or role is CHANGES, and its fraction counts
  only clean approvals.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `review_report.py`, change `_approvals` to count reviewers that completed with verdict APPROVED **and** zero
   counts and zero findings.
2. Change `_heading` (via `_is_approved` or a new guard) so the APPROVED heading requires the ensemble verdict APPROVED
   **and** every reviewer clean **and** no role in the merged role table at `CHANGES`; otherwise render the CHANGES
   REQUESTED heading. This is a defence in depth: the renderer cannot be made to lie by an upstream regression.
3. Confirm `render_reviewer_line` prints each reviewer's verdict and counts from the returned result; because Step 3
   passes returned reviewers, no further change should be needed, but add an assertion.
4. Check `flow/github/review_post.py` for any separate approval wording (for example a GitHub `APPROVE` event) and make
   it follow the same guard.
5. Tests in `tests/test_review_report.py`: render the failing shape (APPROVED, APPROVED, CHANGES_REQUESTED critical 1,
   Security CHANGES) with `ensemble_verdict` forced to APPROVED and assert the heading is not APPROVED, the fraction is
   `2/3`, and reviewer 3's line shows CHANGES_REQUESTED and critical 1; a clean shape still renders APPROVED (3/3).

### Dependencies & Criticality

- **Depends on:** Step 3 (shares the returned-reviewer contract); can be coded in parallel
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` "Review package format" and Invariant 4; Australian English in rendered text.
- **Potential failure modes for this step:** existing golden-text tests of the report change; GitHub posting path uses a
  separate approve event.
- **Guardrails and early‑abort checks:** update golden expectations only where the old text was the defect; keep the
  heading format otherwise byte-identical.

### Success & Discard Criteria

- **Success:** acceptance criterion 3 tests pass; existing report tests pass.
- **Discard:** none expected; if the post path cannot be guarded without touching out-of-scope code, document it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.2   |
| impact_pred         | 80.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 68.54 |

### Step Metrics Rationale

Localised renderer change with clear tests.
`EV = 0.88 * 80 + 0.5 * 2 - 0.3 * 1.2 - 2.5 = 70.4 + 1.0 - 0.36 - 2.5 = 68.54`.

---

## Step 5: Require evidenced reasons for resolver rejections of important or critical findings

- **Sub‑intent recommendation:** NO
- **Reasoning:** Moderately sized and touches the ledger vocabulary, but it closes the input that fed the suppression;
  keeping it in this plan avoids a window where suppression is fixed but "out of scope" still closes serious findings.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** A rejection of an important or critical finding counts only with a specific reason and evidence
  naming the code checked; otherwise the finding stays open and is carried to the next iteration.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `resolver_evidence.py` (package level, next to `resolver_result.py`) holding the evidence rule: a rejection of
   a finding at `important` or `critical` is accepted only when it carries a non-empty `evidence` string that names a
   file and line (a `path:line` token) or a test (a `tests/...::test_name` or `test_*.py` token) **and** its reason is
   not bare `out of scope`. Anything else is re-labelled with a new disposition `open` (reason null) and the original
   resolver text kept in the evidence field for audit.
2. Extend `FindingDisposition` with an optional `evidence` field and `DISPOSITIONS` with `open`; serialise and
   deserialise it in `review_report.py` (`serialize_finding_disposition`, `_disposition_from_mapping`).
3. Stop manufacturing "out of scope": `_normalise_rejection_reason` returns an explicit `unspecified` for an unknown or
   missing reason, and a missing disposition for an important or critical finding becomes `open` rather than `rejected`.
   Nits keep today's defaulting so loops still converge on trivial findings.
4. In `loop.py` (through the adjudication module), only `rejected` dispositions enter `rejected_history`; `open`
   findings are recorded as `ACT-RES` rows with `review_disposition` `open` and stay in the next iteration's findings.
   The end-of-loop fallback for remaining findings records important and critical rows as `open`, not `rejected`.
5. Update the container resolver prompt in `entrypoint/review_loop.py` (around the `rejection_reason` instructions) to
   require an `evidence` key naming `file:line` or a test for any important or critical rejection, and say that "out of
   scope" alone is not accepted for them.
6. Tests in `tests/test_resolver_evidence.py`: important rejected with bare `out of scope` becomes open; critical
   rejected with `inaccurate` and evidence `src/x.py:42` stays rejected; critical with reason but no evidence is open;
   nit with `out of scope` stays rejected; unknown reason no longer becomes `out of scope`. A loop test confirms an open
   finding is passed to the resolver again in the next iteration.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO (criterion 4 fails without it, but criteria 1 to 3 do not)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 3 (ledgers append-only: only new rows with a new
  value, no rewrites); `docs/ledger_schema.md` field definitions; "exactly three ledgers".
- **Potential failure modes for this step:** `open` breaks consumers that assume two dispositions (calibration,
  `ledger_query`, the dispositions table renderer); evidence regex too strict or too loose.
- **Guardrails and early‑abort checks:** grep all readers of `review_disposition` and `DISPOSITIONS` before the change
  and cover each in tests; historical rows are never edited; regex kept simple and documented.

### Success & Discard Criteria

- **Success:** acceptance criterion 4 tests pass and no existing ledger consumer test fails.
- **Discard:** if adding `open` breaks calibration in ways that need non-scoped changes, keep the finding unrecorded
  (pending) instead of a new value and record that choice.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 2.3   |
| impact_pred         | 75.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 54.06 |

### Step Metrics Rationale

New ledger vocabulary has unknown consumers, so success is the lowest of the implementation steps; learning value comes
from discovering how disposition values are consumed downstream.
`EV = 0.75 * 75 + 0.5 * 5 - 0.3 * 2.3 - 4.0 = 56.25 + 2.5 - 0.69 - 4.0 = 54.06`.

---

## Step 6: State the final ensemble verdicts in every end-of-flow message

- **Sub‑intent recommendation:** NO
- **Reasoning:** Wording change in a few known places, low risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** End-of-flow messages name each reviewer's final verdict and say plainly when any is not APPROVED;
  nothing describes such a run as ready for merge.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Use `render_final_verdicts` from Step 3 in: the `loop.py` summary ledger row text and action `error_context`; the
   Stage 4 halt and failure messages in `flow/stages/review.py`; the `holon review-loop` console summary in
   `entrypoint/review_loop.py` (container-side, so it builds the same line from its own results without importing the
   host flow engine).
2. The `[SAFETY HALT]` message is only reachable on real consensus after Step 3; still guard it: if any final verdict is
   not APPROVED it must say "not approved: reviewer N final verdict X" and must not say "consensus approval".
3. Search the flow package and entrypoints for "ready", "approved", "merge" wording in summary or notification paths
   (`flow/__init__.py`, `flow/pipeline.py`, `flow/stages/calibrate.py`, `flow/stages/children.py`) and apply the same
   rule wherever a summary is printed.
4. Keep `flow/stages/review.py` within its 322-line budget by reusing the shared helper (net change near zero); record a
   reason if a raise is unavoidable.
5. Tests: a Stage 4 test with a non-approved final ensemble asserts the message contains each reviewer's verdict and the
   words "not approved", and never "ready for merge" or "consensus approval".

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` "Human review workflow"; Australian English; no entrypoint imports into the flow.
- **Potential failure modes for this step:** brittle tests that assert old message strings; budget overflow in
  `stages/review.py`.
- **Guardrails and early‑abort checks:** update string assertions only where wording is intentionally changed.

### Success & Discard Criteria

- **Success:** acceptance criterion 5 tests pass.
- **Discard:** none expected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.0   |
| impact_pred         | 60.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 49.7  |

### Step Metrics Rationale

Simple wording with a budget wrinkle. `EV = 0.85 * 60 + 0.5 * 2 - 0.3 * 1.0 - 2.0 = 51.0 + 1.0 - 0.3 - 2.0 = 49.7`.

---

## Step 7: Document the nits-only adjudication rule, the verdict rule and the new disposition

- **Sub‑intent recommendation:** NO
- **Reasoning:** Documentation only.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Keep the docs consistent with the new behaviour.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `docs/git_flow.md` Stage 4 section: adjudication suppression is for nits only; critical, important and Security-role
   findings are never filtered; a higher-severity re-raise is a new finding; the verdict is computed from the returned
   reviewers and role tables, cross-checked against the container verdict; a pre/post-filter mismatch blocks consensus;
   the posted heading rule; end-of-flow wording.
2. `docs/ledger_schema.md`: `review_disposition` gains `open`; `rejection_reason` vocabulary and `unspecified`; new
   `evidence` field and the evidence rule for important and higher; restate that exactly three append-only ledgers exist
   and that this change adds no ledger.
3. Mention the follow-up for `holon-config/prompts/review_loop.template.md` wording (out of scope) in the PR
   description.
4. Run `uv run task lint-docs` (prettier check) and fix formatting.

### Dependencies & Criticality

- **Depends on:** Steps 2 to 6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Australian English; `holon-config/world/constraints.md` section 3.
- **Potential failure modes for this step:** prettier reflow; doc describing behaviour that changed during execution.
- **Guardrails and early‑abort checks:** write docs last, from the merged code.

### Success & Discard Criteria

- **Success:** `uv run task lint-docs` passes and both documents describe the rules.
- **Discard:** none.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.5   |
| impact_pred         | 40.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 36.15 |

### Step Metrics Rationale

Routine docs. `EV = 0.92 * 40 + 0.5 * 1 - 0.3 * 0.5 - 1.0 = 36.8 + 0.5 - 0.15 - 1.0 = 36.15`.

---

## Step 8: Refresh the move-proof fixture and budgets, then validate the whole suite

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical closing step; required for green CI.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Record every deliberate definition change and module size, then prove lint and tests pass.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `tests/flow/fixtures/flow_py_definitions.json`: refresh the digest of each changed original definition (for example
   `_filter_adjudicated_findings`, `_augment_pr_text_with_rejected`, `_run_review_loop_iterations`,
   `_reviewers_objected`, Stage 4 review stage functions) and add an `amended_definitions` entry per key naming this
   execution and the reason ("consensus decided from returned reviewer verdicts; adjudication suppression limited to
   nits"); add each new flow function to `added_definitions` with its module and reason. Use the existing
   `definition_digests` helper in `tests/flow/move_proof.py` to compute digests, never hand-written hashes.
2. `apps/sandbox-executor/flow_module_budgets.json`: add entries for `review/adjudication.py` and `review/verdict.py` at
   creation size, lower `review/resolver.py` to its new size, and record a reason for any raised budget.
3. Run affected tests first:
   `uv run pytest apps/sandbox-executor/tests/flow apps/sandbox-executor/tests/test_review_report.py apps/sandbox-executor/tests/test_review_ensemble.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_resolver_evidence.py`.
4. Then the full suite once with `uv run task test`, then `uv run task lint` and `uv run task lint-docs`. No repeated
   runs: the intent has no timing requirement.
5. Write the PR description with the confirmed cause (function, line, both match paths, the "out of scope" default and
   the discarded container verdict), the fix, and a statement that merging remains with the human maintainer.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 7
- **Is Bottleneck:** YES (acceptance criterion 6)

### Safety & Constraint Considerations

- **Relevant rules:** tests strictly via `uv run pytest`; human-only merge (`holon-config/world/constraints.md` section
  1); no new dependencies (`uv lock --check` inside `task lint`).
- **Potential failure modes for this step:** unrelated flaky tests; a missed amended definition.
- **Guardrails and early‑abort checks:** a failure unrelated to the touch set is reported, not "fixed" outside scope.

### Success & Discard Criteria

- **Success:** all three commands pass; PR opened to the plan branch with the cause documented.
- **Discard:** more than two unrelated failures after a rerun of only the failing tests; report and halt.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.7   |
| impact_pred         | 70.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 57.49 |

### Step Metrics Rationale

Mechanical but has many small gates. `EV = 0.85 * 70 + 0.5 * 2 - 0.3 * 1.7 - 2.5 = 59.5 + 1.0 - 0.51 - 2.5 = 57.49`.

---

### Strategy Rationale

The intent is accepted as is: it has very high impact (a false approval already let a sandbox escape reach `main`), a
cause that planning-time reading localises to a single line in `_filter_adjudicated_findings` plus four contributing
defects, and an entropy that fits the 15.0 budget, so no sub-intent split is needed. The plan fixes the cause first
(Steps 1 to 3) and then layers independent defences (Steps 4 to 6) so that the posted review, the halt and the end
message each refuse to claim approval on inconsistent input.

Aggregation: entropy is the sum of step entropies `1.0 + 2.5 + 2.5 + 1.2 + 2.3 + 1.0 + 0.5 + 1.7 = 12.7`; cost is the
sum `3.0 + 4.0 + 4.5 + 2.5 + 4.0 + 2.0 + 1.0 + 2.5 = 23.5`. Probability of success is bounded by the bottleneck chain
(Steps 2, 3 and 8) and the riskiest non-bottleneck step (Step 5), judged qualitatively at 0.75 rather than a strict
product, because the steps share one well-understood code path and failures are correlated rather than independent.
Impact is 90 (the consensus gate is the human-review safety boundary). Learning value is 6 (confirmed root cause and
downstream consumers of dispositions).
`EV = 0.75 * 90 + 0.5 * 6 - 0.3 * 12.7 - 23.5 = 67.5 + 3.0 - 3.81 - 23.5 = 43.19`.

Strong assumptions: the reviewer detail block carries role verdicts per reviewer (seen in `parse_reviewer_detail`); the
injected seams can express the failing shape; the move-proof fixture accepts amendments with reasons (the
`amended_definitions` mechanism exists).

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 1: human-only merge to `main`; work only on this plan's execution
    branch.
  - `holon-config/world/constraints.md` section 3 and `docs/ledger_schema.md`: three append-only ledgers, no rewrites.
  - `holon-config/world/ruleset.md` section 3: test changes are declared in this plan (Steps 1 to 6 and 8).
  - `docs/safety.md` principle 5 and Invariant 4 (human review is the final gate), Invariant 6 (immutability).
  - Flow module line budgets and ruff complexity limits without new `noqa`.
- **Potential violations or edge cases:**
  - Growing `loop.py` or `stages/review.py` past budget.
  - Adding the `open` disposition could be read as a schema change to the ledgers.
  - Stricter rules may make loops end without consensus more often (repeated nits, role objections without findings).
  - Editing out-of-scope prompt template `holon-config/prompts/review_loop.template.md`.
- **Mitigations built into the plan:**
  - New behaviour in new focused modules; extraction of duplicated CI-status code; any budget raise recorded with a
    reason.
  - `open` is a new field value on new rows only; no historical line is edited, no new ledger file.
  - Fail-closed defaults everywhere (unknown role detail, missing container verdict never upgrades to approval).
  - The template is not edited; its follow-up is noted in the PR description.
- **Residual risk accepted (and why):** more runs will halt without consensus and require a human; that is the intended
  safe direction for a gate whose failure mode was a false approval.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 12.7
- **Budget Compliance:** The strategy fits within budget (12.7 at or below 15.0), so it runs as one plan with no
  sub-intent list.
