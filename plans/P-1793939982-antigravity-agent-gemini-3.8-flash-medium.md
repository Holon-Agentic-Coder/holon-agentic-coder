# Plan for I-1793939965-stage-4-resolver-fixes-ci-and-test-failures

- **Plan ID:** P-1793939982-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.5.1)
- **Created At:** 2026-11-06T04:39:42.950Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted hermetic verification of review loop failure pathways.
- **Safety priority level:** standard
- **Priority Justification:** Changes are constrained to Stage 4 review loop flow logic in `sandbox-executor` and
  hermetic test suite without touching external network, live git remotes, or ledger schema definitions.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The execution path is fully specified in the intent goal and acceptance criteria: all architectural
  changes, control-flow branches, and required test scenarios in `flow.py`, `entrypoint/review_loop.py`, and
  `test_review_loop.py` are deterministic.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.89  |
| entropy_pred        | 2.30  |
| impact_pred         | 95.00 |
| cost_pred           | 3.50  |
| learning_value_pred | 5.50  |
| ev_pred             | 83.11 |

### Strategy Rationale

The intent is accepted as-is (`ACCEPT_AS_IS`). Currently, if all ensemble reviewers return unanimous approval
(`APPROVED`) with zero code-level reviewer findings, but GitHub Actions CI checks fail (e.g. Prettier/lint-docs or test
suite failure), `run_review_loop()` marks consensus as `CHANGES_REQUESTED`. However, because `iter_findings` previously
only accumulated reviewer-reported code findings, `iter_findings` remains empty. Consequently, the review loop halts
immediately with the log message `"(no resolver pass applicable)"`. This abort prevents the resolver from dispatching,
repairing the formatting or test regression, and converging to consensus approval.

The chosen strategy addresses this gap comprehensively:

1. **Synthetic CI Finding Generation:** In `apps/sandbox-executor/src/sandbox_executor/flow.py` (`run_review_loop`),
   whenever `ci_finding` is True (failed GitHub CI checks or failed local tests), synthesize a first-class `FindingRow`
   with `id=f"FIND-CI-{iteration:03d}"`, `severity="important"`, extracting the specific failing check names (e.g. from
   `last_ci_status.get("checks", [])` matching failure buckets/states, or `"local_tests"` if `tests_failed`),
   constructing an informative summary: `f"CI checks failed in iteration {iteration}: {', '.join(failing_names)}."`
   (falling back gracefully to `"CI checks failed in iteration {iteration}."` if no names are extracted), and
   recommendation: `"Run linters and tests locally, apply fixes, and commit the changes."`.
2. **Resolver Pass Enablement on Unanimous Reviewer Approval with CI Failure:** Because `iter_findings` receives the
   synthesized `FindingRow`, `not iter_findings` evaluates to `False`. When all ensemble reviewers approve but CI has
   failed, the review loop does not abort with `(no resolver pass applicable)`. Instead, it transitions cleanly to
   `_invoke_resolver_pass()`, passing `FIND-CI-{iteration:03d}` in the resolver input payload.
3. **Resolver Adjudication & Round-Trip Convergence:** The resolver role container receives `FIND-CI` via the result
   file transport, constructs its turn prompt with instructions to run linters and tests, adjudicates `FIND-CI` to
   `applied` upon fixing the issues, and pushes the resolution commit. On the subsequent review iteration, re-review
   occurs and CI checks are re-probed, enabling the loop to reach full consensus approval.
4. **Hermetic Test Harness:** Dedicated unit tests in `apps/sandbox-executor/tests/test_review_loop.py` verify that:
   - When reviewers approve but CI fails, `run_resolver_fn` is dispatched with `FIND-CI`.
   - The resolver adjudicates `FIND-CI` and pushes fixes for the next iteration to verify.
   - Loop fallback ledger recording handles synthetic CI findings if unadjudicated.

**Derivation of Overall Metrics:**

- `p_success_pred`: Derived as the compound probability across the four steps (`0.96 * 0.97 * 0.95 * 0.98 = 0.867`,
  assessed qualitatively as `0.89`). Step 3 (hermetic test harness covering multi-iteration review loop round trips)
  represents the primary bottleneck (`0.95`).
- `entropy_pred`: Sum of step entropies (`0.80 + 0.60 + 0.70 + 0.20 = 2.30`), well within the allocated entropy budget
  of `15.0`.
- `impact_pred`: Sum of step impacts (`40.00 + 25.00 + 25.00 + 5.00 = 95.00`), reflecting autonomous repair of CI breaks
  without manual human intervention.
- `cost_pred`: Sum of step costs (`1.20 + 0.80 + 1.00 + 0.50 = 3.50`).
- `learning_value_pred`: Sum of step learning values (`1.80 + 1.20 + 2.00 + 0.50 = 5.50`), quantifying epistemic
  insights gained into synthetic infrastructure finding lifecycle and resolver convergence.
- `ev_pred`: Derived using the config formula
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` where `mu = 0.5` and `lambda = 0.3`:
  `EV = 0.89 * 95.00 + 0.5 * 5.50 - 0.3 * 2.30 - 3.50 = 84.55 + 2.75 - 0.69 - 3.50 = 83.11`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md#1`: Python runtime `==3.13.*`, dependencies managed strictly via `uv` at the root
    virtualenv. Zero new dependencies introduced.
  - `holon-config/world/ruleset.md#2`: PEP 8 compliance, Australian English spelling, strict typing annotations, and
    comprehensive docstrings for modified functions.
  - `holon-config/world/ruleset.md#3`: Testing via `pytest==9.1.1` (`uv run pytest`); all test additions in
    `tests/test_review_loop.py` are explicitly declared.
  - `holon-config/world/constraints.md#1` & `docs/safety.md#1`: Branch isolation under intent branch
    `I-1793939965-stage-4-resolver-fixes-ci-and-test-failures`; merge to `main` remains strictly human-maintainer gated.
  - `holon-config/world/constraints.md#3`: Immutability of the three append-only ledgers (`intents.jsonl`,
    `plans.jsonl`, `executions.jsonl`).
- **Potential violations or edge cases:**
  - `last_ci_status` may have empty `checks` or missing check names when probed locally or when GH CLI fails; mitigated
    by graceful fallback to `"unspecified CI checks"` or `"local_tests"`.
  - Synthetic `FIND-CI` finding signature must be tracked in `finding_action_ids` so that resolver adjudication can find
    its introducing action ID and prevent duplicate ledger entries; mitigated by registering both finding ID and
    signature in `finding_action_ids`.
  - Unadjudicated findings guard at loop exit (`remaining_findings`) must inspect `iter_findings` rather than only
    reviewer findings so that unhandled CI findings are safely recorded into `executions.jsonl`.
- **Mitigations built into the plan:**
  - Defensive extraction of failed checks from `last_ci_status` using bucket (`"fail"`) and state (`"FAILURE"`,
    `"TIMED_OUT"`, `"ACTION_REQUIRED"`).
  - Hermetic testing using mock ensembles and in-memory test fixtures, avoiding network or subprocess escapes.
- **Residual risk accepted (and why):**
  - None; changes are purely additive to the review loop and resolver dispatch pathways.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 2.30
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

The plan implements first-class CI finding synthesis and resolver dispatch when automated tests or GitHub Actions CI
fail in Stage 4:

1. **Step 1:** In `apps/sandbox-executor/src/sandbox_executor/flow.py`, refine `ci_row` synthesis to extract failing
   check names, set actionable recommendations, register signature in `finding_action_ids`, ensure `iter_findings`
   carries `FIND-CI` even on clean reviewer approval, and update `remaining_findings` check.
2. **Step 2:** In `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`, ensure resolver input
   deserialisation and prompt generation handle `FIND-CI` with empty path cleanly, guiding the resolver agent to verify
   with linters and tests.
3. **Step 3:** In `apps/sandbox-executor/tests/test_review_loop.py`, add hermetic unit tests verifying: (a) resolver
   dispatch on clean reviewer approval with failed CI, and (b) two-iteration convergence where resolver fixes CI
   failures.
4. **Step 4:** Execute verification via `uv run pytest apps/sandbox-executor/tests/test_review_loop.py`, full unit tests
   via `uv run task test`, code formatting/linting via `uv run task lint`, and doc linting via `uv run task lint-docs`.

---

## Step 1: Synthesize first-class FIND-CI FindingRow and enable resolver trigger in `flow.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** A targeted modification to `flow.py` within the existing review loop flow structure with low entropy
  (`0.80`) and high success probability (`0.96`).
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Synthesize a first-class `FindingRow` for `FIND-CI-{iteration:03d}` with failing check names and
  actionable recommendation, include it in `iter_findings`, and prevent the resolver bypass when reviewers approve but
  CI fails.
- **Git branch:**
  `I-1793939965-stage-4-resolver-fixes-ci-and-test-failures/P-1793939982-antigravity-agent-gemini-3.8-flash-medium/E-step1-flow-ci-synthesis`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Open `apps/sandbox-executor/src/sandbox_executor/flow.py`.
2. Inspect `run_review_loop()` where `ci_finding` is evaluated and `FindingRow` is constructed.
3. Helper extraction of failing check names from `last_ci_status`:
   - Inspect `last_ci_status.get("checks", [])`.
   - Identify failed checks matching `bucket.lower() == "fail"` or
     `state.upper() in ("FAILURE", "TIMED_OUT", "ACTION_REQUIRED")`.
   - If `tests_failed` is True and `"local_tests"` is not in the extracted names, add `"local_tests"`.
   - Format failed check names into a comma-separated list or fallback to `"unspecified CI checks"`.
4. Construct the synthetic `FindingRow` for `FIND-CI-{iteration:03d}`:
   - `id`: `f"FIND-CI-{iteration:03d}"`
   - `severity`: `"important"`
   - `path`: `""`
   - `line`: `None`
   - `reviewers`: `()`
   - `summary`: `f"CI checks failed in iteration {iteration}: {names_str}."`
   - `recommendation`: `"Run linters and tests locally, apply fixes, and commit the changes."`
5. Append `ci_row` to `iter_findings` when `ci_finding` is True.
6. Ensure `finding_action_ids` associates both `ci_row.id` and its signature with the action ID generated during
   `_record_infrastructure_findings()`.
7. At lines 4595-4603, update `remaining_findings` to inspect `iter_findings` (or union of reviewer findings and
   synthetic infrastructure findings) rather than exclusively `collect_review_data(reviewers).findings`, ensuring
   unadjudicated synthetic CI findings are recorded into `executions.jsonl`.
8. Ensure Australian English spelling in any updated comments or log messages.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2`, `holon-config/world/constraints.md#3`.
- **Potential failure modes for this step:**
  - Key errors or type errors if `last_ci_status["checks"]` contains malformed entries.
  - Mitigated by defensive `.get()` calls and type assertions.
- **Guardrails and early‑abort checks:**
  - Verify that `iter_findings` contains `ci_row` whenever `ci_finding` is True.

### Success & Discard Criteria

- **Success:** When `ci_finding` is True, `iter_findings` contains `FindingRow` with `id=FIND-CI-{iteration:03d}`,
  populated summary with failing check names, and recommendation to run linters/tests and commit.
- **Discard:** Discard if changes cause regression in consensus evaluation or break reviewer finding collection.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.80  |
| impact_pred         | 40.00 |
| cost_pred           | 1.20  |
| learning_value_pred | 1.80  |
| ev_pred             | 37.86 |

### Step Metrics Rationale

`p_success_pred = 0.96` reflects standard python dictionary parsing and object construction in `flow.py`.
`entropy_pred = 0.80` is low due to localized scope. `cost_pred = 1.20`. `learning_value_pred = 1.80` from establishing
synthetic finding conventions.
`ev_pred = 0.96 * 40.00 + 0.5 * 1.80 - 0.3 * 0.80 - 1.20 = 38.40 + 0.90 - 0.24 - 1.20 = 37.86`.

---

## Step 2: Ensure resolver transport and prompt handling in `entrypoint/review_loop.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small inspection and hardening of resolver payload handling in `entrypoint/review_loop.py` with minimal
  entropy (`0.60`).
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Ensure `FIND-CI` is properly formatted in the resolver prompt and input payload, allowing the
  resolver role to inspect failures, apply code fixes, and commit with proper trailers.
- **Git branch:**
  `I-1793939965-stage-4-resolver-fixes-ci-and-test-failures/P-1793939982-antigravity-agent-gemini-3.8-flash-medium/E-step2-resolver-prompt-handling`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Open `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`.
2. Inspect `_build_resolver_prompt()` and `_load_resolver_findings()`.
3. Verify that `_build_resolver_prompt()` handles findings where `path` is empty and `line` is `None` without syntax
   formatting errors or missing fields.
4. Verify that prompt instructions explicitly direct the resolver to run `uv run task lint` and `uv run task test` when
   resolving `FIND-CI` findings.
5. In `_commit_and_push_resolution()`, verify that when `FIND-CI` is marked `applied`, its finding ID is included in
   `Review-Finding-Ids` commit trailers and commit message.
6. Preserve Australian English spelling across all modified docstrings.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2`, `holon-config/world/constraints.md#1`.
- **Potential failure modes for this step:**
  - Resolver prompt crashing on empty path strings.
- **Guardrails and early‑abort checks:**
  - Confirm `serialize_finding_for_resolver()` and `_build_resolver_prompt()` produce valid JSON markdown blocks.

### Success & Discard Criteria

- **Success:** Resolver prompt and input payload serialise `FIND-CI` cleanly; applied fixes result in properly formed
  commit messages and trailers.
- **Discard:** Discard if serialization introduces schema incompatibilities in `FindingRow`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.60  |
| impact_pred         | 25.00 |
| cost_pred           | 0.80  |
| learning_value_pred | 1.20  |
| ev_pred             | 23.87 |

### Step Metrics Rationale

`p_success_pred = 0.97` as resolver serialization interfaces are already stable. `entropy_pred = 0.60`.
`cost_pred = 0.80`. `learning_value_pred = 1.20`.
`ev_pred = 0.97 * 25.00 + 0.5 * 1.20 - 0.3 * 0.60 - 0.80 = 24.25 + 0.60 - 0.18 - 0.80 = 23.87`.

---

## Step 3: Implement hermetic unit tests in `apps/sandbox-executor/tests/test_review_loop.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Essential unit test coverage in `test_review_loop.py` verifying all acceptance criteria; self-contained
  with moderate entropy (`0.70`).
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add hermetic unit tests verifying that (a) when reviewers approve but CI fails, `run_resolver_fn` is
  dispatched with `FIND-CI`, and (b) the resolver adjudicates `FIND-CI` and pushes fixes for the next iteration to
  verify.
- **Git branch:**
  `I-1793939965-stage-4-resolver-fixes-ci-and-test-failures/P-1793939982-antigravity-agent-gemini-3.8-flash-medium/E-step3-unit-tests`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Open `apps/sandbox-executor/tests/test_review_loop.py`.
2. Implement `test_review_approved_with_failed_ci_dispatches_resolver_with_finding_ci()`:
   - Configure ensemble with `clean_reviewer(1)` returning `APPROVED` with empty findings.
   - Configure failed CI checks dictionary with failing checks (e.g.
     `{"name": "lint", "bucket": "fail", "state": "FAILURE"}`).
   - Run `run_review_loop()`.
   - Assert `run_resolver_fn` is dispatched.
   - Assert resolver receives `findings` containing `FIND-CI-001`.
   - Assert `finding.summary` contains `"lint"` and `finding.recommendation` mentions linters/tests and committing
     fixes.
3. Implement `test_review_resolver_adjudicates_ci_failure_and_converges_to_approval()`:
   - Multi-pass simulation:
     - Iteration 1: Reviewers approve, but CI checks fail. Resolver is called, returns
       `dispositions=[FindingDisposition(id="FIND-CI-001", disposition="applied")]` and new `head_sha="head-v2"`,
       `pushed=True`.
     - Iteration 2: Reviewers approve, CI checks pass (`all_passed=True`).
   - Assert loop reaches consensus approval (`consensus_reached=True`, `ensemble_verdict=ConsensusVerdict.APPROVED`).
   - Assert ledger actions record resolver disposition for `FIND-CI-001`.
4. Ensure all tests run strictly hermetically without network or live GitHub access.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (Primary test verification of the entire intent)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3`, `apps/sandbox-executor/docs/hermetic_testing.md`.
- **Potential failure modes for this step:**
  - Mock state leakage between iterations.
  - Mitigated by using isolated stubs and hermetic test contexts.
- **Guardrails and early‑abort checks:**
  - Verify tests execute quickly under hermetic fixtures.

### Success & Discard Criteria

- **Success:** All new unit tests in `test_review_loop.py` pass cleanly under `uv run pytest`.
- **Discard:** Discard if tests require real network or subprocess calls.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.70  |
| impact_pred         | 25.00 |
| cost_pred           | 1.00  |
| learning_value_pred | 2.00  |
| ev_pred             | 23.54 |

### Step Metrics Rationale

`p_success_pred = 0.95` as test fixtures in `test_review_loop.py` provide proven stubs for loop execution.
`entropy_pred = 0.70`. `cost_pred = 1.00`. `learning_value_pred = 2.00`.
`ev_pred = 0.95 * 25.00 + 0.5 * 2.00 - 0.3 * 0.70 - 1.00 = 23.75 + 1.00 - 0.21 - 1.00 = 23.54`.

---

## Step 4: Full suite regression testing and linting validation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard repository quality gate execution with minimal entropy (`0.20`).
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Verify all unit tests, linters, and documentation formatters pass across the repository.
- **Git branch:**
  `I-1793939965-stage-4-resolver-fixes-ci-and-test-failures/P-1793939982-antigravity-agent-gemini-3.8-flash-medium/E-step4-verification`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run targeted unit tests: `uv run pytest apps/sandbox-executor/tests/test_review_loop.py`.
2. Run full test suite: `uv run task test`.
3. Run python linting and formatting check: `uv run task lint`.
4. Run markdown formatting check: `uv run task lint-docs`.
5. Ensure clean git status and zero unstaged or uncommitted changes.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `.agents/AGENTS.md`, `holon-config/world/ruleset.md#1`.
- **Potential failure modes for this step:**
  - Formatting violations in markdown or docstrings.
- **Guardrails and early‑abort checks:**
  - Run `uv run task format` if minor formatting issues arise.

### Success & Discard Criteria

- **Success:** Zero test failures, zero lint warnings, zero doc format errors.
- **Discard:** Discard if unrelated test suites fail.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.20  |
| impact_pred         | 5.00  |
| cost_pred           | 0.50  |
| learning_value_pred | 0.50  |
| ev_pred             | 4.59  |

### Step Metrics Rationale

`p_success_pred = 0.98`. `entropy_pred = 0.20`. `cost_pred = 0.50`. `learning_value_pred = 0.50`.
`ev_pred = 0.98 * 5.00 + 0.5 * 0.50 - 0.3 * 0.20 - 0.50 = 4.90 + 0.25 - 0.06 - 0.50 = 4.59`.
