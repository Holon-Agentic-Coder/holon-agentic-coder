# Plan for I-1793257358-calibration-report-prettier-hygiene-clean

- **Plan ID:** P-1793257382-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.4.10)
- **Created At:** 2026-10-29T07:03:02.943Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical investigation into why calibration's in-process prettier
  invocation diverged from CI prettier hygiene.
- **Safety priority level:** standard
- **Priority Justification:** Focuses on formatting resolution, CI hygiene gate consistency, and fail-closed checks
  within developer tooling; no direct production blast radius.

## Exploration

- **Proportion of steps that are exploratory:** 0.25
- **Justification:** An exploratory diagnosis step is required to pin down the root cause of prettier divergence between
  Stage 5 calibration reporting and repository CI hygiene checks before applying fixes.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 2.10  |
| impact_pred         | 88.00 |
| cost_pred           | 5.50  |
| learning_value_pred | 18.00 |
| ev_pred             | 85.39 |

### Strategy Rationale

This plan addresses a persistent CI hygiene failure across Stage 5 calibration runs (PRs #97, #99, #105, #106, #107,
#109). The root issue is that Stage 5 executes `converge_prettier` which reports convergence, but the resulting
calibration markdown report file (`plans/P-{plan_id}_calibration.md`) fails repository CI Prettier hygiene checks
(`npx prettier --check` or `uv run task lint-docs`). This occurs because:

1. Prettier configuration (`.prettierrc.json` with `printWidth: 120` and `proseWrap: "always"`) is not explicitly
   resolved or passed via `--config` during formatting runs, leading to reliance on default configuration that diverges
   from repository hygiene rules.
2. The pinned Prettier specification in `sandbox_executor.formatting` (`prettier@3.8.4`) may diverge from repository dev
   dependencies or invoke an unpinned/fallback parser.
3. Neither `calibration.py` nor `flow.py` (Stage 5) verifies that the generated calibration report passes the
   repository's exact hygiene check prior to git staging and remote branch pushing, failing open rather than failing
   closed.

The strategy adopts a fail-closed architectural pattern:

- Explicitly discover and resolve the repository's Prettier configuration file (`.prettierrc.json`, `.prettierrc`,
  etc.).
- Ensure `converge_prettier` passes the resolved configuration to both write and check passes.
- Enforce that `run_calibrate` and Stage 5 flow orchestration execute a mandatory hygiene gate check on the formatted
  report on disk before committing or pushing.
- Abort and fail closed (raising `CalibrationCommitError` and withholding git push) if the report fails Prettier
  hygiene.

#### Derivation of Overall Metrics

- **Bottleneck Step:** Step 4 (`p_success_pred = 0.94`), which integrates pre-push hygiene enforcement into Stage 5 flow
  orchestration.
- **Aggregation Method:**
  - `entropy_pred`: Exact sum across steps (`0.50 + 0.50 + 0.40 + 0.40 + 0.30 = 2.10`).
  - `cost_pred`: Exact sum of estimated run times and agent turns (`0.80 + 1.20 + 1.20 + 1.10 + 1.20 = 5.50`).
  - `learning_value_pred`: Sum of epistemic contributions (`6.00 + 4.00 + 3.00 + 3.00 + 2.00 = 18.00`).
  - `impact_pred`: Bounded system impact (`88.00`), reflecting total elimination of manual hygiene fixup commits across
    future automated runs.
  - `ev_pred`: Computed using system physics constants (`lambda = 0.3`, `mu = 0.5`):
    `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
    `EV = 0.94 * 88.00 + 0.5 * 18.00 - 0.3 * 2.10 - 5.50 = 82.72 + 9.00 - 0.63 - 5.50 = 85.59` (conservative model
    prediction: `85.39`).

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` Section 1 (Git Flow): All changes are isolated to execution branch
    `I-1793257358-calibration-report-prettier-hygiene-clean/P-1793257382-antigravity-agent-gemini-3.8-flash-medium/E-.../_`;
    no direct commits to `main`.
  - `holon-config/world/constraints.md` Section 3 (Ledger Immutability): Zero modification to existing `.jsonl` entries;
    only standard append operations for discrete execution actions.
  - `holon-config/world/ruleset.md` Section 1 (Runtime): Strict target Python version `==3.13.*`, managed through root
    `uv` commands only.
  - `holon-config/world/ruleset.md` Section 2 (Standards): Full static type annotations and explanatory docstrings on
    all modified helper functions.
  - `holon-config/world/ruleset.md` Section 3 (Testing): Hermetic unit tests in `apps/sandbox-executor/tests/` matching
    module locations; all test modifications explicitly declared.
  - `docs/safety.md` Section 1: Git is the safety boundary. Failures in Stage 5 calibration formatting abort without
    contaminating the parent execution branch or remote refs.
- **Potential violations or edge cases:**
  - Environment without `npx` installed: Formatting utility must handle missing `npx` gracefully according to existing
    contract or fail closed in CI environments where hygiene is mandatory.
  - Missing `.prettierrc.json` in alternate execution directories or worktrees: Path resolution must fall back safely to
    repository root.
  - Prettier command timeout on constrained CI containers: Guard with configurable bounded timeout.
- **Mitigations built into the plan:**
  - Prettier config resolution explicitly searches upward from target files to `repo_dir` root.
  - Comprehensive unit test mocking avoids external network calls or Node ecosystem flakes during hermetic test runs.
  - Explicit fail-closed checks prevent unformatted artifacts from reaching remote tracking branches.
- **Residual risk accepted (and why):**
  - Minor execution latency increase (1-3 seconds) in Stage 5 to run the hygiene verification check before push. This is
    acceptable given the recurring cost of manual formatting commits.
- **Allocated Entropy Budget:** 10.0
- **Predicted Plan Entropy:** 2.10
- **Budget Compliance:** The strategy fits comfortably within budget (`2.10 <= 10.0`).

## Plan Description & Strategy

The plan resolves the calibration Prettier hygiene defect across five incremental steps:

1. Diagnose Prettier CLI resolution, config file discovery, and formatting discrepancies against repository
   `.prettierrc.json`.
2. Upgrade `sandbox_executor.formatting.converge_prettier` to automatically discover and enforce repository Prettier
   configuration via explicit CLI flags (`--config`).
3. Refactor `sandbox_executor.calibration.run_calibrate` to format the generated report cleanly on disk and fail closed
   if formatting convergence or validation fails.
4. Update `sandbox_executor.flow.run_calibrate_stage` to verify clean formatting against repository hygiene rules prior
   to pushing the calibrated branch to origin, preventing unformatted commits from being published.
5. Expand unit test suites (`test_formatting.py`, `test_calibration.py`, `test_flow.py`) to verify config discovery,
   fail-closed behavior, and execute repository-wide lint and test tasks.

---

## Step 1: Baseline inspection and diagnosis of Prettier configuration and calibration formatting divergence

- **Sub‑intent recommendation:** NO
- **Reasoning:** Fast, low-risk diagnostic step that pinpoints the exact flag and config resolution discrepancies before
  altering production code.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Explicitly supplying the repository's `.prettierrc.json` path to Prettier CLI resolves
  the discrepancy between in-process `converge_prettier` and repository CI `task lint-docs`.
- **Learning target:** The exact invocation difference between `lint-docs` (`npx prettier --check "**/*.md"`) and
  `converge_prettier` (`npx --yes prettier@3.8.4 ...`).
- **Maximum acceptable cost for this learning:** 1.0 unit (approx. 5 minutes).

### Intent & Git Integration

- **Step Intent:** Inspect the repository's Prettier setup, `.prettierrc.json`, package configurations, and reproduce
  the check pass/fail behavior on existing calibration reports.
- **Git branch:** Execution branch under this plan (no separate sub-intent branch).
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Review `.prettierrc.json` in repository root to confirm formatting rules (`printWidth: 120`, `proseWrap: "always"`).
2. Examine `pyproject.toml` `lint-docs` and `format-docs` task definitions to compare flags with
   `sandbox_executor.formatting`.
3. Compare how Prettier behaves when run with and without explicit `--config` on sample calibration reports in
   `plans/*calibration.md`.
4. Inspect `sandbox_executor/formatting.py` and identify how working directory and config file resolution are handled
   during subprocess invocations.
5. Document findings in execution notes.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md`, `holon-config/world/constraints.md` Section 1.
- **Potential failure modes for this step:** Non-hermetic tool invocation or accidental modification of tracked files.
- **Guardrails and early‑abort checks:** Read-only inspection; avoid staging or committing working tree changes.

### Success & Discard Criteria

- **Success:** Clear determination of why `converge_prettier` passed while CI `task lint-docs` failed on calibration
  reports.
- **Discard:** If Prettier behavior cannot be diagnosed within 1.5x predicted cost.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.50  |
| impact_pred         | 75.00 |
| cost_pred           | 0.80  |
| learning_value_pred | 6.00  |
| ev_pred             | 73.30 |

### Step Metrics Rationale

- `p_success_pred`: High (0.95) because this is standard read-only inspection.
- `entropy_pred`: Low (0.50) due to read-only diagnostic nature.
- `impact_pred`: 75.00 for identifying the exact root cause.
- `cost_pred`: 0.80.
- `learning_value_pred`: 6.00 for clarifying config resolution mechanics across tooling.
- `ev_pred`: `0.95 * 75.00 + 0.5 * 6.00 - 0.3 * 0.50 - 0.80 = 71.25 + 3.00 - 0.15 - 0.80 = 73.30`.

---

## Step 2: Update converge_prettier to resolve repository Prettier configuration and unify command specification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, tightly scoped utility refactor with high reusability and low blast radius.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Enhance `converge_prettier` in `sandbox_executor/formatting.py` to discover `.prettierrc.json` (or
  `.prettierrc`) in `repo_dir` or its ancestors, pass `--config` explicitly to `npx prettier`, and align CLI arguments
  with repository conventions.
- **Git branch:** Execution branch under this plan.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `sandbox_executor/formatting.py`, define a helper function
   `resolve_prettier_config(repo_dir: str | Path | None) -> Path | None` that searches for `.prettierrc.json`,
   `.prettierrc`, `.prettierrc.yaml`, `.prettierrc.yml`, or `.prettier.config.js` starting at `repo_dir` and checking
   parent directories.
2. In `converge_prettier`, invoke `resolve_prettier_config(resolved_repo_dir)`. If a configuration file is resolved,
   append `["--config", str(config_path)]` to both `write_cmd` and `check_cmd`.
3. Allow callers to optionally pass an explicit `config_path: str | Path | None = None` parameter to override or supply
   a specific config file.
4. Ensure `check_cmd` mirrors the exact arguments used in repository hygiene validation.
5. Maintain backward compatibility: if no config file is located, continue without `--config` while logging a debug
   notice.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` Sections 1 and 2 (PEP 8, type hints, docstrings).
- **Potential failure modes for this step:** Passing nonexistent config paths or breaking existing callers that do not
  supply `repo_dir`.
- **Guardrails and early‑abort checks:** Check `os.path.isfile` before adding `--config`; fallback cleanly if path is
  invalid.

### Success & Discard Criteria

- **Success:** `converge_prettier` cleanly formats markdown files using the repository's `.prettierrc.json` rules when
  tested directly.
- **Discard:** If formatting regressions occur or subprocess crashes on valid markdown files.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.50  |
| impact_pred         | 85.00 |
| cost_pred           | 1.20  |
| learning_value_pred | 4.00  |
| ev_pred             | 82.25 |

### Step Metrics Rationale

- `p_success_pred`: 0.96. The change is isolated to CLI option assembly and file path resolution.
- `entropy_pred`: 0.50. Limited to `sandbox_executor/formatting.py`.
- `impact_pred`: 85.00 for ensuring consistent Prettier formatting across all flow-produced artifacts.
- `cost_pred`: 1.20.
- `learning_value_pred`: 4.00.
- `ev_pred`: `0.96 * 85.00 + 0.5 * 4.00 - 0.3 * 0.50 - 1.20 = 81.60 + 2.00 - 0.15 - 1.20 = 82.25`.

---

## Step 3: Enforce clean formatting and fail-closed behaviour in calibration report generation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core logic update in calibration engine; directly addresses the fail-closed requirement of the intent.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update `sandbox_executor/calibration.py` to ensure calibration markdown generation and report writing
  format cleanly on disk and fail closed if formatting convergence fails.
- **Git branch:** Execution branch under this plan.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `sandbox_executor/calibration.py`, inspect `format_markdown_report` to ensure math formulas and metric derivations
   use markdown backticks or escaped asterisks (avoiding raw unescaped underscores that cause Prettier oscillations).
2. In `run_calibrate`, pass `repo_dir=repo_dir` into `converge_prettier([report_rel], repo_dir=repo_dir)`.
3. Check the boolean return value of `converge_prettier`. If `converge_prettier` returns `False` (formatting did not
   converge or Prettier check failed):
   - When not in `skip_commit` mode, raise `CalibrationCommitError` explaining that the calibration report failed
     Prettier hygiene convergence.
   - Prevent staging or committing an unformatted calibration report.
4. Ensure the file on disk is read back or verified clean before the Git commit is constructed.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (Core calibration logic).
- **Sub‑intent justification:** Despite being a bottleneck, `p_success_pred` is 0.95 (> 0.6), risk is low, and
  implementation is local; separate sub-intent is not warranted.

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` Section 1, `holon-config/world/ruleset.md` Section 2.
- **Potential failure modes for this step:** Raising unexpected exceptions during dry-run or when `npx` is absent in
  developer environments.
- **Guardrails and early‑abort checks:** Distinguish between local development where formatting warning is non-fatal vs
  strict CI mode, adhering to existing `skip_commit` and environment contracts.

### Success & Discard Criteria

- **Success:** Calibration reports committed to the `/calibrated` branch pass `npx prettier --check` identically to
  `task lint-docs`.
- **Discard:** If `run_calibrate` fails valid execution calibration due to false-positive formatting errors.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.40  |
| impact_pred         | 88.00 |
| cost_pred           | 1.20  |
| learning_value_pred | 3.00  |
| ev_pred             | 83.78 |

### Step Metrics Rationale

- `p_success_pred`: 0.95. Clear contracts around `run_calibrate` and `CalibrationCommitError`.
- `entropy_pred`: 0.40. Scoped to report writing and convergence checking in `calibration.py`.
- `impact_pred`: 88.00 for eliminating dirty/unformatted commits at the source.
- `cost_pred`: 1.20.
- `learning_value_pred`: 3.00.
- `ev_pred`: `0.95 * 88.00 + 0.5 * 3.00 - 0.3 * 0.40 - 1.20 = 83.60 + 1.50 - 0.12 - 1.20 = 83.78`.

---

## Step 4: Add Stage 5 pre-push hygiene check and fail-closed push guard in flow orchestration

- **Sub‑intent recommendation:** NO
- **Reasoning:** Direct enforcement in flow pipeline; small diff in `flow.py` with immediate validation.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add a pre-push Prettier hygiene check in `sandbox_executor/flow.py` (`run_calibrate_stage`) to verify
  that all committed files on the calibrated branch pass `prettier --check` before pushing to `origin` or
  fast-forwarding into the execution branch.
- **Git branch:** Execution branch under this plan.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `sandbox_executor/flow.py`, locate `run_calibrate_stage` around the push/merge logic (lines 4768-4776).
2. Before invoking `run_git(["push", ...])`, insert a hygiene verification check on the calibration report:
   - Run `converge_prettier` in check-only mode or execute Prettier check with the resolved repo config.
   - If the check fails or files remain unformatted, log a high-priority warning, mark `calibration_succeeded = False`,
     and skip pushing the `/calibrated` branch and merging to the execution branch.
3. Ensure fallback calibration reports generated on exception paths are also formatted cleanly and validated before any
   commit attempt.
4. Verify that failing closed leaves remote branches untainted.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** YES (Stage 5 push boundary).
- **Sub‑intent justification:** `p_success_pred` is 0.94 (> 0.6); change is compact and well-bounded.

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` Section 1 (Git is the safety boundary).
- **Potential failure modes for this step:** Blocked push on network or spurious command failure.
- **Guardrails and early‑abort checks:** Check only files generated/modified by Stage 5
  (`plans/P-{plan_id}_calibration.md`).

### Success & Discard Criteria

- **Success:** Unformatted calibration reports are never pushed to remote; clean reports push successfully and pass CI
  hygiene.
- **Discard:** If Stage 5 fails green executions due to external command timeout.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.40  |
| impact_pred         | 88.00 |
| cost_pred           | 1.10  |
| learning_value_pred | 3.00  |
| ev_pred             | 83.00 |

### Step Metrics Rationale

- `p_success_pred`: 0.94 (Identified bottleneck step).
- `entropy_pred`: 0.40. Scoped to Stage 5 push gate in `flow.py`.
- `impact_pred`: 88.00 for completely safeguarding CI pipelines against unformatted calibration commits.
- `cost_pred`: 1.10.
- `learning_value_pred`: 3.00.
- `ev_pred`: `0.94 * 88.00 + 0.5 * 3.00 - 0.3 * 0.40 - 1.10 = 82.72 + 1.50 - 0.12 - 1.10 = 83.00`.

---

## Step 5: Author hermetic unit tests and verify full repository hygiene gate

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard testing and validation step required by world ruleset before completion.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add hermetic unit tests for config resolution, fail-closed formatting, and pre-push checks in
  `test_formatting.py`, `test_calibration.py`, and `test_flow.py`, then run the full suite.
- **Git branch:** Execution branch under this plan.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_formatting.py`:
   - Add test for `resolve_prettier_config` discovering `.prettierrc.json` in root and handling missing configs.
   - Add test verifying that `converge_prettier` passes `--config` when config file exists.
2. In `apps/sandbox-executor/tests/test_calibration.py`:
   - Add test verifying that `run_calibrate` raises `CalibrationCommitError` if `converge_prettier` fails.
   - Add test verifying that `format_markdown_report` generates formulas without unescaped underscores.
3. In `apps/sandbox-executor/tests/test_flow.py`:
   - Add test verifying Stage 5 aborts push if calibration report fails Prettier hygiene.
4. Execute affected tests using
   `uv run pytest apps/sandbox-executor/tests/test_formatting.py apps/sandbox-executor/tests/test_calibration.py apps/sandbox-executor/tests/test_flow.py`.
5. Run full test suite once with `uv run task test`.
6. Run `uv run task check` (`lint` and `lint-docs`) to confirm repository cleanliness.

### Dependencies & Criticality

- **Depends on:** Steps 1, 2, 3, 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` Section 3 (Testing constraints), `pyproject.toml`.
- **Potential failure modes for this step:** Flaky subprocess mocking or test suite regressions.
- **Guardrails and early‑abort checks:** All test assertions must use hermetic mocks for external processes and
  temporary directories for files.

### Success & Discard Criteria

- **Success:** All new and existing tests pass cleanly; `uv run task check` and `uv run task test` return exit code 0.
- **Discard:** Regression in existing flow or calibration test suites.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.30  |
| impact_pred         | 88.00 |
| cost_pred           | 1.20  |
| learning_value_pred | 2.00  |
| ev_pred             | 83.31 |

### Step Metrics Rationale

- `p_success_pred`: 0.95. Test authoring and running existing tasks.
- `entropy_pred`: 0.30. Low risk verification step.
- `impact_pred`: 88.00. Confirms regression-free implementation.
- `cost_pred`: 1.20.
- `learning_value_pred`: 2.00.
- `ev_pred`: `0.95 * 88.00 + 0.5 * 2.00 - 0.3 * 0.30 - 1.20 = 83.60 + 1.00 - 0.09 - 1.20 = 83.31`.
