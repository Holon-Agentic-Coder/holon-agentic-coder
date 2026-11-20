# Plan for I-1794132504-purge-harness-bean-references-from-code-and-docs

- **Plan ID:** P-1794132519-claude-agent-claude-fable-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-fable-5 (version: 2.1.302)
- **Created At:** 2026-11-08T10:08:39.874Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** Not reframed. One clarification adopted as a planning decision: the intent's
  `scope` array lists 8 files, but the `goal` text ("Remove all 'Bean <id>' references across
  apps/sandbox-executor/src/", "Clean ... apps/sandbox-executor/tests/", "Clean ... docs/") and the acceptance criteria
  ("No harness bean IDs remain in production code or docstrings") require the wider sweep. A pre-planning survey found
  roughly 197 occurrences across roughly 57 files. The goal and acceptance criteria govern; the scope array is treated
  as the highlight list, not the boundary. Historical artefacts (`plans/`, `executions/`,
  `holon-knowledge/ledger/*.jsonl`) are explicitly excluded: the ledgers are append-only per constraints.md §3 and the
  plan/execution records are immutable history, not target-repository code or documentation.
- **Exploration stance:** conservative — this is a mechanical hygiene purge with crisp, greppable acceptance criteria.
  The dominant risk is missed occurrences or broken test assertions, which is best managed with an exhaustive inventory
  step and a hard verification gate, not with exploratory redesign.
- **Safety priority level:** standard
- **Priority Justification:** No destructive operations, no dependency changes, no ledger mutation, no estimator or
  governance changes (safety.md "Human review boundaries" items 2–4 are untouched). The work is textual and fully
  revertible inside the execution branch (safety.md principle 1: git is the safety boundary). Predicted entropy (~6.1)
  is below the process-sandbox threshold of 10 in constraints.md §2, so the lowest containment tier suffices.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Every occurrence is enumerable with `grep -riE 'bean'` before any edit is made. Exploration would
  add entropy with no learning upside; the small genuine uncertainty (how to rename the payload key and the PR-body
  section without breaking consumers) is resolved by the Step 1 inventory, not by trial and error.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 6.1   |
| impact_pred         | 62.0  |
| cost_pred           | 10.5  |
| learning_value_pred | 2.0   |
| ev_pred             | 46.3  |

### Strategy Rationale

The intent is accepted as-is and executed as a single plan. The strategy is a classify-then-sweep: Step 1 builds a
complete occurrence inventory and classifies each hit into one of five buckets (runtime strings, semantic identifiers,
source comments/docstrings, tests, documentation), then Steps 2–6 clean one bucket each in rising order of blast radius
for the riskiest buckets first (runtime strings and semantic identifiers before the bulk comment sweep), and Step 7 is a
hard verification gate (grep sweep + lint + full test suite).

Aggregation of step metrics into the overall table:

- `entropy_pred` is the **sum** of step entropies: `0.2 + 0.8 + 1.5 + 1.6 + 1.2 + 0.6 + 0.2 = 6.1`. Cross-checked
  against the bootstrap formula in metrics.md §2.2 with entropy_config.json weights:
  `Delta_S = 0.30*SSA + 0.25*IRR + 0.20*CL + 0.15*SER + 0.10*NOV` with `SSA ~ 8` (many files, few lines each), `IRR ~ 1`
  (pure text, trivially revertible), `CL ~ 3` (flow.py is a hot file), `SER ~ 0.5`, `NOV ~ 1`, giving
  `0.30*8 + 0.25*1 + 0.20*3 + 0.15*0.5 + 0.10*1 = 3.43` as a floor; the per-step sum of 6.1 is deliberately the more
  conservative of the two estimates and sits well under the 15.0 budget.
- `cost_pred` is the **sum** of step costs: `0.8 + 1.0 + 2.0 + 2.5 + 1.8 + 1.2 + 1.2 = 10.5`.
- `p_success_pred` (0.93) is **qualitative, bottleneck-weighted**: the naive product of step probabilities (~0.65)
  overstates risk because every step is retryable in-branch at small cost; the binding risks are Step 3 (semantic
  renames of `bean_0034_enforced` and the PR-body `beans` field could break an unnoticed consumer) and the Step 7 gate
  (a single missed occurrence fails acceptance). Both are mitigated by the Step 1 inventory and the grep gate, leaving a
  residual ~0.07 failure mass.
- `impact_pred` (62) is the sum of step impacts (~78) **discounted for overlap**, since the step impacts all serve one
  acceptance outcome (Rule 12 compliance of a single app).
- `learning_value_pred` (2.0) is the **max-anchored** value: the only durable learning is the Step 1/Step 3 finding on
  how deeply harness identifiers leaked into semantic surfaces (payload keys, PR-body schema), which is reusable for
  future Rule 12 audits of other apps.
- `ev_pred` uses ev_config.json (`lambda = 0.3`, `mu = 0.5`):
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` =
  `0.93 * 62.0 + 0.5 * 2.0 - 0.3 * 6.1 - 10.5 = 57.66 + 1.00 - 1.83 - 10.50 = 46.33`, reported as 46.3.

The bottleneck step is Step 3 (semantic identifier renames, `p = 0.90`); it is the only step that changes behaviourally
observable surfaces rather than inert text. No step warrants a sub-intent: all are small, cheap, and non-reusable in
isolation, and decomposition overhead would exceed any risk reduction.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - constraints.md §1 (Git Flow): all edits happen on an execution branch
    `I-1794132504-purge-harness-bean-references-from-code-and-docs/P-1794132519-claude-agent-claude-fable-5/E-{timestamp}-{action}/_`,
    rebased from the plan branch first; merging stays with the human maintainer.
  - constraints.md §2 (Sandbox tiers): predicted entropy 6.1 < 10 → process sandbox tier is sufficient.
  - constraints.md §3 (Ledger immutability): `holon-knowledge/ledger/*.jsonl` are never edited, even where historical
    rows mention beans. The intent's own constraint "keep the three append-only ledgers" restates this.
  - ruleset.md §2 (Docstring requirements): rewritten docstrings must remain explanatory — bean parentheticals are
    replaced with descriptive target-native wording, never deleted into emptiness.
  - ruleset.md §3 (Testing constraints): changes to test files must be explicitly declared in planning — **this plan
    explicitly declares test modifications in Steps 2, 3 and 5** (renamed test functions, updated docstrings, updated
    string assertions and fixture keys). No test is weakened or deleted; assertions are retargeted to the new strings
    with identical strictness.
  - Intent constraints: Australian English, no new dependencies, tests strictly via `uv run pytest`, `uv run task lint`
    / `uv run task lint-docs` / `uv run task test` (and `uv run task check`) must pass.
- **Potential violations or edge cases:**
  - Renaming the `bean_0034_enforced` payload key could break an undiscovered consumer (ledger writer, CLI formatter).
  - The PR-body generator reads `intent_data.get("beans")`; removing the key could silently drop content for legacy
    intent files that still carry it.
  - Mass regex replacement could corrupt unrelated text (e.g. "Beta-Wilson" contains no 'bean' but careless patterns
    could over-match); Australian English spelling could regress under bulk editing.
  - Historical docs (`docs/audit_report.md`, `docs/audit-report-2026-10.md`) are documentation, so they are cleaned, but
    `plans/` and `executions/` are immutable records and must not be touched.
- **Mitigations built into the plan:**
  - Step 1 produces a complete, classified inventory before any edit; every subsequent step works from that list, not
    from fresh ad-hoc greps.
  - Semantic renames (Step 3) are preceded by a whole-repo consumer grep for each identifier; edits are per-file
    reviewed, never blind `sed` across the tree.
  - Targeted pytest after each behavioural step, full suite exactly once at the end (test-run discipline; no performance
    measurement is required so no repeated runs are planned).
  - Step 7 gate: `grep -riE 'bean'` over `apps/sandbox-executor/` and `docs/` must return zero hits (excluding immutable
    history paths) before declaring success.
- **Residual risk accepted (and why):** A downstream tool outside this repository could parse the old
  `[BEAN 0034 SAFETY HALT]` marker or the `bean_0034_enforced` key. This is accepted because Rule 12 states target
  repositories are standalone systems; any such coupling is itself the violation this intent removes, and the change is
  contained to a branch the maintainer reviews before merge.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 6.1
- **Budget Compliance:** The strategy fits within budget (6.1 ≤ 15.0), so no sub-intent decomposition is required and no
  `## Sub-intent list` section is included.

## Plan Description & Strategy

Pre-planning survey results that ground the steps (all line numbers verified on the current branch):

1. **Runtime strings** — `"[BEAN 0034 SAFETY HALT]"` printed at
   `apps/sandbox-executor/src/sandbox_executor/cli/commands/flow.py:288`,
   `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py:765`,
   `apps/sandbox-executor/src/sandbox_executor/flow.py:5194` and `flow.py:5302`, plus the halt-reason text
   `"(Bean 0034 safety halt)"` at `flow.py:4804`.
2. **Semantic identifiers** — payload key `"bean_0034_enforced"` at `flow.py:3343, 4866, 5097, 5125, 5177, 5208`,
   asserted in `tests/test_flow.py:515, 1543` and `tests/test_cli.py:1078`; PR-body generator section
   `"## Referenced Beans"` / `"Closes Bean ..."` with variables `beans` / `bean_list` reading `intent_data.get("beans")`
   at `flow.py:1363–1374`, asserted in `tests/test_flow.py:1676–1716` with fixture key `"beans"` at line 1683.
3. **Source comments/docstrings** — roughly 40 files under `apps/sandbox-executor/src/` carry `(Bean NNNN)` annotations
   (artefacts.py, git.py, budgets.py, ledger.py, local*llm.py, turns.py, flow.py, agent_runner.py, knowledge/*,
   cli/commands/\_, entrypoint/\*).
4. **Tests** — roughly 17 test files carry bean references in docstrings, function names (e.g.
   `test_stage4_review_bean_0034_human_only_merge_halt`, `test_review_loop_main_consensus_approval_bean_0034`,
   `test_summary_format_matches_the_bean_example`), assertion messages (`"VIOLATION OF BEAN 0034"`), and string
   assertions on runtime output.
5. **Documentation** — `apps/sandbox-executor/README.md` (7 hits), `apps/sandbox-executor/docs/hermetic_testing.md`, and
   9 files under `docs/` (git_flow.md, ledger_schema.md, budgets.md, sandbox/create_plan.md, sandbox/execute_plan.md,
   executor/execution_architecture_specification.md, executor/agent_credentials_requirements.md, audit_report.md,
   audit-report-2026-10.md).

Strategy: classify first (Step 1), then clean in rising-blast-radius-first order — runtime strings (Step 2), semantic
identifiers (Step 3), source comments/docstrings (Step 4), tests (Step 5), documentation (Step 6) — and finish with a
hard verification gate (Step 7). All replacement prose uses Australian English and target-native vocabulary: a
`(Bean NNNN)` provenance tag becomes either nothing (where the sentence already explains itself) or a short descriptive
clause naming the behaviour (for example, `Enforces Bean 0034 (Human-Only PR Merging)` becomes
`Enforces the human-only PR merge rule`). No new dependencies, no `pyproject.toml` changes, no ledger edits.

---

## Step 1: Build and classify the complete bean-reference inventory; record baseline test status

- **Sub‑intent recommendation:** NO
- **Reasoning:** Cheap, read-only, zero-risk reconnaissance; decomposing it would cost more than the step itself.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Produce an authoritative, classified list of every bean occurrence so later steps edit from a fixed
  checklist, and record the pre-change test baseline so post-change failures are attributable.
- **Git branch:**
  `I-1794132504-purge-harness-bean-references-from-code-and-docs/P-1794132519-claude-agent-claude-fable-5/E-{timestamp}-purge-bean-references/_`
  (single execution branch for all steps; created off the plan branch after rebase per constraints.md §1).
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Rebase the plan branch from the intent branch, then create the execution branch.
2. Run a case-insensitive recursive grep for `bean` across `apps/sandbox-executor/` (src, tests, docs, README,
   entrypoint shell scripts, Dockerfile, pyproject) and `docs/`, excluding `plans/`, `executions/`, `holon-knowledge/`,
   and any build caches.
3. Classify each hit into: (a) runtime output strings, (b) semantic identifiers (dict keys, field names, variable
   names), (c) source comments/docstrings, (d) test names/docstrings/assertions/fixtures, (e) documentation prose.
   Verify the five-bucket survey in the Plan Description against the live tree and note any drift.
4. For each semantic identifier (`bean_0034_enforced`, `beans`, `bean_list`), grep the entire repository (including
   scripts/ and holon-config/) for consumers to confirm the rename surface is closed.
5. Run the targeted baseline:
   `uv run pytest apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_cli.py -n auto --dist worksteal`
   and record pass/fail counts and pytest-reported time.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (every later step consumes the inventory), but `p_success_pred` is 0.98 so no sub-intent is
  needed.

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md §1 (branch creation and rebase discipline); safety.md "Git is the safety boundary".
- **Potential failure modes for this step:** grep pattern misses occurrences split across lines; baseline tests already
  failing for unrelated reasons.
- **Guardrails and early‑abort checks:** if the baseline targeted tests fail before any edit, stop and report — the
  intent presumes a green baseline; cross-check the hit count (~197) against the survey to detect pattern drift.

### Success & Discard Criteria

- **Success:** A classified inventory covering every grep hit; consumer list for each semantic identifier; recorded
  green (or explained) baseline for the three targeted test files.
- **Discard:** Baseline tests fail for reasons unrelated to beans and cannot be attributed within 1.5x cost_pred — halt
  and report instead of proceeding on a red baseline.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.2   |
| impact_pred         | 5.0   |
| cost_pred           | 0.8   |
| learning_value_pred | 1.0   |
| ev_pred             | 4.5   |

### Step Metrics Rationale

Read-only work: near-zero entropy (no state change beyond branch creation), high certainty. Learning value 1.0 for the
reusable Rule 12 audit checklist. `EV = 0.98 * 5.0 + 0.5 * 1.0 - 0.3 * 0.2 - 0.8 = 4.90 + 0.50 - 0.06 - 0.80 = 4.54`,
reported 4.5.

---

## Step 2: Replace the '[BEAN 0034 SAFETY HALT]' runtime strings and their test assertions

- **Sub‑intent recommendation:** NO
- **Reasoning:** Five known call sites plus a handful of assertions; small, mechanical, directly specified by goal
  item 1.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Remove the bean ID from all runtime safety-halt output while preserving the halt semantics and the
  tests that guard them (acceptance criterion: no `[BEAN ...]` strings in runtime output).
- **Git branch:** same execution branch as Step 1.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Replace the literal `[BEAN 0034 SAFETY HALT]` with `[SAFETY HALT]` at
   `src/sandbox_executor/cli/commands/flow.py:288`, `src/sandbox_executor/entrypoint/review_loop.py:765`,
   `src/sandbox_executor/flow.py:5194` and `flow.py:5302`.
2. Rewrite the halt-reason string at `flow.py:4804` from `(Bean 0034 safety halt)` to target-native wording such as
   `(human-only merge safety halt)`.
3. Update the matching string assertions with identical strictness: `tests/test_flow.py:1629`
   (`"BEAN 0034 SAFETY HALT" in captured` becomes `"[SAFETY HALT]" in captured`) and any sibling assertions in
   `tests/test_review_loop.py` and `tests/test_cli.py` found by the Step 1 inventory. Test changes are declared here per
   ruleset.md §3.
4. Sweep the surrounding docstrings in `review_loop.py` (line 11: `Enforces Bean 0034 (Human-Only PR Merging)`) and
   `flow.py` (lines 12, 1515, 3211, 4146) where the sentence names the bean as the rule's identity, rewriting to name
   the rule itself (human-only PR merging / autonomous merge prohibition).
5. Run
   `uv run pytest apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_cli.py -n auto --dist worksteal`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (acceptance criterion 1 depends on it), p_success_pred 0.97 so no sub-intent.

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md §3 (test changes declared); intent constraint "tests strictly via uv run pytest".
- **Potential failure modes for this step:** an assertion elsewhere still expects the old marker; the halt message is
  parsed downstream by substring.
- **Guardrails and early‑abort checks:** after editing, grep for `BEAN 0034` repo-wide (excluding history paths) — must
  be zero in runtime code; targeted pytest must be green before moving on.

### Success & Discard Criteria

- **Success:** Zero `[BEAN` occurrences in `src/`; the three targeted test files pass.
- **Discard:** Targeted tests stay red after one focused fix iteration beyond 1.5x cost_pred — stop and reassess rather
  than loosening assertions.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.8   |
| impact_pred         | 20.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 18.4  |

### Step Metrics Rationale

Highest-impact step: it clears acceptance criterion 1 (runtime output) directly. Small surface (≈5 call sites, ≈3
assertions), hence low entropy and high confidence.
`EV = 0.97 * 20.0 + 0.5 * 0.5 - 0.3 * 0.8 - 1.0 = 19.40 + 0.25 - 0.24 - 1.00 = 18.41`, reported 18.4.

---

## Step 3: Rename semantic identifiers — the 'bean_0034_enforced' payload key and the PR-body 'Referenced Beans' generator

- **Sub‑intent recommendation:** NO
- **Reasoning:** The riskiest step (behaviourally observable surfaces), but the consumer surface was closed by the Step
  1 grep and the whole change is a few dozen lines; a sub-intent would add pipeline overhead without reducing the actual
  risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Remove bean identifiers from machine-readable surfaces (result payload key, PR-body section and
  intent field) so no harness ID leaks into logs, payloads, or generated PR descriptions.
- **Git branch:** same execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Rename the payload key `bean_0034_enforced` to `human_merge_gate_enforced` at
   `flow.py:3343, 4866, 5097, 5125, 5177, 5208`, and retarget the assertions at `tests/test_flow.py:515` and `:1543` and
   the fixture at `tests/test_cli.py:1078` (declared test changes per ruleset.md §3). Confirm via the Step 1 consumer
   grep that no other module, script, or config reads the old key; old ledger rows containing it remain untouched
   (append-only).
2. In the PR-body generator (`flow.py:1251–1374`): rename the section heading `## Referenced Beans` to `## References`,
   change the closing line from `Closes Bean <list>` to a plain `References: <list>` form, rename the local variables
   `beans` → `references` and `bean_list` → `reference_list`, and read the intent field `references` instead of `beans`.
   The 'None' fallback branch keeps its current behaviour under the new heading.
3. Update the generator's docstring (`flow.py:1251–1258`) to describe the new section name and field.
4. Update the PR-body tests (`tests/test_flow.py:1676–1716`): fixture key `beans` becomes `references`, heading and
   closing-line assertions retargeted with identical strictness; the fixture's goal text at line 1682 has its
   `(Bean NNNN)` decorations replaced with neutral numbered items so the body-content assertions at lines 1698–1700 stay
   meaningful without re-introducing bean IDs.
5. Run
   `uv run pytest apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_cli.py -n auto --dist worksteal`.

### Dependencies & Criticality

- **Depends on:** Step 1 (consumer grep), Step 2 (same files; avoids conflicting hunks)
- **Is Bottleneck:** YES for acceptance criterion 2; p_success_pred 0.90 ≥ 0.6, so a sub-intent is not mandated.

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md §3 (declared test changes); constraints.md §3 (historical ledger rows with the old key
  are never rewritten); ruleset.md §2 (docstrings stay explanatory).
- **Potential failure modes for this step:** an unlisted consumer of `bean_0034_enforced` (e.g. a formatter or CLI
  summary) breaks at runtime; a legacy intent JSON still carrying a `beans` field loses its references section.
- **Guardrails and early‑abort checks:** whole-repo grep for both old identifiers must be zero after the edit (excluding
  history paths); if Step 1 found any consumer outside flow.py/tests, pause and widen the rename to it before running
  tests; targeted pytest green before proceeding.

### Success & Discard Criteria

- **Success:** No `bean_0034_enforced`, `Referenced Beans`, `Closes Bean`, `bean_list` or `beans` identifiers remain in
  `src/` or `tests/`; targeted tests pass.
- **Discard:** If the consumer grep reveals the key is part of an externally documented schema with consumers outside
  this repository's control, halt this step and report — that would convert the rename into a governance question for
  the maintainer rather than a hygiene edit.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.5   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 11.8  |

### Step Metrics Rationale

Bottleneck step: the only one touching machine-readable behaviour. Entropy 1.5 reflects schema-adjacent edits in a hot
file; p 0.90 reflects the small chance of a hidden consumer. Learning value 1.5: this teaches where harness IDs tend to
fossilise (payload keys, generated artefacts), reusable for future isolation audits.
`EV = 0.90 * 15.0 + 0.5 * 1.5 - 0.3 * 1.5 - 2.0 = 13.50 + 0.75 - 0.45 - 2.00 = 11.80`, reported 11.8.

---

## Step 4: Rewrite '(Bean NNNN)' comments and docstrings across apps/sandbox-executor/src/

- **Sub‑intent recommendation:** NO
- **Reasoning:** Broad (≈40 files) but inert text with zero behavioural risk; cheap per-file edits off a fixed
  checklist.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Replace every remaining `Bean <id>` / `Beans <range>` mention in source comments and docstrings with
  descriptive target-native explanations, satisfying acceptance criterion 2 for production code.
- **Git branch:** same execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Work through the Step 1 bucket (c) checklist file by file (artefacts.py, git.py, budgets.py, ledger.py, local_llm.py,
   turns.py, agent_runner.py, flow.py remainder, knowledge/{index,consult,promotion,stats,records, wisdom,**init**}.py,
   cli/{**init**}.py, cli/commands/{veto,record,consult,reconcile,review,stats,promote,flow}.py,
   entrypoint/{executor,planner,intent_creator,review_loop}.py).
2. Apply one of two rewrites per occurrence, chosen by reading the sentence: (a) delete the provenance parenthetical
   where the sentence already stands alone (e.g. module headline `... incremental sync (Bean 0052 Goal 1)` keeps the
   headline, drops the tag); (b) replace the bean reference with the behaviour it names where the bean carries meaning
   (e.g. `Safety invariant inherited from Bean 0027` in local_llm.py becomes
   `Safety invariant inherited from the server-connect rewrite`; `the real measurements recorded in Beans 0138 to 0140`
   in budgets.py becomes `real measurements recorded during the artefact, turn and model-envelope work`;
   `split out to its own bean` in executor.py becomes `split out to its own intent`).
3. This includes user-visible help strings in cli/commands/\* (`help="... (Bean 0052)"`), which are runtime output: drop
   the parentheticals there.
4. Keep docstrings compliant with ruleset.md §2 (inputs/outputs/exceptions/side-effects wording untouched) and
   Australian English throughout; edits never touch code semantics, only comment/docstring/help text.
5. Run `uv run pytest apps/sandbox-executor/tests/ -n auto --dist worksteal -x -q` opportunistically only if any help
   string was asserted by a test per the Step 1 inventory; otherwise defer testing to Step 7 (text-only edits).

### Dependencies & Criticality

- **Depends on:** Steps 2–3 (same files; ordered to avoid overlapping hunks)
- **Is Bottleneck:** NO (misses are caught by the Step 7 grep gate and fixable at trivial cost)

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md §2 (docstring quality); intent constraint (Australian English).
- **Potential failure modes for this step:** a CLI help string is asserted verbatim in test_cli.py or
  test_cli_options.py; over-eager editing corrupts an adjacent sentence.
- **Guardrails and early‑abort checks:** per-file review of each diff hunk (no blind tree-wide sed); cross-check edited
  help strings against the Step 1 test-assertion inventory before running tests.

### Success & Discard Criteria

- **Success:** `grep -riE 'bean'` over `apps/sandbox-executor/src/` returns zero hits; no code-semantics lines changed
  (diff touches only comments, docstrings and help strings).
- **Discard:** If any file requires semantic restructuring (not just text) to remove a reference, stop editing that file
  and surface it in the execution report rather than improvising a behaviour change.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 1.6   |
| impact_pred         | 12.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 8.6   |

### Step Metrics Rationale

Widest file surface of the plan (drives SSA, hence the 1.6 entropy) but inert content, so p stays high; cost is the
largest because ≈150 occurrences need individual judgement.
`EV = 0.94 * 12.0 + 0.5 * 0.5 - 0.3 * 1.6 - 2.5 = 11.28 + 0.25 - 0.48 - 2.50 = 8.55`, reported 8.6.

---

## Step 5: Clean remaining bean references from test names, docstrings, and assertion messages

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical renames in ≈17 test files with assertions preserved verbatim; low risk, caught instantly by
  pytest collection if botched.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Remove bean references from the test suite (names, docstrings, assertion messages, class docstrings)
  without weakening a single assertion — declared test modification per ruleset.md §3.
- **Git branch:** same execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. From the Step 1 bucket (d) checklist, rename test functions embedding bean IDs, keeping intent-revealing names:
   `test_stage4_review_bean_0034_human_only_merge_halt` → `test_stage4_review_human_only_merge_halt`,
   `test_review_loop_consensus_approval_halts_under_bean_0034` → `..._halts_without_merge`,
   `test_review_loop_main_consensus_approval_bean_0034` → `..._consensus_approval_halts`,
   `test_stage5_calibrate_bean_0038` / `..._mocked` → `test_stage5_calibrate` / `..._mocked`,
   `test_summary_format_matches_the_bean_example` (test_budgets.py:115) → `..._matches_the_documented_example`.
2. Rewrite assertion messages (`"VIOLATION OF BEAN 0034: ..."` at test_flow.py:511 and 1539 →
   `"VIOLATION OF HUMAN-ONLY MERGE RULE: ..."`), class/module docstrings, and inline comments across the ≈17 files from
   the inventory (test_flow.py, test_review_loop.py, test_cli.py, test_budgets.py, test_container_images.py,
   test_ledger_lock.py, test_calibration.py, test_executor.py, test_planner.py, test_git.py, test_knowledge.py,
   test_local_llm.py, test_pre_commit_check.py, test_sandbox_hermetic_guard.py, test_token_reduction.py,
   test_workspace_survival_stress.py, hermetic_fixtures.py).
3. Verify no test is deleted and no assertion condition is altered — only names, messages and prose change (the
   string-assertion retargets were already done in Steps 2–3).
4. Run `uv run pytest apps/sandbox-executor/tests/ -n auto --dist worksteal -q` once to confirm collection and passing
   status across the touched suite.

### Dependencies & Criticality

- **Depends on:** Steps 2–3 (assertion retargets land first so this step is purely cosmetic)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md §3 (test changes explicitly declared — done here and in the Safety & Constraint
  Alignment section).
- **Potential failure modes for this step:** a renamed test collides with an existing name; a conftest or fixture
  references a test name programmatically.
- **Guardrails and early‑abort checks:** pytest collection error fails fast; grep for each new test name before renaming
  to avoid collisions.

### Success & Discard Criteria

- **Success:** `grep -riE 'bean'` over `apps/sandbox-executor/tests/` returns zero hits; full app test run passes with
  the same pass count as baseline (modulo renames).
- **Discard:** Any fix requiring an assertion-condition change — stop and report, since that exceeds this intent's
  authority over test semantics.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 1.2   |
| impact_pred         | 8.0   |
| cost_pred           | 1.8   |
| learning_value_pred | 0.5   |
| ev_pred             | 5.6   |

### Step Metrics Rationale

Moderate file surface, zero behavioural risk (names and prose only), instant feedback from pytest.
`EV = 0.94 * 8.0 + 0.5 * 0.5 - 0.3 * 1.2 - 1.8 = 7.52 + 0.25 - 0.36 - 1.80 = 5.61`, reported 5.6.

---

## Step 6: Purge bean references from README and documentation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure prose edits in 11 markdown files; lint-docs provides the only gate; trivially revertible.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Remove harness bean IDs from `apps/sandbox-executor/README.md`,
  `apps/sandbox-executor/docs/hermetic_testing.md`, and the nine affected files under `docs/`, replacing them with
  target-native descriptions, per goal item 4.
- **Git branch:** same execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `apps/sandbox-executor/README.md`: strip the `(Bean NNNN)` annotations from the module tree (lines 17–27) and rewrite
   line 43's `Bean 0034 safety halt` to `human-only merge safety halt`, keeping descriptions informative.
2. `apps/sandbox-executor/docs/hermetic_testing.md`: rewrite its bean mention per the Step 1 inventory.
3. `docs/`: update git_flow.md, ledger_schema.md, budgets.md, sandbox/create_plan.md (heading
   `### Incremental plan artefact (Bean 0138)` → `### Incremental plan artefact`), sandbox/execute_plan.md,
   executor/execution_architecture_specification.md, executor/agent_credentials_requirements.md, audit_report.md and
   audit-report-2026-10.md. Where a doc references the renamed payload key or PR-body section from Step 3, update it to
   the new names so docs match code.
4. Do not touch `plans/`, `executions/`, `intents/` history, or `holon-knowledge/` (append-only ledgers and immutable
   records); those are harness-side artefacts, not target documentation.
5. Australian English throughout; preserve heading anchors where other docs link to them (grep for each changed
   heading's anchor before renaming it, and keep the anchor text stable when links exist).
6. Run `uv run task lint-docs` to validate formatting.

### Dependencies & Criticality

- **Depends on:** Step 3 (doc text must reflect the final identifier names)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md §3 (ledger immutability); intent constraint "keep the three append-only ledgers".
- **Potential failure modes for this step:** breaking an intra-doc anchor by renaming a heading; prettier/lint-docs
  reflow churn.
- **Guardrails and early‑abort checks:** anchor grep before heading changes; run lint-docs inside the step so formatting
  drift is fixed immediately.

### Success & Discard Criteria

- **Success:** `grep -riE 'bean'` over the listed docs returns zero hits; `uv run task lint-docs` passes.
- **Discard:** None realistic; if lint-docs cannot be satisfied after 1.5x cost_pred, halt and report.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.6   |
| impact_pred         | 8.0   |
| cost_pred           | 1.2   |
| learning_value_pred | 0.3   |
| ev_pred             | 6.5   |

### Step Metrics Rationale

Prose-only, low entropy, lint-gated. `EV = 0.96 * 8.0 + 0.5 * 0.3 - 0.3 * 0.6 - 1.2 = 7.68 + 0.15 - 0.18 - 1.20 = 6.45`,
reported 6.5.

---

## Step 7: Verification gate — zero-bean grep sweep, lint, lint-docs, full test suite, and check task

- **Sub‑intent recommendation:** NO
- **Reasoning:** The acceptance gate itself; cheap, deterministic, must run on the same branch as the edits.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove all three acceptance criteria on the execution branch before handing the result to the review
  stage and the human maintainer.
- **Git branch:** same execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Final sweep: case-insensitive grep for `bean` across `apps/sandbox-executor/` and `docs/` (excluding `plans/`,
   `executions/`, `holon-knowledge/`, caches). Required result: zero hits. Also grep specifically for `[BEAN`,
   `bean_0034`, `Referenced Beans` repo-wide outside history paths.
2. Run `uv run task lint` — must pass.
3. Run `uv run task lint-docs` — must pass.
4. Run the full suite exactly once: `uv run task test` (repository task; underlying runner is pytest per the intent
   constraint). Record pass/fail counts and pytest-reported time against the Step 1 baseline. No repeated runs: this
   intent measures correctness, not performance, so test-run discipline prescribes targeted-then-full-once.
5. Run `uv run task check` (named in acceptance criterion 3) if it is a defined task; if the task name does not exist in
   pyproject, record that lint + lint-docs + test cover its intent and note the discrepancy in the execution report
   rather than inventing a task.
6. Commit the work with a conventional message on the execution branch; raise the PR toward the parent plan/intent
   branch per git_flow Stage 3. Merging remains with the human maintainer (intent constraint; safety.md Invariant 4).

### Dependencies & Criticality

- **Depends on:** Steps 2–6
- **Is Bottleneck:** YES (defines plan success), p_success_pred 0.90 ≥ 0.6 so no sub-intent mandated.

### Safety & Constraint Considerations

- **Relevant rules:** safety.md Invariant 1 and 4 (no merge to main; human gate); intent constraints on exact commands.
- **Potential failure modes for this step:** lint reflow surfaces in edited files; full suite reveals an assertion on an
  edited help string missed in Step 4; a stray bean hit survives in a file type the sweep initially excluded.
- **Guardrails and early‑abort checks:** failures loop back to the owning step (grep hit → Steps 4–6; test failure →
  Steps 2–5) with at most two remediation cycles before halting and reporting; never silence a failure by weakening a
  test.

### Success & Discard Criteria

- **Success:** Zero-hit sweep; `uv run task lint`, `uv run task lint-docs`, `uv run task test` (and `uv run task check`
  where defined) all pass; branch committed and PR raised without any merge to main.
- **Discard:** More than two remediation cycles or cost beyond 1.5x cost_pred — halt, report partial state, leave the
  branch intact for maintainer inspection.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.2   |
| impact_pred         | 10.0  |
| cost_pred           | 1.2   |
| learning_value_pred | 1.0   |
| ev_pred             | 8.2   |

### Step Metrics Rationale

Verification is near-zero-entropy (read-only plus one commit) but carries the plan's residual failure mass: the full
suite exercises assertions far beyond the targeted files, so p 0.90. Learning value 1.0 from the calibration signal
(predicted vs actual sweep count and suite status).
`EV = 0.90 * 10.0 + 0.5 * 1.0 - 0.3 * 0.2 - 1.2 = 9.00 + 0.50 - 0.06 - 1.20 = 8.24`, reported 8.2.

---
