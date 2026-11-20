# Plan for I-1794976491-shortref

- **Plan ID:** P-1795087811-3604f487-claude-agent-claude-fable-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-fable-5 (version: 2.1.302)
- **Created At:** 2026-11-19T11:30:12.924Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A — re-plan attempt 1 of 2. The intent is unchanged; the previous plan failed
  only its touch-set gate because the execution legitimately needed six files the old touch set omitted
  (`.agents/holon_flow.md`, `apps/sandbox-executor/flow_module_budgets.json`,
  `apps/sandbox-executor/src/sandbox_executor/scaffold.py`, `holon-config/metrics/README.md`,
  `holon-config/prompts/planner.template.md`, `holon-config/world/constraints.md`). This plan widens the touch set to
  include them rather than decomposing, which is the lower-entropy remedy the re-plan request itself offers.
- **Exploration stance:** conservative. The goal enumerates exact required outputs (ticker table, model-code examples,
  ID formats, migration semantics, acceptance tests); the remaining uncertainty is coverage of call sites, which one
  bounded inventory step addresses. High-entropy exploration would add risk without learning value here.
- **Safety priority level:** elevated
- **Priority Justification:** `holon-config/world/constraints.md` §3 declares the ledgers append-only with a zero
  modification policy. Goal item 7 carries an explicit one-off maintainer exemption to rewrite exactly two path fields
  (`plan_file`, `calibration_report_path`). Elevated (not critical) because the exemption is explicit and scoped, but
  the migration step runs with byte-level diff verification, idempotence checks, and the requirement that the
  append-only enforcement test (`test_ledger_append_only.py`) keeps passing for all later changes. The plan also edits
  `constraints.md` itself only to update branch-name examples (goal item 9), never to weaken a rule.

## Exploration

- **Proportion of steps that are exploratory:** 0.11
- **Justification:** One of nine steps (Step 1, the call-site inventory) is INFO_GATHERING; everything else is
  exploitation of a fully specified design. The previous attempt failed precisely on unanticipated file coverage, so the
  inventory step is the targeted, cheap response to that observed failure mode.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 13.0  |
| impact_pred         | 88.0  |
| cost_pred           | 36.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 33.5  |

### Strategy Rationale

The intent is accepted as-is. Decomposition into sub-intents was considered and rejected: the work is one coherent
renaming scheme whose pieces (ticker, model code, ID format, plan paths, keyed lookups, migration) are tightly coupled —
a child intent that changed ID formats without the keyed lookups, or paths without the migration, would leave the tree
broken at its merge point. The entropy prediction of `13.0` fits the budget of `15.0`, so the plan runs as a single plan
with no `## Sub-intent list`.

Aggregation of step metrics into the overall row:

- `entropy_pred` is the **sum** of the nine step entropies:
  `0.3 + 1.2 + 2.0 + 2.0 + 2.5 + 1.5 + 2.5 + 0.8 + 0.2 = 13.0`.
- `cost_pred` is the **sum** of step costs: `1 + 3 + 5 + 5 + 7 + 4 + 6 + 2 + 3 = 36`.
- `p_success_pred = 0.80` is a qualitative product over the serial chain, dominated by the two bottleneck steps: Step 5
  (keyed lookups across calibration, ledger, executor, flow checkpoints, CLI; `p = 0.78`) and Step 7 (ledger migration
  under the append-only exemption; `p = 0.80`). The other steps are near-mechanical with p at or above 0.85.
- `impact_pred = 88` reflects that this removes a real collision bug (same-second plan IDs), unblocks nested child
  intents against the 255-character ref limit, and touches the naming spine of the whole system — close to the maximum
  for a non-feature infrastructure change.
- `learning_value_pred = 6.0` (not the sum of step values, which would exceed the 0–10 scale): the migration pattern
  (append-only exemption with byte-level verification) and the full call-site inventory of ID usage are reusable
  knowledge; the rest is routine.
- `ev_pred` per `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` with `lambda = 0.3`,
  `mu = 0.5`: `EV = 0.80 * 88.0 + 0.5 * 6.0 - 0.3 * 13.0 - 36.0 = 70.4 + 3.0 - 3.9 - 36.0 = 33.5`.

Strong assumptions declared against the world ruleset: (a) the maintainer exemption in goal item 7 lawfully overrides
`constraints.md` §3 for exactly two fields of two ledger files, once; (b) editing documentation examples inside
`holon-config/world/constraints.md` and `holon-config/prompts/planner.template.md` is a documentation change, not a
governance change, because no rule, threshold or constraint semantics are altered — only branch-name examples shown in
prose. If an executor finds a needed change that would alter rule semantics, it must stop and fail rather than proceed.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §3 Ledger Immutability (append-only `.jsonl`) — intersects Step 7.
  - `holon-config/world/constraints.md` §1 Git Flow & Branch Constraints — the branch grammar this plan changes is
    documented there; examples must be updated (Step 8) without changing isolation rules.
  - `docs/safety.md` Invariant 6 (ledger append-only) and the sandbox tiering table (entropy `13.0` → container sandbox,
    standard trust).
  - Ruleset §3 Testing Constraints — changes to test files are explicitly declared here, in planning (Steps 2–7 all add
    or modify tests under `apps/sandbox-executor/tests/`).
  - Ruleset §2 — typing, docstrings, ruff complexity limits (`C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`,
    `PLR0915 <= 50`) with no inline suppressions.
- **Potential violations or edge cases:**
  - Rewriting ledger rows beyond the two exempted fields, or reordering/adding/removing rows, would breach the
    exemption's scope.
  - Touching files outside the widened touch set (the previous attempt's failure mode).
  - Breaking old-generation ID parsing would orphan existing PR head branches.
  - A migration bug could leave `plan_file` paths pointing at nonexistent files.
- **Mitigations built into the plan:**
  - Step 1 inventories every ID construction/parse/lookup site before any edit, checked against the touch set.
  - Step 7 verifies the migration with a field-restricted diff (every row byte-identical except the two path fields), an
    idempotence re-run, and an existence check for every rewritten path; `test_ledger_append_only.py` must still pass
    afterwards.
  - Step 3 keeps both old ID generations parseable with dedicated regression tests against real ledger rows.
  - Collision rule (goal item 5): same parent, same second → second writer gets a distinct name or fails clearly; tested
    explicitly.
- **Residual risk accepted (and why):** A long tail of call sites may exist outside the inventoried modules (e.g. a
  string-formatted path in a prompt template). Accepted because the full test suite plus lint-docs run (Step 9) and the
  acceptance criteria exercise the end-to-end flows; the cost of a third discovery pass exceeds its expected value.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 13.0
- **Budget Compliance:** The strategy fits within budget (`13.0 <= 15.0`), executed as one plan with no sub-intent list.

## Plan Description & Strategy

Replace the long, collision-prone plan/execution ID scheme with short deterministic names, key plan artefacts by their
intent, and migrate the existing tree — in nine steps that each leave the suite green:

1. Inventory every construction, parse and lookup site for plan/execution IDs and plan file paths (including
   `scaffold.py`, flow checkpoints, prompt templates and docs), fixing the coverage gap that failed the previous
   attempt.
2. Introduce agent tickers (CLA/ANA/PIA) in `agent_defaults.py` with duplicate-ticker tests and no-fallback rejection in
   `holon doctor` and at intent-open time; add the pure model-code function with the seven required mappings plus the
   local-model validity case.
3. Generate new-format IDs `P-{epoch}-{ticker}-{model code}` / `E-{epoch}-{ticker}-{model code}` with same-second
   collision safety, while keeping both old generations parseable (no existing ID is ever renamed).
4. Write plan files and calibration reports under `plans/{intent}/...` mirroring the full branch nesting (child intents
   `{parent}--{index}-{slug}`, plan-under-plan), updating `scaffold.py` and calibration writers.
5. Key every plan lookup on (intent, plan ID) and every execution lookup on its full path — calibration, ledger queries,
   executor plan lookup, flow checkpoints, CLI — with the duplicate check failing on a repeated intent-and-plan pair.
6. Remove the now-unnecessary agent/model shortening from `ref_names.py` (label digests, `original_agent`/
   `original_model` for new rows), keep slug shortening and the length check, and enforce the 32-character slug limit
   (reject supplied root slugs, word-boundary-cut derived slugs) with the two-level-nesting ref-limit test.
7. Build and run the repeatable migration script for `plans/` and the two ledgers under the one-off append-only
   exemption, with a full report and a no-op second run.
8. Update every document, template, config example and module budget that shows the old names: `README.md`, `docs/`,
   `.agents/holon_flow.md`, `holon-config/prompts/*.template.md`, `holon-config/metrics/README.md`,
   `holon-config/world/constraints.md` (examples only), and `flow_module_budgets.json` for any module size shifts.
9. Run the affected tests per step, then the full suite once via `uv run task test`, plus `uv run task lint` and
   `uv run task lint-docs`.

New code goes into focused modules rather than growing existing ones past ~400 lines: the model-code/ticker naming
primitives beside `agent_defaults.py`/`ref_names.py`, a plan-path module owning the `plans/{intent}/` layout, and the
migration as a standalone script.

## Touch Set

- `apps/sandbox-executor/src/sandbox_executor/*.py`
- `apps/sandbox-executor/src/sandbox_executor/entrypoint/`
- `apps/sandbox-executor/src/sandbox_executor/flow/`
- `apps/sandbox-executor/src/sandbox_executor/cli/`
- `apps/sandbox-executor/tests/`
- `apps/sandbox-executor/flow_module_budgets.json`
- `scripts/migrate_plan_layout.py`
- `scripts/pre_commit_check.py`
- `.agents/holon_flow.md`
- `holon-config/prompts/planner.template.md`
- `holon-config/prompts/executor.template.md`
- `holon-config/prompts/review_loop.template.md`
- `holon-config/metrics/README.md`
- `holon-config/world/constraints.md`
- `docs/**/*.md`
- `README.md`

---

## Step 1: Inventory every ID and plan-path call site before editing

- **Sub‑intent recommendation:** NO
- **Reasoning:** Cheap, read-only, and directly de-risks the exact failure mode (missed files) that killed the previous
  attempt; no reuse value as a standalone branch.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The complete set of modules that construct, parse or look up plan/execution IDs and plan
  file paths is the widened touch set above, and nothing outside it.
- **Learning target:** A verified list of call sites (module, symbol, old-format assumption) covering `scaffold.py`,
  flow checkpoints, calibration, ledger queries, CLI commands, prompt templates and docs — including any use of an
  execution ID without its plan and intent (goal item 5 prerequisite).
- **Maximum acceptable cost for this learning:** One short read-only pass; abort further search once two consecutive
  search strategies (grep for `P-`/`E-` patterns, grep for `plans/` path joins, import graph of `ref_names`) produce no
  new sites.

### Intent & Git Integration

- **Step Intent:** Enumerate all construction/parse/lookup sites for plan and execution identifiers and paths; diff the
  result against the touch set and fail fast if anything falls outside it.
- **Git branch:** I-1794976491-shortref/P-1795087811-3604f487-claude-agent-claude-fable-5/E-{epoch}-{action}/\_ (single
  execution branch shared by all steps)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Grep the executor source, tests, scripts, prompt templates, `.agents/`, `holon-config/` and `docs/` for: the ID
  regexes (old hexless, old hex, branch grammar), `plans/` path construction, `_calibration.md` suffix handling, and
  checkpoint fields that store plan or execution IDs.
- Record each site with the change it needs (construct new format / parse both formats / key lookup by intent+plan / key
  execution by full path / docs example update).
- Specifically confirm the six files from the re-plan feedback and identify why each is touched (e.g. `scaffold.py`
  writes plan skeletons; `flow_module_budgets.json` caps module sizes that will shift; templates and `constraints.md`
  show branch-name examples).
- Produce the checklist that Steps 3–8 consume; verify every listed file matches the touch set, else stop and re-plan
  rather than editing outside it.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO (a missed site surfaces later in Step 9's full suite, degrading cost, not correctness of the
  plan gate)

### Safety & Constraint Considerations

- **Relevant rules:** touch-set discipline (re-plan feedback); `docs/safety.md` sandboxing (read-only step).
- **Potential failure modes for this step:** incomplete inventory; false confidence from narrow grep patterns.
- **Guardrails and early‑abort checks:** two independent search strategies must converge; the step output is a written
  checklist committed with the execution artefacts so later steps are auditable against it.

### Success & Discard Criteria

- **Success:** A checklist mapping every call site to a later step, with zero sites outside the touch set.
- **Discard:** A discovered site outside the touch set → stop before any code edit and trigger re-plan (attempt 2) with
  the still-wider touch set, instead of repeating the previous violation.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.3   |
| impact_pred         | 20.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 18.91 |

### Step Metrics Rationale

Read-only, so near-zero entropy (`SSA = 0`, `IRR = 0`). Impact is enabling rather than direct. Learning value 2.0: the
call-site map is reusable by the calibration stage and any future naming change.
`EV = 0.95 * 20.0 + 0.5 * 2.0 - 0.3 * 0.3 - 1.0 = 19.0 + 1.0 - 0.09 - 1.0 = 18.91`.

---

## Step 2: Agent tickers and the pure model-code function

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, pure, fully specified by the goal's required mappings; isolated enough to be a single commit
  with its tests.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Declare tickers CLA/ANA/PIA beside the runner definitions in `agent_defaults.py`; reject ticker-less
  runners in `holon doctor` and at intent-open time before any branch is created; add the pure model-code derivation
  function.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add a `ticker` attribute (exactly three uppercase letters) to the runner definitions in `agent_defaults.py` for
  claude-agent = CLA, antigravity-agent = ANA, pi-agent = PIA only; runners such as codex-agent, gemini-agent,
  opencode-agent deliberately get none.
- Place the model-code function in a small focused naming module beside `ref_names.py` (keeps both files under the size
  guideline): split the model name into words on hyphens after dropping everything before a `/`; take the first letter
  of each alphabetic word upper-cased; keep version-number words and join their parts with a dot; strip every character
  invalid in a git ref component. No lookup table.
- Unit tests assert the seven required mappings (`claude-sonnet-5-5 -> CS5.5`, `claude-fable-5 -> CF5`,
  `claude-opus-5-5 -> CO5.5`, `gemini-3.8-flash-medium -> G3.8FM`, `gemini-4-argon -> G4A`, `gpt-5-codex -> G5C`,
  `anthropic/claude-opus-5-5 -> CO5.5`) and that `qwen2.5-coder:32b` yields a valid ref component (checked against the
  git ref validity rules, e.g. no `:`).
- A test fails when two runners share a ticker (uniqueness over all declared runners).
- `holon doctor` and the intent-open path both reject a resolved runner without a ticker with a message naming the
  runner; no fallback ticker is derived. The intent-time check runs before any branch is created.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (every later step consumes tickers and model codes), but `p = 0.92`, so no sub-intent needed

### Safety & Constraint Considerations

- **Relevant rules:** ruleset §2 (typing, docstrings), §3 (tests declared); ruff complexity limits on the derivation
  function.
- **Potential failure modes for this step:** ambiguous tokenisation of version words (`3.8` vs `5-5`); the `:32b` suffix
  case producing an invalid ref.
- **Guardrails and early‑abort checks:** the seven required mappings are written as tests first; if any mapping cannot
  be met by a pure rule, stop and re-derive the rule rather than special-casing with a table (forbidden by the goal).

### Success & Discard Criteria

- **Success:**
  `uv run pytest apps/sandbox-executor/tests/test_agent_defaults.py apps/sandbox-executor/tests/test_ref_names.py` (plus
  the new naming tests) passes with all required mappings and rejection behaviours.
- **Discard:** A required mapping provably unreachable by any pure rule → escalate as intent contradiction instead of
  shipping a table.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.2   |
| impact_pred         | 60.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 52.59 |

### Step Metrics Rationale

Pure functions with exhaustive required examples: low SSA, zero IRR. The required-results list makes success highly
verifiable. `EV = 0.92 * 60.0 + 0.5 * 1.5 - 0.3 * 1.2 - 3.0 = 55.2 + 0.75 - 0.36 - 3.0 = 52.59`.

---

## Step 3: New-format ID generation with collision safety; both old generations stay parseable

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core but well-bounded change in `ref_names.py` and its callers; regression risk is covered by the
  existing `test_ref_names.py` plus new dual-generation tests.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** New plan/execution IDs are `P-{epoch}-{ticker}-{model code}` and `E-{epoch}-{ticker}-{model code}`
  with no hex; parsing accepts the new form plus both legacy forms (`-{agent}-{model}` with and without `-{hex}-`); no
  existing ID is renamed.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Replace the hex-bearing ID constructors in `ref_names.py` with ticker + model-code composition from Step 2.
- Parser handles three generations: new (`P-{epoch}-{TICKER}-{CODE}`), old hexless (`P-{epoch}-{agent}-{model}`), old
  hex (`P-{epoch}-{hex}-{agent}-{model}`), and the same for `E-`; disambiguation keys on the third field's shape
  (exactly three uppercase letters vs 8-char lower hex vs agent name). Property: round-trip for new IDs; parse-only for
  old ones, verified against real IDs sampled from the existing `plans.jsonl`/`executions.jsonl` rows in tests.
- Same-second collision rule (goal item 5): when a new plan or execution would get a name that already exists under the
  same parent, the second writer must not overwrite — it either derives a distinct deterministic variant or fails with a
  clear error; the choice is implemented once in the naming module and tested for both plans and executions.
- Ledger rows keep full `agent` and `model` fields; the ticker and code appear only in names (no ledger schema change in
  this step).

### Dependencies & Criticality

- **Depends on:** Steps 1, 2
- **Is Bottleneck:** YES; `p = 0.85` ≥ 0.6, so no sub-intent required

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §1 branch grammar (changed here, examples updated in Step 8); goal item 6 (old
  IDs match existing PR head branches — renaming is forbidden).
- **Potential failure modes for this step:** a legacy agent/model string that mimics the new shape; same-second
  collision path untested for executions.
- **Guardrails and early‑abort checks:** generation-disambiguation tests over every distinct ID pattern present in the
  real ledgers; explicit collision test fabricating two writes in one second under one parent.

### Success & Discard Criteria

- **Success:** `uv run pytest apps/sandbox-executor/tests/test_ref_names.py` and the ledger-query tests pass; new IDs
  contain no hex; all sampled legacy IDs parse.
- **Discard:** Any legacy ID in the real ledgers that cannot be classified unambiguously → stop and redesign the
  disambiguation before touching callers.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.0   |
| impact_pred         | 75.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 58.90 |

### Step Metrics Rationale

Moderate SSA (one core module plus callers), low IRR (pure naming, no stored-state change yet). Bottleneck for
everything downstream, hence the high impact.
`EV = 0.85 * 75.0 + 0.5 * 1.5 - 0.3 * 2.0 - 5.0 = 63.75 + 0.75 - 0.6 - 5.0 = 58.90`.

---

## Step 4: Plan files and calibration reports under plans/{intent}/ mirroring branch nesting

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical path-derivation change concentrated in one new module plus `scaffold.py` and the calibration
  writer; the nesting rules are fully specified.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** A plan lives at `plans/{intent}/{plan id}.md` with its calibration report beside it, mirroring the
  full branch nesting including child intents (`{parent}--{index}-{slug}`) and a plan nested under another plan
  (`I-.../P-.../P-.../E-...`).
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add a focused plan-path module owning the mapping branch-name → filesystem path (each branch segment becomes a
  directory; the plan file is `{plan id}.md`, the calibration report `{plan id}_calibration.md` beside it). One module,
  pure functions, both directions (path from IDs; IDs from an existing path for legacy and new layouts).
- `scaffold.py` (plan skeleton writer) and the calibration report writer in `calibration.py` consume this module instead
  of formatting flat `plans/{plan_id}.md` paths; directory creation is idempotent.
- Nesting cases covered by tests: root intent plan; child intent (`{parent}--{index}-{slug}`) plan; plan nested under
  another plan.
- Writers refuse to silently overwrite an existing file at the computed path (ties into the Step 3 collision rule).

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** YES; `p = 0.85` ≥ 0.6

### Safety & Constraint Considerations

- **Relevant rules:** ruleset §2 module sizing (new module instead of growing `scaffold.py`); touch-set discipline
  (`scaffold.py` now included).
- **Potential failure modes for this step:** path length on deep nesting; divergence between branch grammar and path
  grammar for child intents.
- **Guardrails and early‑abort checks:** the path module is the single source of truth — grep (from Step 1's checklist)
  confirms no other module formats a `plans/` path; round-trip tests branch→path→IDs.

### Success & Discard Criteria

- **Success:** New plans and calibration reports are written under `plans/{intent}/...` for all three nesting cases;
  `uv run pytest apps/sandbox-executor/tests/test_calibration.py apps/sandbox-executor/tests/test_planner.py` passes.
- **Discard:** Cost exceeding `1.5x cost_pred` on path-grammar edge cases → stop and reconcile grammar with the intent
  author via re-plan rather than inventing semantics.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.0   |
| impact_pred         | 70.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 54.40 |

### Step Metrics Rationale

Touches the write path of two artefact kinds; medium SSA, low IRR before migration (only new writes).
`EV = 0.85 * 70.0 + 0.5 * 1.0 - 0.3 * 2.0 - 5.0 = 59.5 + 0.5 - 0.6 - 5.0 = 54.40`.

---

## Step 5: Key every lookup on (intent, plan ID); executions on their full path

- **Sub‑intent recommendation:** NO
- **Reasoning:** The widest step (calibration, ledger, executor, flow checkpoints, CLI) and the plan's bottleneck, but
  `p = 0.78` is above the 0.6 sub-intent threshold and Step 1's checklist bounds the surface precisely.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** No plan is ever looked up by plan ID alone, and no execution by execution ID alone: calibration,
  ledger queries, the executor's plan lookup, flow checkpoints and every CLI query take the intent (and for executions,
  the plan) as part of the key; the duplicate check fails on a repeated intent-and-plan pair.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Work strictly from Step 1's checklist of flat-key usages: change each ledger query helper in `ledger.py` to take
  intent + plan ID (and full path for executions); update `calibration.py` resolution, the executor's plan lookup in the
  entrypoint/flow modules, flow checkpoint read/write fields, and the CLI query commands.
- The duplicate check that previously compared bare plan IDs now compares (intent, plan ID) pairs and fails on a repeat;
  two intents planned in the same second by the same runner and model yield two distinct plan files and two ledger rows
  that calibration resolves separately (explicit test, per the acceptance criteria).
- Before removing hex from execution IDs is relied upon anywhere, confirm from the checklist that every
  execution-ID-only usage has been re-keyed on the full `I-.../P-.../E-...` path (goal item 5's precondition).
- Legacy rows: lookups accept old-generation IDs and the old flat `plans/{plan_id}.md` locations until Step 7 migrates
  them; resolution order is new layout first, then legacy fallback.

### Dependencies & Criticality

- **Depends on:** Steps 1, 3, 4
- **Is Bottleneck:** YES (`p = 0.78` ≥ 0.6, so NO sub-intent is still consistent with the rule)

### Safety & Constraint Considerations

- **Relevant rules:** ruleset §2 complexity limits (refactor helpers rather than piling branches into one function);
  `docs/safety.md` checkpoint/resume semantics (a resumed flow must find its plan under the new keying).
- **Potential failure modes for this step:** a missed flat-key call site; checkpoint schema drift breaking resume of
  in-flight flows.
- **Guardrails and early‑abort checks:** checkpoint tests covering resume from a checkpoint written before the change;
  the same-second duplicate test; grep gate asserting no remaining call of the removed flat-lookup signatures.

### Success & Discard Criteria

- **Success:**
  `uv run pytest apps/sandbox-executor/tests/test_ledger_query.py apps/sandbox-executor/tests/test_calibration.py apps/sandbox-executor/tests/test_executor_plan_decision.py apps/sandbox-executor/tests/test_cli.py`
  passes; the repeated intent-and-plan pair fails the duplicate check; same-second twin intents resolve separately.
- **Discard:** More than three undiscovered flat-key sites surfacing during this step → pause and refresh the Step 1
  inventory before continuing (signal that the checklist is unreliable).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 2.5   |
| impact_pred         | 80.0  |
| cost_pred           | 7.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 55.65 |

### Step Metrics Rationale

Highest SSA of the plan (five subsystems) and the main CL contributor; this is where the collision bug is actually
killed, hence the top impact. `p = 0.78` reflects breadth, mitigated by the inventory.
`EV = 0.78 * 80.0 + 0.5 * 2.0 - 0.3 * 2.5 - 7.0 = 62.4 + 1.0 - 0.75 - 7.0 = 55.65`.

---

## Step 6: Remove obsolete agent/model shortening; enforce the 32-character slug limit

- **Sub‑intent recommendation:** NO
- **Reasoning:** Two cohesive deletions/additions in `ref_names.py` and the intent-open path, both precisely specified
  with their own acceptance tests.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Drop label digests and the `original_agent`/`original_model` ledger fields for new rows from
  `ref_names.py` (the short names make them unnecessary) while keeping slug shortening and the length check; cap slugs
  at 32 characters with rejection for supplied root slugs and word-boundary truncation for derived slugs.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Remove the agent/model shortening paths (label digests) from `ref_names.py`; new ledger rows no longer carry
  `original_agent`/`original_model`; readers still accept old rows that have them. Slug shortening and the overall ref
  length check stay.
- Root slug longer than 32 characters supplied in the intent file → rejected when the intent is opened, message stating
  the limit and the actual length; engine-derived slugs (from a description or sub-intent title) are cut to at most 32
  characters at a word boundary.
- Ref-limit test from the acceptance criteria: a root intent plus two levels of nested child intents, each with a
  32-character slug, produces a `/calibrated` branch that fits 255 characters including `refs/heads/` with no
  shortening; deeper nesting falls back to the kept slug-shortening path (asserted).
- Arithmetic check encoded in the test, not assumed: lengths are computed from the actual grammar with new-format P/E
  segments.

### Dependencies & Criticality

- **Depends on:** Steps 3, 5
- **Is Bottleneck:** NO (naming works without the deletions; this step delivers goal items 8 and 10)

### Safety & Constraint Considerations

- **Relevant rules:** ruleset §3 (test changes declared); goal item 8's explicit keep-list (slug shortening, length
  check).
- **Potential failure modes for this step:** deleting a shortening path still exercised by legacy parsing; the two-level
  32-char case not actually fitting 255 and exposing an intent inconsistency.
- **Guardrails and early‑abort checks:** run legacy-parsing tests from Step 3 after each deletion; if the ref-limit
  assertion fails arithmetically, report the exact overflow in the execution notes and fail rather than silently
  re-enabling shortening for that case.

### Success & Discard Criteria

- **Success:**
  `uv run pytest apps/sandbox-executor/tests/test_ref_names.py apps/sandbox-executor/tests/test_intent_creator.py`
  passes including the new rejection, truncation and ref-limit tests.
- **Discard:** The 255-fit guarantee provably false for the specified case → surface as intent contradiction (the
  acceptance criterion cannot be met), do not fudge the limit.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.5   |
| impact_pred         | 55.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 42.80 |

### Step Metrics Rationale

Mostly deletions plus one validation path; moderate risk only through coupling with legacy parsing.
`EV = 0.85 * 55.0 + 0.5 * 1.0 - 0.3 * 1.5 - 4.0 = 46.75 + 0.5 - 0.45 - 4.0 = 42.80`.

---

## Step 7: One-off migration of plans/ and the two ledger path fields

- **Sub‑intent recommendation:** NO
- **Reasoning:** Riskiest step in principle (ledger rewrite), but the maintainer exemption is explicit, the change is
  limited to two fields, and verification is mechanical; a sub-intent would duplicate the whole naming context for no
  isolation gain since the script is standalone.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** A repeatable script moves every existing file in `plans/` to the path its branch had (intent taken
  from the row's `intent_branch`), rewrites only `plan_file` in `plans.jsonl` and `calibration_report_path` in
  `executions.jsonl`, routes rowless files to a PR-head match by plan ID or `plans/legacy/`, prints a full report, and
  is a no-op on a migrated tree.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Standalone script at `scripts/migrate_plan_layout.py` (keeps the one-off out of the engine), importing the Step 4 path
  module; it takes the list of pull request head branch names as an input argument.
- Pass 1 (plan): read `plans.jsonl`; for each row compute target path from `intent_branch` + plan ID; plan file and its
  `_calibration.md` sibling move together. Files with no ledger row: match to a supplied PR head branch by plan ID where
  possible, else target `plans/legacy/`. Detect already-migrated entries (target exists, source gone) as no-ops.
- Pass 2 (apply): perform moves; rewrite only the two path fields in the two ledgers — every other byte of every row
  unchanged, no row added, removed or reordered. Verify by structural diff: per-row JSON equality on all fields except
  the two path fields, identical row count and order, then assert every rewritten path exists on disk.
- Report: every file moved (from → to), every path rewritten (row locator, field, old → new), and every file whose new
  path matches no supplied PR head branch name.
- Idempotence test: run twice in a test tree; second run reports zero actions and changes nothing (byte-compare the
  tree). `test_ledger_append_only.py` and `scripts/pre_commit_check.py` must still pass after migration — if the
  append-only check cannot distinguish this exempted commit, coordinate its allowance in the narrowest way (e.g.
  recognise the migration's field-restricted shape) without weakening it for future changes, and keep its own tests
  green.
- Finally run the migration on the real tree and commit the result with the report in the execution artefacts.

### Dependencies & Criticality

- **Depends on:** Steps 3, 4, 5
- **Is Bottleneck:** YES for the migration-related acceptance criteria; `p = 0.80` ≥ 0.6

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §3 Ledger Immutability — overridden once by the maintainer exemption in goal item
  7, scope: two fields, two files, this migration only; `docs/safety.md` Invariant 6.
- **Potential failure modes for this step:** a row whose `intent_branch` is missing or malformed; two plan files mapping
  to one target; accidental reordering via naive rewrite.
- **Guardrails and early‑abort checks:** dry-run (plan pass) output reviewed before apply in tests; rewrite streams rows
  in order and refuses to write if the structural diff check fails; collision at a target path aborts with a clear error
  before any move.

### Success & Discard Criteria

- **Success:** After the real run: no plan file directly in `plans/` outside intent folders or `plans/legacy/`; every
  `plan_file` and `calibration_report_path` points at an existing file; ledgers differ only in those two fields; a
  second run changes nothing; append-only enforcement still passes.
- **Discard:** Any row that would need a change outside the two exempted fields → abort the migration entirely and
  report; the exemption's scope is hard.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 2.5   |
| impact_pred         | 70.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 50.25 |

### Step Metrics Rationale

High IRR term (moves + ledger rewrite) drives entropy despite the script being standalone; mitigated to 2.5 by
idempotence and structural-diff guarantees making it effectively reversible via git. Learning value 2.0: the
exempted-migration-with-verification pattern is reusable.
`EV = 0.80 * 70.0 + 0.5 * 2.0 - 0.3 * 2.5 - 6.0 = 56.0 + 1.0 - 0.75 - 6.0 = 50.25`.

---

## Step 8: Documentation, templates, config examples and module budgets

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure documentation/config-example updates; this step exists chiefly to own, inside the touch set, the
  six files that broke the previous attempt.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Every document, prompt template and config example showing branch names, plan IDs or plan file paths
  reflects the new scheme; `flow_module_budgets.json` accommodates legitimate module-size shifts.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- From Step 1's checklist, update examples in: `README.md`; `docs/` (at least `git_flow.md`, `directory_structure.md`,
  `examples.md`, `ledger_schema.md`, `core_concepts.md`, and the sandbox/executor/planner subpages that print IDs);
  `.agents/holon_flow.md`; `holon-config/prompts/planner.template.md`, `executor.template.md`,
  `review_loop.template.md`; `holon-config/metrics/README.md`; `holon-config/world/constraints.md` — in `constraints.md`
  strictly the branch-name grammar examples in §1, no rule semantics.
- Note the dual-generation reality where relevant: old IDs remain valid and are never renamed.
- Adjust `apps/sandbox-executor/flow_module_budgets.json` only for modules whose size legitimately changed in Steps 2–6
  (new naming and path modules added; `ref_names.py` shrinks), keeping the budget check meaningful rather than
  blanket-raising caps.
- `uv run task lint-docs` is the gate for this step.

### Dependencies & Criticality

- **Depends on:** Steps 2–7 (documents the final state)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** governance boundary — `constraints.md` edits must not change rule meaning (see Strategy Rationale
  assumption b); markdown/prettier formatting rules (formulas in code spans).
- **Potential failure modes for this step:** an example update accidentally rewording a constraint; prettier oscillation
  from raw underscores in ID examples.
- **Guardrails and early‑abort checks:** diff review of `constraints.md` limited to example lines; `lint-docs` run
  before commit.

### Success & Discard Criteria

- **Success:** `uv run task lint-docs` passes; no stale `P-{epoch}-{hex}-...` example outside historical/legacy context
  remains (grep check); budgets file consistent with `scripts/check_flow_module_budgets.py`.
- **Discard:** Any required doc change that would alter a rule's meaning → leave it, flag for human governance review in
  the execution notes.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.8   |
| impact_pred         | 40.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 34.81 |

### Step Metrics Rationale

Text-only, reversible, broad but shallow surface.
`EV = 0.92 * 40.0 + 0.5 * 0.5 - 0.3 * 0.8 - 2.0 = 36.8 + 0.25 - 0.24 - 2.0 = 34.81`.

---

## Step 9: Full verification — test suite and lints

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard closing gate mapping one-to-one onto the final acceptance criterion.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The whole tree is green under the project's own gates after all changes and the migration.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Affected tests were already run per step with `uv run pytest <paths>`; this step runs the full suite once with
  `uv run task test` (`-n auto --dist worksteal` per test-run discipline; no repetition — the intent measures no
  performance or timing behaviour, so a single full run is planned).
- Then `uv run task lint` and `uv run task lint-docs`.
- Report pass/fail counts and the pytest-reported time for the single full run.
- Fix fallout strictly within the touch set; a required fix outside it stops the step and reports instead of violating
  the gate again.

### Dependencies & Criticality

- **Depends on:** Steps 2–8
- **Is Bottleneck:** YES (final acceptance criterion); `p = 0.85` ≥ 0.6

### Safety & Constraint Considerations

- **Relevant rules:** test-run discipline (single full run, parallel); touch-set discipline for any fixes.
- **Potential failure modes for this step:** integration tests exercising a flat-path assumption no earlier step's
  targeted tests cover.
- **Guardrails and early‑abort checks:** failures triaged against Step 1's checklist first; more than ~5 failing modules
  indicates a systemic miss → stop and report rather than patching ad hoc.

### Success & Discard Criteria

- **Success:** `uv run task test`, `uv run task lint` and `uv run task lint-docs` all pass; counts and time reported.
- **Discard:** A failure fixable only outside the touch set → stop, report for re-plan attempt 2 with the specific file
  named.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.2   |
| impact_pred         | 50.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 39.69 |

### Step Metrics Rationale

Verification adds almost no new disorder itself; its p reflects the chance of surfacing latent misses from Steps 3–7.
`EV = 0.85 * 50.0 + 0.5 * 0.5 - 0.3 * 0.2 - 3.0 = 42.5 + 0.25 - 0.06 - 3.0 = 39.69`.

---
