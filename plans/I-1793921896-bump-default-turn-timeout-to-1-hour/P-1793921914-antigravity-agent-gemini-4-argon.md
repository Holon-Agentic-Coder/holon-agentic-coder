# Plan for I-1793921896-bump-default-turn-timeout-to-1-hour

- **Plan ID:** P-1793921914-antigravity-agent-gemini-4-argon
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-4-argon (version: 1.5.1)
- **Created At:** 2026-11-05T23:38:34.925Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A
- **Exploration stance:** conservative with a targeted verification pass across code, tests, and documentation to ensure
  no residual 300-second turn timeout assumptions remain.
- **Safety priority level:** standard
- **Priority Justification:** Raising the default per-turn timeout constant from 300.0 s to 3600.0 s preserves bounded
  execution and existing supervision guardrails (`docs/safety.md`, `holon-config/world/constraints.md`) without altering
  sandbox isolation, network policy, or ledger append-only invariants.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The scope, constant location, unit tests, and documentation targets are deterministic and
  explicitly enumerated; purely exploitative execution is optimal.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 3.20  |
| impact_pred         | 78.00 |
| cost_pred           | 4.50  |
| learning_value_pred | 2.50  |
| ev_pred             | 70.67 |

### Strategy Rationale

The intent `I-1793921896-bump-default-turn-timeout-to-1-hour` is accepted as-is (`ACCEPT_AS_IS`). High-effort and
extended-thinking reasoning models (such as `gemini-4-argon` and Claude Opus with extended thinking) often deliberate
for several minutes during prefill before emitting their first tool invocation or artefact write. Because
`DEFAULT_TURN_SECONDS` in `apps/sandbox-executor/src/sandbox_executor/budgets.py` is the single canonical default used
by `TurnBudgets` and `load_budgets`, bumping it from `300.0` (5 minutes) to `3600.0` (1 hour) directly eliminates
premature turn-budget terminations while preserving the existing stall window (`90.0` s once output streams), per-stage
wall-clock ceilings, and `HOLON_TURN_TIMEOUT_SECONDS` environment override.

Overall plan metrics are derived from the four sequential steps as follows:

- `p_success_pred = 0.96`: Bottleneck step probability (`min(0.99, 0.97, 0.98, 0.96) = 0.96`, governed by Step 4's full
  test and lint verification gate).
- `entropy_pred = 3.20`: Additive sum of step entropies (`0.80 + 1.00 + 0.80 + 0.60 = 3.20`), well below the `15.0`
  entropy budget, so no sub-intent decomposition is required.
- `impact_pred = 78.00`: Maximum step impact (`max(78.00, 65.00, 50.00, 55.00) = 78.00` from Step 1, which unblocks
  deep-reasoning models across planning, execution, and review turns).
- `cost_pred = 4.50`: Additive sum of step costs (`1.00 + 1.40 + 1.20 + 0.90 = 4.50`).
- `learning_value_pred = 2.50`: Maximum step learning value (`max(2.00, 2.50, 2.00, 1.50) = 2.50` from Step 2,
  confirming turn-timeout surface coverage across `budgets` and `holon doctor` CLI reporting).
- `ev_pred = 70.67`: Computed from the bootstrap Expected Value formula
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` with `mu = 0.5` and `lambda = 0.3`:
  `EV = 0.96 * 78.00 + 0.5 * 2.50 - 0.3 * 3.20 - 4.50 = 74.88 + 1.25 - 0.96 - 4.50 = 70.67`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `docs/safety.md` (Invariants 1, 2, 3, 4, 6) & `docs/budgets.md`: Stage ceilings (`StageBudgets`) and process-group
    supervision (`SIGTERM` followed by `SIGKILL`) remain intact; only the default per-turn timeout
    (`DEFAULT_TURN_SECONDS`) is raised from `300.0` to `3600.0`.
  - `holon-config/world/constraints.md` (§1 Git Flow & §3 Ledger Immutability): All work stays on the assigned branch,
    human review remains the final merge gate to `main`, and the three append-only ledgers (`intents.jsonl`,
    `plans.jsonl`, `executions.jsonl`) remain strictly append-only.
  - `holon-config/world/ruleset.md` (§1–§3) & `.agents/AGENTS.md`: Python `3.13.*` conventions, Australian English
    prose, no new dependencies, test file changes explicitly declared in Step 2, and verification strictly via
    `uv run pytest`, `uv run task test`, `uv run task lint`, and `uv run task lint-docs` from the repository root.
- **Potential violations or edge cases:**
  - `holon doctor` output assertions in `apps/sandbox-executor/tests/test_cli.py` hardcoding
    `budgets turn=300s stall=90s`.
  - Documentation tables or `ACT-BUDGET` examples in `docs/budgets.md` and `docs/ledger_schema.md` retaining stale
    `300 s` default descriptions.
- **Mitigations built into the plan:**
  - Explicitly updating both `test_budgets.py` (3 assertions) and `test_cli.py` (2 `holon doctor` assertions) in Step 2.
  - Updating the default table, silence-limit table, and prose in `docs/budgets.md` plus the `ACT-BUDGET` example in
    `docs/ledger_schema.md` in Step 3.
  - Running `uv run task lint-docs` and `uv run task lint` in Step 4 to guarantee Prettier and Ruff compliance.
- **Residual risk accepted (and why):** Minimal residual risk (`0.04` failure probability) from markdown formatting or
  overlooked test assertions; mitigated by running targeted `pytest` suites and `uv run task lint-docs` before
  completion.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 3.20
- **Budget Compliance:** The strategy fits within budget (`3.20 <= 15.0`) and requires no sub-intent decomposition.

## Plan Description & Strategy

We execute this intent in four focused, sequential steps confined strictly to the five files in the intent's `scope`:

1. **Step 1 (`apps/sandbox-executor/src/sandbox_executor/budgets.py`):** Change `DEFAULT_TURN_SECONDS` from `300.0` to
   `3600.0` and update the module docstring (`default 3600 s`).
2. **Step 2 (`apps/sandbox-executor/tests/test_budgets.py`, `apps/sandbox-executor/tests/test_cli.py`):** Update the
   unit and CLI doctor test assertions that check the default turn timeout from `300` (`300s`) to `3600` (`3600s`), and
   assert `DEFAULT_TURN_SECONDS == 3600.0`.
3. **Step 3 (`docs/budgets.md`, `docs/ledger_schema.md`):** Update the turn budget default table, silence-limit table,
   explanatory prose, and `ACT-BUDGET` ledger schema example from `300 s` (`300.0`) to `3600 s` (`3600.0`).
4. **Step 4 (Verification):** Run affected tests first via `uv run pytest`, then the full unit test suite once via
   `uv run task test`, and validate code and markdown formatting via `uv run task lint` and `uv run task lint-docs`.

---

## Step 1: Bump `DEFAULT_TURN_SECONDS` to `3600.0` in `sandbox_executor.budgets`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Single constant and docstring update in one file (`budgets.py`) with minimal entropy (`0.80`) and
  near-certain success (`0.99`).
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update `DEFAULT_TURN_SECONDS` from `300.0` to `3600.0` (1 hour) and update the module-level docstring
  in `apps/sandbox-executor/src/sandbox_executor/budgets.py` so unset `HOLON_TURN_TIMEOUT_SECONDS` resolves to a
  `3600.0` s turn budget.
- **Git branch:** `I-1793921896-bump-default-turn-timeout-to-1-hour`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/budgets.py`, update the module docstring (line 6) where the turn
   budget (`HOLON_TURN_TIMEOUT_SECONDS`) default is stated as `default 300 s` to `default 3600 s`.
2. In `apps/sandbox-executor/src/sandbox_executor/budgets.py`, change `DEFAULT_TURN_SECONDS = 300.0` (line 47) to
   `DEFAULT_TURN_SECONDS = 3600.0`.
3. Confirm that `TurnBudgets.turn_seconds` and `load_budgets()` both reference `DEFAULT_TURN_SECONDS` directly so no
   other literal in `budgets.py` overrides the default.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` (Bounded execution & turn supervision), `holon-config/world/ruleset.md#2` (PEP 8,
  typing, docstrings).
- **Potential failure modes for this step:**
  - Leaving a stale `300 s` reference in the module docstring of `budgets.py`.
- **Guardrails and early‑abort checks:**
  - Verify `DEFAULT_TURN_SECONDS == 3600.0` and `load_budgets({}).turn.turn_seconds == 3600.0`.

### Success & Discard Criteria

- **Success:** `DEFAULT_TURN_SECONDS` in `apps/sandbox-executor/src/sandbox_executor/budgets.py` is `3600.0`,
  `load_budgets({})` resolves `turn.turn_seconds` to `3600.0`, and the module docstring reflects `default 3600 s`.
- **Discard:** Cost exceeds `1.5 * 1.00 = 1.50`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.80  |
| impact_pred         | 78.00 |
| cost_pred           | 1.00  |
| learning_value_pred | 2.00  |
| ev_pred             | 76.98 |

### Step Metrics Rationale

Changing the single canonical constant `DEFAULT_TURN_SECONDS` in `budgets.py` directly delivers the primary runtime
improvement (`impact_pred = 78.00`, preventing false-positive turn timeouts during deep-reasoning prefill) with
negligible risk (`p_success_pred = 0.99`, `entropy_pred = 0.80`). Using the bootstrap EV formula:
`EV = 0.99 * 78.00 + 0.5 * 2.00 - 0.3 * 0.80 - 1.00 = 77.22 + 1.00 - 0.24 - 1.00 = 76.98`.

---

## Step 2: Update default turn timeout assertions in `test_budgets.py` and `test_cli.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped unit test assertion updates across two test modules (`test_budgets.py` and `test_cli.py`)
  directly coupled to Step 1.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update unit test assertions in `apps/sandbox-executor/tests/test_budgets.py` and
  `apps/sandbox-executor/tests/test_cli.py` that assert the default `300` s (`300s`) turn timeout to `3600` s (`3600s`),
  and explicitly assert `DEFAULT_TURN_SECONDS == 3600.0`.
- **Git branch:** `I-1793921896-bump-default-turn-timeout-to-1-hour`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_budgets.py`:
   - Import `DEFAULT_TURN_SECONDS` from `sandbox_executor.budgets` and assert
     `self.assertEqual(DEFAULT_TURN_SECONDS, 3600.0)` and `self.assertEqual(budgets.turn.turn_seconds, 3600)` in
     `TestBudgets.test_defaults` (line 36).
   - In `TestBudgets.test_invalid_values_keep_defaults` (line 66), update
     `self.assertEqual(budgets.turn.turn_seconds, 300)` to `self.assertEqual(budgets.turn.turn_seconds, 3600)`.
   - In `TestBudgets.test_values_above_the_bound_keep_defaults_after_conversion` (line 85), update
     `self.assertEqual(budgets.turn.turn_seconds, 300)` to `self.assertEqual(budgets.turn.turn_seconds, 3600)`.
2. In `apps/sandbox-executor/tests/test_cli.py`:
   - In `test_doctor_prints_envelope_and_cache_state` (line 1135), update
     `self.assertIn("budgets turn=300s stall=90s", output)` to `self.assertIn("budgets turn=3600s stall=90s", output)`.
   - In `test_unified_doctor_reports_resolution_probe_and_envelope` (line 1274), update
     `self.assertIn("budgets turn=300s stall=90s", output)` to `self.assertIn("budgets turn=3600s stall=90s", output)`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3` (Testing constraints: explicit declaration of all modified test
  files in the plan; `pytest` runner).
- **Potential failure modes for this step:**
  - Missing one of the default turn timeout assertions in `test_budgets.py` or `test_cli.py`.
- **Guardrails and early‑abort checks:**
  - Grep `test_budgets.py` and `test_cli.py` for `300` before running `uv run pytest` on both files.

### Success & Discard Criteria

- **Success:** All unit tests in `apps/sandbox-executor/tests/test_budgets.py` and
  `apps/sandbox-executor/tests/test_cli.py` pass via `uv run pytest`.
- **Discard:** Cost exceeds `1.5 * 1.40 = 2.10`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 1.00  |
| impact_pred         | 65.00 |
| cost_pred           | 1.40  |
| learning_value_pred | 2.50  |
| ev_pred             | 62.60 |

### Step Metrics Rationale

Deterministic test updates in `test_budgets.py` (3 assertions) and `test_cli.py` (2 assertions) carry high confidence
(`p_success_pred = 0.97`) and low entropy (`1.00`). Using the bootstrap EV formula:
`EV = 0.97 * 65.00 + 0.5 * 2.50 - 0.3 * 1.00 - 1.40 = 63.05 + 1.25 - 0.30 - 1.40 = 62.60`.

---

## Step 3: Update turn budget default documentation in `docs/budgets.md` and `docs/ledger_schema.md`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure documentation updates across two markdown files (`docs/budgets.md` and `docs/ledger_schema.md`)
  with low entropy (`0.80`).
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update `docs/budgets.md` and `docs/ledger_schema.md` so all tables, prose explanations, and
  `ACT-BUDGET` example records reflect the `3600` s (`3600.0`) turn budget default in Australian English.
- **Git branch:** `I-1793921896-bump-default-turn-timeout-to-1-hour`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `docs/budgets.md`:
   - In the `## Budgets and defaults` table (line 20), change the `Turn` default from `300 s` to `3600 s`.
   - In `## Turn supervision` (lines 95–107), update the prose note and the silence-limit table so the three rows
     referencing the turn budget (`Silent (non-streaming runner, or prefill)`, `Silent` with `max(stall, turn)`, and
     `Streams output but never writes`) state `3600 s` instead of `300 s`, noting that the `3600 s` (1 hour) default
     accommodates extended-thinking and deep-reasoning prefill as well as full local-model responses.
2. In `docs/ledger_schema.md`:
   - In the `###### Budget Breach Actions (ACT-BUDGET)` JSON example (lines 884–888), update `"error_context"`,
     `"budget_seconds"`, and `"elapsed_seconds"` from `300 s` / `300.0` / `300.4` to `3600 s` / `3600.0` / `3600.4` (and
     `stage elapsed 3612.0 s`) so the example reflects the default turn budget.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `.agents/AGENTS.md` (Prettier markdown formatting via `uv run task lint-docs`; Australian
  English).
- **Potential failure modes for this step:**
  - Markdown table alignment or line wrapping violating `npx prettier --check`.
- **Guardrails and early‑abort checks:**
  - Format or check modified markdown files with `uv run task lint-docs`.

### Success & Discard Criteria

- **Success:** `docs/budgets.md` and `docs/ledger_schema.md` accurately describe the `3600 s` (`3600.0`) turn timeout
  default and pass `uv run task lint-docs`.
- **Discard:** Cost exceeds `1.5 * 1.20 = 1.80`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.80  |
| impact_pred         | 50.00 |
| cost_pred           | 1.20  |
| learning_value_pred | 2.00  |
| ev_pred             | 48.56 |

### Step Metrics Rationale

Straightforward documentation updates in two scoped markdown files carry very high success probability (`0.98`) and low
entropy (`0.80`). Using the bootstrap EV formula:
`EV = 0.98 * 50.00 + 0.5 * 2.00 - 0.3 * 0.80 - 1.20 = 49.00 + 1.00 - 0.24 - 1.20 = 48.56`.

---

## Step 4: Run targeted tests, full unit suite, and lint gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard verification gate executing the affected tests first, then the full suite once, and finally
  the code and documentation linters.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Verify all unit and CLI tests pass via `uv run pytest` and `uv run task test`, and verify code and
  documentation formatting via `uv run task lint` and `uv run task lint-docs`.
- **Git branch:** `I-1793921896-bump-default-turn-timeout-to-1-hour`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run the affected unit and CLI test suites first from the repository root:
   `uv run pytest apps/sandbox-executor/tests/test_budgets.py apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_turn_supervisor.py`.
2. Run the full unit test suite once from the repository root: `uv run task test`.
3. Run the code and documentation linters from the repository root: `uv run task lint` and `uv run task lint-docs`.
4. Check `git status --short` and `git diff --name-only` to verify that only the 5 files in the intent's `scope` array
   (plus `plans/`, `executions/`, and the three append-only ledgers under `holon-knowledge/ledger/`) are modified.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `.agents/AGENTS.md` (Single root virtual environment, `uv run pytest`, `uv run task test`,
  `uv run task lint`, `uv run task lint-docs`), `holon-config/world/constraints.md#3` (Keep strictly the three
  append-only ledgers).
- **Potential failure modes for this step:**
  - Unrelated test in the full test suite implicitly depending on `TurnBudgets().turn_seconds == 300.0` or markdown
    formatting drift.
- **Guardrails and early‑abort checks:**
  - Inspect `test_turn_supervisor.py` (which already passes explicit `turn=300` where testing specific 300 s breach
    arithmetic and uses `TurnBudgets()` only where responses take `<= 200 s`) and run `uv run task format` if Prettier
    reports line-length differences in `docs/budgets.md` or `docs/ledger_schema.md`.

### Success & Discard Criteria

- **Success:** `uv run pytest apps/sandbox-executor/tests/test_budgets.py apps/sandbox-executor/tests/test_cli.py`,
  `uv run task test`, `uv run task lint`, and `uv run task lint-docs` all exit `0`.
- **Discard:** Cost exceeds `1.5 * 0.90 = 1.35`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.60  |
| impact_pred         | 55.00 |
| cost_pred           | 0.90  |
| learning_value_pred | 1.50  |
| ev_pred             | 52.47 |

### Step Metrics Rationale

This is the bottleneck verification step (`p_success_pred = 0.96`, `entropy_pred = 0.60`) because it gates the entire
plan on full unit-suite and linter passage. Using the bootstrap EV formula:
`EV = 0.96 * 55.00 + 0.5 * 1.50 - 0.3 * 0.60 - 0.90 = 52.80 + 0.75 - 0.18 - 0.90 = 52.47`.
