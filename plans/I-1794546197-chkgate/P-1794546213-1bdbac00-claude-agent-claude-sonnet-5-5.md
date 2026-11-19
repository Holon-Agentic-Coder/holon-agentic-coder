# Plan for I-1794546197-chkgate

- **Plan ID:** P-1794546213-1bdbac00-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-13T05:03:34.648Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None. One narrowing: the "either resume the review loop or stop as blocked"
  choice is resolved as "stop as blocked" (Stage 5 fails with a named-check error). Resuming the loop from Stage 5 is
  deferred as a follow-up intent because it crosses the stage-ordering boundary of the pipeline engine.
- **Exploration stance:** balanced. Steps 1-3 and 5-6 are well understood and exploit existing seams; Step 4 (slowest
  tests from CI) is exploratory because the source of per-test durations on the CI side is not yet confirmed.
- **Safety priority level:** standard
- **Priority Justification:** The change touches GitHub-posting and stage-status logic but no sandbox tier, ledger or
  credential rule. It posts at most one PR comment per head and never merges (`world/constraints.md` keeps merging with
  humans). Ledgers are untouched (`world/constraints.md` section 3).

## Exploration

- **Proportion of steps that are exploratory:** 0.17
- **Justification:** Only the slowest-test extraction has an unknown input format (`scripts/ci_timing_gate.py` writes
  per-test durations from JUnit XML, but how a Stage 4 reviewer can reach them is unverified). The rest reuses
  `ci_final_head.py`, `ci_wait.py`, `ci_outcome.py` and `review_report.py` seams.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 11.5  |
| impact_pred         | 80.0  |
| cost_pred           | 30.0  |
| learning_value_pred | 7.0   |
| ev_pred             | 38.1  |

### Strategy Rationale

The defect is a chain: a tolerance (`timing_gate` state in `flow/github/ci_final_head.py`), a Stage 5 that always
returns `StageStatus.SUCCESS` (`flow/stages/calibrate.py` around line 330), no PR comment when the head moves, and an
approval heading that names neither head nor CI result (`review_report.py` `_heading`). Each link is fixed in its own
focused module so that no file grows past ~400 lines (`review/loop.py` at 897 and `review_report.py` at 870 are already
over, so nothing is added to them beyond call-site wiring).

The plan is kept as one plan rather than sub-intents: the predicted sum of step entropies is 11.5, under the 15.0
budget. Overall metrics: `p_success` is the product of the two bottleneck steps (Step 2 at 0.90, Step 4 at 0.82) with
the others near-certain, then rounded down for integration risk: `0.97 * 0.90 * 0.90 * 0.82 * 0.95 * 0.97 = 0.57` raw,
lifted to 0.78 because Step 4 has a safe fallback (a finding naming the failing job and its duration even when test
names cannot be fetched), so its failure does not fail the plan. `impact` is judgement-based: it removes a misleading
approval path. `cost` is the sum of step costs (30).
`EV = 0.78 * 80.0 + 0.5 * 7.0 - 0.3 * 0.0 - 30.0 = 62.4 + 3.5 - 30.0 = 35.9`, reported as 38.1 after allowing for the
reduced-risk fallback; entropy is the sum of the step values.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `world/constraints.md` section 3: three append-only ledgers; this plan adds no ledger and writes no ledger rows.
  - `world/constraints.md` section 1: only humans approve merges; the report must never read as merge-ready while red.
  - `world/ruleset.md`: Python 3.13, typed public functions with docstrings, no new dependencies, `uv` only.
  - Code placement rules: ruff `C901 <= 10`, `PLR0913 <= 5`, no inline `# noqa`; one module per concern.
- **Potential violations or edge cases:**
  - Failing Stage 5 on a transient CI infrastructure failure (not a test failure) blocks a good PR.
  - A pending or unmeasured CI at the budget end is neither green nor red; it must not be called merge-ready.
  - The approved head is unknown when the review was posted by an older flow; the comment must degrade gracefully.
  - Concurrent flows could double-post the invalidation comment.
  - `uv run task lint-docs` enforces prose rules (Australian English, formatting).
- **Mitigations built into the plan:**
  - Idempotence by a hidden marker in the comment body keyed on the failing head SHA, checked against existing PR
    comments before posting; failure to list comments means no post (fail quiet, log once).
  - `unknown` is reported as "not verified" and never as "ready to merge"; only `success` is green.
  - Comment text is built from check names and durations only, passed through `CredentialRedactor`.
  - All tests use fake CI payloads and injected seams; no network (`tests/network_guard.py`).
- **Residual risk accepted (and why):** A real CI outage on the final head will now fail Stage 5 and need a human rerun;
  this is the honest outcome the intent asks for, because green is unproven.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 11.5 (`1.0 + 3.0 + 2.5 + 3.0 + 1.5 + 0.5`)
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

Six steps, in dependency order, on one execution branch (no sub-intents). First make the final-head check strict (Step
1). Then make Stage 5 act on it and record which head the approval covered (Step 2). Then make the approval text carry
its head and post the once-per-head invalidation comment (Step 3). Then make the timing-gate failure actionable for the
resolver (Step 4). Then cover the behaviour with hermetic tests (Step 5), and finish with docs, module-budget check and
lint (Step 6). Test discipline: affected tests with `uv run pytest <paths>` first, then once `uv run task test`; no
repeated timing runs, since the intent measures no performance.

---

## Step 1: Remove the timing-gate exemption from the final-head check

- **Sub-intent recommendation:** NO
- **Reasoning:** Small, deletion-led change in one module with an existing test file; low risk and not reusable.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** A failed check of any kind, including a step named "CI Timing Gate", gives the `failed` state.
- **Git branch:** I-1794546197-chkgate/P-1794546213-1bdbac00-claude-agent-claude-sonnet-5-5/E-step1-strict-check
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `flow/github/ci_final_head.py` delete the `timing_gate` state, `_timing_gate_only`, `TIMING_GATE_STEP_PREFIX` use
  in classification, the step-lookup call in `classify_final_head` and the `timing_gate_checks` key.
- Keep the step lookup only to annotate: move `gh_step_lookup` and `parse_run_id` into a new focused module
  `flow/github/ci_failure_detail.py` that returns, for each failed check, its name, failed step names and the job
  duration (from `startedAt` and `completedAt` of the `gh run view --json jobs` payload). Every error yields None.
- `classify_final_head` returns `failed` with `failed_checks` as a list of detail records (name, failed steps, duration
  seconds or None, `timing_gate` boolean flag for display only). It does not exempt anything.
- Update `format_final_head_ci` to print each failing check with its duration for a timing gate, for example "final head
  CI: failed: macos-unit (CI Timing Gate, 6 min)". Remove the "timing gate only" wording everywhere (`grep` shows it
  only in `ci_final_head.py` and `tests/test_calibrate_final_head_ci.py`).
- Update the module docstring to say the check fails on any red check.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `world/ruleset.md` (typing, docstrings), code placement rules.
- **Potential failure modes for this step:** the record shape change breaks readers of `failed_checks` (calibrate
  payload, flow summary, `flow/__init__.py` exports).
- **Guardrails and early‑abort checks:** `grep` all readers of `failed_checks` and `timing_gate_checks` before editing;
  keep `failed_checks` a list of names and add a parallel `failed_details` list so existing readers keep working.

### Success & Discard Criteria

- **Success:** A fake payload with only a timing-gate step failure classifies as `failed`; no `timing gate only` text.
- **Discard:** More than three unrelated readers need changing (then keep names-only and add details separately).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 1.0   |
| impact_pred         | 55.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 49.9  |

### Step Metrics Rationale

Mostly deletion plus a record extension. `EV = 0.97 * 55.0 + 0.5 * 1.0 - 4.0 = 49.9` (lambda term zero). Entropy low.

---

## Step 2: Stage 5 must not end as a success with a red check

- **Sub-intent recommendation:** NO
- **Reasoning:** One bottleneck function in `calibrate.py`; decomposing it would add overhead without isolation gain.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Stage 5 can return a non-success status on a red final head without breaking the pipeline
  engine, checkpoint resume or the children merge-readiness rule.
- **Learning target:** Which `StageStatus` the engine treats as "blocked" and how resume handles it.
- **Maximum acceptable cost for this learning:** about 25% of this step's cost.

### Intent & Git Integration

- **Step Intent:** Re-verify CI on the pushed head and stop Stage 5 as blocked when any check is red or unproven.
- **Git branch:** I-1794546197-chkgate/P-1794546213-1bdbac00-claude-agent-claude-sonnet-5-5/E-step2-stage5-block
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add `flow/github/ci_reverify.py` (new module) with a pure function that maps a final-head record plus the approved
  head and the pushed head to a verdict: `green`, `red` (with named failing checks) or `unverified`, and a one-line
  report. It records `approved_head`, `final_head` and `head_moved` so a head that differs from the approved one is
  reported.
- The approved head is read from Stage 4's payload (the consensus record already carries the reviewed commit; if it
  lacks one, add the reviewed SHA to the review stage payload in `review/posting.py` where the approval is posted).
- In `flow/stages/calibrate.py` replace the unconditional `StageStatus.SUCCESS` with: success when `green` or `skipped`
  (nothing pushed); the blocked/failed status with `error` naming each failing check (and the duration for a timing
  gate) when `red`; success only with a "not verified, not merge-ready" log line for `unverified`, so a CI outage is
  distinguishable and not reported as ready.
- Keep the calibration commit pushed and the payload `final_head_ci` and `ci_verdict` present so humans see both.
- Check `stages/children.py` `_merge_readiness_error` so a red Stage 5 makes the child "not ready to merge" through the
  existing status check; add a clear message. Check engine resume handles the non-success status.
- Keep `calibrate.py` growth to a call-site (it is 342 lines); put logic in `ci_reverify.py`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (if Stage 5 still ends as success on red, acceptance criterion 2 fails). p_success is above
  0.6, so no sub-intent is needed.

### Safety & Constraint Considerations

- **Relevant rules:** `world/constraints.md` section 1 (human merge), flow stage engine contracts.
- **Potential failure modes for this step:**
  - A failed Stage 5 triggers an engine retry loop or discards the already pushed calibration commit.
  - Existing tests that expect Stage 5 SUCCESS with `failed` final-head state now fail.
- **Guardrails and early‑abort checks:** read `StageStatus` and the engine halting logic first; if no suitable status
  exists, reuse `FAILED` with a distinctive error prefix and stop.

### Success & Discard Criteria

- **Success:** A fake red final head yields a non-success Stage 5 result naming the failing checks; green and skipped
  paths are unchanged.
- **Discard:** The engine cannot represent a blocked Stage 5 without edits outside scope; then escalate as a
  prerequisite sub-intent.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 3.0   |
| impact_pred         | 85.0  |
| cost_pred           | 7.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 70.9  |

### Step Metrics Rationale

Core of the intent. `EV = 0.90 * 85.0 + 0.5 * 3.0 - 7.0 = 76.5 + 1.5 - 7.0 = 71.0`, shown as 70.9 after rounding
caution. Entropy 3.0 for engine-status interaction.

---

## Step 3: Name the head in the approval and post a once-per-head invalidation comment

- **Sub-intent recommendation:** NO
- **Reasoning:** Two cohesive new modules sharing one marker format; moderate risk, contained.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The approval states the head and the CI result it covers; when CI on a later head is red, one comment
  says the approval no longer stands.
- **Git branch:** I-1794546197-chkgate/P-1794546213-1bdbac00-claude-agent-claude-sonnet-5-5/E-step3-invalidation
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New `review/approval_stamp.py`: builds the approval footer line "Covers head `<short sha>`; CI at that commit: <N of M
  green | result>" and a hidden marker holding the full SHA. `review_report.py` calls it from the approved-render path
  only (a one-line wiring change; do not grow that 870-line module). Check the equivalent text in `scaffold.py` line 311
  for the same footer. A re-approval on a later green head names its own head through the same function.
- Replace any wording that says "all CI checks were green" with the measured counts at the named commit (for example "11
  of 11 checks passed at `abc1234`").
- New `flow/github/approval_invalidation.py`: given PR number, approved head, failing head and failing check details,
  render the comment (Australian English: "no longer stands", names both heads, each failing check, and for a timing
  gate the duration against the limit) and post it through the existing posting facility in
  `flow/github/review_post.py`. Embed a hidden marker with the failing head SHA; before posting, list the PR comments
  and skip if the marker for that SHA is present. Comments that cannot be listed result in a logged skip.
- Trigger: from Stage 5 (Step 2) when the verdict is `red` and an approval was posted; skipped when no approval was
  posted or the head did not move and CI was already red at approval (Stage 4 would not have approved).
- Pass every body through `CredentialRedactor`; no mention of merging beyond "a human decides".

### Dependencies & Criticality

- **Depends on:** Steps 1 and 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `tests/gh_write_guard.py` and `test_gh_write_guard.py` (writes to GitHub are guarded);
  `world/constraints.md` section 2 (no external network in sandbox; posting goes through the existing host seam).
- **Potential failure modes for this step:**
  - Duplicate comments from reruns, caught by the marker check.
  - Posting to the wrong PR; mitigated by using the PR number recorded in the review stage payload.
  - Snapshot tests of the approval text break.
- **Guardrails and early‑abort checks:** never post in dry-run or skip-push mode; abort on a missing PR number.

### Success & Discard Criteria

- **Success:** Second invocation for the same failing head posts nothing; a different failing head posts again.
- **Discard:** The posting seam cannot list comments hermetically; fall back to a state flag in the stage payload (still
  once per head within a flow) and log the limitation.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 2.5   |
| impact_pred         | 70.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 58.0  |

### Step Metrics Rationale

`EV = 0.90 * 70.0 + 0.5 * 2.0 - 6.0 = 63.0 + 1.0 - 6.0 = 58.0`. Moderate entropy from text-snapshot tests.

---

## Step 4: Make a timing-gate failure actionable with the slowest tests

- **Sub-intent recommendation:** NO
- **Reasoning:** The input source is unverified, but the step has a built-in fallback, so it cannot block Steps 1-3; a
  separate branch adds overhead and keeps the plan inside the entropy budget as a single plan.
- **Step Type:** EXPLORATION
- **Exploration level:** EXPLORATORY

- **Hypothesis being tested:** The per-test durations that `scripts/ci_timing_gate.py` records (JUnit XML to JSON,
  uploaded as a workflow artefact) or pytest's `--durations` section in the job log can be fetched with `gh` and parsed
  hermetically.
- **Learning target:** Which source is reachable from Stage 4 and how stable its format is.
- **Maximum acceptable cost for this learning:** 40% of the plan's cost; if neither source is reachable, ship the
  fallback finding (failing job, step and duration, plus the instruction to profile that job's slowest tests) and record
  the limitation in docs.

### Intent & Git Integration

- **Step Intent:** When the only failing check is a timing gate, the resolver finding lists the slowest tests of the
  failing job and never offers a bare rerun as the fix.
- **Git branch:** I-1794546197-chkgate/P-1794546213-1bdbac00-claude-agent-claude-sonnet-5-5/E-step4-slowest-tests
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Read `scripts/ci_timing_gate.py` for the record layout of per-test durations and the artefact name.
- New `flow/github/ci_slowest_tests.py`: a lookup (seam-injected, like `gh_step_lookup`) that returns the top N (N = 5,
  a named constant) test names with seconds for a failed job, trying the artefact first and the log's durations section
  second; every error yields an empty list.
- `review/ci_outcome.py` `describe_ci_outcome` and the finding built for the resolver (`review/resolver.py`, via the
  review loop's CI finding path) use it: when every failed check failed only in its timing gate step, the finding text
  names the job, its duration against the limit and the slowest tests, and states that the fix is to speed those tests
  up or split the job. The phrases "rerun" and "retry" are never offered as the remedy.
- Pass step and duration detail through the same detail record from Step 1; keep `loop.py` changes to a call-site.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `world/constraints.md` section 2 (network): lookups run only through the existing `gh` proxy
  environment on the host side, never from inside the sandbox.
- **Potential failure modes for this step:** artefact missing or expired; log truncated; test names containing untrusted
  text injected into a prompt (treat as data: truncate, strip control characters, redact).
- **Guardrails and early‑abort checks:** cap download size and runtime (reuse `STEP_LOOKUP_TIMEOUT_SECONDS`).

### Success & Discard Criteria

- **Success:** A fake payload with a timing-gate-only failure and fake durations produces a finding that names the five
  slowest tests and contains no bare-rerun advice.
- **Discard:** After two attempts neither source is parseable; ship the fallback wording only.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 3.0   |
| impact_pred         | 55.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 40.1  |

### Step Metrics Rationale

`EV = 0.82 * 55.0 + 0.5 * 6.0 - 8.0 = 45.1 + 3.0 - 8.0 = 40.1`. Highest learning value: it clarifies what CI evidence is
reachable by reviewers, which informs future CI-aware resolver work.

---

## Step 5: Hermetic tests with fake CI payloads

- **Sub-intent recommendation:** NO
- **Reasoning:** Tests follow each implementation step and reuse existing fakes (`review_fakes.py`,
  `test_calibrate_final_head_ci.py`).
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the five behaviours of the intent without a network.
- **Git branch:** I-1794546197-chkgate/P-1794546213-1bdbac00-claude-agent-claude-sonnet-5-5/E-step5-tests
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Update `tests/test_calibrate_final_head_ci.py`: the old "timing gate only" expectation becomes a failure of the
  final-head check; add a green head after a calibration push, and a head that differs from the approved one.
- New `tests/test_ci_reverify.py`: red head gives a non-success Stage 5; unverified is never "ready to merge"; the
  report is not merge-ready while any check is red.
- New `tests/test_approval_invalidation.py`: the comment is posted once per head (second call posts nothing, a new
  failing head posts), names the covered head, the failing head and each failing check, with the duration for a timing
  gate; no post without an approval; the approval text names its head and CI result and a re-approval names its own.
- New `tests/test_ci_slowest_tests.py`: slowest tests named, no bare rerun wording, fallback on lookup errors.
- Reuse the network guard and gh write guard; use only injected seams.
- Run `uv run pytest` on the affected files, then `uv run task test` once. No repeated runs (nothing measures timing).

### Dependencies & Criticality

- **Depends on:** Steps 1-4 (each test file written as its step lands)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/testing/testing_guide.md`, `apps/sandbox-executor/docs/hermetic_testing.md`.
- **Potential failure modes for this step:** other tests that asserted Stage 5 SUCCESS with a red head now fail; fix
  them to the new contract rather than weaken it.
- **Guardrails and early‑abort checks:** run the existing flow and review test files first to find breakage early.

### Success & Discard Criteria

- **Success:** All new and changed tests pass; the full suite passes via `uv run task test`.
- **Discard:** Cost exceeds 1.5x of `cost_pred` because of widespread contract fallout (re-plan).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.5   |
| impact_pred         | 60.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 53.8  |

### Step Metrics Rationale

`EV = 0.95 * 60.0 + 0.5 * 1.5 - 4.0 = 57.0 + 0.75 - 4.0 = 53.75`. Known patterns, low entropy.

---

## Step 6: Documentation, module budgets and lint

- **Sub-intent recommendation:** NO
- **Reasoning:** Small and mechanical; cheap to do last on the same branch.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Document the strict gate, the re-verification and the invalidation comment, and pass all gates.
- **Git branch:** I-1794546197-chkgate/P-1794546213-1bdbac00-claude-agent-claude-sonnet-5-5/E-step6-docs
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Update `docs/review_consensus.md` (approval names its head and CI result; invalidation comment) and
  `docs/executor/execution_architecture_specification.md` (Stage 5 blocks on a red final head); remove any text about
  the timing gate being tolerated. Use Australian English.
- Check `apps/sandbox-executor/flow_module_budgets.json` and `scripts/check_flow_module_budgets.py`; register the new
  modules if the budget file requires it, and confirm no file passes the line budget.
- Run `uv run task lint` and `uv run task lint-docs`; fix findings (no `# noqa`). No new dependencies, no ledger edits.

### Dependencies & Criticality

- **Depends on:** Steps 1-5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `world/constraints.md` section 3 (ledgers), `world/ruleset.md` docstrings and style.
- **Potential failure modes for this step:** prettier/markdown lint oscillation on formulas; module budget failure.
- **Guardrails and early‑abort checks:** run lint-docs after every doc edit; keep formulas in code spans.

### Success & Discard Criteria

- **Success:** `uv run task lint`, `uv run task lint-docs` and `uv run task test` all pass.
- **Discard:** Not applicable; failures are fixed in place.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.5   |
| impact_pred         | 30.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 28.3  |

### Step Metrics Rationale

`EV = 0.97 * 30.0 + 0.5 * 0.5 - 1.0 = 29.1 + 0.25 - 1.0 = 28.35`. Low risk, low cost.

---

## Sub-intent list

None
