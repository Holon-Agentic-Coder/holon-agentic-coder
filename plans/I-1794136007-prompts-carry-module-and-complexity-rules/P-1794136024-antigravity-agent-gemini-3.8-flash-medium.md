# Plan for I-1794136007-prompts-carry-module-and-complexity-rules

- **Plan ID:** P-1794136024-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.5.3)
- **Created At:** 2026-11-08T11:07:04.863Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None
- **Exploration stance:** balanced with focus on template consistency, strict adherence to Australian English, and
  comprehensive test coverage.
- **Safety priority level:** standard
- **Priority Justification:** All proposed modifications are strictly confined to prompt templates in
  `holon-config/prompts/`, twin template constants in `apps/sandbox-executor/src/sandbox_executor/scaffold.py`, and test
  assertions in `apps/sandbox-executor/tests/test_prompt_templates.py` without modifying sandbox policies, credential
  mechanisms, or system security invariants (`docs/safety.md#Sandboxing-model`).

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Deterministic prompt template updates, in-code twin synchronisation, and regression test authoring
  with clear constraints and acceptance criteria.

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

The intent is accepted as is (`ACCEPT_AS_IS`). Historical code growth analysis reveals that several core modules grew
disproportionately large because agent prompt templates did not specify architectural boundaries, module ownership,
complexity ceilings, or file size limits. Consequently, agents placed new behaviour into existing entrypoints or
monolithic files rather than creating dedicated, cohesive modules.

To curb architectural debt and monolithic sprawl at the source, this strategy embeds explicit guidelines into the three
authoring stage prompt templates:

1. **Planner & Executor Prompt Templates (`planner.template.md`, `executor.template.md`, and `scaffold.py` twins):**
   Introduce a dedicated "Code placement" directive mandating that new behaviour belongs in the module that owns the
   data (or a new module); one module per stage, command, runner or provider; function complexity strictly respects
   repository ruff thresholds (`C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`, `PLR0915 <= 50`) without inline `# noqa`
   suppressions; domain language names (`intent`, `plan`, `execution`, `review`, `calibration`, `ledger`) are required;
   and no new imports from entrypoints into the host flow engine are permitted. In `planner.template.md`, additionally
   specify touch sets at module granularity and establish a preference for adding focused modules over expanding
   existing files beyond ~400 lines.
2. **Reviewer Loop Prompt Template (`review_loop.template.md` and `scaffold.py` twin):** Add a concise bullet
   instructing reviewers to flag file growth over 1,000 lines or new inline complexity suppressions (`# noqa`) as
   Important findings, and to verify module data ownership with no new entrypoint-to-engine imports, while preserving
   line count constraints (`< 288` lines) and loop invariants.
3. **Rigorous Verification (`test_prompt_templates.py`):** Author comprehensive unit tests asserting presence of all
   rule phrases, module touch-set granularity, 1,000-line growth flagging, placeholder hygiene, and line count
   boundaries.

Overall plan metrics are derived as follows:

- **Success Probability (`p_success_pred = 0.96`):** Governed by the bottleneck verification step, Step 3
  (`p_success_pred = 0.96`), ensuring template placeholder integrity, linting compliance, and regression-free test
  passes.
- **Plan Entropy (`entropy_pred = 2.10`):** Sum of step-level entropies: `Delta_S = 0.70 + 0.60 + 0.80 = 2.10`, fitting
  safely within the allocated entropy budget of `15.0`.
- **Impact (`impact_pred = 85.0`):** Enforces upstream prevention of code bloat and complexity across all future
  automated planning, execution, and review cycles.
- **Cost (`cost_pred = 4.00`):** Sum of estimated execution costs across template updates, twin synchronisation, and
  test execution: `Cost = 1.20 + 1.00 + 1.80 = 4.00`.
- **Learning Value (`learning_value_pred = 4.00`):** Validates the effectiveness of encoding architectural constraints
  directly into agent role prompts.
- **Expected Value (`ev_pred = 78.97`):** Derived using the standard formula (`lambda = 0.3`, `mu = 0.5`):
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.96 * 85.0 + 0.5 * 4.00 - 0.3 * 2.10 - 4.00 = 81.60 + 2.00 - 0.63 - 4.00 = 78.97`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md#1-git-flow--branch-constraints`: All changes developed on the intent branch
    `I-1794136007-prompts-carry-module-and-complexity-rules`; merge to `main` remains reserved for the human maintainer.
  - `holon-config/world/constraints.md#2-sandbox-containment-tiers`: Low predicted entropy (`2.10 < 10.0`) qualifies for
    process sandbox execution.
  - `holon-config/world/constraints.md#3-ledger-immutability`: The three append-only ledgers (`intents.jsonl`,
    `plans.jsonl`, `executions.jsonl`) remain strictly untouched and append-only.
  - `holon-config/world/ruleset.md#1-runtime--environment-specification`: Strictly Python `==3.13.*` standard library;
    no new third-party dependencies.
  - `holon-config/world/ruleset.md#2-coding-conventions--standards`: Adhere to PEP 8, Australian English spelling
    (`behaviour`, `synchronised`, `initialise`, `artefact`), intention-revealing domain naming, and comprehensive type
    annotations.
  - `holon-config/world/ruleset.md#3-testing-constraints`: Tests run strictly through `uv run pytest`; existing test
    assertions remain intact.
  - Prompt cleanliness constraint: No harness paths, internal test fixtures, or tracking IDs in prompts or shipped
    templates.
- **Potential violations or edge cases:**
  - Unmatched curly braces introduced into markdown templates that cause
    `test_shipped_templates_have_no_unmatched_placeholders()` to fail.
  - Line count of `review_loop.template.md` exceeding `FORMER_REVIEW_PROMPT_LINES` (`288` lines).
  - Divergence between `holon-config/prompts/*.template.md` and scaffold twin strings in `sandbox_executor/scaffold.py`.
  - Accidental omission of existing required phrases in `REVIEW_INVARIANT_PHRASES` or `TEST_RUN_RULE_PHRASES`.
- **Mitigations built into the plan:**
  - Avoid literal unescaped curly braces in template prose; use clean formatting that does not mimic template variable
    placeholders.
  - Formulate concise, multi-clause instructions to keep the review loop template under 230 lines (well below the 288
    line ceiling).
  - Explicitly update both the standalone markdown template files and the twin constants in `scaffold.py` in tandem.
  - Extend unit test assertions in `test_prompt_templates.py` to continuously verify synchronisation and invariance
    across all prompt templates.
- **Residual risk accepted (and why):**
  - Minimal residual risk of prompt token expansion; accepted because concise phrasing minimises token overhead while
    delivering high-value architectural boundaries.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 2.10
- **Budget Compliance:** The strategy fits within budget (`2.10 <= 15.0`).

## Plan Description & Strategy

This plan equips the planning, execution, and review prompt templates with concrete rules governing code placement, file
sizing, and algorithmic complexity.

The implementation is structured into three discrete, verifiable steps:

1. **Planner and Executor Templates:** Augment `holon-config/prompts/planner.template.md`,
   `holon-config/prompts/executor.template.md`, and their twins `PLANNER_TEMPLATE` and `EXECUTOR_TEMPLATE` in
   `apps/sandbox-executor/src/sandbox_executor/scaffold.py` with code placement, domain naming, complexity limits, and
   no-entrypoint-to-engine-import rules, including module-granularity touch sets and 400-line modularity preferences in
   the planner prompt.
2. **Reviewer Prompt Template:** Augment `holon-config/prompts/review_loop.template.md` and `REVIEW_LOOP_TEMPLATE` in
   `apps/sandbox-executor/src/sandbox_executor/scaffold.py` with instructions to flag file growth past 1,000 lines or
   inline complexity suppressions as Important findings and check module data ownership, staying well below the 288-line
   limit.
3. **Unit Tests & Quality Gate Verification:** Author comprehensive assertions in
   `apps/sandbox-executor/tests/test_prompt_templates.py` validating that rendered prompts carry all required directives
   and that placeholder hygiene and linting checks pass cleanly.

---

## Step 1: Add Code Placement, Complexity, and Touch Set Rules to Planner and Executor Templates

- **Sub‑intent recommendation:** NO
- **Reasoning:** Cohesive modification of prompt text in markdown templates and corresponding scaffold Python constants
  with low entropy and deterministic impact.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add concise code placement, domain naming, ruff complexity limits, module-granularity touch sets, and
  file sizing rules to `planner.template.md`, `executor.template.md`, and their twins in `scaffold.py`.
- **Git branch:**
  `I-1794136007-prompts-carry-module-and-complexity-rules/P-1794136024-antigravity-agent-gemini-3.8-flash-medium/E-planner-executor-code-placement`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `holon-config/prompts/planner.template.md`:
   - Under an appropriate section (e.g., following the Write Protocol or Markdown Hygiene section), add a concise **Code
     Placement & Sizing** section.
   - Specify:
     - New behaviour belongs in the module that owns the data (or in a new focused module).
     - Maintain one module per stage, command, runner or provider.
     - Functions must stay within repository ruff complexity limits (`C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`,
       `PLR0915 <= 50`) without adding inline `# noqa` suppressions.
     - Use intention-revealing names drawn from the domain language (`intent`, `plan`, `execution`, `review`,
       `calibration`, `ledger`).
     - Add no new imports from entrypoints into the host flow engine.
     - The plan must list its touch set at module granularity and prefer adding focused modules over growing existing
       files past ~400 lines.
2. In `holon-config/prompts/executor.template.md`:
   - Under **Critical Execution Directives**, add a concise **Code placement** directive.
   - Instruct the executing agent that:
     - New behaviour goes in the module that owns the data (or a new module).
     - Maintain one module per stage, command, runner or provider.
     - Functions stay within repository ruff complexity limits (`C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`,
       `PLR0915 <= 50`) without adding inline `# noqa` suppressions.
     - Use intention-revealing names in the domain language (`intent`, `plan`, `execution`, `review`, `calibration`,
       `ledger`).
     - Add no new imports from entrypoints into the host flow engine.
3. In `apps/sandbox-executor/src/sandbox_executor/scaffold.py`:
   - Synchronise `PLANNER_TEMPLATE` to mirror the code placement, module-granularity touch set, 400-line sizing
     preference, and complexity rules added to `planner.template.md`.
   - Synchronise `EXECUTOR_TEMPLATE` to mirror the code placement and complexity directives added to
     `executor.template.md`.
4. Ensure all newly added text adheres strictly to Australian English (`behaviour`, `synchronised`, `initialise`,
   `artefact`) and contains no unescaped brace sequences that could interfere with placeholder substitutions.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2-coding-conventions--standards`,
  `docs/safety.md#Sandboxing-model`.
- **Potential failure modes for this step:**
  - Introducing unintended placeholder braces like `{C901}` that break
    `test_shipped_templates_have_no_unmatched_placeholders()`.
  - Subtle text drift between standalone markdown files and their scaffold string twins in `scaffold.py`.
- **Guardrails and early‑abort checks:**
  - Verify that no bare single braces enclose complexity identifiers; format limits cleanly as plain text or backticked
    expressions.

### Success & Discard Criteria

- **Success:** `planner.template.md`, `executor.template.md`, `PLANNER_TEMPLATE`, and `EXECUTOR_TEMPLATE` consistently
  contain the code placement, ruff complexity, domain naming, no-entrypoint-to-engine-import, touch-set granularity, and
  file sizing rules.
- **Discard:** Discard if prompt modifications break placeholder expansion or introduce syntax errors in `scaffold.py`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.70  |
| impact_pred         | 82.0  |
| cost_pred           | 1.20  |
| learning_value_pred | 3.00  |
| ev_pred             | 80.45 |

### Step Metrics Rationale

High success probability (`0.98`) and low entropy (`0.70`) as the modifications are purely declarative prompt
instructions and synchronised string constants.
`EV = 0.98 * 82.0 + 0.5 * 3.00 - 0.3 * 0.70 - 1.20 = 80.36 + 1.50 - 0.21 - 1.20 = 80.45`.

---

## Step 2: Add File Growth and Complexity Review Gates to Review Loop Templates

- **Sub‑intent recommendation:** NO
- **Reasoning:** Targeted addition of a concise review rule into reviewer prompt templates while maintaining strict line
  count and loop invariant constraints.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add a concise review gate bullet to `holon-config/prompts/review_loop.template.md` and
  `REVIEW_LOOP_TEMPLATE` in `scaffold.py` instructing reviewers to flag file growth over 1,000 lines or inline
  complexity suppressions as Important findings and check module data ownership with no new entrypoint-to-engine
  imports.
- **Git branch:**
  `I-1794136007-prompts-carry-module-and-complexity-rules/P-1794136024-antigravity-agent-gemini-3.8-flash-medium/E-reviewer-prompt-gates`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `holon-config/prompts/review_loop.template.md`:
   - Under the **Reviewer turn contract** section (or the **Loop invariants** section), add a concise directive bullet.
   - Instruct reviewers to:
     - Flag growth of any file over 1,000 lines or new inline complexity suppressions (`# noqa`) as an `Important`
       finding.
     - Check that new behaviour lives in the module that owns the data with no new entrypoint-to-engine imports.
   - Ensure the total line count of `review_loop.template.md` remains well below `FORMER_REVIEW_PROMPT_LINES = 288`
     (currently ~211 lines).
   - Ensure all invariant phrases in `REVIEW_INVARIANT_PHRASES` and the exact structure in `posted_shape_block` and
     `reviewer_data_section` remain completely intact.
2. In `apps/sandbox-executor/src/sandbox_executor/scaffold.py`:
   - Update `REVIEW_LOOP_TEMPLATE` with the identical bullet so that markdown and in-code twin strings remain strictly
     synchronised.
3. Validate that Australian English is maintained throughout and no tracking IDs or harness paths are mentioned.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2-coding-conventions--standards`,
  `docs/safety.md#Human-review-boundaries`.
- **Potential failure modes for this step:**
  - Expanding the prompt beyond the 288-line threshold tested by
    `test_review_prompt_is_shorter_and_has_no_registry_matrix_or_legend()`.
  - Inadvertently modifying or disrupting `REVIEW_INVARIANT_PHRASES` or posted review shape sections.
- **Guardrails and early‑abort checks:**
  - Formulate the directive as a single, high-density bullet of 3-4 lines maximum.
  - Verify line count directly against `FORMER_REVIEW_PROMPT_LINES` (288).

### Success & Discard Criteria

- **Success:** Both `review_loop.template.md` and `scaffold.REVIEW_LOOP_TEMPLATE` instruct reviewers to flag file growth
  over 1,000 lines, flag inline complexity suppressions as Important, and check module data ownership with no new
  entrypoint-to-engine imports, while total line count stays well under 288 lines.
- **Discard:** Discard if line count exceeds 288 or if existing review loop invariants are modified.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.60  |
| impact_pred         | 80.0  |
| cost_pred           | 1.00  |
| learning_value_pred | 3.00  |
| ev_pred             | 77.92 |

### Step Metrics Rationale

Very high predictability (`0.97`) and minimal entropy (`0.60`) given clear line-count ceilings and specific wording
requirements. `EV = 0.97 * 80.0 + 0.5 * 3.00 - 0.3 * 0.60 - 1.00 = 77.60 + 1.50 - 0.18 - 1.00 = 77.92`.

---

## Step 3: Extend Unit Tests in `test_prompt_templates.py` and Verify Quality Gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard unit test extension and suite verification acting as the definitive quality gate for the
  entire plan.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Author unit tests in `apps/sandbox-executor/tests/test_prompt_templates.py` verifying that rendered
  planner, executor, and review prompts carry code placement, module-granularity touch-set, and 1,000-line growth rules,
  and verify that all test suite and lint tasks pass cleanly.
- **Git branch:**
  `I-1794136007-prompts-carry-module-and-complexity-rules/P-1794136024-antigravity-agent-gemini-3.8-flash-medium/E-test-prompt-rules`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_prompt_templates.py`:
   - Define tuple `CODE_PLACEMENT_RULE_PHRASES` containing key normalised phrases:
     - `"module that owns the data"`
     - `"one module per stage, command, runner or provider"`
     - `"c901 <= 10"`
     - `"plr0912 <= 12"`
     - `"plr0913 <= 5"`
     - `"plr0915 <= 50"`
     - `"without adding inline '# noqa' suppressions"`
     - `"intention-revealing names in the domain language"`
     - `"no new imports from entrypoints into the host flow engine"`
   - Add unit test `test_rendered_templates_carry_code_placement_rule()` parameterized over
     `[rendered_planner, rendered_executor]` asserting that every phrase in `CODE_PLACEMENT_RULE_PHRASES` is present in
     the normalised rendered text.
   - Add unit test `test_rendered_planner_carries_touch_set_and_file_sizing_rules()` asserting that `rendered_planner()`
     contains normalised phrases for:
     - `"touch set at module granularity"`
     - `"prefers adding focused modules over growing existing files past ~400 lines"` (or equivalent exact wording from
       the prompt).
   - Add unit test `test_review_templates_carry_file_growth_and_complexity_gates()` parameterized over
     `REVIEW_TEMPLATES` asserting that both markdown and scaffold templates instruct reviewers to:
     - flag growth of any file over 1,000 lines as Important
     - flag new inline complexity suppressions as Important
     - check that new behaviour lives in the module that owns the data with no new entrypoint-to-engine imports.
   - Ensure `test_review_prompt_is_shorter_and_has_no_registry_matrix_or_legend()` continues to pass with line count
     strictly less than `FORMER_REVIEW_PROMPT_LINES` (288).
   - Ensure `test_shipped_templates_have_no_unmatched_placeholders()` passes with no defects reported.
2. Run tests and linting gates strictly from the repository root:
   - `uv run pytest apps/sandbox-executor/tests/test_prompt_templates.py`
   - `uv run task check` (runs `uv run task lint` and `uv run task lint-docs`)
   - `uv run task test`
3. Verify that all ledgers remain append-only and that no temporary or scratch files remain in the workspace.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (Acts as the definitive acceptance gate verifying all criteria)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3-testing-constraints`, `docs/safety.md#Sandboxing-model`.
- **Potential failure modes for this step:**
  - Normalisation discrepancies between test assertions and template whitespace rewrapping.
  - Prettier formatting altering markdown tables or bullet wrapping during `uv run task lint-docs`.
- **Guardrails and early‑abort checks:**
  - Use `normalised()` helper to collapse whitespace and lower-case text before substring checking.
  - Run `uv run task format` if needed followed by `uv run task check` to verify formatting stability in a single pass.

### Success & Discard Criteria

- **Success:** All unit tests in `test_prompt_templates.py` pass, confirming all prompt templates and twins carry code
  placement, touch set, and file-growth rules, and `uv run task check` and `uv run task test` pass with zero failures.
- **Discard:** Discard if any test failure cannot be resolved without violating prompt line length or invariant
  constraints.

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

High success probability (`0.96`) and modest cost (`1.80`) reflecting comprehensive verification and automated lint gate
checks. `EV = 0.96 * 85.0 + 0.5 * 4.00 - 0.3 * 0.80 - 1.80 = 81.60 + 2.00 - 0.24 - 1.80 = 81.56`.
