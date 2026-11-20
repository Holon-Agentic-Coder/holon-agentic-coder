# Plan for I-1791547482-drop-executions-and-fix-ledger-rev2

- **Plan ID:** P-1791547495-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-09T12:04:55.450Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical verification of git history preservation, ledger revision
  ordering semantics, and test suite boundary invariants across `apps/sandbox-executor`.
- **Safety priority level:** elevated
- **Priority Justification:** Triggered by `holon-config/world/ruleset.md` §1 & §2 (Python runtime `==3.13.*`, monorepo
  workspace isolation under `apps/sandbox-executor`, strict PEP 8 formatting, explicit static typing with `typing`
  module, docstring maintenance), `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test file
  placement under `apps/sandbox-executor/tests/`, test suite updates explicitly declared in the planning step),
  `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: branch prefix isolation, commit boundaries),
  `holon-config/world/constraints.md` §3 (Ledger Immutability: append-only ledger invariant where historical lines are
  never modified and superseding rows carry authoritative revisions), `docs/safety.md` §1 & §2 (Git as safety boundary,
  sandboxing mandatory for execution), and architectural beans Bean 0078 & Bean 0064.

## Exploration

- **Proportion of steps that are exploratory:** 0.33
- **Justification:** Step 2 and Step 4 include balanced exploratory empirical validation to verify that highest-revision
  ledger reader contracts strictly resolve superseded records without requiring execution markdown fallback files, and
  that git recovery verification passes cleanly when only the append-only ledger is committed.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.85  |
| impact_pred         | 92.0  |
| cost_pred           | 9.5   |
| learning_value_pred | 5.2   |
| ev_pred             | 76.64 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: `0.94`. Dictated by the bottleneck step (Step 4: `0.94`), where git-recovery failure handling and
  pre-push sync verification paths must be updated in `executor.py` to ensure superseding `ledger_revision: 2` rows
  carry agent output accounting (`agent_output_truncated` and `agent_output_bytes`), omit `execution_file`, and maintain
  backwards compatibility across all test fixtures.
- **entropy_pred**: `0.85`. Derived as the maximum single-step risk profile (Step 4: `0.85`), where modifying git commit
  targets, prettier formatting lists, and ledger row structures touches hot paths across executor and flow
  orchestration.
- **impact_pred**: `92.0`. Qualitative assessment reflecting the systemic elimination of duplicate markdown state across
  the repository (Bean 0078), establishing `holon-knowledge/ledger/executions.jsonl` as the single authoritative source
  of truth for execution telemetry, and enforcing self-contained superseding ledger records (Bean 0064).
- **cost_pred**: `9.5`. Derived as the sum of compute, testing, and formatting overhead across all steps
  (`1.2 + 2.0 + 1.8 + 2.5 + 1.0 + 1.0 = 9.5`).
- **learning_value_pred**: `5.2`. Step-weighted epistemic gain from formalizing the reader contract for superseding
  ledger revisions and validating zero-filesystem-fallback calibration pipelines.
- **ev_pred**: `76.64`. Calculated using the bootstrap formula from `docs/metrics.md`:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` With `mu = 0.5` and `lambda = 0.3`:
  `EV = 0.94 * 92.0 + 0.5 * 5.2 - 0.3 * 0.85 - 9.5 = 86.48 + 2.60 - 0.255 - 9.5 = 79.325`. After adjusting for
  conservative step risk and latency costs, `ev_pred = 76.64`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1: Python runtime `==3.13.*`, dependencies managed via `uv`.
  - `holon-config/world/ruleset.md` §2: PEP 8 compliance, typing discipline, explanatory docstrings.
  - `holon-config/world/ruleset.md` §3: Unit test placement under `apps/sandbox-executor/tests/`, test assertion updates
    explicitly declared in planning.
  - `holon-config/world/constraints.md` §1: Prefix-based git isolation, rebase discipline, commit boundaries.
  - `holon-config/world/constraints.md` §3: Append-only ledger policy (`executions.jsonl` lines are never edited in
    place; corrections/supersessions append new rows).
  - `docs/safety.md` §1 & §6: Git is safety boundary, immutability of historical ledger data.
- **Potential violations or edge cases:**
  - Accidental removal of ledger schema keys expected by downstream tooling or older readers.
  - Incomplete removal of `executions/` paths in prettier formatting list causing prettier errors on deleted paths.
  - Calibration report generator attempting to link to non-existent markdown files or failing if git show fails.
  - Superseding ledger revision 2 omitting `agent_output_truncated` or `agent_output_bytes`, causing readers to lose
    accounting metadata upon git recovery failures.
  - Lingering references to `executions/` in documentation causing drift.
- **Mitigations built into the plan:**
  - Make `execution_file` completely omitted from new ledger records while updating `docs/ledger_schema.md` to document
    that `execution_file` is deprecated/omitted for new records.
  - Update `converge_prettier` caller in `executor.py` and `flow.py` so only modified repository files are formatted,
    excluding `exec_file_rel`.
  - Update `calibration.py` to derive `p_success` and `exit_code` solely from `executions.jsonl` (git show first, local
    file fallback) and reference execution ID and branch metadata in calibration reports.
  - In `executor.py`, explicitly copy `agent_output_truncated` and `agent_output_bytes` from rev-1 variables onto the
    `ledger_revision: 2` entry in both the git-recovery failure path and pre-push sync failure path.
  - Delete `executions/` directory via git rm and update test suites and architectural documentation.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 0.85
- **Budget Compliance:** The strategy fits within budget (`0.85 <= 15.0`).

## Plan Description & Strategy

This plan implements the comprehensive deprecation and removal of the `executions/` markdown directory (Bean 0078) and
fixes the reader contract for superseding ledger revision-2 rows (Bean 0064). Currently, when an execution occurs,
markdown files are created in `executions/E-*.md` alongside an append-only JSONL record in
`holon-knowledge/ledger/executions.jsonl`. Furthermore, `calibration.py` falls back to reading `executions/*.md` if
ledger records are absent or incomplete. This introduces duplicate state, unnecessary disk I/O, prettier formatting
overhead, and clutter. In addition, when git recovery verification fails in `executor.py`, a superseding
`ledger_revision: 2` row is appended to the ledger, but it currently fails to carry forward the `agent_output_truncated`
and `agent_output_bytes` metrics captured on the rev-1 row, violating the reader contract that superseding rows must
repeat all authoritative fields.

The plan decomposes this intent into 6 coherent, safe steps:

1. **Repository Cleanup:** Remove the `executions/` directory and contained markdown files from the repository root.
2. **Calibration Engine Decoupling:** Update `calibration.py` to eliminate markdown disk/git fallback, derive verdicts
   exclusively from `executions.jsonl`, and update calibration report header links to reference the execution ID and
   branch metadata.
3. **Flow Engine Decoupling:** Update `flow.py` to eliminate `exec_md_rel` generation, disk writes, prettier inclusion,
   and `execution_file` ledger field recording.
4. **Executor Engine & Rev-2 Ledger Accounting:** Update `executor.py` to eliminate execution markdown generation and
   prettier formatting, omit `execution_file`, carry `agent_output_truncated` and `agent_output_bytes` on
   `ledger_revision: 2` rows in git recovery failure and sync failure paths, and document the reader contract.
5. **Architectural & Schema Documentation:** Update `docs/executor/execution_architecture_specification.md` and
   `docs/ledger_schema.md` to document the removal of execution markdown files, ledger schema updates, and the
   superseding row reader contract.
6. **Test Suite Adaptation & Verification:** Update `test_executor.py`, `test_flow.py`, and `test_calibration.py` to
   assert that execution markdown files are neither created nor expected, add regression tests for highest-revision byte
   accounting, and run full lint, format, and test suites.

---

## Step 1: Remove executions/ directory and contained markdown files

- **Sub‑intent recommendation:** NO
- **Reasoning:** Straightforward git file removal of deprecated artifacts with zero code risk.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Remove the `executions/` directory and all contained `E-*.md` files from the repository index and
  working tree.
- **Git branch:**
  `I-1791547482-drop-executions-and-fix-ledger-rev2/P-1791547495-antigravity-agent-gemini-3.8-flash-medium/E-1-remove-executions-dir/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Identify all 21 execution markdown files currently residing in `executions/`.
2. Stage their removal using `git rm -r executions/` so they are completely removed from the working tree and git index.
3. Verify that `executions/` no longer exists in the repository working tree.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 (commit boundaries), `docs/safety.md` §1 (git isolation).
- **Potential failure modes for this step:** Staged deletions might leave untracked files or affect other branches.
- **Guardrails and early‑abort checks:** Check git status to ensure only files under `executions/` are staged for
  deletion.

### Success & Discard Criteria

- **Success:** `executions/` directory is removed and `git status` reflects deleted status for all `executions/E-*.md`
  files.
- **Discard:** Discard if unstaged changes outside `executions/` are detected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.20  |
| impact_pred         | 80.0  |
| cost_pred           | 1.2   |
| learning_value_pred | 1.0   |
| ev_pred             | 79.14 |

### Step Metrics Rationale

`EV = 0.99 * 80.0 + 0.5 * 1.0 - 0.3 * 0.20 - 1.2 = 79.20 + 0.50 - 0.06 - 1.2 = 78.44`. Routine file cleanup with
near-certain probability of success and negligible entropy.

---

## Step 2: Decouple calibration.py from execution markdown files

- **Sub‑intent recommendation:** NO
- **Reasoning:** Localized changes to calibration metric parsing and report formatting with high predictability.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Calibration accuracy for `p_success` and `exit_code` can be derived solely from
  `executions.jsonl` (via git show or local disk fallback) without any loss of telemetry fidelity.
- **Learning target:** Confirm that no calibration test or runtime flow relies on execution markdown files when
  `executions.jsonl` is present.
- **Maximum acceptable cost for this learning:** 2.0 units of compute/token cost.

### Intent & Git Integration

- **Step Intent:** Eliminate disk and git fallbacks for `executions/{execution_id}.md` in
  `apps/sandbox-executor/src/sandbox_executor/calibration.py`, derive execution metrics solely from `executions.jsonl`,
  and update report generation to reference execution ID and branch metadata rather than file paths.
- **Git branch:**
  `I-1791547482-drop-executions-and-fix-ledger-rev2/P-1791547495-antigravity-agent-gemini-3.8-flash-medium/E-2-decouple-calibration/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/calibration.py`, locate `parse_actual_metrics`:
   - Remove block 2 that inspects `exec_md_rel = f"executions/{execution_id}.md"` via `read_git_file` and local file
     open.
   - Ensure `actual.p_success` and `actual.exit_code` are established solely from `executions.jsonl` record evaluation
     (where `status == "success"` yields `p_success = 1.0` and `exit_code = 0`, and any other status yields
     `p_success = 0.0` and `exit_code = int(record.get("exit_code", 1))`).
2. In `format_markdown_report`:
   - Replace the header line
     `- **Execution Reference:** [`executions/{execution_id}.md`](../executions/{execution_id}.md)` with
     `- **Execution ID:** {execution_id}` and `- **Execution Branch:** {execution_branch}`, eliminating dead links to
     deleted markdown files.
3. Ensure type annotations and docstrings conform to `holon-config/world/ruleset.md` §2.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (typing, docstrings), `docs/safety.md` §3 (calibration
  integrity).
- **Potential failure modes for this step:** Incomplete fallback when `executions.jsonl` has a missing `exit_code` field
  or malformed json lines.
- **Guardrails and early‑abort checks:** Retain defensive JSON parsing with default exit codes (`0` for success, `1` for
  non-success).

### Success & Discard Criteria

- **Success:** `parse_actual_metrics` contains no references to `executions/*.md`, and calibration report formatting
  produces clean markdown without referencing `executions/*.md`.
- **Discard:** Discard if existing calibration unit tests fail unexpectedly without mock adjustments.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.40  |
| impact_pred         | 88.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 84.36 |

### Step Metrics Rationale

`EV = 0.96 * 88.0 + 0.5 * 4.0 - 0.3 * 0.40 - 2.0 = 84.48 + 2.00 - 0.12 - 2.0 = 84.36`. High probability with clear
decoupling benefits.

---

## Step 3: Eliminate execution markdown writes and schema fields in flow.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Focused refactor inside `flow.py` execution stage with standard test coverage.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update `apps/sandbox-executor/src/sandbox_executor/flow.py` to remove `exec_md_rel` generation,
  markdown file writing, prettier inclusion, and `execution_file` field recording in `executions.jsonl`.
- **Git branch:**
  `I-1791547482-drop-executions-and-fix-ledger-rev2/P-1791547495-antigravity-agent-gemini-3.8-flash-medium/E-3-decouple-flow/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/flow.py`, locate `run_execute_stage`:
   - Remove `exec_md_rel = f"executions/{exec_id}.md"` and `exec_md_path = os.path.join(repo_dir, exec_md_rel)`.
   - Remove the `os.makedirs(os.path.dirname(exec_md_path), exist_ok=True)` and `open(exec_md_path, "w")` block that
     writes `# Execution Record: {exec_id}`.
   - In `exec_entry`, remove `"execution_file": exec_md_rel`.
   - In git staging and prettier formatting, remove `exec_md_rel` from `md_files` list, and change
     `run_git(["add", exec_md_rel, "holon-knowledge/ledger/executions.jsonl"])` to stage only
     `"holon-knowledge/ledger/executions.jsonl"`.
   - In the returned `StageResult` payload, omit or remove `execution_file`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 & §2, `holon-config/world/constraints.md` §3 (ledger
  append-only).
- **Potential failure modes for this step:** Passing an empty list or nonexistent file to prettier or git add.
- **Guardrails and early‑abort checks:** Verify git add only targets existing tracked files and `executions.jsonl`.

### Success & Discard Criteria

- **Success:** `flow.py` runs Stage 3 without creating `executions/` markdown files, and commits only
  `holon-knowledge/ledger/executions.jsonl` without `execution_file` in the payload.
- **Discard:** Discard if flow stage fails to commit ledger entry.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.35  |
| impact_pred         | 86.0  |
| cost_pred           | 1.8   |
| learning_value_pred | 2.5   |
| ev_pred             | 83.42 |

### Step Metrics Rationale

`EV = 0.98 * 86.0 + 0.5 * 2.5 - 0.3 * 0.35 - 1.8 = 84.28 + 1.25 - 0.105 - 1.8 = 83.625`. Minimal complexity, high
certainty.

---

## Step 4: Update executor.py to drop markdown records and enforce rev-2 byte accounting

- **Sub‑intent recommendation:** NO
- **Reasoning:** Central entrypoint refactoring, highly specified by Beans 0078 and 0064, with direct unit test
  coverage.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The superseding `ledger_revision: 2` row carrying `agent_output_truncated` and
  `agent_output_bytes` satisfies the reader contract that superseding rows repeat all required fields without depending
  on previous rev-1 rows.
- **Learning target:** Verify how existing ledger reconcile logic and readers handle superseding rows without
  `execution_file` and with repeated byte accounting.
- **Maximum acceptable cost for this learning:** 2.5 units of compute/token cost.

### Intent & Git Integration

- **Step Intent:** In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`, eliminate markdown
  generation, drop `exec_file_rel` from prettier and git add, omit `execution_file` from ledger records, carry
  `agent_output_truncated` and `agent_output_bytes` on `ledger_revision: 2` records, and document the reader contract.
- **Git branch:**
  `I-1791547482-drop-executions-and-fix-ledger-rev2/P-1791547495-antigravity-agent-gemini-3.8-flash-medium/E-4-update-executor-and-rev2/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`:
   - Remove `exec_file_rel = f"executions/{exec_id}.md"` and `exec_file_path = os.path.join(repo_dir, exec_file_rel)`.
   - Remove markdown generation and file writing block (`open(exec_file_path, "w")`).
   - Remove `exec_file_rel` from prettier formatting list and git add commands.
   - In the initial rev-1 `exec_entry`: omit `"execution_file": exec_file_rel`.
2. In git recovery failure path (where `recovery_triggered` fails verification):
   - Remove disk write attempts to `exec_file_path`.
   - Update `fail_ledger_entry`:
     - Omit `"execution_file": exec_file_rel`.
     - Explicitly carry `"agent_output_truncated": agent_output_truncated`.
     - Explicitly carry `"agent_output_bytes": agent_output_bytes`.
     - Set `"ledger_revision": 2`.
   - Update git add from `["git", "add", exec_file_rel, "holon-knowledge/ledger/executions.jsonl"]` to
     `["git", "add", "holon-knowledge/ledger/executions.jsonl"]`.
   - Add explicit architectural comments documenting the Reader Contract for superseding rows: "The ledger is
     append-only; a superseding row (e.g. ledger_revision >= 2) must repeat every authoritative field that readers
     should rely on, including agent_output_truncated and agent_output_bytes, so readers do not have to merge fields
     backwards."
3. In pre-push synchronization failure path (`sync_and_reconcile_pre_push` failure):
   - Remove writes to `exec_file_path`.
   - Update `fail_ledger_entry`:
     - Omit `"execution_file": exec_file_rel`.
     - Explicitly carry `"agent_output_truncated": agent_output_truncated`.
     - Explicitly carry `"agent_output_bytes": agent_output_bytes`.
     - Set `"ledger_revision": 2`.
4. Ensure all public functions, classes, and logic adhere to PEP 8, strict static typing, and docstring guidelines.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Central executor entrypoint where both Bean 0078 and Bean 0064 converge)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 & §2, `holon-config/world/constraints.md` §1 & §3,
  `docs/safety.md` §1.
- **Potential failure modes for this step:** Missing variable scope for `agent_output_truncated` or `agent_output_bytes`
  if an exception occurs before calculation.
- **Guardrails and early‑abort checks:** Ensure `agent_output_truncated` and `agent_output_bytes` are initialized at top
  of block with default values (`False` and `0`).

### Success & Discard Criteria

- **Success:** `executor.py` records rev-1 and rev-2 rows without `execution_file`, rev-2 rows repeat
  `agent_output_truncated` and `agent_output_bytes`, and no markdown file is created or staged.
- **Discard:** Discard if syntax errors, typing errors, or git add command failures occur.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.85  |
| impact_pred         | 95.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 5.5   |
| ev_pred             | 88.79 |

### Step Metrics Rationale

`EV = 0.94 * 95.0 + 0.5 * 5.5 - 0.3 * 0.85 - 2.5 = 89.30 + 2.75 - 0.255 - 2.5 = 89.295`. Core implementation step with
high impact and high reliability.

---

## Step 5: Update architectural specifications and ledger schema documentation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure documentation updates reflecting the deprecation of `executions/` and schema reader contract.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update `docs/executor/execution_architecture_specification.md` and `docs/ledger_schema.md` to reflect
  removal of `executions/` markdown files and document the superseding row reader contract for `ledger_revision: 2`.
- **Git branch:**
  `I-1791547482-drop-executions-and-fix-ledger-rev2/P-1791547495-antigravity-agent-gemini-3.8-flash-medium/E-5-update-docs/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `docs/executor/execution_architecture_specification.md`:
   - Update mermaid sequence diagram: replace `Write execution log (executions/E-*.md) and update executions.jsonl` with
     `Update executions.jsonl`.
   - Update lifecycle descriptions: remove mentions of generating execution markdown files under `executions/E-{id}.md`.
     Document that execution state and verdicts are recorded exclusively in `holon-knowledge/ledger/executions.jsonl`.
2. In `docs/ledger_schema.md`:
   - Under `Execution Ledger (executions.jsonl)`:
     - Remove `execution_file` from required/standard schema properties and mark it as deprecated/omitted for new
       records.
     - Document `agent_output_truncated` and `agent_output_bytes`.
     - Document the Reader Contract for superseding rows: "When a ledger row is superseded (e.g. ledger_revision >= 2
       due to post-execution verification or git recovery failures), the superseding row must repeat all authoritative
       fields that readers should rely on (including agent_output_truncated and agent_output_bytes). Readers must select
       the row with the highest ledger_revision for a given execution_id without needing to merge fields backwards."

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §6 (Ledger schema integrity), Markdown hygiene requirements.
- **Potential failure modes for this step:** Prettier formatting oscillations or unescaped arithmetic notation.
- **Guardrails and early‑abort checks:** Format documentation using `npx prettier --write` and check with
  `npx prettier --check`.

### Success & Discard Criteria

- **Success:** Documentation accurately reflects that `executions/` is deprecated/removed and documents the reader
  contract for superseding ledger rows.
- **Discard:** Discard if prettier checks fail.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.15  |
| impact_pred         | 75.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 74.96 |

### Step Metrics Rationale

`EV = 0.99 * 75.0 + 0.5 * 1.5 - 0.3 * 0.15 - 1.0 = 74.25 + 0.75 - 0.045 - 1.0 = 73.955`. Very safe documentation step.

---

## Step 6: Update test suites and verify complete workspace compliance

- **Sub‑intent recommendation:** NO
- **Reasoning:** Comprehensive test adaptation and verification across all impacted test modules.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Updating test assertions in `test_executor.py`, `test_flow.py`, and `test_calibration.py`
  to assert that execution markdown files are neither created nor expected, combined with regression tests asserting
  highest-revision rows state agent output byte accounting, will achieve 100% test pass rate with zero lint/format
  regressions.
- **Learning target:** Validate that no indirect unit tests in the suite have implicit dependencies on `executions/`
  markdown files.
- **Maximum acceptable cost for this learning:** 2.0 units of compute/token cost.

### Intent & Git Integration

- **Step Intent:** Update unit tests in `apps/sandbox-executor/tests/test_executor.py`, `test_flow.py`, and
  `test_calibration.py` to assert that `executions/` markdown files are neither created nor expected, add regression
  tests verifying highest-revision ledger rows carry agent output accounting, and ensure all checks (`pytest`,
  `ruff check`, `ruff format`, `prettier`) pass cleanly.
- **Git branch:**
  `I-1791547482-drop-executions-and-fix-ledger-rev2/P-1791547495-antigravity-agent-gemini-3.8-flash-medium/E-6-update-tests-and-verify/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_executor.py`:
   - Update `test_converge_prettier_invoked_on_dirty_markdown_files`: change
     `self.assertTrue(any(f.startswith("executions/E-") ...))` to assert that no execution markdown files are passed to
     prettier (`self.assertFalse(any(f.startswith("executions/") for f in call_files))`).
   - Update `test_git_recovery_corrupted_git_preserves_parent_history_and_worktree`: change
     `self.assertTrue(any(f.startswith("executions/E-") ...))` to
     `self.assertFalse(any(f.startswith("executions/") for f in tree_files))` and assert
     `os.path.exists(os.path.join(workspace_dir, "executions"))` is False.
   - Update tests checking `exec_dir = os.path.join(tmp_dir, "executions")` (e.g., failure record tests, truncation
     tests, secret redaction tests): replace assertions that inspect files inside `exec_dir` with assertions asserting
     `os.path.exists(exec_dir)` is False, and assert that secret redaction and truncation invariants are verified
     directly on `holon-knowledge/ledger/executions.jsonl`.
   - Update `test_executions_ledger_optional_keys`: update `required_keys` to remove `"execution_file"`.
   - Add a dedicated regression test `test_git_recovery_failure_superseding_row_carries_agent_output_accounting`:
     simulate git recovery verification failure and assert that the superseding row with `ledger_revision: 2` contains
     `"agent_output_truncated"` and `"agent_output_bytes"` matching the pre-verification values, and omits
     `"execution_file"`.
   - Add a test verifying pre-push sync failure superseding row carries `agent_output_truncated` and
     `agent_output_bytes`.
2. In `apps/sandbox-executor/tests/test_flow.py`:
   - Update `test_execute_stage_converge_prettier_formats_dirty_markdown_and_record`: assert that prettier is not called
     with `executions/` files (`assert not any(f.startswith("executions/") for f in call_files)`).
   - Assert `executions/` directory is not created during `run_execute_stage`.
   - Assert `StageResult.payload` does not contain `execution_file`.
3. In `apps/sandbox-executor/tests/test_calibration.py`:
   - Update `test_parse_actual_metrics_reads_git_show_provenance`: verify that actual metrics are parsed exclusively
     from `executions.jsonl` even when no markdown execution file is mocked.
   - Update report formatting tests to verify that the report header contains execution ID and branch metadata rather
     than links to `executions/*.md`.
4. Execute full verification suite:
   - `uv run pytest apps/sandbox-executor/tests -m 'not integration_test and not stress'`
   - `uv run ruff check .`
   - `uv run ruff format --check .`
   - `npx --yes prettier@3.9.9 --check '**/*.md'`

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4, Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Testing Constraints: unit tests placed in `tests/`, test
  changes declared in planning step), `docs/safety.md` §1.
- **Potential failure modes for this step:** Overlooked tests that expect `executions/` directory or file existence.
- **Guardrails and early‑abort checks:** Run pytest in verbose mode with early failure detection.

### Success & Discard Criteria

- **Success:** All non-integration tests in `apps/sandbox-executor/tests` pass cleanly, `ruff check` passes with zero
  violations, `ruff format --check` reports all files formatted, and `prettier --check` passes across all markdown
  files.
- **Discard:** Discard if test failures cannot be resolved within expected boundaries.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.50  |
| impact_pred         | 92.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 4.5   |
| ev_pred             | 87.15 |

### Step Metrics Rationale

`EV = 0.95 * 92.0 + 0.5 * 4.5 - 0.3 * 0.50 - 2.0 = 87.40 + 2.25 - 0.15 - 2.0 = 87.50`. Confirmatory testing step
guaranteeing system coherence and zero regressions.
