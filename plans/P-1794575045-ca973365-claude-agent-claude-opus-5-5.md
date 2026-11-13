# Plan for I-1794575029-nopost

- **Plan ID:** P-1794575045-ca973365-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-13T13:04:06.723Z

## Planner Autonomy Summary

- **Intent handling:** REFRAME
- **Reframed intent (if applicable):**
  `{"slug": "nopost", "change": "close the remaining gap in the existing terminal-post gate (flow/review/posting.py decide_post, added by #177): a halt that lands on the last label (FAILED ensemble verdict from a crashed, timed-out or stalled reviewer turn, a review_iteration budget breach, an unresolved ref, unmeasured tests) is currently mistaken for 'iteration budget exhausted' and posted", "tests": "apps/sandbox-executor/tests/flow/review/test_loop.py (the intent's tests/flow/test_loop.py does not exist; the loop tests live under tests/flow/review/) plus matrix rows in tests/flow/review/test_posting_terminal.py", "extra_touch_set": ["flow/review/posting.py", "flow_module_budgets.json (only if loop.py grows)", "docs/review_consensus.md"]}`
- **Exploration stance:** balanced. Most of the goal has already shipped: `decide_post` in `flow/review/posting.py`
  withholds posts before the cap, on resolver errors, on no-convergence and on infrastructure failures. The plan starts
  with one cheap red test that proves the remaining gap before it changes any code.
- **Safety priority level:** elevated
- **Priority Justification:** The change controls an outward-facing write to GitHub (`post_pr_review_consensus`).
  `docs/safety.md` §5 (human review is the final gate) and the human-only merge rule in
  `holon-config/world/constraints.md` §1 both depend on the PR showing only trustworthy review state. A wrong gate could
  either spam the PR or suppress the one terminal approval the human relies on, so fail-closed behaviour must be proven
  by tests.

## Exploration

- **Proportion of steps that are exploratory:** 0.17
- **Justification:** One of six steps (Step 1, the red reproduction test) is BALANCED and exists to confirm the gap
  empirically. The rest is low-entropy exploitation of a gate that already exists.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.87  |
| entropy_pred        | 4.1   |
| impact_pred         | 68.0  |
| cost_pred           | 9.0   |
| learning_value_pred | 3.5   |
| ev_pred             | 49.68 |

### Strategy Rationale

**Current state (verified while planning):**

- `run_review_loop` in `apps/sandbox-executor/src/sandbox_executor/flow/review/loop.py` (904 lines, exactly at its
  `flow_module_budgets.json` budget of 904) already routes posting through `decide_post(PostFacts(...))` in
  `flow/review/posting.py` (139 lines, budget 139).
- `decide_post` posts when `consensus_reached` is true. It also posts whenever `iteration >= last_label`, unless the
  exit was `failed` (refusal or infrastructure error), had no completed reviewer, or was `halted_early` (resolver error
  or no-convergence).
- **Remaining gap:** "iteration budget exhausted" is inferred only from the label. Any other halt that lands on the last
  label is therefore posted. Such halts include the `ensemble_verdict is not CHANGES_REQUESTED` break (a FAILED verdict
  from a crashed, timed-out or stalled reviewer while another reviewer completed), the `iteration_breached` stop,
  `missing_ref` and `tests_not_measured`.
- Checkpoint resumes make the gap worse. `_run_engine_review_loop` calls `run_review_loop` with
  `max_iterations=remaining` and `first_iteration=done+1`. When one pass remains, `first_label == last_label`, so a
  FAILED pass after a resume is always posted. This is the orphaned-comment scenario the intent describes.
- `docs/review_consensus.md` ("When the review is posted") already promises that a stage budget stop posts nothing. The
  code breaks that promise when the stop happens at the cap.

**Strategy:** keep the decision in `posting.py`, the module that owns it. Add one fact to `PostFacts`: whether the
deciding iteration ended in a terminal verdict, meaning `CHANGES_REQUESTED` or `APPROVED` with every reviewer
`COMPLETED`. Also extend the loop's existing `halted_early` expression so that a budget breach, a missing ref and
unmeasured tests count as early halts. `decide_post` then refuses to post a non-terminal exit even at the cap. `loop.py`
changes by only about 2–4 lines, so its budget rises by that amount and the reason is recorded in the budget file's
description, following repository convention. The executions ledger already gets a `failed` summary row with
`halt_reasons`. The plan adds the skip reason to that row so the local record says why nothing was posted.

**Why REFRAME rather than ACCEPT_AS_IS:**

1. The intent's test path `tests/flow/test_loop.py` does not exist. The real loop tests are
   `tests/flow/review/test_loop.py` and `tests/flow/review/test_posting_terminal.py`.
2. Placing the code correctly requires touching `posting.py` and possibly `flow_module_budgets.json`, both outside the
   declared scope.
3. Much of goal 1 already exists, so the work is a narrow gap fix, not a new gate.

Rejecting or deferring was considered and dismissed. The gap is real, the PR pollution it causes is visible, and EV is
clearly positive.

**Aggregation:**

- `p_success` is the bottleneck product of the dependent implementation chain: Step 2 (0.93) times Step 4 (0.95) times
  the Step 5 gates (0.97), rounded down to 0.87 for module-budget or lint-docs surprises.
- Entropy is the sum of step entropies: `0.4 + 1.2 + 0.6 + 0.8 + 0.4 + 0.7 = 4.1`.
- Cost is the sum of step costs: `1.0 + 2.5 + 1.0 + 2.0 + 0.5 + 2.0 = 9.0`.
- Impact is a qualitative judgement (68). The fix makes resumed flows idempotent on GitHub, but it is a narrow edge of
  an existing gate.
- Learning value is the maximum step value plus a small bonus (3.5).
- Overall EV, with `mu = 0.5` and `lambda = 0.3` from `docs/metrics.md`:
  `EV = 0.87 * 68.0 + 0.5 * 3.5 - 0.3 * 4.1 - 9.0 = 59.16 + 1.75 - 1.23 - 9.0 = 50.68`. The table records a slightly
  conservative 49.68 to allow for a possible ledger-schema detour in Step 3.

**Strong assumptions:**

- `decide_consensus` reports a FAILED ensemble verdict whenever any reviewer turn is not `COMPLETED`. Step 1 verifies
  this.
- The executions summary row accepts one extra optional key. Step 3 checks the ledger schema and falls back to the
  existing `summary` text if it does not.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1: only humans merge to `main`; commits stay on the execution branch.
  - `docs/safety.md` §1 (Git is the safety boundary) and §5 (human review is the final gate).
  - `holon-config/world/ruleset.md` §2 and §3: Australian English, ruff complexity limits, tests only through
    `uv run pytest` and hermetic.
  - The intent's rule of exactly three append-only ledgers (`intents.jsonl`, `plans.jsonl`, `executions.jsonl`).
  - The `scripts/check_flow_module_budgets.py` line budgets run by `uv run task lint`.
- **Potential violations or edge cases:**
  - Over-suppression: an approval or a genuine cap-with-open-findings must still post exactly once.
  - `loop.py` exceeding its 904-line budget.
  - Adding complexity to `run_review_loop`, which already carries a historical
    `# noqa: C901, PLR0912, PLR0913, PLR0915`. No new suppression may be added anywhere.
  - A test that reaches the real `gh` or the network.
  - Writing a fourth ledger.
- **Mitigations built into the plan:**
  - The decision logic lives in `posting.py` (small and focused). `loop.py` only passes facts.
  - The full `decide_post` matrix is re-tested, including the existing positive cases.
  - The budget rise is limited to the measured line delta and documented.
  - Tests use the existing hermetic fakes (`tests/review_fakes.py`, `_LoopStubs`, `FakeEnsemble`) that patch
    `post_pr_review_consensus`.
  - Failure records go only into the existing `executions.jsonl` summary row.
- **Residual risk accepted (and why):** The new terminal-verdict rule could, in principle, suppress a cap post when a
  reviewer stalled at the very last label even though other reviewers produced findings. This is accepted on purpose:
  the intent explicitly treats a reviewer crash, timeout or stall as non-terminal, and the next resumed pass (or a
  human) produces the terminal post.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 4.1
- **Budget Compliance:** The strategy fits within budget (4.1 of 15.0), so it runs as one plan with no sub-intent list.

## Plan Description & Strategy

The plan has six steps, all executed on one branch,
`I-1794575029-nopost/P-1794575045-ca973365-claude-agent-claude-opus-5-5/E-{timestamp}-nopost/_`:

1. Write red tests that reproduce the cap-landing FAILED-verdict post (exploration and verification of the gap).
2. Extend `PostFacts` and `decide_post` in `posting.py`, and pass the new facts from `loop.py`.
3. Record the skip reason in the existing executions summary row and log it locally.
4. Complete the hermetic tests in `tests/flow/review/test_loop.py` and the `decide_post` matrix.
5. Align `docs/review_consensus.md` and the module budget description.
6. Run the gates: the targeted `uv run pytest`, `uv run task test` once, `uv run task lint` and `uv run task lint-docs`.

---

## Step 1: Reproduce the cap-landing FAILED-verdict post with red tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is a small, cheap test that runs locally and decides whether the gap is real. It is not reusable
  enough to justify its own branch.
- **Step Type:** EXPLORATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** With `max_iterations=1` (so `first_label == last_label`, exactly the resumed-flow case)
  and an ensemble of one `clean_reviewer` plus one `failed_reviewer`, `run_review_loop` currently calls the patched
  `post_pr_review_consensus` and writes an `ACT-POST` row. The same happens with a `review_iteration` budget breach on
  the last label.
- **Learning target:** Whether the leak exists exactly where the code reading predicts (`decide_post` falls through to
  `post=True` when `iteration >= last_label`). Also whether `decide_consensus` maps a mixed completed/failed ensemble to
  `ConsensusVerdict.FAILED`.
- **Maximum acceptable cost for this learning:** about 1.5 cost units (one test-writing session plus one targeted pytest
  run).

### Intent & Git Integration

- **Step Intent:** Prove the remaining posting leak before changing any production code.
- **Git branch:** `I-1794575029-nopost/P-1794575045-ca973365-claude-agent-claude-opus-5-5/E-{timestamp}-nopost/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/flow/review/test_loop.py`, reuse the helpers already imported by
   `test_posting_terminal.py`: `_ensemble_ctx`, `_LoopStubs`, `_rows` from `tests.test_review_loop`, `FakeEnsemble`,
   `clean_reviewer` and `failed_reviewer` from `tests.review_fakes`, and `EXEC_TARGET`.
2. Add a test in which a reviewer turn fails at the cap: run with `max_iterations=1`, `ensemble_size=2` and
   `FakeEnsemble([clean_reviewer(1), failed_reviewer(2)])`. Use a reason string such as "reviewer turn timed out".
   Assert that the post stub was not called and that no `ACT-POST` row exists.
3. Add a second test that resumes from a checkpoint: `first_iteration=3`, `max_iterations=1`, same mixed ensemble, same
   assertions.
4. Run only those tests with `uv run pytest apps/sandbox-executor/tests/flow/review/test_loop.py -k <names>` from the
   package directory, and confirm they fail (red) for the predicted reason. If either test passes, stop and re-read
   `decide_consensus` and the break order before changing any code. The remaining work then shrinks to tests only.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (hermetic tests via `uv run pytest`), `docs/safety.md` §2
  (sandboxed execution).
- **Potential failure modes for this step:**
  - The fakes do not support `ensemble_size=2`.
  - The mixed ensemble yields `CHANGES_REQUESTED` rather than `FAILED`.
- **Guardrails and early‑abort checks:**
  - Use only the patched `post_pr_review_consensus`.
  - The `gh_write_guard` and `network_guard` fixtures already fail any real GitHub write.

### Success & Discard Criteria

- **Success:** The new tests fail on unmodified `main` with the post stub called once.
- **Discard:** If the tests already pass on `main`, record the finding and skip Step 2's logic change. Keep the tests as
  regression guards.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.4   |
| impact_pred         | 15.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 14.18 |

### Step Metrics Rationale

`EV = 0.92 * 15.0 + 0.5 * 3.0 - 0.3 * 0.4 - 1.0 = 13.80 + 1.50 - 0.12 - 1.0 = 14.18`. Learning value is the highest of
any step because this step confirms or refutes the root-cause model behind the whole plan. Entropy is minimal because
only test code is added.

---

## Step 2: Gate posting on a terminal verdict in posting.py and pass the facts from loop.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is a small, contained edit to a 139-line module plus a 2–4 line call-site change, so a separate
  branch adds overhead without lowering risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make `decide_post` treat "budget exhausted" as a terminal state only when the deciding iteration
  produced a terminal verdict and no other early stop fired.
- **Git branch:** `I-1794575029-nopost/P-1794575045-ca973365-claude-agent-claude-opus-5-5/E-{timestamp}-nopost/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `flow/review/posting.py`, add a boolean field `verdict_terminal` to the frozen `PostFacts` dataclass, with a
   one-line comment. It is true only when the ensemble verdict is `CHANGES_REQUESTED` or `APPROVED` and every reviewer
   finished with `ReviewerStatus.COMPLETED`. Update the module docstring so it names the halted-iteration rule in
   Australian English.
2. In `decide_post`, keep the existing order: dry run or `--skip-post`, then failed or no reviewer completed, then
   `consensus_reached` posts. Directly after the consensus check, add a branch: when `verdict_terminal` is false, return
   no post with the reason "the iteration halted on a failed verdict or a crashed, timed-out or stalled reviewer turn;
   the report is kept local". Keep `count_suppressed` false so the existing suppressed-record count stays unchanged.
   Keep the function well below `C901 <= 10`.
3. Add a small pure helper next to `decide_post`, for example `is_terminal_verdict(verdict, reviewers)`. It takes the
   `ConsensusVerdict | None` and the reviewer results and returns the boolean. The decision rule then lives in
   `posting.py`, not in the loop. Import `ConsensusVerdict` and `ReviewerStatus` from the modules `loop.py` already
   imports them from (no new dependencies).
4. In `flow/review/loop.py`, at the `decide_post(PostFacts(...))` call:
   - Pass `verdict_terminal=is_terminal_verdict(ensemble_verdict, reviewers)`.
   - Widen `halted_early` so it also covers the other early stops: `budget_breach is not None`, `missing_ref` and
     `tests_not_measured`. This makes a `review_iteration` budget stop or an unmeasured-tests stop at the cap stay
     local, as `docs/review_consensus.md` already promises.
   - Import the helper through the existing `from sandbox_executor.flow.review.posting import ...` line.
5. Count the net line delta in `loop.py` and `posting.py`. If either file exceeds its entry in
   `apps/sandbox-executor/flow_module_budgets.json`, raise only that entry by the exact delta, and append one sentence
   to the budget file's `description` naming this intent's reason. Do not add any `# noqa`.
6. Re-run the Step 1 tests and confirm they now pass (green).

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:**
  - `docs/safety.md` §5 (the human gate relies on a truthful PR).
  - `holon-config/world/ruleset.md` §2 (ruff limits, Australian English).
  - The intent's "no new dependencies" rule.
  - The repository's "no imports from entrypoints into the host flow engine" rule.
- **Potential failure modes for this step:**
  - Over-suppressing the genuine cap post when every reviewer completed with `CHANGES_REQUESTED`.
  - Circular imports between `posting.py` and the verdict or ensemble modules.
  - A module-budget overrun.
- **Guardrails and early‑abort checks:**
  - Run the existing `test_posting_terminal.py` (`test_iteration_cap_with_open_findings_posts_changes_requested_once`
    and `test_clean_approval_posts_exactly_once`) right after the edit.
  - If either regresses, revert the `halted_early` widening first to isolate the cause.

### Success & Discard Criteria

- **Success:** The Step 1 tests pass, all existing `tests/flow/review/` and `tests/test_review_*` tests pass, and ruff
  reports no new findings.
- **Discard:** If correct placement makes the `loop.py` delta exceed 10 lines, or forces a new `# noqa`, stop. Re-plan
  by moving the whole `PostFacts` construction into a `posting.py` builder that takes the loop's state.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 1.2   |
| impact_pred         | 40.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 34.60 |

### Step Metrics Rationale

`EV = 0.93 * 40.0 + 0.5 * 1.5 - 0.3 * 1.2 - 2.5 = 37.20 + 0.75 - 0.36 - 2.5 = 35.09`. The table records 34.60 to allow
for a possible budget-file edit. This step carries most of the plan's impact. The risk is low because the decision is a
pure function that already has a parametrised test matrix.

---

## Step 3: Record the withheld post locally and in the executions ledger

- **Sub‑intent recommendation:** NO
- **Reasoning:** This adds one optional key and one log line to records the loop already writes, so the risk is low and
  nothing here is reusable.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make sure a halted iteration is logged locally and recorded in `executions.jsonl` with the reason
  nothing was posted, so an operator can audit a resume.
- **Git branch:** `I-1794575029-nopost/P-1794575045-ca973365-claude-agent-claude-opus-5-5/E-{timestamp}-nopost/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Confirm that `log_skipped_post` already writes "Not posting a review consensus for '<target>': <reason>." to
   `context.log`. Step 2's new reason flows through it unchanged.
2. Confirm that the summary row `run_review_loop` writes to `exec_file` already carries `status: failed` and the
   `halt_reasons` list (including `not approved: ...` from `not_approved_verdicts`, which names failed reviewers).
3. Add the skip reason to that summary row as one optional key, `review_post_withheld`, set to the decision reason or
   `None`. First check `docs/ledger_schema.md` and any schema validation used by `tests/test_ledger_append_only.py` and
   the ledger writer. If extra keys are rejected, append the reason to the existing `halt_reasons` list instead (no
   schema change, no new ledger).
4. Do not write an `ACT-POST` row for a withheld post. The existing contract writes `ACT-POST` only when a post was
   attempted.
5. Keep the `loop.py` delta to one line and account for it in Step 2's budget arithmetic.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** The intent's "exactly three append-only ledgers" rule, `docs/ledger_schema.md`, and
  `docs/safety.md` §1 (git and ledger as the audit boundary).
- **Potential failure modes for this step:** A schema test rejects the new key. The reason text could also leak
  credentials, but it is a fixed string, so it carries no credential data.
- **Guardrails and early‑abort checks:** Write only through the existing `exec_file` append path. Run
  `tests/test_ledger_append_only.py` after the edit.

### Success & Discard Criteria

- **Success:** A test run of a halted iteration produces exactly one summary row with `status == "failed"` and the
  withheld-post reason, the local log contains the "Not posting" line, and no `ACT-POST` row exists.
- **Discard:** If the summary-row change needs more than about 3 lines in `loop.py`, rely on the existing `halt_reasons`
  plus the log line instead.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.6   |
| impact_pred         | 10.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 8.52  |

### Step Metrics Rationale

`EV = 0.92 * 10.0 + 0.5 * 1.0 - 0.3 * 0.6 - 1.0 = 9.20 + 0.50 - 0.18 - 1.0 = 8.52`. Most of the logging already exists,
so the impact is modest. The step is still required by acceptance criterion 3.

---

## Step 4: Complete the hermetic tests for halted, aborted and terminal posting

- **Sub‑intent recommendation:** NO
- **Reasoning:** These tests belong with the code change they verify. Splitting them off would leave Step 2 unverified
  on its branch.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Encode the intent's three test requirements as hermetic regression tests.
- **Git branch:** `I-1794575029-nopost/P-1794575045-ca973365-claude-agent-claude-opus-5-5/E-{timestamp}-nopost/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/flow/review/test_loop.py`, finalise the Step 1 tests:
   - A failed reviewer turn (crash, timeout, stall) on the last label does not call `post_pr_review_consensus`. The test
     also asserts the "Not posting" log line and the Step 3 ledger reason.
   - The same holds for a resumed run (`first_iteration > 1`, `max_iterations=1`).
2. Add an aborted-loop test. A `review_iteration` budget breach on the last label, driven by an injected `clock` that
   advances past the stage budget, posts nothing and records `budget_breach` in the result. Optionally add a
   parametrised case for an infrastructure error (an exception from `run_ensemble_fn` or the existing refusal fake),
   which must also post nothing.
3. Add a positive-path test. An approved iteration posts exactly once, and a cap reached with every reviewer completed
   and `CHANGES_REQUESTED` (resolver pushing nothing) posts exactly once. Reuse `_rejecting_resolver` from
   `test_posting_terminal.py`, or a local equivalent if importing a private test helper across modules is unwanted.
4. In `tests/flow/review/test_posting_terminal.py`, extend the `_facts` default with `verdict_terminal=True` so existing
   rows keep their meaning. Add matrix rows:
   - `verdict_terminal False` at the cap means no post, with reason "failed verdict".
   - `verdict_terminal False` with `consensus_reached True` cannot happen and is not tested.
   - `halted_early True` at the cap remains no post.
5. Run
   `uv run pytest apps/sandbox-executor/tests/flow/review apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_review_ensemble.py`
   from the package directory (the `uv run task test` target sets cwd as configured).
6. If `test_loop.py` grows past about 400 lines, move the new posting tests into `test_posting_terminal.py` instead. The
   intent names `test_loop.py`, but the rule to keep files focused takes precedence.

### Dependencies & Criticality

- **Depends on:** Step 2, Step 3
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:**
  - `holon-config/world/ruleset.md` §3 (hermetic tests, `uv run pytest` only).
  - The sleep-patch guard (`tests/flow/sleep_patch.py`): do not patch `time.sleep` process-wide. Inject `sleep` and
    `clock` through the `run_review_loop` parameters.
- **Potential failure modes for this step:**
  - The fakes cannot express a timeout distinct from a crash. In that case, use `failed_reviewer` with distinct reason
    strings.
  - The clock injection does not trigger `iteration_breached`.
- **Guardrails and early‑abort checks:**
  - `gh_write_guard` and `network_guard` must stay active.
  - Each new test must finish in under 1 s, with no real sleeps.

### Success & Discard Criteria

- **Success:** All new and existing review tests pass under `uv run pytest`. Each acceptance criterion maps to at least
  one named test.
- **Discard:** If the budget-breach test needs real time or flakes once, replace it with a `decide_post` matrix row plus
  a fact-wiring assertion.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.8   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 12.76 |

### Step Metrics Rationale

`EV = 0.95 * 15.0 + 0.5 * 1.5 - 0.3 * 0.8 - 2.0 = 14.25 + 0.75 - 0.24 - 2.0 = 12.76`. The existing fakes cover nearly
every scenario needed, which keeps the probability of success high.

---

## Step 5: Align the review documentation and the module budget note

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is a short documentation edit that keeps the docs truthful and passes `lint-docs`.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the documented posting rule match the code.
- **Git branch:** `I-1794575029-nopost/P-1794575045-ca973365-claude-agent-claude-opus-5-5/E-{timestamp}-nopost/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `docs/review_consensus.md`, section "When the review is posted", add one sentence. It should say that an iteration
   halted by a FAILED verdict (a crashed, timed-out or stalled reviewer turn), by a stage budget stop or by unmeasured
   tests keeps the report local even when it lands on the last label. It should also say that a resumed run therefore
   posts only its terminal review, and that the withheld reason is logged and recorded on the executions summary row.
2. Keep the docs prettier-formatted (120-column wrap) and in Australian English.
3. If Step 2 raised a budget, confirm that the one-sentence note in `flow_module_budgets.json`'s `description` is in
   place.

### Dependencies & Criticality

- **Depends on:** Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** The Australian English convention and the `uv run task lint-docs` gate.
- **Potential failure modes for this step:** Prettier reflow differences, or a markdown-lint failure.
- **Guardrails and early‑abort checks:** Run `uv run task lint-docs` immediately after the edit.

### Success & Discard Criteria

- **Success:** `lint-docs` is clean and the docs describe the new rule.
- **Discard:** NONE; this step is trivially revertible.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.4   |
| impact_pred         | 4.0   |
| cost_pred           | 0.5   |
| learning_value_pred | 0.3   |
| ev_pred             | 3.36  |

### Step Metrics Rationale

`EV = 0.97 * 4.0 + 0.5 * 0.3 - 0.3 * 0.4 - 0.5 = 3.88 + 0.15 - 0.12 - 0.5 = 3.41`. The table records a rounded-down
3.36. The impact is small but this step prevents documentation drift.

---

## Step 6: Run the verification gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is a mandatory final check on the same branch.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the acceptance criteria: tests, lint and lint-docs all pass.
- **Git branch:** `I-1794575029-nopost/P-1794575045-ca973365-claude-agent-claude-opus-5-5/E-{timestamp}-nopost/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run the affected tests first:
   `uv run pytest tests/flow/review tests/test_review_loop.py tests/test_review_ensemble.py tests/flow/test_module_budgets.py tests/test_ledger_append_only.py`,
   from `apps/sandbox-executor`.
2. Run the full suite once with `uv run task test`. No repeated runs: the intent does not measure timing.
3. Run `uv run task lint` (ruff, complexity limits and `check_flow_module_budgets.py`), then `uv run task lint-docs`.
4. Commit on the execution branch with an Australian English message. Do not merge: merging stays with the human
   maintainer.

### Dependencies & Criticality

- **Depends on:** Step 4, Step 5
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 (no commits to `main`; human merge),
  `holon-config/world/ruleset.md` §3, and the test-run discipline (affected tests, then the full suite once).
- **Potential failure modes for this step:** An unrelated flaky test in the full suite, or a module-budget check
  failure.
- **Guardrails and early‑abort checks:** On an unrelated failure, re-run only that test once to classify it, and report
  it rather than editing outside scope.

### Success & Discard Criteria

- **Success:** All three gates pass, and the pass counts and pytest-reported times are recorded in the execution report.
- **Discard:** If a gate fails twice on in-scope code, stop and report it as a failed execution with the evidence.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.7   |
| impact_pred         | 8.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 5.80  |

### Step Metrics Rationale

`EV = 0.97 * 8.0 + 0.5 * 0.5 - 0.3 * 0.7 - 2.0 = 7.76 + 0.25 - 0.21 - 2.0 = 5.80`. The cost is dominated by the single
full-suite run.

---
