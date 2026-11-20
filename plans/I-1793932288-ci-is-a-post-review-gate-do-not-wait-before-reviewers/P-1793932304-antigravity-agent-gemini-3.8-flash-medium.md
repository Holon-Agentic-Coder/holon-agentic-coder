# Plan for I-1793932288-ci-is-a-post-review-gate-do-not-wait-before-reviewers

- **Plan ID:** P-1793932304-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.5.1)
- **Created At:** 2026-11-06T02:31:45.653Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with a focused implementation and verification strategy: decouples reviewer ensemble
  launch from CI registration and pending checks to eradicate wall-clock latency while preserving strict safety gating
  prior to consensus approval.
- **Safety priority level:** elevated
- **Priority Justification:** Modifies core consensus and review gating workflows in
  `apps/sandbox-executor/src/sandbox_executor/flow.py`, affecting how GitHub CI status is evaluated, polled, and
  recorded in the immutable execution ledger.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The execution path is fully specified in the intent goal and acceptance criteria: all architectural
  changes, control-flow branches, and required test scenarios in `flow.py`, `test_flow.py`, and `test_review_loop.py`
  are deterministic.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 2.80  |
| impact_pred         | 95.00 |
| cost_pred           | 5.50  |
| learning_value_pred | 6.50  |
| ev_pred             | 82.41 |

### Strategy Rationale

The intent is accepted as-is (`ACCEPT_AS_IS`). Currently, Stage 4 `run_review_loop()` blocks on
`_wait_for_ci_registration()` before the first review iteration begins, and then inside the loop polls
`_wait_for_pending_ci()` before dispatching the reviewer ensemble. This wastes significant wall-clock time (5–10
minutes) whenever reviewer models identify actionable findings, because the subsequent resolver pass pushes new commits
that immediately invalidate and supersede the in-flight CI run.

The chosen strategy re-architects `run_review_loop()` to treat CI as a post-review safety gate:

1. **Immediate Reviewer Launch:** Reviewers are dispatched immediately after initial empty-diff verification, pull
   request creation/synchronisation, and mergeability inspection. The pre-iteration blocking wait
   `_wait_for_ci_registration()` is removed.
2. **Fast Path on Reviewer Findings:** If reviewers report any open findings (`CHANGES_REQUESTED`), CI is probed once
   without waiting on pending checks. If any check has already failed (`failed > 0` or local test failure),
   `FIND-CI-{iteration:03d}` is recorded alongside reviewer findings. If checks are merely pending
   (`pending > 0 and failed == 0`), pending CI is not waited on, no `FIND-CI` is created, and findings are immediately
   dispatched to the resolver.
3. **Post-Review CI Gate on Clean Review:** Only when all reviewers return `APPROVED` with zero findings and
   mergeability is clean, `_wait_for_ci_registration()` (if needed) and `_wait_for_pending_ci()` are invoked to allow
   pending checks to settle within the iteration budget before calling `decide_consensus()`. If CI settles green,
   consensus approval is finalised; if CI fails or times out, `FIND-CI-{iteration:03d}` is recorded and handed to the
   resolver.
4. **Hermetic Test Verification:** Targeted unit tests in `apps/sandbox-executor/tests/test_review_loop.py` and
   `apps/sandbox-executor/tests/test_flow.py` verify both the fast-path bypass and the post-review gate, followed by the
   complete repository test and lint suite.

**Derivation of Overall Metrics:**

- `p_success_pred`: Calculated as the product of step success probabilities (`0.95 * 0.98 * 0.97 * 0.99 = 0.8938`,
  rounded to `0.90`). Step 1 is the primary bottleneck (`0.95`) as it restructures the review loop control flow and
  finding aggregation logic.
- `entropy_pred`: Sum of step entropies (`1.20 + 0.70 + 0.60 + 0.30 = 2.80`), which is comfortably within the allocated
  entropy budget of `15.0`.
- `impact_pred`: Sum of step impacts (`45.00 + 25.00 + 18.00 + 7.00 = 95.00`), reflecting substantial wall-clock savings
  across all iterative review cycles in Holon.
- `cost_pred`: Sum of step costs (`2.00 + 1.50 + 1.20 + 0.80 = 5.50`).
- `learning_value_pred`: Sum of step learning values (`2.50 + 2.00 + 1.50 + 0.50 = 6.50`), quantifying empirical
  insights gained into parallel reviewer execution and asynchronous CI gating.
- `ev_pred`: Derived using the config formula
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` where `mu = 0.5` and `lambda = 0.3`:
  `EV = 0.90 * 95.00 + 0.5 * 6.50 - 0.3 * 2.80 - 5.50 = 85.50 + 3.25 - 0.84 - 5.50 = 82.41`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md#1`: Python runtime `==3.13.*`, dependencies managed strictly via `uv` at the root
    virtualenv. No new external dependencies.
  - `holon-config/world/ruleset.md#2`: PEP 8 compliance, Australian English spelling, strict typing annotations, and
    comprehensive docstrings for modified functions.
  - `holon-config/world/ruleset.md#3`: Testing via `pytest==9.1.1` (`uv run pytest`); test modifications are explicitly
    declared in Steps 2 and 3.
  - `holon-config/world/constraints.md#1` & `docs/safety.md#1`: Branch isolation under intent branch
    `I-1793932288-ci-is-a-post-review-gate-do-not-wait-before-reviewers`; merge to `main` remains strictly
    human-maintainer gated.
  - `holon-config/world/constraints.md#3`: Immutability of the three append-only ledgers (`intents.jsonl`,
    `plans.jsonl`, `executions.jsonl`).
- **Potential violations or edge cases:**
  - Reviewers might pass cleanly when CI checks have not yet registered on GitHub, leading to a false-positive approval
    if registration is skipped. Mitigated by explicitly calling `_wait_for_ci_registration()` before
    `_wait_for_pending_ci()` on the clean-review path if test results are unmeasured.
  - Reviewer findings might coincide with already-failed CI checks (`failed > 0`); the resolver must receive both the
    reviewer findings and `FIND-CI-{iteration:03d}` to resolve both syntax/logic issues and build/test failures.
  - If CI times out pending while reviewers are clean, it must not approve; it must record `FIND-CI-{iteration:03d}` and
    hand off to the resolver.
- **Mitigations built into the plan:**
  - Explicit branching logic for: (a) reviewer findings present, (b) reviewer unanimous approval with CI green, (c)
    reviewer unanimous approval with CI failing or timing out.
  - Injectable clock and sleep seams preserved for deterministic testing without wall-clock sleeps.
- **Residual risk accepted (and why):**
  - Probing CI once on reviewer findings may miss a late CI failure that occurs while reviewers are deliberating, but
    this is entirely benign because the resolver will push a new commit that invalidates that CI run anyway.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 2.80
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

This plan transforms GitHub CI into a post-review gate within `run_review_loop()` in
`apps/sandbox-executor/src/sandbox_executor/flow.py`. Instead of serialising execution by waiting for CI registration
and pending checks before reviewer models start deliberation, reviewers are dispatched immediately in parallel with
GitHub CI. CI checks are waited on only when reviewers find no issues and code is otherwise ready for consensus
approval.

The plan is decomposed into four sequential steps:

1. Re-architecting `run_review_loop()` in `sandbox_executor/flow.py` for immediate reviewer launch, finding fast-path,
   and post-review gating.
2. Implementing dedicated hermetic unit tests in `apps/sandbox-executor/tests/test_review_loop.py`.
3. Updating existing flow tests in `apps/sandbox-executor/tests/test_flow.py` to match the new ordering and post-review
   gating behaviour.
4. Running the full test suite (`uv run task test`), linting (`uv run task lint`), and documentation checks
   (`uv run task lint-docs`).

---

## Step 1: Refactor `run_review_loop` in `flow.py` to make CI a post-review gate

- **Sub‑intent recommendation:** NO
- **Reasoning:** A targeted control-flow refactor in a single module `flow.py` with moderate entropy (`1.20`) and high
  success probability (`0.95`).
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make GitHub CI a post-review gate in `run_review_loop()`: launch reviewers immediately without
  waiting for CI, bypass pending CI when reviewers report findings, and gate consensus on settled CI when reviewers
  return clean.
- **Git branch:**
  `I-1793932288-ci-is-a-post-review-gate-do-not-wait-before-reviewers/P-1793932304-antigravity-agent-gemini-3.8-flash-medium/E-step1-refactor-review-loop`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Open `apps/sandbox-executor/src/sandbox_executor/flow.py`.
2. In `run_review_loop()`:
   - Remove the pre-iteration blocking call to `_wait_for_ci_registration(...)` prior to the `while` loop.
   - Inside the iteration loop, keep step 1 (mergeability inspection: `check_pr_mergeability`).
   - Move step 3 (reviewer ensemble execution) ahead of the CI polling/waiting block so reviewers launch immediately
     after mergeability check.
   - Evaluate reviewer outcomes:
     - Check whether any reviewer requested changes or reported open findings (`_any_reviewer_objects(reviewers)` or
       open findings exist).
   - If reviewers report open findings (changes requested path):
     - Probe CI once using `probe_ci_checks(repo_dir, review_target, proxy_env=proxy_env)`.
     - Do not wait on pending checks (do not call `_wait_for_pending_ci()`).
     - Check probe outcome: if any check has already failed (`failed > 0` or local test failure), set
       `ci_finding = True` so `FIND-CI-{iteration:03d}` is included alongside reviewer findings.
     - If checks are merely pending (`pending > 0 and failed == 0`), set `ci_finding = False` (do not add
       `FIND-CI-{iteration:03d}`).
     - Call `decide_consensus()` which returns `CHANGES_REQUESTED`.
     - Pass the reviewer findings (plus `FIND-CI` if failed) straight to the resolver pass.
   - If all reviewers return `APPROVED` with zero open findings (and mergeability is clean):
     - If test pass rate was not recorded (`test_pass_rate is None`) and CI source is not yet `github_cli` and
       `shutil.which("gh")` is available, call `_wait_for_ci_registration(...)` within the remaining iteration budget.
     - Probe CI checks with `probe_ci_checks(...)`.
     - If CI status indicates pending checks (`last_ci_status.get("pending", 0) > 0`) and not a dry run, invoke
       `_wait_for_pending_ci(...)` with `iteration_timer.remaining()` and `ci_poll_interval`.
     - Evaluate settled CI status:
       - If CI settles green (`_ci_green(last_ci_status, tests_failed)` is `True`), call `decide_consensus()` with
         `ci_green=True`, achieving `ConsensusVerdict.APPROVED`.
       - If CI failed (`failed > 0`) or timed out pending, set `ci_finding = True`, record `FIND-CI-{iteration:03d}`,
         and call `decide_consensus()` which resolves to `CHANGES_REQUESTED` to dispatch the resolver.
3. Update docstrings in `run_review_loop()` to describe the post-review CI gate architecture in Australian English.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (core control flow changes occur here)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2` (Australian English, PEP 8, typing),
  `holon-config/world/constraints.md#3` (ledger preservation), `docs/safety.md#5` (human review as final gate).
- **Potential failure modes for this step:**
  - Inadvertently adding `FIND-CI` when CI is still pending on reviewer findings.
  - Failing to wait for CI to settle when reviewers are clean, resulting in premature approval of broken code.
  - Swallowing or miscalculating remaining iteration budget during post-review CI waits.
- **Guardrails and early‑abort checks:**
  - Verify that `_wait_for_pending_ci` receives `iteration_timer.remaining()`.
  - Verify that when `failed == 0 and pending > 0` on reviewer findings, `ci_finding` evaluates to `False`.

### Success & Discard Criteria

- **Success:** `run_review_loop()` logic matches the intent specification: reviewers launch first; findings trigger
  immediate resolver handoff without CI wait; clean reviews wait for CI settlement before consensus.
- **Discard:** If the control flow breaks backward compatibility with dry runs, empty-diff guards, or unmeasured test
  detection.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.20  |
| impact_pred         | 45.00 |
| cost_pred           | 2.00  |
| learning_value_pred | 2.50  |
| ev_pred             | 41.64 |

### Step Metrics Rationale

- `p_success_pred`: `0.95` because the flow logic is well isolated within `run_review_loop()` and has comprehensive test
  coverage.
- `entropy_pred`: `1.20` reflecting modest structural changes to loop sequencing and conditional gating.
- `impact_pred`: `45.00` as this implements the primary functionality of parallel reviewer execution and latency
  elimination.
- `cost_pred`: `2.00` for authoring and verifying the refactored loop logic.
- `learning_value_pred`: `2.50` for establishing clear execution patterns between asynchronous external CI and internal
  review ensembles.
- `ev_pred`: `EV = 0.95 * 45.00 + 0.5 * 2.50 - 0.3 * 1.20 - 2.00 = 42.75 + 1.25 - 0.36 - 2.00 = 41.64`.

---

## Step 2: Implement hermetic unit tests in `test_review_loop.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Adding unit tests in an existing test suite has very low risk (`0.70` entropy) and high predictability
  (`0.98` success probability).
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add hermetic unit tests in `apps/sandbox-executor/tests/test_review_loop.py` validating fast-path
  resolver invocation without CI wait sleeps on reviewer findings, and CI settlement waiting on clean reviews.
- **Git branch:**
  `I-1793932288-ci-is-a-post-review-gate-do-not-wait-before-reviewers/P-1793932304-antigravity-agent-gemini-3.8-flash-medium/E-step2-test-review-loop`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Open `apps/sandbox-executor/tests/test_review_loop.py`.
2. Add a test function `test_review_findings_with_pending_ci_bypasses_wait_and_omits_find_ci`:
   - Mock reviewer ensemble to return `CHANGES_REQUESTED` with at least one finding.
   - Mock CI probe to return `github_cli` status with `pending=1, failed=0, passed=0`.
   - Mock sleep function and mock resolver function.
   - Execute `run_review_loop()`.
   - Assert `sleep.assert_not_called()` (zero CI wait sleep occurred).
   - Assert resolver function was called immediately.
   - Assert `FIND-CI` is NOT in the findings passed to the resolver.
3. Add a test function `test_review_findings_with_failed_ci_includes_find_ci`:
   - Mock reviewer ensemble to return `CHANGES_REQUESTED` with findings.
   - Mock CI probe to return `github_cli` status with `pending=0, failed=1, passed=0`.
   - Execute `run_review_loop()`.
   - Assert `FIND-CI-001` is included in the findings alongside reviewer findings.
4. Add a test function `test_clean_review_with_pending_ci_waits_for_settlement_and_approves_when_green`:
   - Mock reviewer ensemble to return unanimous `APPROVED` with zero findings.
   - Mock CI probes transitioning from `pending=1` to `all_passed=True, pending=0, failed=0`.
   - Execute `run_review_loop()`.
   - Assert `_wait_for_pending_ci` was invoked and sleep was called.
   - Assert consensus verdict is `APPROVED`.
5. Add a test function `test_clean_review_with_pending_ci_dispatches_resolver_when_ci_fails`:
   - Mock reviewer ensemble to return unanimous `APPROVED` with zero findings.
   - Mock CI probes transitioning from `pending=1` to `failed=1, pending=0`.
   - Mock resolver function.
   - Execute `run_review_loop()`.
   - Assert consensus was not approved; `FIND-CI-001` was recorded and handed to resolver.
6. Verify tests run hermetically using `uv run pytest apps/sandbox-executor/tests/test_review_loop.py`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3` (test declarations and hermetic execution),
  `docs/testing/testing_guide.md`.
- **Potential failure modes for this step:**
  - Leaking real monotonic time or sleeping in tests.
  - Flaky mocking of CI probe state sequences.
- **Guardrails and early‑abort checks:**
  - Use injected mocks for clock and sleep; verify sleep duration assertions without actual delays.

### Success & Discard Criteria

- **Success:** All new and existing tests in `test_review_loop.py` pass cleanly with zero real wall-clock sleeps.
- **Discard:** Any test failure or assertion mismatch in `test_review_loop.py`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.70  |
| impact_pred         | 25.00 |
| cost_pred           | 1.50  |
| learning_value_pred | 2.00  |
| ev_pred             | 23.79 |

### Step Metrics Rationale

- `p_success_pred`: `0.98` due to established hermetic fixtures and clear test patterns in `test_review_loop.py`.
- `entropy_pred`: `0.70` as new unit tests introduce minimal system entropy.
- `impact_pred`: `25.00` ensuring rigorous regression protection for the fast path and post-review gate.
- `cost_pred`: `1.50` for writing and verifying tests.
- `learning_value_pred`: `2.00` for validating all edge-case state transitions.
- `ev_pred`: `EV = 0.98 * 25.00 + 0.5 * 2.00 - 0.3 * 0.70 - 1.50 = 24.50 + 1.00 - 0.21 - 1.50 = 23.79`.

---

## Step 3: Update existing flow tests in `test_flow.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Updating existing flow-level test assertions to align with the new sequence is straightforward and
  low-risk (`0.60` entropy).
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update existing test cases in `apps/sandbox-executor/tests/test_flow.py` that expected pre-iteration
  CI registration waiting, verifying they now reflect post-review CI gating.
- **Git branch:**
  `I-1793932288-ci-is-a-post-review-gate-do-not-wait-before-reviewers/P-1793932304-antigravity-agent-gemini-3.8-flash-medium/E-step3-test-flow`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Open `apps/sandbox-executor/tests/test_flow.py`.
2. Inspect `test_checks_registering_after_a_delay_burn_no_review_iterations`:
   - In the previous flow, `_wait_for_ci_registration` was called before iteration 1.
   - In the new flow, reviewers run on iteration 1 first. Since reviewers approve cleanly and no test pass rate was
     recorded, `_wait_for_ci_registration` is invoked post-clean-review to register checks before consensus.
   - Adjust mock expectations and call count assertions if probe sequencing moved from pre-loop to post-review.
3. Inspect `test_guard_refusal_is_reported_as_its_own_reason_without_a_ci_wait`:
   - Confirm that empty-diff and unverified-diff guard tests continue to bypass CI registration and review dispatches
     cleanly.
4. Run `uv run pytest apps/sandbox-executor/tests/test_flow.py` to ensure all tests pass without regressions.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3`, `docs/safety.md#3`.
- **Potential failure modes for this step:**
  - Mock call count mismatches on `probe_ci_checks` due to reviewer-first execution.
- **Guardrails and early‑abort checks:**
  - Compare probe call sequence against expected post-review execution trace.

### Success & Discard Criteria

- **Success:** All test cases in `test_flow.py` pass under `uv run pytest`.
- **Discard:** Lingering pre-iteration wait assumptions causing assertions to fail.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.60  |
| impact_pred         | 18.00 |
| cost_pred           | 1.20  |
| learning_value_pred | 1.50  |
| ev_pred             | 16.83 |

### Step Metrics Rationale

- `p_success_pred`: `0.97` as flow test harness is well-understood and thoroughly instrumented.
- `entropy_pred`: `0.60` for straightforward test alignment.
- `impact_pred`: `18.00` confirming complete end-to-end integration safety.
- `cost_pred`: `1.20` for test updates and local test execution.
- `learning_value_pred`: `1.50` verifying integration behaviour between flow stages.
- `ev_pred`: `EV = 0.97 * 18.00 + 0.5 * 1.50 - 0.3 * 0.60 - 1.20 = 17.46 + 0.75 - 0.18 - 1.20 = 16.83`.

---

## Step 4: Full verification, formatting, and lint compliance

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard repository verification and quality gate execution with negligible risk (`0.30` entropy).
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Execute comprehensive repository test suite and quality gates (`uv run task test`,
  `uv run task lint`, and `uv run task lint-docs`), ensuring formatting, Australian English spelling, and static
  analysis compliance.
- **Git branch:**
  `I-1793932288-ci-is-a-post-review-gate-do-not-wait-before-reviewers/P-1793932304-antigravity-agent-gemini-3.8-flash-medium/E-step4-verification-lint`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Execute targeted test suite via
   `uv run pytest apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_review_loop.py`.
2. Execute full standard test suite via `uv run task test`.
3. Execute code linting via `uv run task lint` (`uv lock --check`, `git diff uv.lock`, `ruff check`,
   `ruff format --check`).
4. Execute documentation linting via `uv run task lint-docs` (`prettier --check`).
5. Ensure all code and documentation comments strictly adhere to Australian English spelling conventions (e.g.
   synchronise, prioritise, behaviour, categorise).
6. Verify no new external dependencies were introduced into `pyproject.toml` or `uv.lock`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** YES (must pass completely before human maintainer review)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#1`, `holon-config/world/ruleset.md#2`, `AGENTS.md`.
- **Potential failure modes for this step:**
  - Ruff formatting or import ordering violations.
  - Prettier markdown formatting issues.
- **Guardrails and early‑abort checks:**
  - Use `uv run task format` if any minor formatting differences arise, followed by re-verification with
    `uv run task check`.

### Success & Discard Criteria

- **Success:** All tasks (`test`, `lint`, `lint-docs`, `check`) exit with returncode 0.
- **Discard:** Unresolvable lint or test failures in the sandbox executor.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.30  |
| impact_pred         | 7.00  |
| cost_pred           | 0.80  |
| learning_value_pred | 0.50  |
| ev_pred             | 6.29  |

### Step Metrics Rationale

- `p_success_pred`: `0.99` as lint and standard task execution are automated and deterministic.
- `entropy_pred`: `0.30` representing minimal disorder.
- `impact_pred`: `7.00` ensuring the repository remains clean, correctly formatted, and production-ready.
- `cost_pred`: `0.80` for execution time.
- `learning_value_pred`: `0.50` confirming hygiene compliance.
- `ev_pred`: `EV = 0.99 * 7.00 + 0.5 * 0.50 - 0.3 * 0.30 - 0.80 = 6.93 + 0.25 - 0.09 - 0.80 = 6.29`.
