# Plan for I-1794976491-shortref

- **Plan ID:** P-1794976507-654a2c7f-claude-agent-claude-fable-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-fable-5 (version: 2.1.302)
- **Created At:** 2026-11-18T04:35:08.310Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A
- **Exploration stance:** balanced — the intent is prescriptive (exact tickers, exact model-code results, exact
  migration semantics), so most steps are EXPLOIT; one up-front INFO_GATHERING step and the migration dry-run carry the
  exploratory weight.
- **Safety priority level:** elevated
- **Priority Justification:** The plan rewrites stored paths in `plans.jsonl` and `executions.jsonl` under a one-off
  maintainer exemption from the append-only ledger rule, and moves every file in `plans/`. Ledger integrity is a core
  world constraint, so the migration step gets elevated guardrails (byte-level diff verification, idempotence check, no
  row added/removed/reordered).

## Exploration

- **Proportion of steps that are exploratory:** 0.15
- **Justification:** The goal enumerates required behaviour precisely. Exploration is confined to Step 1 (mapping every
  flat-key usage of plan/execution IDs, which the intent itself demands before hex removal) and to the migration dry-run
  report, where unknown legacy files may surface.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.70  |
| entropy_pred        | 13.6  |
| impact_pred         | 88.0  |
| cost_pred           | 30.0  |
| learning_value_pred | 6.5   |
| ev_pred             | 30.7  |

### Strategy Rationale

The intent is accepted as-is: it is precise, internally consistent, and its own rationale (remove the hex by keying
plans under their intent) is the highest-EV path — no reframe found a cheaper route that still meets the acceptance
criteria. The work is executed as **one plan with no sub-intent list** because the predicted entropy (13.6) fits the
15.0 budget and the ten goals are tightly coupled: the new IDs (goal 3) need the ticker (1) and model code (2); the
nested plan paths (4) need the new IDs; the keyed lookups (5) and backward compatibility (6) must land atomically with
the path change or intermediate states break calibration; the migration (7) needs the final path scheme. Splitting into
children would force each child to carry a half-migrated naming scheme across a merge boundary, raising conflict
likelihood rather than lowering risk.

Aggregation of step metrics: `entropy_pred` is the **sum** of step entropies
(`0.5 + 1.0 + 0.8 + 1.6 + 1.5 + 2.5 + 1.0 + 1.0 + 2.5 + 0.7 + 0.5 = 13.6`). `cost_pred` is the sum of step costs
(`30.0`). `p_success_pred = 0.70` is **not** the raw product of step probabilities (steps are retryable inside the
execution loop); it is dominated by the two bottleneck steps, Step 6 (keyed lookups, `p = 0.75`) and Step 9 (ledger
migration, `p = 0.75`), whose failures are the only ones not cheaply recoverable. `impact_pred = 88` reflects that this
unblocks nested child intents repo-wide and removes a real collision bug. `learning_value_pred = 6.5` is close to the
max over steps (the flat-key inventory and the migration report teach the system where its IDs leak). Overall
`EV = 0.70 * 88.0 + 0.5 * 6.5 - 0.3 * 13.6 - 30.0 = 61.6 + 3.25 - 4.08 - 30.0 = 30.77 ≈ 30.7`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §3 Ledger Immutability (append-only `plans.jsonl` / `executions.jsonl`).
  - `holon-config/world/constraints.md` §1 Git Flow (branch formats; old-generation names must stay parseable — the
    intent forbids renaming existing IDs for exactly this reason).
  - `holon-config/world/ruleset.md` §3 Testing (test changes must be declared in the plan: this plan declares edits and
    additions across `apps/sandbox-executor/tests/`, including `test_ref_names.py`, `test_agent_defaults.py`,
    `test_ledger_query.py`, `test_calibration.py`, `test_ledger_append_only.py` and new migration tests).
  - `docs/safety.md` Invariant 6 (immutability) and the entropy-budget enforcement (budget 15.0).
- **Potential violations or edge cases:**
  - Step 9 rewrites `plan_file` / `calibration_report_path` in ledger rows — normally prohibited; covered by the
    maintainer's explicit one-off exemption stated in the intent.
  - The append-only CI/test guard (`test_ledger_append_only.py`, `scripts/pre_commit_check.py`) may reject the migration
    commit; it must be satisfied via its sanctioned mechanism, not weakened.
  - Plan files with no ledger row and no matching PR head branch (data loss risk if moved wrongly).
  - Same-second ID collisions under the new hex-free scheme.
- **Mitigations built into the plan:**
  - Migration is a dry-run-first, repeatable, idempotent script that only moves files and rewrites the two named fields;
    a byte-level diff assertion proves no other field, row order, or row count changed.
  - Unmatched files go to `plans/legacy/` instead of being guessed into an intent folder; every decision is printed in
    the report.
  - Collision rule: a second same-name plan/execution under the same parent gets a distinct name or fails clearly (Step
    4), with a regression test.
  - Old-generation parsers are kept and tested against real ledger rows (Step 4, Step 11).
- **Residual risk accepted (and why):** the migration commit itself is irreversible once merged to `main` in the sense
  that branch names of historical PRs cannot be regenerated from moved files alone — accepted because the ledger rows
  (unchanged except two path fields) remain the source of truth and the script's report is committed as evidence. The
  root-intent PR gets a human review, which is the final gate.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 13.6
- **Budget Compliance:** The strategy fits within budget (13.6 ≤ 15.0), so no `## Sub-intent list` section is emitted.

## Plan Description & Strategy

Replace the long, collision-prone plan/execution ID scheme with short ticker + model-code names, key every plan file and
lookup by its intent path, keep both old generations parseable, and migrate the existing tree under a one-off ledger
exemption.

Order of work: (1) inventory every flat-key use of plan/execution IDs; (2–3) introduce the ticker registry and the pure
model-code function; (4) new-generation ID builders and a three-generation parser with same-second collision handling;
(5) nested plan-file paths for new writes; (6) re-key every lookup on (intent, plan ID) / full path; (7) remove
now-unneeded agent/model shortening; (8) enforce the 32-character slug limit and prove the ref-limit fit; (9) one-off
migration script, run against the repo; (10) documentation; (11) full test suite and lint gates.

New focused modules are preferred over growing existing files: the model-code function and new-generation ID logic go
beside the existing naming code in `ref_names.py` only if it stays under ~400 lines, otherwise in a new
`sandbox_executor/plan_paths.py`; the migration lives in a new `sandbox_executor/plan_migration.py` with a thin CLI
entry. This extends the intent's scope list by those two new module paths, justified by the world ruleset's
module-per-concern sizing rule.

## Touch Set

- `apps/sandbox-executor/src/sandbox_executor/agent_defaults.py`
- `apps/sandbox-executor/src/sandbox_executor/ref_names.py`
- `apps/sandbox-executor/src/sandbox_executor/plan_paths.py`
- `apps/sandbox-executor/src/sandbox_executor/plan_migration.py`
- `apps/sandbox-executor/src/sandbox_executor/ledger.py`
- `apps/sandbox-executor/src/sandbox_executor/calibration.py`
- `apps/sandbox-executor/src/sandbox_executor/entrypoint/planner.py`
- `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`
- `apps/sandbox-executor/src/sandbox_executor/entrypoint/intent_creator.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/`
- `apps/sandbox-executor/src/sandbox_executor/cli/`
- `apps/sandbox-executor/tests/`
- `README.md`
- `docs/*.md`
- `docs/**/*.md`

---

## Step 1: Inventory every flat-key use of plan and execution IDs

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only reconnaissance, cheap and low risk, but it de-risks the two bottleneck steps and is
  explicitly demanded by goal 5 ("find every place an execution ID is used without its plan and intent").
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** All flat-key uses of plan/execution IDs are confined to `ref_names.py`, `ledger.py`,
  `calibration.py`, `flow/checkpoint.py`, `flow/calibrate_*.py`, `flow/child_progress.py`, the entrypoints and the CLI
  query paths.
- **Learning target:** A complete written inventory (file, symbol, read-or-write) of every place a plan ID or execution
  ID is used alone as a key: file paths under `plans/` and `executions/`, `plans.jsonl` lookups, duplicate checks,
  checkpoint fields, CLI arguments.
- **Maximum acceptable cost for this learning:** Half of Step 6's cost; abort broadening the search once two consecutive
  sweeps add no new sites.

### Intent & Git Integration

- **Step Intent:** Enumerate all code paths that must change before the hex can be removed, so Steps 4–7 and 9 edit a
  known closed set of sites.
- **Git branch:** I-1794976491-shortref/P-1794976507-654a2c7f-claude-agent-claude-fable-5/E-{epoch}-{id}/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Grep for ID construction (`uuid`, `hex`, `P-`, `E-`, epoch formatting) in `ref_names.py` and callers.
- Trace every reader of `plan_file`, `calibration_report_path`, and every `plans/{...}.md` / `_calibration.md` path join
  in `ledger.py`, `calibration.py`, `calibration_diff.py`, `calibration_ev.py`, `flow/checkpoint.py`,
  `flow/calibrate_rebuild.py`, `flow/calibrate_rerun.py`, `flow/calibrate_sync.py`, `flow/child_progress.py`,
  `entrypoint/planner.py`, `entrypoint/executor.py`, `cli/`.
- Record the duplicate-check location (same-ID plan rejection) and the ledger append/lock paths
  (`test_ledger_append_only.py`, `scripts/pre_commit_check.py`) to understand how the migration commit will be gated.
- Write the inventory into the execution notes; it becomes the checklist for Steps 4–7 and the migration's path map.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** read-only; `docs/safety.md` sandboxing (whitelisted tools only).
- **Potential failure modes for this step:** missed call sites (silent flat-key survivors found later).
- **Guardrails and early‑abort checks:** cross-check the inventory against the test files that exercise lookups
  (`test_ledger_query.py`, `test_calibration.py`, `test_calibrate_rerun.py`, `test_executor_plan_decision.py`).

### Success & Discard Criteria

- **Success:** A written inventory whose sites, when stubbed with a grep for the hex pattern, account for every match of
  the old ID regex in `src/`.
- **Discard:** Two full sweeps disagree on the site list — stop and re-plan with a larger Step 6 budget.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.5   |
| impact_pred         | 6.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 4.55  |

### Step Metrics Rationale

Read-only, so `SSA = IRR = SER = 0`; entropy 0.5 is almost all NOV (unknown corners of the flow package).
`EV = 0.95 * 6.0 + 0.5 * 2.0 - 0.3 * 0.5 - 2.0 = 5.7 + 1.0 - 0.15 - 2.0 = 4.55`.

---

## Step 2: Agent ticker registry with doctor and intent-time enforcement

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, self-contained, fully specified by the intent (three tickers, no fallback); no reuse value as a
  separate branch.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Each runner declares a three-uppercase-letter ticker beside its definition in `agent_defaults.py`;
  missing or duplicate tickers are hard failures.
- **Git branch:** I-1794976491-shortref/P-1794976507-654a2c7f-claude-agent-claude-fable-5/E-{epoch}-{id}/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add a `ticker` attribute beside each runner definition in `agent_defaults.py`: `claude-agent = CLA`,
  `antigravity-agent = ANA`, `pi-agent = PIA`. Other runners (`codex-agent`, `gemini-agent`, `opencode-agent`) get no
  ticker, deliberately.
- Add an accessor that returns the ticker for a runner or raises a typed error naming the runner; no fallback and no
  derivation.
- Wire the rejection into `holon doctor` (report path) and into intent opening (`entrypoint/intent_creator.py` / `flow`
  admission) so a tickerless runner is refused before any branch is created, with a message naming the runner.
- Tests in `test_agent_defaults.py`: the three fixed tickers; a uniqueness test that fails when two runners share a
  ticker; format test (exactly three uppercase letters); doctor and intent-time rejection tests naming the runner.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO (later steps depend on it, but its failure mode is loud and cheap to fix)

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md §2 typing/docstrings; ruleset.md §3 declared test changes.
- **Potential failure modes for this step:** intent-time check placed after branch creation (violates "before any
  branch"); doctor JSON payload shape drift.
- **Guardrails and early‑abort checks:** the rejection test asserts no branch exists after refusal; run
  `uv run pytest apps/sandbox-executor/tests/test_agent_defaults.py` before moving on.

### Success & Discard Criteria

- **Success:** Acceptance criterion 2 satisfied end-to-end in tests (tickers, duplicate-ticker failure, doctor and
  intent-time rejection naming the runner, no branch created).
- **Discard:** Cost exceeds `1.5x cost_pred` — re-examine where intent opening actually creates its first ref.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.0   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 7.40  |

### Step Metrics Rationale

Small SSA (one module plus two enforcement sites plus tests), zero IRR, low CL.
`EV = 0.92 * 10.0 + 0.5 * 1.0 - 0.3 * 1.0 - 2.0 = 9.2 + 0.5 - 0.3 - 2.0 = 7.40`.

---

## Step 3: Pure model-code derivation function

- **Sub‑intent recommendation:** NO
- **Reasoning:** A single pure function with a fixed required input/output table in the intent; trivially testable.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Derive a short model code from any model name with no lookup table, satisfying the seven required
  mappings and producing a valid git-ref component for arbitrary local model names.
- **Git branch:** I-1794976491-shortref/P-1794976507-654a2c7f-claude-agent-claude-fable-5/E-{epoch}-{id}/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New pure function (in `ref_names.py` or `plan_paths.py` per the sizing rule): drop everything before a `/`; split the
  rest into words; take the first letter of each alphabetic word upper-cased; keep version-number words whole and join
  consecutive numeric parts with a dot; strip every character invalid in a git ref component.
- Word-split on `-`; treat a word that begins with a digit as a version part; a word like `3.8` stays `3.8`; a trailing
  `:32b`-style suffix must come out as valid ref characters (`:` removed or mapped), never an empty string.
- Required results as a parametrised test: `claude-sonnet-5-5 -> CS5.5`, `claude-fable-5 -> CF5`,
  `claude-opus-5-5 -> CO5.5`, `gemini-3.8-flash-medium -> G3.8FM`, `gemini-4-argon -> G4A`, `gpt-5-codex -> G5C`,
  `anthropic/claude-opus-5-5 -> CO5.5`; plus a property test that `qwen2.5-coder:32b` and other fuzzed names yield
  non-empty strings passing `git check-ref-format --allow-onelevel` semantics (validated by the existing ref-validity
  helper in `ref_names.py`).
- Keep the function under the C901 limit by separating tokenising from joining.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md §2 (typing, docstrings, complexity limits).
- **Potential failure modes for this step:** edge grammar (consecutive numeric words, `provider/model`, `:quant`
  suffixes) producing invalid or colliding codes.
- **Guardrails and early‑abort checks:** the seven required mappings are hard asserts; fuzz across the ledger's actual
  historical model names to confirm none maps to an empty or invalid component.

### Success & Discard Criteria

- **Success:** All required mappings pass; fuzzed names give valid ref components; no table exists.
- **Discard:** If the no-table constraint cannot meet all seven mappings, stop — that would falsify the intent's premise
  and requires maintainer input, not a workaround table.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.8   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 8.01  |

### Step Metrics Rationale

Pure function, zero IRR/SER; entropy is NOV of the naming grammar.
`EV = 0.90 * 10.0 + 0.5 * 1.5 - 0.3 * 0.8 - 1.5 = 9.0 + 0.75 - 0.24 - 1.5 = 8.01`.

---

## Step 4: New-generation ID builders, three-generation parser, same-second collision rule

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core of the change but mechanically bounded; splitting it from Step 5/6 across branches would ship a
  half-renamed scheme.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** New plan/execution IDs are `P-{epoch}-{ticker}-{model code}` and `E-{epoch}-{ticker}-{model code}`
  with no hex; both old generations still parse; same-second same-parent duplicates get a distinct name or fail clearly.
- **Git branch:** I-1794976491-shortref/P-1794976507-654a2c7f-claude-agent-claude-fable-5/E-{epoch}-{id}/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Replace the hex-bearing ID constructors in `ref_names.py` with ticker + model-code builders (using Steps 2–3); ledger
  rows keep full `agent` and `model` fields unchanged — the short forms appear only in names.
- Parser accepts three generations: `X-{epoch}-{agent}-{model}` (gen 1), `X-{epoch}-{hex}-{agent}-{model}` (gen 2),
  `X-{epoch}-{ticker}-{model code}` (gen 3), for both `P-` and `E-`; disambiguation: an 8-char lowercase-hex second
  field marks gen 2, a 3-uppercase-letter second field marks gen 3, otherwise gen 1. Expose the generation on the parse
  result so callers never re-guess.
- Collision rule at creation time, scoped to the parent (intent path for plans, plan path for executions): if the target
  name already exists (file, ledger row for that parent, or ref), bump the epoch by one second deterministically or fail
  with a clear duplicate error — never overwrite. Regression test freezes the clock and creates two in the same second
  under one parent.
- Tests in `test_ref_names.py`: round-trips for all three generations against real IDs sampled from `plans.jsonl`;
  new-ID shape test (no hex); collision test.

### Dependencies & Criticality

- **Depends on:** Steps 1, 2, 3
- **Is Bottleneck:** YES — every later step consumes these builders/parsers. `p_success_pred = 0.85 ≥ 0.6`, so no forced
  sub-intent.

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md §1 (existing branch names must keep matching their IDs — never rename old IDs).
- **Potential failure modes for this step:** gen-1 vs gen-3 ambiguity (an agent segment could in theory be three
  uppercase letters); silent overwrite on collision.
- **Guardrails and early‑abort checks:** parser fuzz over every ID in both ledgers must classify 100% of rows into gen
  1/2 without error before the new builder is switched on.

### Success & Discard Criteria

- **Success:** Acceptance criterion 1 (new shapes, required model codes) and the collision clause of criterion 4 pass;
  all historical ledger IDs still parse.
- **Discard:** If any historical ID fails to parse under the three-generation grammar, stop and re-derive the
  disambiguation rule before touching callers.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.6   |
| impact_pred         | 14.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 9.42  |

### Step Metrics Rationale

Moderate SSA (naming module plus tests), low IRR (pure code, no data moves yet), NOV in the grammar disambiguation.
`EV = 0.85 * 14.0 + 0.5 * 2.0 - 0.3 * 1.6 - 3.0 = 11.9 + 1.0 - 0.48 - 3.0 = 9.42`.

---

## Step 5: Nested plan-file paths for new writes

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical once Step 4 exists; the path builder is one function used by planner and calibration
  writers.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** A new plan lives at `plans/{intent}/{plan id}.md` with its calibration report beside it, mirroring
  the full branch nesting including child intents (`{parent}--{index}-{slug}`) and plans nested under plans
  (`I-.../P-.../P-.../E-...`).
- **Git branch:** I-1794976491-shortref/P-1794976507-654a2c7f-claude-agent-claude-fable-5/E-{epoch}-{id}/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- One path-builder in `plan_paths.py`: given the branch segments (intent, optional ancestor plan segments, plan ID),
  return the plan file path and the sibling calibration report path (`{plan id}_calibration.md` beside it), mirroring
  the branch nesting as directories.
- Writers switch to it: `entrypoint/planner.py` (plan file), `calibration.py` and the `flow/calibrate_*` writers
  (calibration report). Old-generation paths remain readable (Step 6 handles lookups).
- Tests: a root-intent plan, a child-intent (`{parent}--{index}-{slug}`) plan, and a plan nested under another plan each
  produce paths that mirror their branch names exactly (acceptance criterion 3).

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md §2 module sizing; no entrypoint-to-flow-engine imports added.
- **Potential failure modes for this step:** path built from the ID instead of the branch (diverges for nested plans);
  directory-creation races.
- **Guardrails and early‑abort checks:** property test asserting path == branch name with `/calibrated` and
  `refs/heads/` stripped, for all three nesting shapes.

### Success & Discard Criteria

- **Success:** Acceptance criterion 3: new plan + calibration report written under `plans/{intent}/` mirroring branch
  nesting for all three shapes.
- **Discard:** Cost exceeds `1.5x cost_pred` — likely means writers are more scattered than Step 1 found; refresh the
  inventory first.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.5   |
| impact_pred         | 12.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 7.50  |

### Step Metrics Rationale

SSA across three writer modules; IRR low (new writes only, no moves).
`EV = 0.85 * 12.0 + 0.5 * 1.5 - 0.3 * 1.5 - 3.0 = 10.2 + 0.75 - 0.45 - 3.0 = 7.50`.

---

## Step 6: Key every plan and execution lookup on (intent, plan ID) / full path

- **Sub‑intent recommendation:** YES
- **Reasoning:** Widest blast radius in the plan (calibration, ledger queries, executor lookup, flow checkpoints, CLI)
  and the first bottleneck; it stays in-plan because the entropy budget holds and splitting would strand Steps 4–5
  unreferenced, but if re-planning is ever forced, this is the natural seam.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** No lookup anywhere resolves a plan or execution by bare ID: calibration, ledger queries, the
  executor's plan lookup, flow checkpoints and every CLI query take intent + plan ID (and plan + intent for executions);
  the duplicate check fails on a repeated intent-and-plan pair.
- **Git branch:** I-1794976491-shortref/P-1794976507-654a2c7f-claude-agent-claude-fable-5/E-{epoch}-{id}/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Work through Step 1's inventory site by site: `ledger.py` query helpers gain intent-scoped signatures; the executor's
  plan lookup (`entrypoint/executor.py` / `flow/pipeline.py`) passes the intent through; checkpoints
  (`flow/checkpoint.py`, `flow/child_progress.py`) store and resolve full paths; `flow/calibrate_rebuild.py`,
  `calibrate_rerun.py`, `calibrate_sync.py` resolve reports via the Step 5 path-builder with an old-generation fallback
  (flat `plans/{plan_id}.md` and `plans/{plan_id}_calibration.md`) so both generations calibrate.
- Duplicate check becomes (intent, plan ID) pair equality; a repeated pair fails, same ID under different intents is
  legal (two intents planned in the same second by the same runner and model produce two files and two rows that
  calibration resolves separately).
- CLI query surfaces (`cli/`, ledger query commands) take or infer the intent and pass it down; bare-ID invocations on
  old-generation IDs keep working via the fallback.
- Execution IDs: apply the same keying for every site Step 1 found where an execution ID is used without its plan and
  intent — this is the precondition the intent sets for removing their hex.
- Tests: `test_ledger_query.py`, `test_calibration.py`, `test_calibrate_rerun.py`, `test_executor_plan_decision.py`,
  plus a new same-second two-intents test (acceptance criterion 4) and both-generations lookup tests (criterion 5).

### Dependencies & Criticality

- **Depends on:** Steps 1, 4, 5
- **Is Bottleneck:** YES. `p_success_pred = 0.75 ≥ 0.6`, so STRONGLY_YES is not mandated.

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md §3 (declared test edits); constraints.md §1 (old branches stay resolvable).
- **Potential failure modes for this step:** a missed site keeps a flat key and silently resolves the wrong plan; a
  signature ripple breaks entrypoint/flow layering.
- **Guardrails and early‑abort checks:** after the refactor, grep for the old bare-ID lookup signatures must return only
  the explicitly kept old-generation fallbacks; run the affected test files before proceeding.

### Success & Discard Criteria

- **Success:** Acceptance criteria 4 and 5 pass; no call site resolves a plan or execution without its intent except the
  labelled old-generation fallbacks.
- **Discard:** If signature ripples exceed the inventory by more than ~50% of sites, pause and re-plan (this is the step
  a forced decomposition would split out).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 2.5   |
| impact_pred         | 16.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 2.5   |
| ev_pred             | 7.50  |

### Step Metrics Rationale

Highest SSA (many modules) and CL (hot files shared with other steps); IRR still low (code-only).
`EV = 0.75 * 16.0 + 0.5 * 2.5 - 0.3 * 2.5 - 5.0 = 12.0 + 1.25 - 0.75 - 5.0 = 7.50`.

---

## Step 7: Remove now-unneeded agent/model shortening from ref_names.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure deletion gated by the new names already working; cheap, and reverting it is trivial.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Drop the agent/model label digests and stop writing `original_agent` / `original_model` ledger fields
  for new rows; keep slug shortening and the length check.
- **Git branch:** I-1794976491-shortref/P-1794976507-654a2c7f-claude-agent-claude-fable-5/E-{epoch}-{id}/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `ref_names.py`, remove the agent/model shortening (label digests) made redundant by ticker + model code; slug
  shortening and the overall ref-length check stay, because deeper-than-two nesting still relies on them (goal 10).
- New ledger rows stop carrying `original_agent` / `original_model`; readers keep accepting rows that have them
  (historical rows are never rewritten beyond Step 9's two path fields).
- Update `test_ref_names.py` and any ledger-schema tests accordingly; confirm `docs/ledger_schema.md` wording in
  Step 10.

### Dependencies & Criticality

- **Depends on:** Steps 4, 6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md §3 (readers must keep accepting old rows — removal applies to new rows only).
- **Potential failure modes for this step:** a reader that required `original_agent` on old-generation rows breaks.
- **Guardrails and early‑abort checks:** run ledger-reading tests against the real `plans.jsonl` fixture rows after the
  removal.

### Success & Discard Criteria

- **Success:** New rows lack the two fields; old rows still parse; label-digest code is gone; slug shortening and the
  length check remain covered by tests.
- **Discard:** Any old-row parse failure — restore the reader tolerance before continuing.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.0   |
| impact_pred         | 8.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 5.24  |

### Step Metrics Rationale

Deletion-dominated diff, small SSA, near-zero NOV.
`EV = 0.88 * 8.0 + 0.5 * 1.0 - 0.3 * 1.0 - 2.0 = 7.04 + 0.5 - 0.3 - 2.0 = 5.24`.

---

## Step 8: 32-character slug limit and ref-limit fit proof

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, arithmetic-verifiable feature with a fixed acceptance test.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Root slugs over 32 characters are rejected at intent open with limit and actual length;
  engine-derived slugs are cut to 32 at a word boundary; a root plus two nested child levels with 32-character slugs
  fits 255 with `refs/heads/` and `/calibrated`, unshortened.
- **Git branch:** I-1794976491-shortref/P-1794976507-654a2c7f-claude-agent-claude-fable-5/E-{epoch}-{id}/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Intent-open validation (same site as Step 2's ticker check, before any branch): a supplied root slug longer than 32 is
  rejected with a message giving the limit (32) and the offending length.
- Engine-derived slugs (from a description or sub-intent title) are truncated to at most 32 at a word boundary (cut at
  the last `-` at or before 32; if no boundary, hard-cut at 32), applied for root and each child intent.
- Fit test (acceptance criterion 7): build root + two levels of nested child intents, all with 32-character slugs,
  compose the calibrated branch with gen-3 plan and execution segments, assert `len('refs/heads/' + branch) <= 255` with
  no shortening invoked. Sanity arithmetic: intent chain `45 + 36 + 36 = 117` chars, plus `/P-...` and `/E-...` segments
  (about 23 each for the longest current model code) plus `/calibrated` (11) and `refs/heads/` (11) lands near 185–190,
  comfortably under 255.
- Deeper nesting falls back to existing slug shortening + length check; regression test that the fallback still triggers
  there.

### Dependencies & Criticality

- **Depends on:** Steps 4, 7
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md §1 branch formats; safety.md Invariant 1 (sibling slug uniqueness — truncation must
  not collide siblings: if truncation makes two siblings equal, the engine's existing uniqueness handling must still
  fire; add a test).
- **Potential failure modes for this step:** word-boundary cut yields an empty or duplicate sibling slug.
- **Guardrails and early‑abort checks:** truncation never returns an empty slug; sibling-collision test.

### Success & Discard Criteria

- **Success:** Acceptance criterion 7 passes in full.
- **Discard:** Fit test fails arithmetically — re-check the model-code length bound before touching shortening logic.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.0   |
| impact_pred         | 8.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 5.40  |

### Step Metrics Rationale

Bounded SSA; the fit is provable by arithmetic before coding.
`EV = 0.90 * 8.0 + 0.5 * 1.0 - 0.3 * 1.0 - 2.0 = 7.2 + 0.5 - 0.3 - 2.0 = 5.40`.

---

## Step 9: One-off migration of plans/ and the two ledger path fields

- **Sub‑intent recommendation:** YES
- **Reasoning:** The irreversible step (moves ~250 files, rewrites ledger rows under a one-off exemption) and the second
  bottleneck; kept in-plan because it depends on every prior step's final path scheme and the budget holds, but it is
  the other natural seam under forced re-planning.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Every plan file in `plans/` either has a `plans.jsonl` row (giving `intent_branch`) or
  matches a supplied PR head branch by plan ID; the remainder is small enough for `plans/legacy/`.
- **Learning target:** The dry-run report quantifies exactly how many files are row-matched, PR-matched, and legacy —
  the ground truth of ledger/file drift.
- **Maximum acceptable cost for this learning:** The dry run itself (no writes); if the legacy bucket exceeds ~20% of
  files, stop and consult the maintainer before moving anything.

### Intent & Git Integration

- **Step Intent:** A repeatable script moves every existing plan file to the path its branch had, rewrites only
  `plan_file` in `plans.jsonl` and `calibration_report_path` in `executions.jsonl`, is a no-op on a migrated tree, and
  prints a full report; unmatched files go to `plans/legacy/`.
- **Git branch:** I-1794976491-shortref/P-1794976507-654a2c7f-claude-agent-claude-fable-5/E-{epoch}-{id}/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New module `plan_migration.py` with a thin CLI entry (under `cli/`), taking the repo root and a file listing PR head
  branch names.
- Resolution order per plan file: (a) `plans.jsonl` row → target path from `intent_branch` + plan ID via the Step 5
  path-builder; calibration reports follow their plan; (b) no row → match a supplied PR head branch by plan ID; (c)
  otherwise → `plans/legacy/`. `git mv` for moves so history survives.
- Ledger rewrite: stream both files row by row; change only the two named fields on rows whose files moved; assert
  afterwards that a field-masked byte comparison of old vs new ledgers is identical (no row added, removed, reordered,
  or otherwise changed) — abort and restore on any difference.
- Idempotence: a second run finds every file already at its target and both ledgers already pointing at existing files,
  reports zero actions, exits 0. This is a test, not a hope: run the script twice in the test.
- Report: every file moved (from → to), every path rewritten (row locator, old → new), every file whose new path does
  not match a supplied PR head branch name.
- Append-only guard: the migration commit must pass `test_ledger_append_only.py` and `scripts/pre_commit_check.py` via
  their sanctioned mechanism for maintainer-exempted changes (identified in Step 1); the guard itself is not weakened,
  and a post-migration test proves it still rejects a non-exempt historical-row edit.
- Run the script on the real tree in this execution branch; commit moves, rewritten ledgers, and the report.

### Dependencies & Criticality

- **Depends on:** Steps 4, 5, 6
- **Is Bottleneck:** YES. `p_success_pred = 0.75 ≥ 0.6`, so STRONGLY_YES is not mandated.

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md §3 Ledger Immutability (one-off maintainer exemption, this migration only);
  safety.md Invariant 6; ruleset.md §3 (new migration tests declared).
- **Potential failure modes for this step:** ledger rewrite touches a third field or row order; a plan file moved to a
  wrong intent folder; append-only guard blocks the commit; non-idempotent second run.
- **Guardrails and early‑abort checks:** dry run first with full report; field-masked byte-identity assertion; hard
  abort + restore on any ledger diff outside the two fields; idempotence executed twice in tests; legacy bucket
  threshold (~20%) halts for maintainer input.

### Success & Discard Criteria

- **Success:** Acceptance criterion 6 in full: nothing left directly in `plans/` outside intent folders or
  `plans/legacy/`; every `plan_file` and `calibration_report_path` points at an existing file; ledgers differ only in
  those two fields; second run changes nothing.
- **Discard:** Any ledger byte-diff outside the two fields, or legacy bucket over threshold — restore from git and stop.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 2.5   |
| impact_pred         | 14.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 6.25  |

### Step Metrics Rationale

IRR dominates (data moves + ledger rewrite) with high SSA (~250 file moves); mitigated by dry-run, byte-identity
assertion and git-level reversibility pre-merge. The dry-run report is the plan's second-largest learning source.
`EV = 0.75 * 14.0 + 0.5 * 3.0 - 0.3 * 2.5 - 5.0 = 10.5 + 1.5 - 0.75 - 5.0 = 6.25`.

---

## Step 10: Documentation and examples

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical doc sweep; zero code risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Every documented branch name, plan ID and plan file path shows the new generation, with old
  generations described as still-valid history.
- **Git branch:** I-1794976491-shortref/P-1794976507-654a2c7f-claude-agent-claude-fable-5/E-{epoch}-{id}/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Sweep `README.md` and `docs/` (notably `git_flow.md`, `directory_structure.md`, `ledger_schema.md`,
  `core_concepts.md`, `examples.md`, `docs/sandbox/*.md`, `docs/agents.md` runner table gains the ticker column) for
  branch-name, plan-ID and plan-path examples; update to gen-3 forms and `plans/{intent}/` paths; note both old
  generations remain valid and unrenamed.
- `holon-config/world/constraints.md` §1 documents the old P-segment format; update it in the same sweep but flag it
  explicitly in the PR description as a world-file edit for the human reviewer (root-intent merge is the human gate).
- `uv run task lint-docs` after the sweep.

### Dependencies & Criticality

- **Depends on:** Steps 4, 5, 8, 9
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** safety.md §Human review (governance/world files) — the constraints.md edit is descriptive, not a
  rule change, but is surfaced to the reviewer regardless.
- **Potential failure modes for this step:** missed example paths; prettier/markdown-lint oscillation on formulas.
- **Guardrails and early‑abort checks:** grep docs for the old hex ID pattern after the sweep; keep arithmetic in code
  spans.

### Success & Discard Criteria

- **Success:** Acceptance criterion 8's `lint-docs` leg passes; no doc shows a gen-2 ID as the current format.
- **Discard:** N/A (cheap, reversible).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.7   |
| impact_pred         | 5.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 3.29  |

### Step Metrics Rationale

Docs-only; entropy is residual SSA breadth.
`EV = 0.95 * 5.0 + 0.5 * 0.5 - 0.3 * 0.7 - 1.5 = 4.75 + 0.25 - 0.21 - 1.5 = 3.29`.

---

## Step 11: Full test suite and lint gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** Verification gate for the acceptance criteria; no new behaviour.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** All tests and both lint tasks pass on the final tree.
- **Git branch:** I-1794976491-shortref/P-1794976507-654a2c7f-claude-agent-claude-fable-5/E-{epoch}-{id}/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Affected tests first (per test-run discipline):
  `uv run pytest apps/sandbox-executor/tests/test_ref_names.py apps/sandbox-executor/tests/test_agent_defaults.py apps/sandbox-executor/tests/test_ledger_query.py apps/sandbox-executor/tests/test_calibration.py apps/sandbox-executor/tests/test_calibrate_rerun.py apps/sandbox-executor/tests/test_ledger_append_only.py`
  plus the new migration and slug tests.
- Then the full suite once with `uv run task test` (which runs with `-n auto --dist worksteal`); no repeated runs — the
  intent measures no performance or timing behaviour. Report pass/fail counts and pytest-reported time for each run.
- `uv run task lint` and `uv run task lint-docs`.
- Fix fallout within the plan's touch set only; anything outside it triggers re-examination, not drive-by edits.

### Dependencies & Criticality

- **Depends on:** Steps 2–10
- **Is Bottleneck:** YES (the acceptance gate) — but `p_success_pred = 0.85 ≥ 0.6`.

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md §3; acceptance criterion 8.
- **Potential failure modes for this step:** hermetic-guard or repository-hygiene tests reacting to the moved `plans/`
  tree; append-only test reacting to the migration commit.
- **Guardrails and early‑abort checks:** run `test_repository_hygiene.py` and `test_ledger_append_only.py` early in the
  pass, not last.

### Success & Discard Criteria

- **Success:** `uv run pytest` green, `uv run task lint` green, `uv run task lint-docs` green (acceptance criterion 8).
- **Discard:** A failure rooted outside the touch set — stop and re-plan rather than widening the diff.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.5   |
| impact_pred         | 8.0   |
| cost_pred           | 3.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 4.15  |

### Step Metrics Rationale

Verification only; residual entropy covers fix-up commits the suite may force.
`EV = 0.85 * 8.0 + 0.5 * 1.0 - 0.3 * 0.5 - 3.0 = 6.8 + 0.5 - 0.15 - 3.0 = 4.15`.

---
