# Plan for I-1794205862-resolver-commits-locally-skip-push-fix

- **Plan ID:** P-1794205877-cbad6e47-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.5.3)
- **Created At:** 2026-11-09T06:31:18.563Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** conservative. Targeted fix in review loop and harness git state management with hermetic
  testing.
- **Safety priority level:** elevated
- **Priority Justification:** Modifies container review loop git state handling and review head tracking in host flow
  engine.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The problem domain and required git operations are fully understood and deterministic.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.80  |
| impact_pred         | 8.50  |
| cost_pred           | 3.20  |
| learning_value_pred | 2.10  |
| ev_pred             | 5.39  |

### Strategy Rationale

The intent addresses a critical flaw in Stage 4 review resolution where resolver agents commit fixes locally within the
container rather than leaving uncommitted changes in the working tree. When `_commit_and_push_resolution` ran
subsequently, `git status --porcelain` showed no working-tree changes, causing verification, trailer attachment, and
push to be skipped. Concurrently, the host review flow loop erroneously inspected only `head_sha != current_sha` and
logged that a new head was pushed, even though `pushed` was `False`. On the following iteration, a fresh checkout or
re-clone erased the unpushed local commit, silently losing fixes.

The strategy resolves this through four coordinated interventions:

1. Recording `pre_resolver_head` prior to running `_run_resolver_agent` and forwarding it to
   `_commit_and_push_resolution`.
2. Running `git reset --soft pre_resolver_head` when the current HEAD differs from `pre_resolver_head`, safely staging
   the agent's edits into the index/working tree so standard verification, trailer attachment, and push execute
   reliably.
3. Updating the resolver prompt to explicitly prohibit both `git commit` and `git push`, instructing agents to leave
   edits in the working tree.
4. Conditioning host flow loop head-advancement and logging strictly on `resolver_outcome.pushed is True`.

Metric derivations from individual steps:

- `p_success_pred = 0.95`: Bottleneck is Step 1 (`0.95`) due to coordinating git subprocess state and containerised
  error recovery.
- `entropy_pred = 1.80`: Direct sum of step entropies (`0.60 + 0.40 + 0.50 + 0.30 = 1.80`), comfortably within the 15.0
  budget.
- `impact_pred = 8.50`: The maximum impact delivered across steps, eliminating silent loss of resolver fixes across
  review iterations.
- `cost_pred = 3.20`: Direct sum of step costs (`0.80 + 0.60 + 1.00 + 0.80 = 3.20`).
- `learning_value_pred = 2.10`: Epistemic gain from establishing explicit harness-agent git boundary contracts and
  hermetic test coverage.
- `ev_pred = 5.39`: Derived using the standard system EV formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.95 * 8.50 + 0.5 * 2.10 - 0.3 * 1.80 - 3.20 = 8.075 + 1.05 - 0.54 - 3.20 = 5.385` (rounded to `5.39`).

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md#1`: Strict target Python version `==3.13.*` and workspace `uv` execution.
  - `holon-config/world/ruleset.md#2`: PEP 8 conformity, strict typing (`typing`), docstrings, clean architecture.
  - `holon-config/world/ruleset.md#3`: Testing constraints with `pytest==9.1.1` and hermetic test isolation.
  - `holon-config/world/constraints.md#1`: Git branch isolation and rebase discipline.
  - `holon-config/world/constraints.md#3` and `docs/safety.md#invariant-6`: Ledger immutability (append-only ledgers;
    zero historical record modifications).
  - Per-module line budget constraints: `apps/sandbox-executor/flow_module_budgets.json` dictates
    `review/loop.py <= 962`. Net line delta must be `<= 0`.
  - Repository complexity gate rules: `C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`, `PLR0915 <= 50`, with zero `# noqa`
    suppressions.
  - Australian English language convention.
- **Potential violations or edge cases:**
  - `pre_resolver_head` git rev-parse failing if repository checkout is dirty or headless.
  - Soft-reset command triggering `_assert_safe_git_args` forbidden token check if not properly structured.
  - Line count increase in `apps/sandbox-executor/src/sandbox_executor/flow/review/loop.py` exceeding the 962-line
    budget.
  - Resolver agent committing multiple commits or creating unrecognised git state.
- **Mitigations built into the plan:**
  - Validate and capture `pre_resolver_head` safely via `_checked(["git", "rev-parse", "HEAD"], repo_dir, "resolve")`.
  - `git reset --soft` uses only safe arguments permitted by `_assert_safe_git_args` (`_FORBIDDEN_GIT_FLAGS` contains
    `--force`, `-f`, `--force-with-lease`, `--amend`, `rebase`, none of which are used).
  - In `flow/review/loop.py`, replace the multi-line `head_changed` evaluation with `bool(resolver_outcome.pushed)` or
    equivalent compact expression, ensuring line count stays strictly `<= 962`.
  - Soft-reset back to `pre_resolver_head` flattens any arbitrary number of local commits made by the resolver into
    staged index changes against `pre_resolver_head`.
- **Residual risk accepted (and why):**
  - Minimal residual risk accepted: containerised resolver environment is isolated and ephemeral; soft-reset operates
    purely within the working clone.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 1.80
- **Budget Compliance:** The strategy fits within budget (`1.80 <= 15.0`).

## Plan Description & Strategy

This plan implements safe local resolver commit handling and push status verification across four steps:

1. **Container Review Loop Git State Management:** In `sandbox_executor.entrypoint.review_loop`, record
   `pre_resolver_head` before `_run_resolver_agent` runs, pass it to `_commit_and_push_resolution`, soft-reset local
   commits back to `pre_resolver_head` when current HEAD has changed, and update `_build_resolver_prompt` to forbid both
   `git commit` and `git push`.
2. **Host Review Flow Honest Push Check:** In `sandbox_executor.flow.review.loop`, update `head_changed` and target head
   progression to strictly require `resolver_outcome.pushed is True` before logging new head push and advancing
   `resolved_target`, preserving the 962-line budget.
3. **Hermetic Unit Test Suite:** Add unit tests in `apps/sandbox-executor/tests/test_review_loop.py` and
   `tests/flow/review/test_loop.py` validating soft-reset on local commit, prompt instructions, and honest push gate in
   the host loop.
4. **Validation & Quality Checks:** Execute targeted tests via `uv run pytest`, run `uv run task lint`,
   `uv run task lint-docs`, check line budgets via `scripts/check_flow_module_budgets.py`, and run `uv run task test`.

---

## Step 1: Capture pre-resolver HEAD, soft-reset local resolver commits, and update prompt instruction in `review_loop.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped strictly to resolver mode functions in `review_loop.py` with clear deterministic behaviour.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Ensure `_run_resolver_mode` captures `pre_resolver_head`, `_commit_and_push_resolution` soft-resets
  any local resolver commits back to `pre_resolver_head`, and `_build_resolver_prompt` explicitly forbids running
  `git commit` and `git push`.
- **Git branch:** `I-1794205862-resolver-commits-locally-skip-push-fix/step-1-review-loop-soft-reset`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`:
   - In `_build_resolver_prompt`: Update the prompt text from forbidding only `git push` to explicitly stating:
     `"Never force-push or rewrite git history. Do not run git commit or git push yourself; leave your edits in the working tree so the harness can verify, attach review trailers, commit, and push. Write the JSON list of dispositions to {dispositions_path} with keys id, disposition, and rejection_reason."`
   - In `_run_resolver_mode`: Before executing `_run_resolver_agent`, record
     `pre_resolver_head = (_checked(["git", "rev-parse", "HEAD"], repo_dir, "resolve").stdout or "").strip()`.
   - Update `_commit_and_push_resolution` signature to accept `pre_resolver_head: str | None = None`.
   - In `_commit_and_push_resolution`:
     - Inspect current HEAD using `git rev-parse HEAD`.
     - If `pre_resolver_head` is provided and current HEAD differs from `pre_resolver_head`, construct
       `reset_cmd = ["git", "reset", "--soft", pre_resolver_head]`, validate with `_assert_safe_git_args(reset_cmd)`,
       and run via `run_cmd(reset_cmd, cwd=repo_dir, check=True)`.
     - Set `initial_head = pre_resolver_head or current_head`.
     - Check `git status --porcelain` for worktree or index changes (now present due to soft-reset).
     - Run `_verify_before_push(repo_dir)`.
     - Run `git add -A`, commit with canonical `Review-Iteration` and `Review-Finding-Ids` trailers, and push
       `HEAD:refs/heads/{head_branch}` with `pushed=True`.
   - In `_run_resolver_mode`: Pass `pre_resolver_head` into
     `_commit_and_push_resolution(..., pre_resolver_head=pre_resolver_head)`. Initialize `head_sha = pre_resolver_head`.
2. Verify that functions adhere to ruff complexity thresholds (`C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`,
   `PLR0915 <= 50`) without inline suppressions.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (Downstream testing and honest host loop handling rely on this core mechanism).

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2` (Coding conventions, typing, docstrings),
  `docs/safety.md#invariant-1` (Git discipline).
- **Potential failure modes for this step:**
  - `git reset --soft` failing if repository is in an invalid detached HEAD state.
  - `_assert_safe_git_args` throwing unexpected exception on valid reset command.
- **Guardrails and early‑abort checks:**
  - Verify that `_assert_safe_git_args` allows `git reset --soft <sha>` because `--soft` is not in
    `_FORBIDDEN_GIT_FLAGS`.
  - Fall back gracefully to `initial_head` if `pre_resolver_head` is not supplied or matches current HEAD.

### Success & Discard Criteria

- **Success:** `_run_resolver_mode` captures `pre_resolver_head`, `_commit_and_push_resolution` soft-resets commits made
  by the agent back to index, verifies, commits with trailers, pushes, and returns `pushed=True`.
- **Discard:** Discard if soft-reset results in working-tree corruption or violates git safety assertions.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.60  |
| impact_pred         | 8.00  |
| cost_pred           | 0.80  |
| learning_value_pred | 1.50  |
| ev_pred             | 7.37  |

### Step Metrics Rationale

- `p_success_pred = 0.95`: Git operations (`rev-parse`, `reset --soft`, `status`, `commit`, `push`) are well-specified
  and tested.
- `entropy_pred = 0.60`: Small blast radius contained entirely within the container review loop entrypoint.
- `impact_pred = 8.00`: Completely fixes the defect of skipped verification and dropped local resolver fixes.
- `cost_pred = 0.80`: Straightforward modification of fewer than 30 lines.
- `learning_value_pred = 1.50`: Clarifies git state synchronization rules between harness and LLM agent.
- `ev_pred = 7.37`: Derived using `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`:
  `EV = 0.95 * 8.00 + 0.5 * 1.50 - 0.3 * 0.60 - 0.80 = 7.60 + 0.75 - 0.18 - 0.80 = 7.37`.

---

## Step 2: Update host review flow loop to require `pushed=True` before advancing review head

- **Sub‑intent recommendation:** NO
- **Reasoning:** Localised logic change in `sandbox_executor.flow.review.loop` with zero risk of breaking other stages.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** In `sandbox_executor.flow.review.loop`, update the review loop to evaluate
  `head_changed = bool(resolver_outcome.pushed)` so the host never logs that a new head was pushed or advances
  `resolved_target` unless the resolver pass actually pushed to origin.
- **Git branch:** `I-1794205862-resolver-commits-locally-skip-push-fix/step-2-flow-loop-honest-push`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/flow/review/loop.py`:
   - Locate the resolver outcome processing block around line 669.
   - Replace the legacy `head_changed` evaluation: Change
     `head_changed = bool(resolver_outcome.pushed or (resolver_outcome.head_sha and resolver_outcome.head_sha != current_sha))`
     to: `head_changed = bool(resolver_outcome.pushed)`.
   - Verify that when `head_changed` is `True`, `resolved_target` is updated with `resolver_outcome.head_sha` and
     `context.log` reports
     `[Review Loop] Resolver pushed new head '{new_sha}' at iteration {iteration}; proceeding to re-review in full.`
   - Verify that when `head_changed` is `False`, no log about pushing a new head is emitted, and `resolved_target`
     remains unchanged.
   - Check line count of `apps/sandbox-executor/src/sandbox_executor/flow/review/loop.py` to ensure it does not exceed
     the line budget of 962 lines defined in `flow_module_budgets.json` (net line delta `<= 0`).

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `apps/sandbox-executor/flow_module_budgets.json` (`review/loop.py <= 962`),
  `scripts/check_flow_module_budgets.py`.
- **Potential failure modes for this step:**
  - Net line count increasing past 962, failing `uv run task lint`.
  - Dry-run or test mocks failing if they do not set `pushed=True` when expecting head advancement.
- **Guardrails and early‑abort checks:**
  - Ensure compact syntax so line count shrinks or remains equal to 962.
  - Review all existing loop test mocks to ensure test compatibility.

### Success & Discard Criteria

- **Success:** Flow review loop strictly requires `pushed=True` to advance review target and log push; module line
  budget stays `<= 962`.
- **Discard:** Discard if budget is breached or existing flow tests regress.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.40  |
| impact_pred         | 8.50  |
| cost_pred           | 0.60  |
| learning_value_pred | 1.20  |
| ev_pred             | 8.13  |

### Step Metrics Rationale

- `p_success_pred = 0.97`: Highly targeted change to a single boolean condition in `loop.py`.
- `entropy_pred = 0.40`: Minimal disorder introduced, perfectly aligned with system invariants.
- `impact_pred = 8.50`: Prevents host loop from falsely claiming a head was pushed and losing changes.
- `cost_pred = 0.60`: Very low cost (small 3-line edit).
- `learning_value_pred = 1.20`: Enforces truthfulness in distributed host-container coordination.
- `ev_pred = 8.13`: Derived using `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`:
  `EV = 0.97 * 8.50 + 0.5 * 1.20 - 0.3 * 0.40 - 0.60 = 8.245 + 0.60 - 0.12 - 0.60 = 8.125` (rounded to `8.13`).

---

## Step 3: Add comprehensive hermetic unit tests in `apps/sandbox-executor/tests/test_review_loop.py` and `tests/flow/review/test_loop.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard testing step within existing test files.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add hermetic pytest unit tests covering the resolver local commit soft-reset, prompt instruction
  prohibitions, and flow loop honest push check.
- **Git branch:** `I-1794205862-resolver-commits-locally-skip-push-fix/step-3-hermetic-unit-tests`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_review_loop.py`:
   - Add test `test_resolver_mode_soft_resets_local_commit_and_commits_with_trailers`:
     - Initialise a temporary git repository with an initial commit.
     - Simulate `_run_resolver_agent` performing a local git commit modifying a file.
     - Call `_commit_and_push_resolution` with `pre_resolver_head` set to the initial commit.
     - Assert that `_commit_and_push_resolution` soft-resets to `pre_resolver_head`, verifies via `_verify_before_push`,
       creates a new commit containing canonical `Review-Iteration` and `Review-Finding-Ids` trailers, and pushes with
       `pushed=True`.
     - Assert that the final commit's parent is `pre_resolver_head`.
   - Add test `test_build_resolver_prompt_forbids_both_git_commit_and_push`:
     - Invoke `_build_resolver_prompt` with sample findings.
     - Assert that the returned prompt string contains
       `"Do not run git commit or git push yourself; leave your edits in the working tree so the harness can verify, attach review trailers, commit, and push."`
   - Add test `test_review_loop_does_not_advance_head_when_pushed_is_false`:
     - Run `run_review_loop` with a simulated resolver outcome returning a new `head_sha` but `pushed=False`.
     - Assert that the review target head is NOT advanced to `head_sha` and no re-review proceeds on the unpushed
       commit.
2. In `apps/sandbox-executor/tests/flow/review/test_loop.py`:
   - Add test `test_review_loop_requires_pushed_true_to_log_and_advance_head`:
     - Run the loop with `ResolverOutcome(head_sha="new_sha", pushed=False)` and verify that `context.log` does NOT log
       `"Resolver pushed new head"`.
     - Run the loop with `ResolverOutcome(head_sha="new_sha", pushed=True)` and verify that `context.log` DOES log
       `"Resolver pushed new head"`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3` (Testing constraints, hermetic fixtures, no network calls).
- **Potential failure modes for this step:**
  - Git tests leaking state outside `tmp_path`.
  - Non-hermetic subprocess invocations affecting host git config.
- **Guardrails and early‑abort checks:**
  - Use pytest `tmp_path` fixture for all git repo initialisation with isolated user.name and user.email configs.

### Success & Discard Criteria

- **Success:** All new unit tests pass cleanly under `uv run pytest`.
- **Discard:** Discard if tests fail or show non-deterministic flakiness.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.50  |
| impact_pred         | 7.50  |
| cost_pred           | 1.00  |
| learning_value_pred | 2.00  |
| ev_pred             | 7.05  |

### Step Metrics Rationale

- `p_success_pred = 0.96`: Hermetic test patterns in this repository are mature and well-established.
- `entropy_pred = 0.50`: Low entropy additions to test modules.
- `impact_pred = 7.50`: Provides permanent regression guard against dropped commits and dishonest push logging.
- `cost_pred = 1.00`: Authoring tests across two test files.
- `learning_value_pred = 2.00`: Documents exact behavioural expectations for future maintainers and agents.
- `ev_pred = 7.05`: Derived using `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`:
  `EV = 0.96 * 7.50 + 0.5 * 2.00 - 0.3 * 0.50 - 1.00 = 7.20 + 1.00 - 0.15 - 1.00 = 7.05`.

---

## Step 4: Quality verification, linting, complexity checks, and test suite execution

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard quality assurance and validation step.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Verify all acceptance criteria, lint rules, documentation rules, line budgets, and test suites across
  the repository.
- **Git branch:** `I-1794205862-resolver-commits-locally-skip-push-fix/step-4-quality-verification`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Execute the affected test suites via:
   `uv run pytest apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/flow/review/test_loop.py`
2. Run standard project lint checks: `uv run task lint` (verifies `uv lock --check`, `git diff uv.lock`, `ruff check`,
   and `ruff format --check`).
3. Run markdown formatting and doc linter: `uv run task lint-docs` (runs prettier check).
4. Run line budget validation script: `python scripts/check_flow_module_budgets.py` to confirm `review/loop.py <= 962`.
5. Run the repository test suite: `uv run task test`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#1` (Single root virtual environment, `uv run task` execution),
  `holon-config/world/ruleset.md#2` (Style and complexity limits).
- **Potential failure modes for this step:**
  - Ruff formatting or import ordering issues.
  - Prettier formatting discrepancies on markdown files.
- **Guardrails and early‑abort checks:**
  - Run `uv run task format` if formatting discrepancies arise.

### Success & Discard Criteria

- **Success:** All linters pass with zero warnings, line budgets are satisfied, and all tests pass cleanly.
- **Discard:** Discard if tests fail or cannot meet linters without `# noqa`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.30  |
| impact_pred         | 6.00  |
| cost_pred           | 0.80  |
| learning_value_pred | 1.00  |
| ev_pred             | 5.49  |

### Step Metrics Rationale

- `p_success_pred = 0.98`: Verification commands are standard automated tasks.
- `entropy_pred = 0.30`: Pure verification introduces minimal entropy.
- `impact_pred = 6.00`: Guarantees regression-free delivery and CI green gate.
- `cost_pred = 0.80`: Standard test suite execution time.
- `learning_value_pred = 1.00`: Confirms end-to-end coherence across tools and harness.
- `ev_pred = 5.49`: Derived using `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`:
  `EV = 0.98 * 6.00 + 0.5 * 1.00 - 0.3 * 0.30 - 0.80 = 5.88 + 0.50 - 0.09 - 0.80 = 5.49`.
