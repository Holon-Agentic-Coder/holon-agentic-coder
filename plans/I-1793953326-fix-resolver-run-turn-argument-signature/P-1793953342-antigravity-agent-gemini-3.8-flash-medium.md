# Plan for I-1793953326-fix-resolver-run-turn-argument-signature

- **Plan ID:** P-1793953342-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.5.2)
- **Created At:** 2026-11-06T08:22:23.642Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** conservative with 1–2 sentence justification: The scope is narrowly focused on bugfixes to
  `_run_resolver_agent` command building and fail-closed verification defaults in `review_loop.py` with hermetic unit
  tests.
- **Safety priority level:** standard
- **Priority Justification:** Modifies sandbox review loop internal dispatch and test verification status reporting
  without altering sandbox escape boundary or ledger integrity.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The defect and fix mechanisms are well-understood and deterministic; exploratory probing is
  unnecessary.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.90  |
| impact_pred         | 95.00 |
| cost_pred           | 4.00  |
| learning_value_pred | 2.00  |
| ev_pred             | 85.08 |

### Strategy Rationale

The intent is accepted as-is (`ACCEPT_AS_IS`) with a focused exploitation (`EXPLOIT`) strategy across three sequential
steps:

1. Fix `_run_resolver_agent` command building and `run_turn` argument signature, and enforce fail-closed verification
   defaults in `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`.
2. Add comprehensive hermetic unit tests in `apps/sandbox-executor/tests/test_review_loop.py` validating command
   generation, argument passing to `run_turn`, fail-closed verification defaults on failure and skipped execution, and
   error recording.
3. Validate with targeted pytest execution, repository linting, doc linting, and full test suite execution.

**Intent Assessment & Defect Mechanism:**

In `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py` line 362, `_run_resolver_agent` invokes
`run_turn(runner, prompt, model_name, cwd=repo_dir)`. However, `run_turn` defined in `sandbox_executor.turns` accepts
exactly one positional argument (`cmd: list[str]`). Calling it with three positional arguments
(`runner, prompt, model_name`) causes an immediate `TypeError` on every Stage 4 resolver pass, preventing findings
adjudication and aborting the review loop.

Furthermore, `_run_resolver_mode` initializes `lint_ok = True` and `tests_ok = True`. When a resolver pass fails or
execution is skipped, these optimistic defaults leak into the resolver result JSON, falsely reporting that pre-push
verification tasks succeeded. Adopting fail-closed defaults (`lint_ok = False`, `tests_ok = False`) ensures that
verification success is only reported when `_verify_before_push` actually executes and passes both lint and test
commands with return code 0.

**Derivation of Overall Metrics:**

- `p_success_pred`: Derived from the compound product of step probabilities (`0.98 * 0.96 * 0.99 = 0.9314`, rounded to
  `0.93`). Step 2 represents the bottleneck step (`0.96`) owing to mock setup across entrypoint execution paths and
  result file assertions.
- `entropy_pred`: Sum of step entropies (`0.30 + 0.50 + 0.10 = 0.90`), fitting comfortably within the allocated `15.0`
  entropy budget.
- `impact_pred`: Sum of step impacts (`50.00 + 35.00 + 10.00 = 95.00`), reflecting that fixing the resolver argument
  signature restores critical Stage 4 review convergence and eliminates deceptive verification status reporting.
- `cost_pred`: Sum of step costs (`1.50 + 1.80 + 0.70 = 4.00`).
- `learning_value_pred`: Sum of step learning values (`0.50 + 1.20 + 0.30 = 2.00`).
- `ev_pred`: Computed using the system-wide EV formula
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` with constants `mu = 0.5` and
  `lambda = 0.3`: `EV = 0.93 * 95.00 + 0.5 * 2.00 - 0.3 * 0.90 - 4.00 = 88.35 + 1.00 - 0.27 - 4.00 = 85.08`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md#1`: Virtual environment commands strictly run from repository root using
    `uv run pytest` and `uv run task`. No new dependencies are introduced.
  - `holon-config/world/ruleset.md#2`: Maintain PEP 8 compliance, Australian English spelling, strict type annotations,
    and docstring integrity.
  - `holon-config/world/ruleset.md#3`: Changes to tests are explicitly planned in Step 2 for
    `apps/sandbox-executor/tests/test_review_loop.py`.
  - `holon-config/world/constraints.md#1` & `docs/safety.md#1`: Git branch isolation is preserved; all changes are
    committed on the execution branch without merging to `main`. Merging stays with the human maintainer.
  - `holon-config/world/constraints.md#3`: Append-only ledgers (`intents.jsonl`, `plans.jsonl`, `executions.jsonl`)
    remain strictly untouched.
- **Potential violations or edge cases:**
  - Unhandled exceptions during runner resolution or turn execution propagating as unhandled process crashes.
  - Optimistic verification status reported when verification is skipped due to no worktree changes, rejected findings,
    or dry-run execution.
- **Mitigations built into the plan:**
  - `_run_resolver_agent` wraps command generation and turn execution cleanly; `_run_resolver_mode` catches exceptions,
    formats them via `_bounded_error_context`, and writes them to the outcome error field.
  - Fail-closed defaults initialize `lint_ok = False` and `tests_ok = False` across both `_run_resolver_mode` and
    `_commit_and_push_resolution`, only setting them to `True` when `_verify_before_push` executes and passes both tasks
    with exit code 0.
- **Residual risk accepted (and why):**
  - Minimal residual risk accepted: changes are strictly contained to `review_loop.py` and unit tests in
    `test_review_loop.py`, with complete isolation from production git remotes.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 0.90
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

This plan resolves the fatal `TypeError` in `_run_resolver_agent` and replaces optimistic verification defaults with
fail-closed defaults in `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`.

In Step 1, `_run_resolver_agent` is updated to invoke `runner.build_command(prompt, model_name)` to generate the command
list, which is then passed as the sole positional argument to `run_turn(cmd, cwd=repo_dir)`. The exit code check is made
robust against both `TurnResult.returncode` and `exit_code`. In `_run_resolver_mode` and `_commit_and_push_resolution`,
`lint_ok` and `tests_ok` are flipped to initialize to `False`, ensuring that any run where verification is skipped or
fails accurately reports `False` for both `lint_passed` and `tests_passed`.

In Step 2, comprehensive hermetic unit tests are added to `apps/sandbox-executor/tests/test_review_loop.py` verifying
command construction, argument signature compliance, fail-closed defaults on failure and skipped execution, and error
recording in the result JSON.

In Step 3, the entire solution is verified using `uv run pytest apps/sandbox-executor/tests/test_review_loop.py`,
repository and documentation linters via `uv run task check`, and the full unit test suite via `uv run task test`.

---

## Step 1: Fix resolver command construction, argument signature, and fail-closed verification defaults in `review_loop.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Localized implementation with zero external dependencies and low entropy, directly resolving the
  `TypeError` and optimistic verification defect in `review_loop.py`.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Build runner CLI command via `runner.build_command(prompt, model_name)`, invoke
  `run_turn(cmd, cwd=repo_dir)`, check returncode cleanly, and enforce fail-closed verification defaults
  (`lint_ok = False`, `tests_ok = False`) in `_run_resolver_mode` and `_commit_and_push_resolution`.
- **Git branch:**
  `I-1793953326-fix-resolver-run-turn-argument-signature/P-1793953342-antigravity-agent-gemini-3.8-flash-medium/E-fix-resolver-run-turn/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py` inside `_run_resolver_agent`:
  - Fetch runner via `runner = get_runner(agent_name)`.
  - Build command line using `cmd = runner.build_command(prompt, model_name)`.
  - Invoke `run_turn(cmd, cwd=repo_dir)` passing the built command list as the sole positional argument.
  - Determine return code cleanly via `code = getattr(outcome, "returncode", getattr(outcome, "exit_code", 0))` and
    raise `RuntimeError(f"Resolver agent exited with code {code}")` when non-zero.
- In `_run_resolver_mode`:
  - Change initial boolean state: initialize `lint_ok = False` and `tests_ok = False` (fail-closed defaults) instead of
    `True`.
  - When `error is None and findings`, invoke `_commit_and_push_resolution(...)`. `lint_ok` and `tests_ok` only become
    `True` when `_verify_before_push` actually runs and passes both lint and test checks.
  - Ensure any caught exception from `_run_resolver_agent` is recorded in
    `error = f"Resolver turn failed: {_bounded_error_context(str(exc))}"` and propagated to the outcome dictionary and
    result JSON.
- In `_commit_and_push_resolution`:
  - When execution is skipped (e.g., `not applied_items or not has_worktree_changes` or `opts.dry_run`), return
    `dispositions, initial_head, False, False, False, None` rather than optimistic `True, True`, ensuring that runs
    where verification tasks were not executed fail closed.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2`, `holon-config/world/constraints.md#1`, `docs/safety.md#1`
- **Potential failure modes for this step:** Passing non-list command arguments to `run_turn`, or runner lookup raising
  `ValueError` for unknown agent names.
- **Guardrails and early‑abort checks:** Wrap turn execution in existing try/except block to catch turn failures and
  record cleanly in outcome error field without crashing.

### Success & Discard Criteria

- **Success:** `_run_resolver_agent` passes command list to `run_turn(cmd, cwd=repo_dir)`; `lint_passed` and
  `tests_passed` default to `False` on failure or skipped verification.
- **Discard:** `TypeError` remains or verification reports optimistic `True` when verification was skipped or failed.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.30  |
| impact_pred         | 50.00 |
| cost_pred           | 1.50  |
| learning_value_pred | 0.50  |
| ev_pred             | 47.66 |

### Step Metrics Rationale

High probability of success (`0.98`) and minimal entropy (`0.30`) due to well-defined function signatures and
deterministic control flow. Expected value derivation:
`EV = 0.98 * 50.00 + 0.5 * 0.50 - 0.3 * 0.30 - 1.50 = 49.00 + 0.25 - 0.09 - 1.50 = 47.66`.

---

## Step 2: Add hermetic unit tests for resolver command generation, fail-closed defaults, and error logging in `test_review_loop.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Adding hermetic unit tests within the existing `test_review_loop.py` suite tests all edge cases
  deterministically using mocked runner turns and repository fixtures.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add hermetic unit tests in `apps/sandbox-executor/tests/test_review_loop.py` verifying that
  `_run_resolver_agent` invokes `runner.build_command` and passes the resulting command list to `run_turn`, that
  `lint_passed` and `tests_passed` default to `False` on failure or skipped execution, and that errors are cleanly
  recorded in the outcome dictionary.
- **Git branch:**
  `I-1793953326-fix-resolver-run-turn-argument-signature/P-1793953342-antigravity-agent-gemini-3.8-flash-medium/E-test-resolver-run-turn/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/tests/test_review_loop.py`, add a dedicated test section `TestResolverAgentExecution`:
  - Test 1 (`test_run_resolver_agent_builds_command_and_invokes_run_turn`):
    - Mock `get_runner` to return a mock runner whose `build_command(prompt, model_name)` returns
      `["my-agent", "-p", "prompt", "--model", "test-model"]`.
    - Mock `run_turn` to return a successful `TurnResult(returncode=0)`.
    - Write mock dispositions JSON to `dispositions_path`.
    - Call `_run_resolver_agent(repo_dir, "my-agent", "test-model", "prompt", dispositions_path)`.
    - Assert `runner.build_command` was called with `("prompt", "test-model")`.
    - Assert `run_turn` was called with 1 positional argument
      `cmd=["my-agent", "-p", "prompt", "--model", "test-model"]` and keyword `cwd=repo_dir`.
    - Assert returned dispositions match the parsed JSON content.
  - Test 2 (`test_run_resolver_agent_raises_on_non_zero_exit_code`):
    - Mock `run_turn` to return `TurnResult(returncode=42, stderr="agent failed")`.
    - Assert `_run_resolver_agent` raises `RuntimeError` stating resolver agent exited with code 42.
  - Test 3 (`test_run_resolver_mode_fail_closed_defaults_on_failure`):
    - Mock `_run_resolver_agent` to raise `RuntimeError("Simulated agent turn failure")`.
    - Run `_run_resolver_mode` with a temporary findings file and result file.
    - Assert process exits with code 1.
    - Inspect written result JSON file and assert `"lint_passed": False`, `"tests_passed": False`, and `"error"`
      contains `"Simulated agent turn failure"`.
  - Test 4 (`test_run_resolver_mode_fail_closed_defaults_on_skipped_execution`):
    - Run `_run_resolver_mode` with empty findings or dry-run configuration where verification is skipped.
    - Inspect written result JSON file and assert `"lint_passed": False` and `"tests_passed": False`.
  - Test 5 (`test_run_resolver_mode_passes_verification_when_verify_succeeds`):
    - Mock `_run_resolver_agent` returning applied dispositions.
    - Mock `_verify_before_push` returning `(True, True, None)` and mock git commit/push.
    - Assert result JSON reports `"lint_passed": True`, `"tests_passed": True`, and `"error": None`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Primary verification gate for acceptance criteria)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3`, `docs/safety.md#1`,
  `apps/sandbox-executor/docs/hermetic_testing.md`
- **Potential failure modes for this step:** Unmocked subprocess calls attempting to invoke real agent CLIs or git
  remotes.
- **Guardrails and early‑abort checks:** Use `tmp_path`, `monkeypatch`, and `unittest.mock.patch` for complete
  hermeticity without network or host environment access.

### Success & Discard Criteria

- **Success:** All new unit tests pass cleanly under `uv run pytest apps/sandbox-executor/tests/test_review_loop.py`.
- **Discard:** Flaky or non-hermetic tests relying on external environment state.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.50  |
| impact_pred         | 35.00 |
| cost_pred           | 1.80  |
| learning_value_pred | 1.20  |
| ev_pred             | 32.25 |

### Step Metrics Rationale

Bottleneck step (`0.96` probability of success) due to mocking multiple entrypoint interactions and result file
assertions. Expected value derivation:
`EV = 0.96 * 35.00 + 0.5 * 1.20 - 0.3 * 0.50 - 1.80 = 33.60 + 0.60 - 0.15 - 1.80 = 32.25`.

---

## Step 3: Run targeted test suite, repository linting, documentation checks, and full test suite

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard quality assurance pass ensuring zero regressions across the codebase and confirming compliance
  with repository linting standards.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Verify all acceptance criteria hermetically using
  `uv run pytest apps/sandbox-executor/tests/test_review_loop.py`, check formatting and types via `uv run task check`
  (`uv run task lint` and `uv run task lint-docs`), and execute the entire test suite once via `uv run task test`.
- **Git branch:**
  `I-1793953326-fix-resolver-run-turn-argument-signature/P-1793953342-antigravity-agent-gemini-3.8-flash-medium/E-verify-quality-gates/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Execute targeted tests: `uv run pytest apps/sandbox-executor/tests/test_review_loop.py`.
- Run repository linters: `uv run task check` (runs `uv run task lint` and `uv run task lint-docs`).
- If formatting adjustments are reported by ruff or prettier, run `uv run task format` and verify with
  `uv run task check`.
- Run full test suite once: `uv run task test`.
- Ensure all tests pass with zero failures and exit code 0.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#1`, `holon-config/world/ruleset.md#3`, User Rules for Holon Agentic
  Coder
- **Potential failure modes for this step:** Prettier formatting discrepancies on markdown files or Ruff linting errors.
- **Guardrails and early‑abort checks:** Run `uv run task format` if linter highlights style issues before final
  assertion.

### Success & Discard Criteria

- **Success:** All unit tests pass, `uv run task check` passes, and `uv run task test` passes.
- **Discard:** Lingering linter failures or regressions in test suite.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.10  |
| impact_pred         | 10.00 |
| cost_pred           | 0.70  |
| learning_value_pred | 0.30  |
| ev_pred             | 9.32  |

### Step Metrics Rationale

High confidence (`0.99`) and near-zero entropy (`0.10`) since this is an automated verification gate. Expected value
derivation: `EV = 0.99 * 10.00 + 0.5 * 0.30 - 0.3 * 0.10 - 0.70 = 9.90 + 0.15 - 0.03 - 0.70 = 9.32`.
