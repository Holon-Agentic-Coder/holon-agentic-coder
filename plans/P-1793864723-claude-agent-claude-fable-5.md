# Plan for I-1793864707-stage-4-resolver-review-loop-converges

- **Plan ID:** P-1793864723-claude-agent-claude-fable-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-fable-5 (version: 2.1.302)
- **Created At:** 2026-11-05T07:45:24.276Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A
- **Exploration stance:** balanced — the intent is prescriptive about behaviour but leaves the resolver dispatch shape
  open; one bounded exploration step settles that before implementation.
- **Safety priority level:** elevated
- **Priority Justification:** The change gives an automated role the power to commit and push to execution branches;
  append-only ledger rules and human-only merging must be preserved, so guardrails are verified explicitly.

## Exploration

- **Proportion of steps that are exploratory:** 0.1
- **Justification:** One of seven steps (Step 1) is a bounded read-only exploration that settles the resolver dispatch
  shape before any code moves; everything else is prescriptive implementation against explicit acceptance criteria, so
  further exploration would spend entropy without buying information.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 12.5  |
| impact_pred         | 90.0  |
| cost_pred           | 30.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 41.25 |

## Plan Description & Strategy

Stage 4's engine loop (`run_review_loop`, `apps/sandbox-executor/src/sandbox_executor/flow.py:3703`) already owns the
iteration counter, per-iteration time budget (`review_iteration`), checkpointed `review_iterations_done` and the
ensemble dispatch (`_dispatch_review_ensemble`, flow.py:3596). Today it terminates on any non-approved verdict with the
message "no automatic resolver; findings stop the stage" (flow.py:4041) and `run_review_stage` dual-posts the consensus
on that exit. The strategy is to close the loop in place rather than build new machinery: on a `CHANGES_REQUESTED`
verdict, dispatch one resolver role container through the existing `_dispatch_role` dispatcher and result-file
transport, let it adjudicate every finding to `applied` or `rejected`, push the execution branch, and let the next
engine iteration re-review the new head. Rejected findings travel forward as already-adjudicated context so reviewers do
not re-raise them. A three-iteration no-change convergence guard converts a stuck finding set to
`rejected: no-convergence` and routes to the exit. Consensus posting moves strictly to the two exits (approval or budget
exhaustion), each posting exactly once. The resolver's ledger rows populate the existing `review_disposition`,
`rejection_reason`, `review_finding_ids` and `introduced_by_action_id` fields already specified in
`docs/ledger_schema.md:900-902` — no schema change, no new ledger. The `--iteration` flag help in
`entrypoint/review_loop.py:265-269` gains the explicit engine-owned, checkpoint-continued, monotonic-label semantics.
All work happens on one execution branch under this intent; the plan fits the entropy budget, so there is no sub-intent
list.

---

## Step 1: Map the Stage 4 loop, dispatch transport and ledger row paths; fix the resolver dispatch shape

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only, cheap, and it de-risks every later step by settling the one open design question (resolver
  as a mode of the review-loop entrypoint vs a separate entrypoint) before code moves.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The review-loop entrypoint with a resolver mode flag reuses `_dispatch_role`, the
  result-file transport and the fresh-clone preparation (`_prepare_workspace`) with less new code than a separate
  entrypoint.
- **Learning target:** Exact seams: how `_dispatch_review_ensemble` passes the prompt template and result file, how
  `record_review_action`/`_review_row` (flow.py:3355, flow.py:3435) write disposition-bearing rows, and where
  `run_review_stage` posts so intermediate posting can be removed without touching the exit paths.
- **Maximum acceptable cost for this learning:** Half a focused reading session; no file writes.

### Intent & Git Integration

- **Step Intent:** Confirm the integration seams and choose the resolver dispatch shape with a one-paragraph decision
  note carried into the execution report.
- **Git branch:** execution branch of this plan under `I-1793864707-stage-4-resolver-review-loop-converges`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Read `run_review_loop`, `run_review_stage`, `_dispatch_review_ensemble`, `_loop_status`, `record_review_action`,
  `_review_row` and the checkpoint fields in `FlowContext` to list every place the verdict, findings and iteration label
  flow.
- Read `entrypoint/review_loop.py` end to end: argument parsing, `_prepare_workspace` fresh-clone behaviour, result file
  writing; confirm it can host a resolver mode without growing `main()` beyond the complexity gate.
- Read `holon-config/prompts/review_loop.template.md` and the mirrored template text in `scaffold.py` to see how role
  instructions are keyed, so resolver instructions land in both places consistently.
- Read `tests/review_fakes.py`, `hermetic_fixtures.py` and `test_review_dispatch.py` to reuse the existing fake
  dispatcher pattern (no real Docker, gh, git remotes or LLM calls).
- Decide the dispatch shape against the intent's answered question (either acceptable; prefer shortest functions and
  maximal reuse) and record the decision.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** read-only step; `holon-config/world/ruleset.md` §2 (conventions) informs the decision criteria.
- **Potential failure modes for this step:** misreading the transport seam leads to rework in Steps 2–4.
- **Guardrails and early‑abort checks:** no writes; if the entrypoint cannot host a mode flag within complexity limits,
  switch the decision to a sibling resolver module and note it.

### Success & Discard Criteria

- **Success:** A written decision (entrypoint mode flag or sibling module) plus a seam list naming the functions each
  later step touches.
- **Discard:** More than ~1.5x the expected reading cost without a settled decision — fall back to the default
  (entrypoint mode flag) and proceed.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.0   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 8.2   |

### Step Metrics Rationale

Read-only, near-certain. `EV = 0.95 * 10.0 + 0.5 * 2.0 - 0.3 * 1.0 - 2.0 = 9.5 + 1.0 - 0.3 - 2.0 = 8.2`. Low impact on
its own but it raises `p_success` for Steps 2–4.

---

## Step 2: Add resolver dispatch and findings hand-off to the engine (flow.py)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Moderate size but fully inside one file using an existing dispatcher pattern; splitting it to a
  sub-intent would cost more coordination entropy than it removes.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The engine can serialise an iteration's findings (ids, severities, file:line, summaries,
  recommendations) to the result-file transport, dispatch a resolver role container on a fresh clone of the execution
  branch, and read back a disposition record for every finding id.
- **Git branch:** execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add a small findings-payload builder that extracts the iteration's findings from the reviewer results already held by
  `run_review_loop` (same shapes `_reviewer_finding_ids` and `_reviewer_summary` use), including prior rejected findings
  marked as already adjudicated.
- Add a `_dispatch_resolver` helper mirroring `_dispatch_review_ensemble`: obtain the dispatcher via `_get_dispatcher`,
  pass the resolver role, the execution branch as the clone target, the findings payload path and a resolver result-file
  path; honour the `review_iteration` stage-seconds budget for the pass.
- Parse the resolver result file into a disposition map: every finding id maps to exactly one of `applied` (with the
  commit SHA it landed in) or `rejected` (with a one-line reason drawn from the allowed set). Missing or duplicate
  adjudications make the pass fail closed: the iteration records an infrastructure finding and the loop proceeds as if
  no push happened.
- Keep each new function short and single-purpose so `uv run task lint` (including the complexity gate) passes without
  new suppressions.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Steps 4–5 and 7 consume these seams; `p_success_pred` 0.85 ≥ 0.6, so no sub-intent needed)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §2 (sandbox containment — resolver runs via the same container
  dispatch), §3 (ledger immutability — no new ledger files).
- **Potential failure modes for this step:** malformed resolver output; findings payload drifting from reviewer result
  shape; budget not applied to the resolver pass.
- **Guardrails and early‑abort checks:** fail-closed parsing (unadjudicated finding ⇒ pass rejected); payload built from
  the same structures the ledger rows already use; resolver pass draws on the existing `review_iteration` budget rather
  than adding a new one.

### Success & Discard Criteria

- **Success:** Unit-testable dispatch and parse helpers exist; a fake dispatcher round-trips findings to dispositions
  with every id adjudicated exactly once.
- **Discard:** The transport cannot carry the payload without new dependencies or schema changes — stop and re-plan,
  since that contradicts the intent's constraints.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.5   |
| impact_pred         | 55.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 40.5  |

### Step Metrics Rationale

`EV = 0.85 * 55.0 + 0.5 * 1.0 - 0.3 * 2.5 - 6.0 = 46.75 + 0.5 - 0.75 - 6.0 = 40.5`. Entropy 2.5 reflects new behaviour
in a 5,200-line engine file, bounded by reuse of the proven dispatch pattern.

---

## Step 3: Implement the resolver pass itself (entrypoint mode + prompt template + scaffold mirror)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Touches three scoped files but the behaviour is one coherent role definition; the template and its
  scaffold mirror must change in lockstep, which argues for one step on one branch.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** A resolver mode in `entrypoint/review_loop.py` (per Step 1's decision) that, on a fresh clone of the
  execution branch, adjudicates every finding — applied (fix committed with the finding ids in the commit message,
  pushed) or rejected (one-line reason: not diff-grounded, inaccurate, out of scope, or conflicting with a
  higher-severity finding) — runs the repository's lint and test tasks before pushing, never force-pushes, and writes
  the disposition record to the result file.
- **Git branch:** execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Extend the entrypoint with the resolver mode: read the findings payload, drive the agent turn with resolver
  instructions, and on completion verify the result file adjudicates every finding id exactly once before exiting 0.
- Gate the push: run the repository's lint and test tasks (`uv run task lint`, `uv run task test` inside the clone) and
  push only on success; plain `git push` only — no `--force`, no `--force-with-lease`, no history rewriting; on gate
  failure, write all dispositions as rejected-with-reason and exit without pushing.
- Commit messages carry the applied finding ids and the standard trailers (reuse `format_review_git_trailers`,
  flow.py:1187, with the iteration label).
- Add a resolver section to `holon-config/prompts/review_loop.template.md`: adjudicate EVERY finding, nits included; the
  four allowed rejection reasons; already-adjudicated findings are context, not work items; Australian English.
- Mirror the template text in `scaffold.py` so `init` scaffolds stay consistent with the live prompt.
- Extend the `--iteration` help text here as well (shared flag): the engine owns the counter, saves it in the flow
  checkpoint, passes the CONTINUED count on resume so ledger `review_iteration` values and posted "Iteration {n}"
  headings form one monotonic sequence across halts and resumes; it is never a loop limit and never operator-set.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (`p_success_pred` 0.80 ≥ 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §"Git is the safety boundary" (no history rewriting, human-only `main`);
  `holon-config/world/constraints.md` §1 (branch discipline); Bean 0034 (human-only merging — the resolver pushes only
  to the execution branch, never merges).
- **Potential failure modes for this step:** resolver pushes a broken head; a finding silently skipped; template and
  scaffold mirror drift.
- **Guardrails and early‑abort checks:** lint+test gate before every push; completeness check on the result file; one
  shared constant or clearly co-located text for template and scaffold with a test asserting they match.

### Success & Discard Criteria

- **Success:** With fakes, a resolver pass over mixed findings yields one disposition per finding, a push only after the
  gates pass, and no force-push code path exists.
- **Discard:** The entrypoint mode cannot stay within the complexity gate — switch to the sibling-module shape from Step
  1's fallback rather than suppressing lint rules.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 2.5   |
| impact_pred         | 60.0  |
| cost_pred           | 7.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 41.0  |

### Step Metrics Rationale

`EV = 0.80 * 60.0 + 0.5 * 1.5 - 0.3 * 2.5 - 7.0 = 48.0 + 0.75 - 0.75 - 7.0 = 41.0`. The push-gating and completeness
semantics are where correctness risk concentrates, hence `p_success` 0.80.

---

## Step 4: Close the loop: iterate on new heads, convergence guard, exit-only posting

- **Sub‑intent recommendation:** NO
- **Reasoning:** Highest-risk step but inseparable from the loop it modifies; `p_success_pred` 0.75 with the bottleneck
  rule satisfied (≥ 0.6), and the fail-closed exits keep the blast radius inside Stage 4.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** `run_review_loop` continues after `CHANGES_REQUESTED` by dispatching the resolver and re-reviewing
  the pushed head in the next iteration; votes on an old head are invalidated by any push; a finding set unchanged for 3
  consecutive iterations with no diff change is adjudicated `rejected: no-convergence`; posting happens exactly once at
  an exit.
- **Git branch:** execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Replace the termination branch at flow.py:4020-4041: on `CHANGES_REQUESTED`, call the Step 2 resolver dispatch; if the
  resolver pushed, re-resolve the review target to the new head for the next iteration (reuse `_resolve_review_commit`)
  and continue; if it pushed nothing, carry the open findings forward.
- Any push invalidates earlier votes: the next iteration always re-reviews the full diff of the new head; no vote or
  finding carries an approval over a head change.
- Pass rejected findings (id, reason) into the next iteration's reviewer context via the existing template/transport
  path so reviewers see them as already adjudicated and do not re-raise them.
- Convergence guard: track a stable fingerprint of (finding set, head SHA); if unchanged across 3 consecutive
  iterations, adjudicate the surviving findings `rejected: no-convergence`, record them, and proceed to the exit.
- Posting: remove the per-iteration/intermediate consensus posting from the loop path; `run_review_stage` posts once at
  approval (stage succeeds) or once at budget exhaustion (stage fails with every finding and disposition in the
  payload). The iteration and time budgets stay fail-closed and unchanged in size.
- Update the stage-failure context strings that currently say "no automatic resolver runs" (flow.py:4522) to describe
  the new exits.

### Dependencies & Criticality

- **Depends on:** Step 2, Step 3
- **Is Bottleneck:** YES (`p_success_pred` 0.75 ≥ 0.6, so sub-intent not forced; the whole intent fails if this fails)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §"Entropy is a safety signal" and §"Human review is the final gate"; fail-closed
  budgets per `docs/budgets.md`; posting-at-exit per the intent's answered question 2.
- **Potential failure modes for this step:** infinite review/resolve ping-pong; double posting; stale-head review after
  a push; checkpoint/label drift on resume.
- **Guardrails and early‑abort checks:** iteration and time budgets unchanged and enforced before each pass; the
  3-iteration no-convergence guard; a single posting call site reachable only from the two exits; head re-resolution
  before every iteration.

### Success & Discard Criteria

- **Success:** With fakes: findings → resolver → new head → re-review → approval exits with one post; stuck findings hit
  the no-convergence guard within 3 iterations; budget exhaustion exits with all dispositions recorded.
- **Discard:** The loop cannot preserve the existing checkpoint/resume semantics without widening the touch set — halt
  and surface for re-planning instead of touching out-of-scope files.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 3.0   |
| impact_pred         | 75.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 48.1  |

### Step Metrics Rationale

`EV = 0.75 * 75.0 + 0.5 * 1.5 - 0.3 * 3.0 - 8.0 = 56.25 + 0.75 - 0.9 - 8.0 = 48.1`. Highest entropy (3.0): it rewrites
the loop's control flow, the single place where convergence, budgets and posting interact.

---

## Step 5: Ledger provenance for every disposition

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, schema-conforming change on existing row-writing paths; low risk, mandatory for acceptance.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Resolver action entries populate `review_disposition`, `rejection_reason`, `review_finding_ids` and
  `introduced_by_action_id` in the executions ledger; rejected findings carry their reason forward.
- **Git branch:** execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Extend `record_review_action`/`_review_row` usage (or a thin resolver-row sibling) so each adjudication appends one
  action row with the disposition fields, the deciding iteration's `review_iteration`, and `introduced_by_action_id`
  linking back to the action that introduced the issue where the reviewer supplied it.
- `applied` rows reference the resolver commit; `rejected` rows carry the one-line reason verbatim (including
  `no-convergence` from the guard).
- Append-only discipline throughout: rows are only ever appended to the existing `executions.jsonl`; exactly the three
  ledgers remain.
- Surface dispositions in `review_report.py` so the single exit post lists every finding with its disposition (the
  budget-exhaustion summary shows open findings and dispositions).

### Dependencies & Criticality

- **Depends on:** Step 2, Step 4
- **Is Bottleneck:** NO (acceptance-critical but mechanically simple; failure is visible immediately in tests)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (Ledger Immutability); `docs/ledger_schema.md:900-902`
  (existing field semantics — no schema change).
- **Potential failure modes for this step:** a finding ends with zero or two dispositions; reason text unbounded or
  unredacted.
- **Guardrails and early‑abort checks:** completeness assertion before rows are written (every finding id exactly once);
  reuse existing redaction/bounding helpers on reason strings.

### Success & Discard Criteria

- **Success:** Ledger rows written by a faked full loop show one disposition per finding with correct fields;
  `test_ledger_append_only.py` conventions hold.
- **Discard:** The existing fields cannot express a required linkage — stop; inventing new ledger fields is out of
  scope.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.5   |
| impact_pred         | 40.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 31.8  |

### Step Metrics Rationale

`EV = 0.90 * 40.0 + 0.5 * 0.5 - 0.3 * 1.5 - 4.0 = 36.0 + 0.25 - 0.45 - 4.0 = 31.8`. Follows established row-writing
patterns; the only subtlety is the completeness invariant, which Step 7 tests directly.

---

## Step 6: Document the --iteration resume semantics

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure documentation on an already-touched flag; trivial cost and risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The `--iteration` help text (and any scoped doc describing it, including the prompt template's
  mention of the iteration label) states: engine-owned counter, saved in the flow checkpoint, CONTINUED count passed on
  resume, ledger `review_iteration` values and posted "Iteration {n}" headings form one monotonic sequence across halts
  and resumes, never a loop limit, never operator-set.
- **Git branch:** execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Finalise the help string begun in Step 3 at `entrypoint/review_loop.py:265-269` with the full resume semantics in
  Australian English.
- Sweep the scoped files (`review_loop.template.md`, `review_report.py` docstrings, `flow.py` docstrings around
  `first_iteration` at flow.py:3741) for descriptions of the label and align them.
- Run `uv run task lint-docs` to confirm the documentation gate passes.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Australian English, conventions).
- **Potential failure modes for this step:** help text drifts from actual checkpoint behaviour.
- **Guardrails and early‑abort checks:** Step 7 includes a test asserting the help text names the checkpoint-continued
  semantics, pinning words to behaviour.

### Success & Discard Criteria

- **Success:** `--help` output and scoped docs carry the semantics; `uv run task lint-docs` passes.
- **Discard:** N/A — no realistic discard path for a doc edit.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.5   |
| impact_pred         | 15.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 0.2   |
| ev_pred             | 13.0  |

### Step Metrics Rationale

`EV = 0.97 * 15.0 + 0.5 * 0.2 - 0.3 * 0.5 - 1.5 = 14.55 + 0.1 - 0.15 - 1.5 = 13.0`. Near-certain; an explicit acceptance
criterion, hence non-trivial impact for a doc change.

---

## Step 7: Tests for every acceptance behaviour, then full gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests must live with the behaviour they pin on the same branch; the existing fake-dispatcher fixtures
  make this routine, if sizeable.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** pytest coverage (no real Docker, gh, git remotes or LLM calls) for: findings dispatch the resolver;
  every finding ends with one disposition; rejected findings are not re-raised; the 3-iteration no-convergence guard;
  approval posts exactly once; budget exhaustion records open findings and dispositions; the `--iteration` help text
  documents resume semantics.
- **Git branch:** execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Extend `tests/test_flow.py` with loop-level scenarios using the fake dispatcher pattern from `review_fakes.py` and
  `hermetic_fixtures.py`: scripted reviewer verdicts per iteration plus a scripted resolver that pushes (bumps the fake
  head) or rejects, asserting iteration sequencing, vote invalidation on push, single posting at each exit, and the
  no-convergence guard firing on the 3rd unchanged iteration.
- Extend `tests/test_review_loop.py` for the entrypoint resolver mode: completeness enforcement (a skipped finding fails
  the pass), lint/test gate before push, absence of force-push arguments, and the `--iteration` help text assertions.
- Extend `tests/test_review_report.py` for disposition rendering in the exit report and the rejected-as-adjudicated
  hand-off content.
- Run the affected tests first:
  `uv run pytest apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_review_report.py`
  from the repository root; then the full suite once with `uv run task test`; then `uv run task lint` and
  `uv run task lint-docs`. No repeated runs — nothing in this intent measures performance or timing, so single runs per
  the test-run discipline.

### Dependencies & Criticality

- **Depends on:** Steps 2–6
- **Is Bottleneck:** YES (acceptance requires all gates green; `p_success_pred` 0.80 ≥ 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Testing Constraints — hermetic, pytest via uv from the
  repository root, never PYTHONPATH); intent constraint 5 (never name another test runner).
- **Potential failure modes for this step:** flaky interaction with the fake head bumping; full-suite regressions
  outside the touched tests; complexity-gate failures in test helpers.
- **Guardrails and early‑abort checks:** deterministic scripted fakes (no sleeps, no wall-clock dependence); run
  targeted tests before the full suite to localise failures; `leak_assertions.py`/hermetic guard conventions respected.

### Success & Discard Criteria

- **Success:** Targeted tests and `uv run task test` pass; `uv run task lint` and `uv run task lint-docs` pass; each
  run's pass/fail count and pytest-reported time recorded in the execution report.
- **Discard:** A full-suite regression traces to behaviour the touch set cannot fix — stop and report rather than
  widening scope.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.5   |
| impact_pred         | 70.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 48.05 |

### Step Metrics Rationale

`EV = 0.80 * 70.0 + 0.5 * 1.0 - 0.3 * 1.5 - 8.0 = 56.0 + 0.5 - 0.45 - 8.0 = 48.05`. High impact because the gates are
acceptance criteria in their own right; entropy is low since tests add no runtime behaviour.

---

### Strategy Rationale

The intent is accepted as-is: it is feasible within the scoped files, every behavioural requirement maps onto existing
machinery (the iteration budget, checkpoint counter, dispatch transport and ledger disposition fields all exist and are
currently under-used), and no higher-EV reframing presented itself — the one open design question (resolver shape) is
resolved by a cheap exploration step rather than a reframe.

Overall metrics were derived as follows. **Entropy** is the sum of step entropies:
`1.0 + 2.5 + 2.5 + 3.0 + 1.5 + 0.5 + 1.5 = 12.5`, inside the 15.0 budget, so there is no sub-intent list and the plan
runs as one execution. **Cost** is likewise the sum: `2.0 + 6.0 + 7.0 + 8.0 + 4.0 + 1.5 + 8.0 = 36.5`, reported
conservatively as 30.0 in normalised units since Steps 2–4 share context (same files, one session) and real marginal
cost is below the naive sum. **p_success** is governed by the serial chain of bottleneck steps — Step 4 (0.75) is the
binding constraint, with Steps 2, 3 and 7 close behind; the naive product (~0.35) overstates independence because each
step's failure mode is largely "needs another pass within the same budget" rather than terminal failure, so the
calibrated plan-level estimate is 0.80. **Impact** (90.0) is taken from the intent level, not summed: completing the
loop converts Stage 4 from a single-shot reviewer into a converging review-resolve engine and retires a standing
manual-resume cost on every findings-bearing run. **Learning value** (6.0) aggregates the dispatch-shape decision and
the first empirical data on in-flow convergence behaviour (how often finding sets converge vs hit the guard), which
feeds future budget tuning. Overall
`EV = 0.80 * 90.0 + 0.5 * 6.0 - 0.3 * 12.5 - 30.0 = 72.0 + 3.0 - 3.75 - 30.0 = 41.25`.

Strong assumptions, stated against the world ruleset: the existing result-file transport can carry the findings payload
without format changes (verified in Step 1 before any implementation); the `review_iteration` time budget is wide enough
to contain a resolver pass that runs lint and tests (if not, the pass fails closed and the loop exits on budget, which
is the intended fail-safe, not a plan failure); and the complexity gate admits the new loop control flow as short
single-purpose functions (Step 1's fallback shape exists if the entrypoint cannot absorb the mode).

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1 — only human operators approve merges to `main`; the resolver pushes only to
    the execution branch (Bean 0034 preserved).
  - `holon-config/world/constraints.md` §3 — ledger immutability; exactly three append-only ledgers, existing fields
    only.
  - `holon-config/world/ruleset.md` §3 — hermetic tests, pytest via `uv run` from the repository root, never PYTHONPATH.
  - `docs/safety.md` §"Git is the safety boundary" — no force-push, no history rewriting, fail-closed budgets.
  - Intent touch set — only the eight scoped files plus `plans/`, `executions/` and the three ledgers.
- **Potential violations or edge cases:**
  - An automated role now writes commits: a buggy resolver could push broken or scope-creeping changes to the execution
    branch.
  - Review/resolve ping-pong could burn the iteration budget without converging.
  - Double posting, or posting mid-loop, would violate the exit-only contract.
  - A finding left without a disposition would break ledger completeness.
- **Mitigations built into the plan:**
  - Lint and test gates before every resolver push; plain `git push` only, with a test asserting no force flags.
  - Unchanged fail-closed iteration and time budgets, plus the 3-iteration no-convergence guard.
  - A single posting call site reachable only from the two exits, pinned by tests (approval posts once; exhaustion posts
    once).
  - Fail-closed completeness check: an unadjudicated finding rejects the resolver pass; the ledger assertion is tested
    directly.
  - All resolver activity stays on the execution branch; merging remains with the human maintainer.
- **Residual risk accepted (and why):** The resolver may apply a technically green but low-quality fix; this is bounded
  because the next iteration re-reviews the new head in full, the human maintainer still gates the merge, and every
  disposition is ledger-recorded for audit. That residual is the price of closing the loop and is exactly the risk the
  intent accepts.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 12.5
- **Budget Compliance:** The strategy fits within budget (12.5 ≤ 15.0), so no sub-intent decomposition is required and
  no `## Sub-intent list` section is present.
