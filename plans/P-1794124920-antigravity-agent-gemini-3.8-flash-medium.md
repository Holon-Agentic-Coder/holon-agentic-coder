# Plan for I-1794124904-fix-review-loop-observability-blindspot

- **Plan ID:** P-1794124920-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.5.3)
- **Created At:** 2026-11-08T08:02:10Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None
- **Exploration stance:** balanced with focus on robust observability, sanitised error propagation, and complete
  alignment with established harness logging standards.
- **Safety priority level:** standard
- **Priority Justification:** All modifications are strictly scoped to internal diagnostic reporting, secret redaction,
  and unit testing within `apps/sandbox-executor/` without altering network isolation, credential stores, or sandbox
  security boundaries (`docs/safety.md#Sandboxing-model`).

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Deterministic bug fix and observability enhancement targeting error reporting paths in existing
  reviewer turn orchestration.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 2.10  |
| impact_pred         | 85.0  |
| cost_pred           | 4.00  |
| learning_value_pred | 4.00  |
| ev_pred             | 78.97 |

### Strategy Rationale

The intent is accepted as is (`ACCEPT_AS_IS`). In the current implementation of `review_ensemble.py`, when a reviewer
agent crashes prior to writing `report.md` (for instance, encountering exit code 127 due to a missing binary, an
argument length overflow, or an immediate Python fatal exception), `_result_from_outcome()` falls back to a generic
error message: `"the reviewer wrote no report"`. This discards the subprocess exit code, stderr diagnostic stream, and
command execution details, creating an observability blindspot that obliges operators to reproduce failures manually
inside containers.

The strategy resolves this blindspot systematically across three focused steps:

1. **Diagnostic Error Extraction:** Updating `_turn_failure()` and `_result_from_outcome()` in `review_ensemble.py` to
   inspect the `TurnResult` returncode and stderr when `report.md` is absent, formatting rich diagnostics with mandatory
   secret redaction and providing a clean `.reason` property alias on `ReviewerResult`.
2. **Command & Completion Observability:** Bringing `review_ensemble.py` into strict alignment with `planner.py` and
   `executor.py` conventions by introducing `format_agent_cmd_for_display()`, logging invoked reviewer command lines,
   and recording process completion telemetry.
3. **Rigorous Verification:** Enhancing `apps/sandbox-executor/tests/test_review_ensemble.py` with test cases verifying
   exit code 127 surfacing, stderr extraction, secret redaction, and command logging.

Overall plan metrics are derived as follows:

- **Success Probability (`p_success_pred = 0.96`):** Governed by the bottleneck verification step, Step 3
  (`p_success_pred = 0.96`), ensuring complete coverage without regressions against the existing test suite.
- **Plan Entropy (`entropy_pred = 2.10`):** Calculated as the sum of step-level entropies:
  `Delta_S = 0.70 + 0.60 + 0.80 = 2.10`. This is well below the allocated budget of `15.0`.
- **Impact (`impact_pred = 85.0`):** Directly eliminates silent failure modes across all review loops, drastically
  reducing container debugging latency for automated reviewers.
- **Cost (`cost_pred = 4.00`):** Derived from cumulative execution, formatting, and unit testing costs:
  `Cost = 1.20 + 1.00 + 1.80 = 4.00`.
- **Learning Value (`learning_value_pred = 4.00`):** Provides valuable behavioural telemetry and consistent
  observability patterns across all authoring stages.
- **Expected Value (`ev_pred = 78.97`):** Computed strictly using the system configuration constants (`lambda = 0.3`,
  `mu = 0.5`): `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.96 * 85.0 + 0.5 * 4.00 - 0.3 * 2.10 - 4.00 = 81.60 + 2.00 - 0.63 - 4.00 = 78.97`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md#1-git-flow--branch-constraints`: Execution runs strictly on isolated intent and
    plan branches. Merging to `main` remains the exclusive prerogative of human maintainers.
  - `holon-config/world/constraints.md#2-sandbox-containment-tiers`: Low predicted plan entropy (`2.10 < 10.0`) allows
    execution within the hermetic process sandbox with complete network isolation.
  - `holon-config/world/constraints.md#3-ledger-immutability`: Append-only ledgers (`executions.jsonl`, `intents.jsonl`,
    `plans.jsonl`) remain strictly append-only; no rewriting or deletion of historical records.
  - `holon-config/world/ruleset.md#1-runtime--environment-specification`: Strictly Python `==3.13.*` standard library
    without adding external package dependencies.
  - `holon-config/world/ruleset.md#2-coding-conventions--standards`: Full PEP 8 compliance, comprehensive type
    annotations, Australian English spelling conventions, and descriptive docstrings.
  - `holon-config/world/ruleset.md#3-testing-constraints`: All unit testing executed strictly through `uv run pytest`;
    existing test assertions remain intact.
  - `docs/safety.md#Sandboxing-model`: Credential sanitisation via `redact_agent_secrets()` is enforced on all error
    messages before console logging or ledger storage.
- **Potential violations or edge cases:**
  - Accidental leakage of provider API keys or bearer tokens in reviewer stderr diagnostic messages.
  - Unbounded or multiline stderr outputs causing ledger formatting corruption or log line flooding.
  - Breaking existing tests that expect `"the reviewer wrote no report"` when returncode is 0 and stderr is clean.
  - Subprocess exit code 0 turns that legitimately produced no file being misclassified as crashes.
- **Mitigations built into the plan:**
  - Route all stderr strings through `redact_agent_secrets()` prior to concatenation, display, or persistence.
  - Enforce character clamping (`[:300]`) on truncated stderr fragments within failure reasons, respecting
    `MAX_REASON_CHARS` (500 characters).
  - Preserve the canonical `"the reviewer wrote no report"` message when returncode is 0 and stderr is whitespace or
    empty.
  - Treat non-zero exit codes as informational if a valid, parseable report was written to disk, preserving existing
    salvage and trailer evaluation behaviour.
- **Residual risk accepted (and why):**
  - Minimal residual risk of obscure multi-token secrets split across truncation boundaries; accepted because
    `redact_agent_secrets()` runs prior to slicing.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 2.10
- **Budget Compliance:** The strategy fits within budget (`2.10 <= 15.0`).

## Plan Description & Strategy

This plan resolves the observability blindspot in `apps/sandbox-executor/src/sandbox_executor/review_ensemble.py`.
Currently, when a reviewer agent fails before producing `report.md`, the failure reason defaults to a generic string,
discarding critical diagnostics such as non-zero returncodes (e.g. exit code 127 from missing executables) and standard
error messages.

The solution encompasses:

1. Enhancing error inspection in `_turn_failure()` and `_result_from_outcome()` to examine the underlying `TurnResult`
   objects within `AuthoringOutcome`. When a report is missing, any non-zero exit code or stderr content is extracted,
   sanitised via `redact_agent_secrets()`, truncated cleanly, and formatted into the failure reason.
2. Introducing a convenient `@property def reason(self) -> str | None:` alias on `ReviewerResult` pointing to
   `self.failure_reason` for intuitive and backward-compatible access.
3. Adding `format_agent_cmd_for_display()` in `review_ensemble.py` and integrating command logging in
   `_command_builder()` alongside process completion logging in `_supervise_reviewer_turn()`, harmonising with
   conventions in `planner.py` and `executor.py`.
4. Adding thorough unit tests in `apps/sandbox-executor/tests/test_review_ensemble.py` covering returncode 127 handling,
   stderr extraction, secret masking, and backward compatibility.

---

## Step 1: Surface Process Errors and Sanitise Diagnostics in Review Ensemble

- **Sub‑intent recommendation:** NO
- **Reasoning:** High-cohesion internal modification in a single module with low entropy and deterministic behaviour.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update `_turn_failure()` and `_result_from_outcome()` in `review_ensemble.py` to inspect `TurnResult`
  returncode and stderr when the report is missing, redact secrets, format diagnostic failure reasons, and add the
  `reason` property on `ReviewerResult`.
- **Git branch:**
  `I-1794124904-fix-review-loop-observability-blindspot/P-1794124920-antigravity-agent-gemini-3.8-flash-medium/E-surface-process-errors`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/review_ensemble.py`, add a property `reason` to `ReviewerResult` that
   returns `self.failure_reason`, ensuring both attribute names resolve seamlessly.
2. Define a helper function `_missing_report_reason(outcome: AuthoringOutcome) -> str` that inspects the most recent
   `TurnResult` in `outcome.turns`:
   - If no turns are present, return the default `"the reviewer wrote no report"`.
   - Retrieve `turn.returncode` and extract any non-empty `turn.stderr`.
   - If `turn.stderr` contains text, strip whitespace, run it through `redact_agent_secrets()`, and clamp it to 300
     characters appending an ellipsis if truncated.
   - If `turn.returncode != 0` and stderr is present, format the reason as
     `"the reviewer exited with code {turn.returncode}: {truncated_stderr}"`.
   - If `turn.returncode != 0` and stderr is empty, format the reason as
     `"the reviewer exited with code {turn.returncode}"`.
   - If `turn.returncode == 0` and stderr is present, format the reason as
     `"the reviewer wrote no report (stderr: {truncated_stderr})"`.
   - If `turn.returncode == 0` and stderr is empty, return the baseline `"the reviewer wrote no report"`.
3. Update `_turn_failure(outcome: AuthoringOutcome, *, report_missing: bool = False) -> str | None`:
   - First check `outcome.breach`; if present, retain the existing budget breach description
     (`"review turn cut short: ..."`).
   - If `report_missing` is True, invoke `_missing_report_reason(outcome)` and return the formatted diagnostic string.
   - Otherwise, return None when the report is present and no breach occurred.
4. Update `_result_from_outcome()`:
   - Check `_turn_failure(outcome)`. If a breach occurred, return `ReviewerResult.failed()` immediately.
   - Read the report from disk. If the report text is None, call `_turn_failure(outcome, report_missing=True)` to obtain
     the diagnostic reason and return `ReviewerResult.failed(index, agent, model, report_rel, reason)`.
   - If report text is present, continue with existing trailer parsing and consistency checks.
5. Ensure all error strings pass through `_bounded_reason()` so that `MAX_REASON_CHARS` (500 characters) and secret
   redaction are strictly maintained.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md#Sandboxing-model`,
  `holon-config/world/ruleset.md#2-coding-conventions--standards`.
- **Potential failure modes for this step:**
  - Attribute errors if `outcome.turns` is empty.
  - Accidental leakage of sensitive credentials if unredacted stderr strings are formatted directly.
  - Formatting regressions in existing tests expecting `"the reviewer wrote no report"` for benign clean turns.
- **Guardrails and early‑abort checks:**
  - Safe access to `outcome.turns` with fallback to default string if empty.
  - Strict wrapping with `redact_agent_secrets()` on all stderr extractions.

### Success & Discard Criteria

- **Success:** When `report.md` is absent, `ReviewerResult.failure_reason` and `ReviewerResult.reason` capture non-zero
  returncodes and redacted stderr diagnostics instead of generic messages.
- **Discard:** Discard if changes cause regressions in valid report trailer parsing or if secrets escape redaction
  filters.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.70  |
| impact_pred         | 82.0  |
| cost_pred           | 1.20  |
| learning_value_pred | 3.50  |
| ev_pred             | 80.70 |

### Step Metrics Rationale

This step carries high probability of success (`0.98`) and low entropy (`0.70`) because it modifies well-isolated pure
functions in `review_ensemble.py`. Cost is minimal (`1.20`), and expected value is high:
`EV = 0.98 * 82.0 + 0.5 * 3.50 - 0.3 * 0.70 - 1.20 = 80.36 + 1.75 - 0.21 - 1.20 = 80.70`.

---

## Step 2: Harmonise Command Logging and Completion Status Telemetry

- **Sub‑intent recommendation:** NO
- **Reasoning:** Straightforward observability alignment matching existing patterns from `planner.py` and `executor.py`.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add `format_agent_cmd_for_display()` to `review_ensemble.py`, log the agent execution command line in
  `_command_builder()`, and log process completion status in `_supervise_reviewer_turn()`.
- **Git branch:**
  `I-1794124904-fix-review-loop-observability-blindspot/P-1794124920-antigravity-agent-gemini-3.8-flash-medium/E-command-logging-telemetry`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/review_ensemble.py`, import `redact_command_args` from
   `sandbox_executor.redaction`.
2. Define `_AGENT_ARG_DISPLAY_LEN = 100` and `format_agent_cmd_for_display(cmd: Sequence[str]) -> str`:
   - Iterate over arguments sanitised by `redact_command_args(cmd)`.
   - Clamp arguments exceeding `_AGENT_ARG_DISPLAY_LEN` characters by appending an ellipsis (`...`).
   - Return a single space-joined string safe for console display and logging.
   - Export `format_agent_cmd_for_display` in `__all__`.
3. In `_command_builder(runner, model, prompt_file, intent_file)`:
   - When constructing the command vector via `runner.build_cmd(...)`, format the display representation using
     `format_agent_cmd_for_display(cmd)`.
   - Log the execution command with `logger.info("Running command: %s", formatted)`.
   - Print the execution command via `print(f"Running command: {formatted}")` matching `planner.py` console conventions.
4. Define `_log_turn_completion(outcome: AuthoringOutcome) -> None`:
   - Inspect `outcome.turns`. If empty, return cleanly.
   - Extract the last `TurnResult` and check `outcome.report`.
   - If the report exists and is usable, log informational notice if returncode was non-zero or output was truncated:
     `"Info: reviewer agent exited with code %d (stop reason: %s) after writing a valid review report; treating the exit status as informational."`
     matching `planner.py` and `executor.py`.
   - If the report is missing or unusable and returncode is non-zero, log warning:
     `"Reviewer agent command failed with code %d (stop reason: %s)."` and log redacted stderr if non-empty.
5. Call `_log_turn_completion(outcome)` at the conclusion of `_supervise_reviewer_turn()` before returning the outcome.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md#Sandboxing-model`,
  `holon-config/world/ruleset.md#2-coding-conventions--standards`.
- **Potential failure modes for this step:**
  - Token leakage if argument clamping occurs before credential redaction.
  - Logging noise or unhandled formatting exceptions during unexpected argument types.
- **Guardrails and early‑abort checks:**
  - Enforce per-argument redaction via `redact_command_args` prior to length clamping.

### Success & Discard Criteria

- **Success:** Reviewer command execution is printed and logged with credentials masked, and process completion status
  is recorded matching planner and executor observability standards.
- **Discard:** Discard if command formatting leaks credentials or fails on non-standard argv lists.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.60  |
| impact_pred         | 78.0  |
| cost_pred           | 1.00  |
| learning_value_pred | 3.00  |
| ev_pred             | 76.76 |

### Step Metrics Rationale

High confidence (`0.98`) and minimal entropy (`0.60`) as this directly replicates established patterns from `planner.py`
and `executor.py`. `EV = 0.98 * 78.0 + 0.5 * 3.00 - 0.3 * 0.60 - 1.00 = 76.44 + 1.50 - 0.18 - 1.00 = 76.76`.

---

## Step 3: Implement Unit Tests for Returncode and Stderr Diagnostics Surfacing

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard test suite extension verifying bug fixes and diagnostic surfacing.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add comprehensive unit tests in `apps/sandbox-executor/tests/test_review_ensemble.py` asserting that
  non-zero exit codes (127), stderr diagnostics, and secrets redaction surface in `ReviewerResult.reason`.
- **Git branch:**
  `I-1794124904-fix-review-loop-observability-blindspot/P-1794124920-antigravity-agent-gemini-3.8-flash-medium/E-test-review-diagnostics`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_review_ensemble.py`, update `FakeTurnRunner`:
   - Extend `_play()` to support an action specification that specifies non-zero returncodes, stdout, and stderr strings
     (e.g. `("exit", code, stderr)` or custom `TurnResult` attributes).
2. Add test `test_failed_turn_surfaces_exit_code_127_in_reason(tmp_path: Path)`:
   - Configure a fake turn that produces no report and exits with returncode 127.
   - Run `run_reviewer()`.
   - Assert `result.status` is `ReviewerStatus.FAILED`.
   - Assert `result.verdict` is None.
   - Assert `"127"` and `"exited with code 127"` appear in `result.reason` and `result.failure_reason`.
3. Add test `test_failed_turn_surfaces_stderr_in_reason(tmp_path: Path)`:
   - Configure a fake turn that exits with returncode 1 and emits standard error text such as
     `"bash: /bin/agent: No such file or directory"`.
   - Run `run_reviewer()`.
   - Assert the stderr text appears in `result.reason`.
4. Add test `test_failed_turn_redacts_secrets_in_stderr(tmp_path: Path)`:
   - Configure a fake turn that emits a realistic secret (e.g. GitHub PAT or provider token `ghp_...` matching `SECRET`)
     in stderr.
   - Run `run_reviewer()`.
   - Assert `result.status` is `ReviewerStatus.FAILED`.
   - Assert `SECRET` is not in `result.reason` or `result.failure_reason`, and `*******` is present.
5. Add test `test_format_agent_cmd_for_display_in_review_ensemble()`:
   - Assert that sensitive arguments in reviewer command lines are masked before long argument clamping.
6. Verify that all existing unit tests in `test_review_ensemble.py` pass without regression.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (Acts as the primary quality gate for the entire plan)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3-testing-constraints`, `docs/safety.md#Sandboxing-model`.
- **Potential failure modes for this step:**
  - Mock runner discrepancies between test seams and production `supervise_authoring_turn`.
  - Fragile assertion matchers that fail if log wording shifts slightly.
- **Guardrails and early‑abort checks:**
  - Test assertions target key semantic tokens (`"127"`, stderr substring, redaction marker) rather than rigid
    full-string equality where appropriate.

### Success & Discard Criteria

- **Success:** All new tests pass, confirming exit code 127 and stderr are surfaced in `ReviewerResult.reason` with
  secrets redacted, while all prior tests continue to pass.
- **Discard:** Discard if tests fail to converge or cause regressions in consensus calculation.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.80  |
| impact_pred         | 85.0  |
| cost_pred           | 1.80  |
| learning_value_pred | 4.00  |
| ev_pred             | 81.56 |

### Step Metrics Rationale

Slightly higher entropy (`0.80`) and cost (`1.80`) due to comprehensive test fixture enhancements and boundary
assertions. `EV = 0.96 * 85.0 + 0.5 * 4.00 - 0.3 * 0.80 - 1.80 = 81.60 + 2.00 - 0.24 - 1.80 = 81.56`.
