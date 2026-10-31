# Plan for I-1793438366-standardise-review-loop-agent-output-with-skill-consensus-template

- **Plan ID:** P-1793438381-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.4.12)
- **Created At:** 2026-10-31T09:19:41.961Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted verification of consensus template formats across container and harness
  tools.
- **Safety priority level:** standard
- **Priority Justification:** Scope is contained within review prompt templates, review consensus reporting logic, and
  test coverage; no destructive filesystem or git operations.

## Exploration

- **Proportion of steps that are exploratory:** 0.20
- **Justification:** Exploration is limited to investigating existing review output discrepancies and test fixtures
  before refactoring report generation and prompt templates.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.30  |
| impact_pred         | 82.00 |
| cost_pred           | 6.90  |
| learning_value_pred | 5.20  |
| ev_pred             | 73.21 |

### Strategy Rationale

The intent is accepted as-is (`ACCEPT_AS_IS`). The native containerised Review Loop Agent and consensus report generator
currently produce an older mechanical format (`## Holon Review: <title> (iteration N)` with unstructured lists) that
diverges from the structured consensus review template (`## ✅ Ensemble Consensus Review — APPROVED (3/3)` with bulleted
`**Verified:**` points, and `## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration {iteration})` with a
structured findings table). Standardising the template and Python generator aligns native flow output with production
quality standards and ensures consistent review artifacts across containerised execution, CLI flow, and harness tooling.

The plan decomposes this intent into 5 ordered, coherent steps:

- Step 1 updates the review consensus report generator (`sandbox_executor/review_report.py`) to render structured review
  consensus headers, structured `**Verified:**` bullet points for approved reviews, a structured findings table for
  changes-requested reviews, and human-only merge boundary notices, while preserving backward-compatible aliases.
- Step 2 updates the Review Loop agent prompt template (`holon-config/prompts/review_loop.template.md`) to instruct
  reviewer personas to synthesize verified architectural refactorings, backward compatibility, test execution counts,
  quality gates, complexity baselines, and 3-ledger provenance integrity, aligning the template output schema with the
  skill consensus format.
- Step 3 synchronises the project scaffolding constant `REVIEW_LOOP_TEMPLATE` in `sandbox_executor/scaffold.py` with the
  updated prompt template to guarantee that newly scaffolded projects and test runs receive identical review prompt
  instructions.
- Step 4 reviews and standardises containerised entrypoint logging, summary banners, and result serialization in
  `entrypoint/review_loop.py` to ensure alignment with consensus report status and iteration semantics.
- Step 5 updates unit and regression tests in `test_review_report.py` and `test_prompt_templates.py`, running affected
  test suites, followed by full test suite validation (`uv run task test`) and repository hygiene checks
  (`uv run task check`).

#### Metric Derivations & Bottleneck Identification

- **P(success):** `0.95`. Step 5 is the bottleneck step (`p_success_pred = 0.95`) because validating updated report
  schemas, template placeholder checks, and full regression test execution touches multiple test suites across
  `apps/sandbox-executor`. Overall plan success is `0.95`, reflecting high confidence in deterministic refactoring
  backed by comprehensive unit tests.
- **Entropy:** `1.30`. Aggregated as the exact sum of step entropies: `0.35 + 0.25 + 0.15 + 0.25 + 0.30 = 1.30`. This
  fits comfortably within the allocated entropy budget of `5.0`.
- **Impact:** `82.00`. Permanently standardises Stage 4 review consensus reporting across containerised execution, CLI
  flow, and harness tooling, providing clear, structured review artifacts and verified quality guarantees.
- **Cost:** `6.90`. Derived as the exact sum of step costs: `1.80 + 1.20 + 0.80 + 1.10 + 2.00 = 6.90`.
- **Learning Value:** `5.20`. Establishes consistent patterns for consensus report rendering, markdown table
  structuring, prompt template linting, and multi-persona synthesis.
- **Expected Value (EV):** `73.21`, calculated using `lambda = 0.3` and `mu = 0.5` from
  `holon-config/metrics/ev_config.json`:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.95 * 82.00 + 0.5 * 5.20 - 0.3 * 1.30 - 6.90 = 77.90 + 2.60 - 0.39 - 6.90 = 73.21`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md#1` (Python 3.13 runtime, uv workspace dependencies; no external packages added)
  - `holon-config/world/ruleset.md#2` (PEP 8 compliance, typing guidelines, docstring requirements)
  - `holon-config/world/ruleset.md#3` (Testing with pytest 9.1.1, test declarations in plan)
  - `holon-config/world/constraints.md#1` (Git branch isolation and commit boundaries)
  - `holon-config/world/constraints.md#3` (Ledger immutability; append-only action shapes)
  - `docs/safety.md#invariant-4` (Human review is the final gate; Bean 0034 human-only PR merging boundary strictly
    enforced)
  - `.agents/AGENTS.md` (Use `uv run pytest`, single root virtual environment, `uv run task check`, `uv run task test`,
    no throwaway scripts)
- **Potential violations or edge cases:**
  - Breaking existing consumers or tests that expect legacy report title prefix `## Holon Review:` instead of the
    consensus status headers.
  - Introducing unescaped or unmatched placeholder braces in `review_loop.template.md` that fail
    `test_prompt_templates.py`.
  - Inconsistency between `holon-config/prompts/review_loop.template.md` and `scaffold.py`'s embedded
    `REVIEW_LOOP_TEMPLATE`.
  - Malformed markdown table formatting when reviewer findings contain pipes or newlines.
- **Mitigations built into the plan:**
  - Step 1 preserves `build_pr_review_consensus_report` as a backward-compatible alias and safely sanitises table cell
    contents with `_inline`.
  - Step 2 and Step 3 ensure all template modifications are synchronised across the prompt file and scaffolding
    constant, adhering to placeholder rules.
  - Step 5 updates existing assertions and adds new targeted unit tests for both approved and changes-requested
    formatting.
- **Residual risk accepted (and why):** Minimal residual risk of external consumers expecting exact legacy markdown
  strings; accepted because standardisation conforms to the official skill consensus format and internal callers are
  fully covered by tests.
- **Allocated Entropy Budget:** 5.0
- **Predicted Plan Entropy:** 1.30
- **Budget Compliance:** The strategy fits within budget (`1.30 <= 5.0`).

## Plan Description & Strategy

This plan standardises the Stage 4 Review Loop Agent output, review prompt template (`review_loop.template.md`), and
consensus report generator (`sandbox_executor/review_report.py`) in `holon-agentic-coder` to conform to the skill
consensus template (`APPROVED (3/3)` with structured verified points and human merge notice), ensuring consistent review
artifacts across containerised execution and harness tools.

Currently, `build_review_consensus_report` outputs an older mechanical header (`## Holon Review: <title> (iteration N)`)
and an unformatted bullet list of findings regardless of verdict. The skill consensus template requires:

1. Approved reviews formatted with `## ✅ Ensemble Consensus Review — APPROVED ({configured}/{configured})`, evaluation
   summary, structured `**Verified:**` bullet points, and the Bean 0034 human-only merge boundary notice.
2. Changes-requested reviews formatted with
   `## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration {iteration})` followed by a structured findings
   table.
3. Review prompt templates instructing reviewer personas to synthesize verified architectural refactorings, backward
   compatibility, test execution counts, quality gates, complexity baselines, and 3-ledger provenance integrity.
4. Preserving backward-compatible aliases for `build_pr_review_consensus_report` and associated parameters.

The plan executes across 5 focused steps:

1. Update `sandbox_executor/review_report.py` to format approved and changes-requested consensus reports conforming to
   the skill consensus template.
2. Update `holon-config/prompts/review_loop.template.md` with persona synthesis instructions and updated output schema.
3. Synchronise `apps/sandbox-executor/src/sandbox_executor/scaffold.py` to keep `REVIEW_LOOP_TEMPLATE` aligned.
4. Verify and align `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py` summary outputs and result
   serialization.
5. Update unit tests in `test_review_report.py` and `test_prompt_templates.py`, and run the full test suite and quality
   gates.

---

## Step 1: Update Consensus Report Generator with Structured Review Formatting

- **Sub‑intent recommendation:** NO
- **Reasoning:** Modifying report rendering logic in a single module (`review_report.py`) with low risk, localized
  scope, and clear acceptance criteria.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement verdict-driven consensus report formatting in `sandbox_executor/review_report.py` to render
  `## ✅ Ensemble Consensus Review — APPROVED ({configured}/{configured})` with structured `**Verified:**` bullet points
  for approved reviews, and `## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration {iteration})` with a
  structured findings table for changes-requested reviews, preserving backward compatibility aliases.
- **Git branch:**
  `I-1793438366-standardise-review-loop-agent-output-with-skill-consensus-template/P-1793438381-antigravity-agent-gemini-3.8-flash-medium/E-consensus-report-generator/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/review_report.py`:
   - Inspect the verdict and approval status from `consensus_data` (e.g., `consensus_data.get("ensemble_verdict")` and
     `consensus_data.get("approved")`).
   - Define a helper to format structured findings into a markdown table
     (`| Severity | Location | Reviewer | Summary |`) for changes-requested reviews, ensuring that summaries and paths
     are properly sanitized with `_inline` to avoid broken table markup.
   - For approved reviews (`verdict == "APPROVED"`):
     - Format report header as: `## ✅ Ensemble Consensus Review — APPROVED ({configured}/{configured})`.
     - Render evaluation summary including target PR/branch link, target commit, base branch, and iteration count.
     - Render structured `**Verified:**` bullet points synthesizing:
       - Architectural refactorings and code hygiene verified.
       - Backward compatibility and API surface stability maintained (including legacy function aliases).
       - Test execution counts and passing assertions verified (CI checks and unit test pass rates).
       - Quality gates and complexity baselines fully satisfied.
       - 3-ledger provenance integrity maintained across `intents.jsonl`, `plans.jsonl`, and `executions.jsonl`.
     - Render checks section including mergeability line and CI status line.
     - Render the Bean 0034 human-only PR merging boundary notice (`HUMAN_MERGE_NOTICE`).
   - For changes-requested reviews (`verdict != "APPROVED"`):
     - Format report header as: `## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration {iteration})`.
     - Render evaluation summary and reviewer status lines.
     - Render the structured findings table, or display `"No findings were reported."` if the findings list is empty.
     - Render checks section including mergeability line and CI status line.
   - Maintain the backward-compatible alias `build_pr_review_consensus_report = build_review_consensus_report` with
     identical parameter signatures (`repo_dir`, `target`, `consensus_data`, `reviewers`, `title`).
   - Update module docstring and function docstrings to reflect the new standardized skill consensus format.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2`, `docs/safety.md#invariant-4`, `docs/safety.md#invariant-5`.
- **Potential failure modes for this step:** Markdown formatting breaking when finding summaries contain pipes (`|`) or
  backticks; missing parameters in legacy callers.
- **Guardrails and early‑abort checks:** Sanitise finding summaries and paths via `_inline` escaping table delimiters;
  preserve default parameter values.

### Success & Discard Criteria

- **Success:** `build_review_consensus_report()` produces the required approved and changes-requested markdown
  structures with correct headers, tables, verified points, and notices.
- **Discard:** Rendering breaks existing parameter signatures or fails basic unit assertions.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.35  |
| impact_pred         | 82.00 |
| cost_pred           | 1.80  |
| learning_value_pred | 5.00  |
| ev_pred             | 79.32 |

### Step Metrics Rationale

- **Derivations:** `p_success_pred = 0.96`, `entropy_pred = 0.35`, `impact_pred = 82.00`, `cost_pred = 1.80`,
  `learning_value_pred = 5.00`.
- **EV Calculation:** `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.96 * 82.00 + 0.5 * 5.00 - 0.3 * 0.35 - 1.80 = 78.72 + 2.50 - 0.105 - 1.80 = 79.315 -> 79.32`.
- High confidence implementation of deterministic formatting with immediate unit test feedback.

---

## Step 2: Standardise Review Loop Prompt Template

- **Sub‑intent recommendation:** NO
- **Reasoning:** Template refinement in `holon-config/prompts/review_loop.template.md` without architectural risk;
  strictly constrained by template linting rules.
- **Step Type:** CONFIG
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Explicit persona synthesis instructions for architectural refactorings, backward
  compatibility, test execution counts, quality gates, complexity baselines, and 3-ledger provenance integrity produce
  higher-quality review consensus artifacts without causing template linting failures.
- **Learning target:** Verify that prompt guidelines enhance reviewer rigor while satisfying `test_prompt_templates.py`
  placeholder constraints.
- **Maximum acceptable cost for this learning:** 1.50

### Intent & Git Integration

- **Step Intent:** Update `holon-config/prompts/review_loop.template.md` to instruct reviewer personas to synthesize
  verified architectural refactorings, backward compatibility, test execution counts, quality gates, complexity
  baselines, and 3-ledger provenance integrity, aligning the prompt output schema with the skill consensus template.
- **Git branch:**
  `I-1793438366-standardise-review-loop-agent-output-with-skill-consensus-template/P-1793438381-antigravity-agent-gemini-3.8-flash-medium/E-prompt-template-standardisation/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `holon-config/prompts/review_loop.template.md`:
   - Under section `## 📤 Standard Review Output Schema`:
     - Update the approved report template example to use header:
       `## ✅ Ensemble Consensus Review — APPROVED ({configured}/{configured})`.
     - Update the changes-requested report template example to use header:
       `## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration {iteration})`.
     - Update the Evaluation Summary section to explicitly showcase structured `**Verified:**` bullet points
       synthesizing:
       - Architectural refactorings and code simplification.
       - Backward compatibility guarantees and interface stability.
       - Test execution counts and passing assertions.
       - Quality gates and complexity baselines.
       - 3-ledger provenance integrity across `intents.jsonl`, `plans.jsonl`, and `executions.jsonl`.
     - Update findings section in the changes-requested schema to reflect a structured markdown table.
   - Under section `## 👥 Comprehensive Dynamic Persona Registry & Focus Areas`:
     - Add explicit synthesis instructions directing specialist personas to verify architectural refactoring, backward
       compatibility, test counts, quality gates, complexity baselines, and 3-ledger provenance integrity.
   - Ensure all placeholder tokens (`{repo_dir}`, `{target}`, `{target_branch}`, `{max_iterations}`, `{iteration}`,
     etc.) remain properly formatted and closed to satisfy `test_prompt_templates.py`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2`, `apps/sandbox-executor/tests/test_prompt_templates.py`.
- **Potential failure modes for this step:** Introducing unmatched `{` or `}` braces causing template linting failures;
  breaking variable substitution in `review_ensemble.py`.
- **Guardrails and early‑abort checks:** Validate with `unmatched_placeholders` check from `test_prompt_templates.py`.

### Success & Discard Criteria

- **Success:** Prompt template contains updated persona instructions and consensus schemas with zero unmatched
  placeholders.
- **Discard:** Template linter detects unmatched placeholders or syntax defects.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.25  |
| impact_pred         | 80.00 |
| cost_pred           | 1.20  |
| learning_value_pred | 4.50  |
| ev_pred             | 79.38 |

### Step Metrics Rationale

- **Derivations:** `p_success_pred = 0.98`, `entropy_pred = 0.25`, `impact_pred = 80.00`, `cost_pred = 1.20`,
  `learning_value_pred = 4.50`.
- **EV Calculation:** `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.98 * 80.00 + 0.5 * 4.50 - 0.3 * 0.25 - 1.20 = 78.40 + 2.25 - 0.075 - 1.20 = 79.375 -> 79.38`.
- Clear textual updates with deterministic automated validation via pytest.

---

## Step 3: Synchronise Project Scaffolding Template

- **Sub‑intent recommendation:** NO
- **Reasoning:** Synchronisation of embedded string constant in `scaffold.py` with minimal risk and high predictability.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Synchronise `REVIEW_LOOP_TEMPLATE` in `apps/sandbox-executor/src/sandbox_executor/scaffold.py` with
  `holon-config/prompts/review_loop.template.md` so that new projects scaffold the standardised review template.
- **Git branch:**
  `I-1793438366-standardise-review-loop-agent-output-with-skill-consensus-template/P-1793438381-antigravity-agent-gemini-3.8-flash-medium/E-scaffold-synchronisation/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/scaffold.py`:
   - Locate the constant `REVIEW_LOOP_TEMPLATE`.
   - Update the string contents of `REVIEW_LOOP_TEMPLATE` to mirror the updated
     `holon-config/prompts/review_loop.template.md`.
   - Ensure proper string escaping for any characters that could interact with Python multiline string delimiters.
   - Verify that `init_project()` continues to write out the scaffold file accurately.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#1`, `holon-config/world/ruleset.md#2`.
- **Potential failure modes for this step:** Drift between `review_loop.template.md` and `scaffold.py`.
- **Guardrails and early‑abort checks:** Diff comparison between the template file and scaffolded constant.

### Success & Discard Criteria

- **Success:** `REVIEW_LOOP_TEMPLATE` in `scaffold.py` is identical to `holon-config/prompts/review_loop.template.md`.
- **Discard:** Scaffolding tests in `test_init.py` fail due to malformed constants.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.15  |
| impact_pred         | 75.00 |
| cost_pred           | 0.80  |
| learning_value_pred | 3.00  |
| ev_pred             | 74.91 |

### Step Metrics Rationale

- **Derivations:** `p_success_pred = 0.99`, `entropy_pred = 0.15`, `impact_pred = 75.00`, `cost_pred = 0.80`,
  `learning_value_pred = 3.00`.
- **EV Calculation:** `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.99 * 75.00 + 0.5 * 3.00 - 0.3 * 0.15 - 0.80 = 74.25 + 1.50 - 0.045 - 0.80 = 74.905 -> 74.91`.
- Near-zero risk step synchronising a Python string constant with a tested markdown template.

---

## Step 4: Align Review Loop Entrypoint and Flow Orchestration

- **Sub‑intent recommendation:** NO
- **Reasoning:** Localized review and verification of console summaries and outcome serialization in `review_loop.py`.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Ensure containerised entrypoint logging, summary banners, and result serialization in
  `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py` cleanly align with the updated review consensus
  formatting and Bean 0034 human halt notice.
- **Git branch:**
  `I-1793438366-standardise-review-loop-agent-output-with-skill-consensus-template/P-1793438381-antigravity-agent-gemini-3.8-flash-medium/E-entrypoint-alignment/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`:
   - Inspect console output formatting in `_fail_preparation` and summary banner rendering.
   - Confirm that consensus status display cleanly handles `APPROVED` and `CHANGES_REQUESTED`.
   - Ensure the `[BEAN 0034 SAFETY HALT]` console message and manual approval command instructions remain clear,
     accurate, and aligned with consensus reports.
   - Verify that `--result-file` output preserves the full consensus payload (`consensus`, `ensemble_verdict`,
     `ci_checks`, `reviewers`) for downstream harness consumption.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md#invariant-4`, `holon-config/world/constraints.md#1`.
- **Potential failure modes for this step:** Breaking exit code semantics or result JSON structure.
- **Guardrails and early‑abort checks:** Exit code must remain 0 only for an approved consensus; Bean 0034 notice must
  be preserved.

### Success & Discard Criteria

- **Success:** Entrypoint console summary and result file cleanly align with consensus review specifications.
- **Discard:** Regression in exit code behavior or JSON output schema.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.25  |
| impact_pred         | 78.00 |
| cost_pred           | 1.10  |
| learning_value_pred | 4.00  |
| ev_pred             | 76.49 |

### Step Metrics Rationale

- **Derivations:** `p_success_pred = 0.97`, `entropy_pred = 0.25`, `impact_pred = 78.00`, `cost_pred = 1.10`,
  `learning_value_pred = 4.00`.
- **EV Calculation:** `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.97 * 78.00 + 0.5 * 4.00 - 0.3 * 0.25 - 1.10 = 75.66 + 2.00 - 0.075 - 1.10 = 76.485 -> 76.49`.
- Straightforward verification of console banners and JSON serialization without invasive logic changes.

---

## Step 5: Update Test Assertions and Verify Complete Test Suite

- **Sub‑intent recommendation:** NO
- **Reasoning:** Test verification step covering affected unit tests, prompt template linter, and full suite validation
  with standard testing tools.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update unit test assertions in `test_review_report.py` and `test_prompt_templates.py` to validate the
  new consensus report headers, structured findings table, `**Verified:**` bullet points, and Bean 0034 notice; run full
  test suite and task checks.
- **Git branch:**
  `I-1793438366-standardise-review-loop-agent-output-with-skill-consensus-template/P-1793438381-antigravity-agent-gemini-3.8-flash-medium/E-test-suite-verification/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_review_report.py`:
   - Update tests checking report headers to assert:
     - Approved consensus report starts with `## ✅ Ensemble Consensus Review — APPROVED ({configured}/{configured})`.
     - Changes-requested report starts with
       `## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration {iteration})`.
   - Update `test_report_has_no_commit_derived_reviewer_ids_or_fabricated_sections` to allow the new `**Verified:**`
     points while continuing to guard against fabricated hash reviewer IDs or fake commit identifiers.
   - Add unit test verifying that findings in changes-requested reviews are rendered in a structured markdown table
     (`| Severity | Location | Reviewer | Summary |`).
   - Add unit test verifying that approved reviews render structured `**Verified:**` bullet points synthesizing
     architectural refactoring, backward compatibility, test execution, quality gates, complexity baselines, and
     3-ledger provenance integrity.
   - Verify `build_pr_review_consensus_report` backward compatibility alias remains tested and returns identical content
     to `build_review_consensus_report`.
2. In `apps/sandbox-executor/tests/test_prompt_templates.py`:
   - Run `test_shipped_templates_have_no_unmatched_placeholders` to confirm zero unmatched placeholder defects across
     `holon-config/prompts/`.
3. In workspace execution:
   - Run targeted test suites:
     `uv run pytest apps/sandbox-executor/tests/test_review_report.py apps/sandbox-executor/tests/test_prompt_templates.py`.
   - Run full unit test suite once: `uv run task test`.
   - Run repository hygiene checks: `uv run task check`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4
- **Is Bottleneck:** YES (Final gate validating the entire intent's acceptance criteria)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3`, `.agents/AGENTS.md`.
- **Potential failure modes for this step:** Uncovered edge cases in markdown table parsing or prompt placeholder linter
  regressions.
- **Guardrails and early‑abort checks:** Execute targeted pytest first before workspace-wide task runs; abort
  immediately if regressions emerge.

### Success & Discard Criteria

- **Success:** All targeted review report and prompt tests pass; full unit test suite passes with 100% pass rate; lint
  and doc checks pass cleanly.
- **Discard:** Pytest failures cannot be resolved within budget or break acceptance criteria.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.30  |
| impact_pred         | 85.00 |
| cost_pred           | 2.00  |
| learning_value_pred | 5.50  |
| ev_pred             | 81.41 |

### Step Metrics Rationale

- **Derivations:** `p_success_pred = 0.95`, `entropy_pred = 0.30`, `impact_pred = 85.00`, `cost_pred = 2.00`,
  `learning_value_pred = 5.50`.
- **EV Calculation:** `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.95 * 85.00 + 0.5 * 5.50 - 0.3 * 0.30 - 2.00 = 80.75 + 2.75 - 0.090 - 2.00 = 81.41`.
- Bottleneck step because it validates the full integration and runs the workspace test suite.
