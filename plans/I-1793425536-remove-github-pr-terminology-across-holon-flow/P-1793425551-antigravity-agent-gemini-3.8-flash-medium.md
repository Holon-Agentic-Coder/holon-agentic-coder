# Plan for I-1793425536-remove-github-pr-terminology-across-holon-flow

- **Plan ID:** P-1793425551-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.4.12)
- **Created At:** 2026-10-31T05:45:52.190Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with focus on precise terminology decoupling across CLI, flow engine, report
  generators, and prompt templates without breaking GitHub transport adapters.
- **Safety priority level:** standard
- **Priority Justification:** The intent standardizes core engine terminology, CLI help, logs, and prompt scaffolding to
  be platform-agnostic (e.g. 'Review Loop', 'review consensus', 'change branch', 'Change Diff'). It preserves GitHub CLI
  interactions as a remote transport adapter and touches no privileged security boundaries or sandbox policies.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The intent requires deterministic, mechanical terminology updates across CLI definitions, engine
  logging, docstrings, report rendering, and role templates, accompanied by regression test updates.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 1.85  |
| impact_pred         | 70.0  |
| cost_pred           | 5.00  |
| learning_value_pred | 4.00  |
| ev_pred             | 62.25 |

### Strategy Rationale

The intent is accepted as-is (`ACCEPT_AS_IS`). Holon Flow is Git-native, branch-based, and platform-agnostic. However,
throughout CLI help text, flow engine logging, review consensus reports, and role prompts, Stage 4 has historically been
described using GitHub-specific phrasing such as "PR review loop", "PR review consensus", "PR diff", "PR description",
and "PR merging". Decoupling core engine concepts from GitHub terminology preserves clean architectural boundaries while
retaining GitHub CLI (`gh`) interactions strictly as a remote transport adapter.

The plan decomposes this intent into 5 ordered, coherent steps:

- Step 1 updates CLI help text and argument descriptions in `flow.py`, `review.py`, and `entrypoint/review_loop.py`,
  replacing `--skip-review` help text with platform-agnostic phrasing.
- Step 2 standardizes the review consensus report generator (`review_report.py`) and module docstrings to refer to Stage
  4 review consensus, rendering platform-agnostic report headers while preserving backward-compatible function aliases.
- Step 3 updates review ensemble prompt builders (`review_ensemble.py`), scaffolding (`scaffold.py`), and the review
  loop template (`review_loop.template.md`) to standardize on 'change diff' and 'change description' while maintaining
  Bean 0034 safety boundaries and GitHub remote command guardrails.
- Step 4 standardizes flow engine logging, console outputs, and discrete action records in `flow.py` and
  `entrypoint/review_loop.py` to display "Stage 4: Review Loop" and "Review consensus".
- Step 5 updates affected test assertions across `test_flow.py`, `test_review_report.py`, `test_review_ensemble.py`, and
  `test_ledger_lock.py`, running targeted pytest suites, the full unit test suite (`uv run task test`), and code hygiene
  checks (`uv run task check`).

#### Metric Derivations & Bottleneck Identification

- **P(success):** `0.94`. Step 4 is the bottleneck step (`p_success_pred = 0.94`) because modifying flow engine logs,
  action records, and console summaries across `flow.py` touches multiple orchestration paths and ledger event emission
  points. Overall plan success is `0.94`, reflecting high confidence in deterministic refactoring backed by
  comprehensive unit tests.
- **Entropy:** `1.85`. Aggregated as the exact sum of step entropies: `0.20 + 0.35 + 0.45 + 0.50 + 0.35 = 1.85`. This
  fits comfortably within the allocated entropy budget of `5.0`.
- **Impact:** `70.0`. Permanently decouples core engine concepts from vendor-specific terminology, establishing
  platform-agnostic architecture across CLI, engine logs, consensus reports, and agent prompts.
- **Cost:** `5.00`. Derived as the exact sum of step costs: `0.60 + 0.80 + 1.10 + 1.30 + 1.20 = 5.00`.
- **Learning Value:** `4.00`. Deepens architectural knowledge regarding clean separation between core Git workflows and
  remote transport adapters.
- **Expected Value (EV):** `62.25`, calculated using `lambda = 0.3` and `mu = 0.5` from
  `holon-config/metrics/ev_config.json`:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.94 * 70.0 + 0.5 * 4.00 - 0.3 * 1.85 - 5.00 = 65.80 + 2.00 - 0.555 - 5.00 = 62.245 -> 62.25`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md#1` (Python 3.13 runtime, uv workspace dependencies; no new dependencies added)
  - `holon-config/world/ruleset.md#2` (PEP 8 compliance, typing guidelines, docstring requirements)
  - `holon-config/world/ruleset.md#3` (Testing with pytest 9.1.1, test declarations in plan)
  - `holon-config/world/constraints.md#1` (Git branch isolation and commit boundaries)
  - `holon-config/world/constraints.md#3` (Ledger immutability; append-only action shapes)
  - `docs/safety.md#invariant-4` (Human review is the final gate; Bean 0034 human-only PR merging boundary strictly
    enforced)
  - `.agents/AGENTS.md` (Use `uv run pytest`, single root virtual environment, `uv run task check`, `uv run task test`,
    no throwaway scripts)
- **Potential violations or edge cases:**
  - Accidentally breaking GitHub transport adapter commands (e.g. `gh pr view`, `gh pr review`) when removing PR
    terminology from core modules.
  - Introducing template placeholder mismatches in `review_loop.template.md` that would trigger failures in
    `test_prompt_templates.py`.
  - Breaking external scripts or existing tests that expect exact legacy action strings or report header formats.
- **Mitigations built into the plan:**
  - Step 1 and Step 4 explicitly preserve GitHub CLI transport adapter execution paths in `flow.py`, `review_target.py`,
    and `review_loop.py` when communicating with GitHub remotes.
  - Step 2 retains `build_pr_review_consensus_report` as a backward-compatible alias for
    `build_review_consensus_report`.
  - Step 3 ensures prompt template variables and placeholder linter constraints pass cleanly.
  - Step 5 verifies all test suites and repository tasks (`uv run task check`, `uv run task test`) pass cleanly.
- **Residual risk accepted (and why):** Minimal residual risk of third-party log consumers parsing exact legacy action
  strings in `executions.jsonl`; accepted because Holon Flow is Git-native and internal to this repository, and action
  schemas remain intact.
- **Allocated Entropy Budget:** 5.0
- **Predicted Plan Entropy:** 1.85
- **Budget Compliance:** The strategy fits within budget (`1.85 <= 5.0`).

## Plan Description & Strategy

This plan eliminates GitHub-specific PR terminology across CLI help text, flow engine logging, review consensus reports,
and role prompts in `holon-agentic-coder`, standardizing on platform-agnostic terms like 'Review Loop', 'review
consensus', 'change branch', and 'Change Diff'.

Throughout Holon Flow, CLI help, logs, and prompt scaffolding, Stage 4 has frequently been termed the "PR review loop"
with references to "PR", "PR diff", "PR description", and "PR merging". While Holon Flow interacts with GitHub via the
`gh` CLI as a transport mechanism, its core architecture is Git-native, branch-based, and platform-agnostic. Decoupling
core engine concepts from GitHub terminology preserves clean architectural boundaries while retaining GitHub CLI
interactions strictly as a remote transport adapter.

The plan executes across 5 focused steps:

1. Standardize CLI help text in `flow.py` and `review.py`, replacing `--skip-review` description with
   `"Skip Stage 4 (review loop) and proceed to calibration."` without 'PR' phrasing.
2. Refactor `review_report.py` to refer to Stage 4 review consensus, rendering `## Holon Review: <title> (iteration N)`
   headers and exporting `build_review_consensus_report` with backward-compatible aliases.
3. Update review ensemble prompt builders in `review_ensemble.py`, scaffolding in `scaffold.py`, and
   `review_loop.template.md` to describe changes as change diffs and change descriptions while preserving Bean 0034
   safety guardrails.
4. Standardize flow engine logging, console outputs, and discrete action records in `flow.py` and
   `entrypoint/review_loop.py` to display "Stage 4: Review Loop" and "Review consensus".
5. Align test assertions across `test_flow.py`, `test_review_report.py`, `test_review_ensemble.py`, and `test_cli.py`,
   and run targeted pytest suites, `uv run task test`, and `uv run task check`.

---

## Step 1: Update CLI help text and argument descriptions in flow and review commands

- **Sub‑intent recommendation:** NO
- **Reasoning:** Localized modification of CLI help strings and argument specifications with minimal blast radius and
  zero architectural risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Replace GitHub-specific PR references in CLI help text across
  `apps/sandbox-executor/src/sandbox_executor/cli/commands/flow.py`,
  `apps/sandbox-executor/src/sandbox_executor/cli/commands/review.py`, and
  `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py` to use platform-agnostic terminology ('review
  loop', 'execution branch or change branch').
- **Git branch:**
  `I-1793425536-remove-github-pr-terminology-across-holon-flow/P-1793425551-antigravity-agent-gemini-3.8-flash-medium/E-cli-help-standardisation/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/cli/commands/flow.py`:
   - Locate argument definition for `--skip-review`.
   - Update `help` parameter from `"Skip Stage 4 (PR review loop) and proceed to calibration."` to
     `"Skip Stage 4 (review loop) and proceed to calibration."`.
2. In `apps/sandbox-executor/src/sandbox_executor/cli/commands/review.py`:
   - Inspect module docstring and command help text.
   - Refine help string for `review-loop` command from describing an execution branch or pull request to describing an
     execution branch or change branch, retaining alias `pr-review-loop` for backwards compatibility.
   - Verify argument descriptions for `target` and `--auto-pr` remain clear while distinguishing Git branch targets from
     GitHub remote targets.
3. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`:
   - Verify CLI parser description and argument help strings use platform-agnostic review loop phrasing.
4. Run CLI argument parsing tests via `uv run pytest apps/sandbox-executor/tests/test_cli.py` to ensure all parser
   definitions and `--skip-review` flags continue to parse correctly.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2` (PEP 8, docstring hygiene), `docs/safety.md#git-discipline`.
- **Potential failure modes for this step:** Breaking existing CLI test assertions that expect exact help text strings.
- **Guardrails and early‑abort checks:** Run `uv run pytest apps/sandbox-executor/tests/test_cli.py` immediately after
  editing.

### Success & Discard Criteria

- **Success:** Running `uv run holon flow --help` displays `Skip Stage 4 (review loop) and proceed to calibration.` with
  zero 'PR' phrasing in the `--skip-review` description, and CLI tests pass.
- **Discard:** Discard if CLI argument parser fails to initialize or breaks core CLI subcommands.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.20  |
| impact_pred         | 50.0  |
| cost_pred           | 0.60  |
| learning_value_pred | 2.00  |
| ev_pred             | 49.34 |

### Step Metrics Rationale

- **P(success):** `0.98`. Changes are simple string adjustments in CLI parser definitions with negligible technical
  risk.
- **Entropy:** `0.20`. Minimal disorder added; purely textual adjustment.
- **Impact:** `50.0`. Satisfies primary acceptance criterion for CLI help text consistency.
- **Cost:** `0.60`. Minimal compute and editing time required.
- **Learning Value:** `2.00`. Verifies CLI regression testing sensitivity to help-text alterations.
- **Expected Value (EV):** `49.34`, derived as
  `EV = 0.98 * 50.0 + 0.5 * 2.00 - 0.3 * 0.20 - 0.60 = 49.00 + 1.00 - 0.06 - 0.60 = 49.34`.

---

## Step 2: Standardize review consensus report generator and module docstrings

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped refactor of report header formatting and docstrings within `review_report.py`, preserving
  transport adapter functionality with low entropy.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Standardize `apps/sandbox-executor/src/sandbox_executor/review_report.py` to refer to Stage 4 review
  consensus rather than PR review consensus, update rendered report headers to platform-agnostic phrasing, and export
  `build_review_consensus_report` with backward-compatible aliases.
- **Git branch:**
  `I-1793425536-remove-github-pr-terminology-across-holon-flow/P-1793425551-antigravity-agent-gemini-3.8-flash-medium/E-report-generator-standardisation/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/review_report.py`:
   - Update module docstring from `"The Stage 4 PR review consensus report, rendered only from real reviewer results."`
     to `"The Stage 4 review consensus report, rendered only from real reviewer results."`.
   - Update markdown header in `build_pr_review_consensus_report` (or the unified generator) from
     `f"## Holon PR Review: {report_title} (iteration {consensus_data.get('iterations', 1)})"` to
     `f"## Holon Review: {report_title} (iteration {consensus_data.get('iterations', 1)})"`.
   - Update report prose explanation from
     `"Approval needs every reviewer to approve with zero findings, plus a mergeable PR and green CI."` to
     `"Approval needs every reviewer to approve with zero findings, plus a mergeable change branch and green CI."`.
   - Define canonical function `build_review_consensus_report` and retain
     `build_pr_review_consensus_report = build_review_consensus_report` alias to prevent breaking external callers. Add
     both to `__all__`.
   - Preserve remote PR linking helper `_pr_link` to maintain full GitHub transport adapter functionality when a GitHub
     remote is detected.
2. In `apps/sandbox-executor/tests/test_review_report.py`:
   - Update test expectations that assert on report header starting with `## Holon PR Review:` to expect
     `## Holon Review:`.
3. Validate changes by running `uv run pytest apps/sandbox-executor/tests/test_review_report.py`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2` (docstrings and Clean Architecture),
  `docs/safety.md#invariant-6` (immutability of schemas).
- **Potential failure modes for this step:** Breaking report parsing or downstream consumers that regex-match the exact
  header `## Holon PR Review:`.
- **Guardrails and early‑abort checks:** Backward-compatible function alias `build_pr_review_consensus_report` is
  retained; targeted unit tests in `test_review_report.py` are run immediately.

### Success & Discard Criteria

- **Success:** Report markdown renders `## Holon Review: <title> (iteration N)` and docstrings reference Stage 4 review
  consensus without 'PR review consensus', while unit tests pass cleanly.
- **Discard:** Discard if report formatting changes prevent Markdown parsers or GitHub dual-posting adapter from
  transmitting consensus comments.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.35  |
| impact_pred         | 60.0  |
| cost_pred           | 0.80  |
| learning_value_pred | 3.00  |
| ev_pred             | 58.20 |

### Step Metrics Rationale

- **P(success):** `0.96`. Clean decoupling with backward-compatible aliases and existing test coverage.
- **Entropy:** `0.35`. Modest changes confined to report rendering and docstring phrasing.
- **Impact:** `60.0`. Eliminates vendor lock-in phrasing from user-facing consensus reports and documentation.
- **Cost:** `0.80`. Straightforward editing and unit test execution.
- **Learning Value:** `3.00`. Establishes template standards for platform-agnostic consensus summaries.
- **Expected Value (EV):** `58.20`, derived as
  `EV = 0.96 * 60.0 + 0.5 * 3.00 - 0.3 * 0.35 - 0.80 = 57.60 + 1.50 - 0.105 - 0.80 = 58.195 -> 58.20`.

---

## Step 3: Update review ensemble prompt builders, scaffolding, and review loop template

- **Sub‑intent recommendation:** NO
- **Reasoning:** Coordinated template and prompt updates across `review_ensemble.py`, `scaffold.py`, and
  `review_loop.template.md` without runtime side-effects.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update review ensemble prompts, role templates, and scaffold helpers to describe reviewed changes as
  'change diffs' and 'change descriptions' rather than 'PR diffs' and 'PR descriptions', while preserving safety
  instructions and GitHub remote command guardrails.
- **Git branch:**
  `I-1793425536-remove-github-pr-terminology-across-holon-flow/P-1793425551-antigravity-agent-gemini-3.8-flash-medium/E-prompt-scaffold-standardisation/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `holon-config/prompts/review_loop.template.md`:
   - Change heading `## 🔍 Multi-Agent Ensemble PR Review (Iteration {iteration})` to
     `## 🔍 Multi-Agent Ensemble Review (Iteration {iteration})`.
   - Update persona evaluation instructions: change
     `"Evaluate the Pull Request diff through activated specialist roles"` to
     `"Evaluate the change diff through activated specialist roles"`.
   - Update reviewer turn contract item 1: change `"Review only the diff and PR description supplied in this prompt."`
     to `"Review only the change diff and change description supplied in this prompt."`.
   - Update markdown evaluation context section: change `### PR Description:` to `### Change Description:` and
     `### Git Diff:` to `### Change Diff:`.
   - Retain placeholder variable names `{pr_description}` and `{diff}` or support aliases so that variable bindings in
     existing callers do not fail.
   - Retain mandatory Bean 0034 safety notices forbidding automated merging via `gh pr merge`.
2. In `apps/sandbox-executor/src/sandbox_executor/review_ensemble.py`:
   - In `_missing_inputs()`, update rendered Markdown headers from `"PR description:"` and `"Git diff:"` to
     `"Change description:"` and `"Change diff:"`. Support both `{change_description}` and `{pr_description}` in
     template checks.
   - In `reviewer_contract()`, update closing turn contract instructions from
     `"review only the diff and PR description above"` to `"review only the change diff and change description above"`.
   - Keep `_REVIEWER_PROHIBITIONS` intact, explicitly forbidding write actions like `gh pr review`, `gh pr comment`,
     `gh pr edit`, and `gh pr merge`.
3. In `apps/sandbox-executor/src/sandbox_executor/scaffold.py`:
   - Standardize scaffold prompt templates to format change titles and change descriptions cleanly, maintaining
     parameter compatibility with existing callers.
4. Run template verification tests:
   - Execute `uv run pytest apps/sandbox-executor/tests/test_prompt_templates.py` to verify no unmatched placeholder
     braces exist.
   - Execute `uv run pytest apps/sandbox-executor/tests/test_review_ensemble.py`.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/prompts/` hygiene rules, `docs/safety.md#invariant-4` (human review and merge
  boundaries).
- **Potential failure modes for this step:** Injecting unmatched placeholder braces in `review_loop.template.md` which
  would fail `test_prompt_templates.py`, or breaking prompt parameter substitution in `review_ensemble.py`.
- **Guardrails and early‑abort checks:** Run `test_prompt_templates.py` to validate template braces immediately after
  modifying templates.

### Success & Discard Criteria

- **Success:** Prompts rendered for ensemble reviewers describe targets using 'change diff' and 'change description',
  `test_prompt_templates.py` passes with zero defects, and Bean 0034 prohibitions remain active.
- **Discard:** Discard if prompt template formatting causes placeholder substitution errors or breaks ensemble agent
  dispatch.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.45  |
| impact_pred         | 65.0  |
| cost_pred           | 1.10  |
| learning_value_pred | 4.00  |
| ev_pred             | 62.52 |

### Step Metrics Rationale

- **P(success):** `0.95`. High probability with clear validation available via `test_prompt_templates.py`.
- **Entropy:** `0.45`. Edits involve template strings and prompt builders across multiple files.
- **Impact:** `65.0`. Aligns agent-facing prompts with platform-agnostic architecture principles.
- **Cost:** `1.10`. Requires careful verification of template placeholders and prompt rendering logic.
- **Learning Value:** `4.00`. Validates compatibility between template placeholder linters and evolving prompt
  terminology.
- **Expected Value (EV):** `62.52`, derived as
  `EV = 0.95 * 65.0 + 0.5 * 4.00 - 0.3 * 0.45 - 1.10 = 61.75 + 2.00 - 0.135 - 1.10 = 62.515 -> 62.52`.

---

## Step 4: Standardize flow engine logging, console outputs, and review loop action records

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped updates to log messages, console output, and discrete ledger action descriptions in `flow.py`
  and `entrypoint/review_loop.py` without altering core flow control logic.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update flow engine logging, console summaries, and discrete ledger action records in
  `apps/sandbox-executor/src/sandbox_executor/flow.py` and
  `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py` to display 'Stage 4: Review Loop' and 'Review
  consensus' rather than 'PR review loop' or 'PR review consensus', while keeping remote GitHub transport adapters fully
  functional.
- **Git branch:**
  `I-1793425536-remove-github-pr-terminology-across-holon-flow/P-1793425551-antigravity-agent-gemini-3.8-flash-medium/E-flow-logging-standardisation/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - Update Stage 4 initial log line from `context.log("Starting Stage 4: PR Review Loop")` to
     `context.log("Starting Stage 4: Review Loop")`.
   - Update Stage 4 docstrings: change `"""Execute Stage 4: PR Review Loop."""` to
     `"""Execute Stage 4: Review Loop."""`.
   - Update discrete action record descriptions:
     - Change `f"PR Review Loop iteration {iteration} reviewer {result.index} {summary['status'].lower()}"` to
       `f"Review Loop iteration {iteration} reviewer {result.index} {summary['status'].lower()}"`.
     - Change `f"PR Review Loop iteration {iteration} finding detected: {finding_id}"` to
       `f"Review Loop iteration {iteration} finding detected: {finding_id}"`.
     - Change `f"PR Review Loop iteration {iteration} evaluation for {review_target}"` to
       `f"Review Loop iteration {iteration} evaluation for {review_target}"`.
     - Change `f"PR Review Loop executed for {review_target}"` to `f"Review Loop executed for {review_target}"`.
     - Change `f"PR Review consensus dual-posted to GitHub for {review_target}"` to
       `f"Review consensus dual-posted to GitHub for {review_target}"`.
     - Change `f"PR Review consensus posting to GitHub failed for {review_target}"` to
       `f"Review consensus posting to GitHub failed for {review_target}"`.
   - Update console halt and summary messages:
     - Change `print("[BEAN 0034 SAFETY HALT] PR Review Loop reached consensus approval.")` to
       `print("[BEAN 0034 SAFETY HALT] Review Loop reached consensus approval.")`.
     - Change `print(f"PR Review Loop did not reach consensus approval (status={consensus.get('status')}).")` to
       `print(f"Review Loop did not reach consensus approval (status={consensus.get('status')}).")`.
     - Change summary ledger labels:
       `f"PR Review Loop reached consensus approval after {iteration} iteration(s) (Bean 0034 safety halt)"` to
       `f"Review Loop reached consensus approval after {iteration} iteration(s) (Bean 0034 safety halt)"`.
     - Change summary ledger labels: `f"PR Review Loop halted without consensus after {iteration} iteration(s)"` to
       `f"Review Loop halted without consensus after {iteration} iteration(s)"`.
   - In `dual_post_review_consensus()`, keep the GitHub transport adapter logic (e.g. `gh pr comment`, `gh pr review`)
     fully active when interacting with GitHub remotes, while updating internal diagnostic logs to refer to 'Review
     consensus dual-posting'.
2. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`:
   - Update preparation failure action record from `f"PR Review Loop preparation ({stage}) failed for {raw}"` to
     `f"Review Loop preparation ({stage}) failed for {raw}"`.
   - Update safety halt message from `print("[BEAN 0034 SAFETY HALT] PR Review Loop reached consensus approval.")` to
     `print("[BEAN 0034 SAFETY HALT] Review Loop reached consensus approval.")`.
3. Verify flow engine syntax and imports by executing targeted flow tests:
   - Run
     `uv run pytest apps/sandbox-executor/tests/test_flow.py -k "test_pipeline_engine_skip_review or test_dual_post"`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (Central coordination step linking core engine logging, ledger action emission, and console
  output; errors here could cause flow execution failures)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md#3` (Ledger immutability; newly appended action shapes must
  conform), `docs/safety.md#invariant-4` (Bean 0034 human merge safety boundary).
- **Potential failure modes for this step:** Modifying ledger action strings without updating corresponding test queries
  or assertions in `test_flow.py`.
- **Guardrails and early‑abort checks:** Ensure action record schemas retain valid fields (`action_id`, `action`,
  `intent_id`, `created_at`) and only description string phrasing is modernized.

### Success & Discard Criteria

- **Success:** Flow engine logs, console output, and recorded action descriptions display 'Stage 4: Review Loop' and
  'Review consensus', while `gh` CLI transport calls execute unhindered.
- **Discard:** Discard if flow execution throws unhandled exceptions during Stage 4 review loop or dual-posting.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.50  |
| impact_pred         | 70.0  |
| cost_pred           | 1.30  |
| learning_value_pred | 3.00  |
| ev_pred             | 65.85 |

### Step Metrics Rationale

- **P(success):** `0.94`. Bottleneck step representing the primary surface area of string updates across the flow
  orchestration engine.
- **Entropy:** `0.50`. Moderately higher entropy due to multiple touchpoints in `flow.py`.
- **Impact:** `70.0`. Core delivery of the intent's rationale: decoupling the flow engine logs and console summaries
  from GitHub-specific PR terminology.
- **Cost:** `1.30`. Detailed inspection and testing of flow execution logging paths.
- **Learning Value:** `3.00`. Provides insights into flow ledger event consumers and diagnostic log parsing patterns.
- **Expected Value (EV):** `65.85`, derived as
  `EV = 0.94 * 70.0 + 0.5 * 3.00 - 0.3 * 0.50 - 1.30 = 65.80 + 1.50 - 0.15 - 1.30 = 65.85`.

---

## Step 5: Update test assertions and verify complete test suite and code hygiene

- **Sub‑intent recommendation:** NO
- **Reasoning:** Final integration and verification step ensuring all unit, CLI, and flow test suites pass cleanly
  without regressions.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Align test assertions across `test_flow.py`, `test_review_report.py`, `test_review_ensemble.py`, and
  `test_cli.py` with the updated platform-agnostic strings, and execute full test suite and lint checks to guarantee
  zero regressions.
- **Git branch:**
  `I-1793425536-remove-github-pr-terminology-across-holon-flow/P-1793425551-antigravity-agent-gemini-3.8-flash-medium/E-test-suite-verification/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_flow.py`:
   - Update action record filter assertion in `test_dual_post_consensus_approval_and_comment`: change search string from
     `"PR Review consensus dual-posted"` to `"Review consensus dual-posted"`.
   - Verify any other test asserting on Stage 4 log messages or console outputs matches the new `"Stage 4: Review Loop"`
     and `"Review Loop reached consensus approval."` phrasing.
2. In `apps/sandbox-executor/tests/test_review_ensemble.py`:
   - Verify assertions checking reviewer contract strings match
     `"review only the change diff and change description above"`.
3. In `apps/sandbox-executor/tests/test_ledger_lock.py`:
   - Update synthetic test action strings from `f"PR Review Loop iteration ..."` to `f"Review Loop iteration ..."` to
     maintain codebase consistency.
4. Execute affected tests in targeted fashion:
   - Run
     `uv run pytest apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_review_report.py apps/sandbox-executor/tests/test_prompt_templates.py apps/sandbox-executor/tests/test_review_ensemble.py apps/sandbox-executor/tests/test_flow.py`.
5. Execute full unit test suite:
   - Run `uv run task test`.
6. Run linter and code formatting hygiene tasks:
   - Run `uv run task check` (runs lint and lint-docs).
   - Verify zero formatting or documentation lint failures exist.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `.agents/AGENTS.md` (Run tests strictly via `uv run pytest`, use `uv run task check`),
  `holon-config/world/ruleset.md#3` (pytest standard runner, explicit test updates).
- **Potential failure modes for this step:** Stale assertions in unaffected test files that incidentally inspect flow
  execution logs.
- **Guardrails and early‑abort checks:** Targeted pytest run provides rapid feedback on specific test files before full
  suite execution.

### Success & Discard Criteria

- **Success:** All affected tests and the full unit test suite (`uv run task test`) pass cleanly with 100% success rate,
  and `uv run task check` passes with zero lint or formatting violations.
- **Discard:** Discard if regressions are detected that cannot be resolved without violating platform-agnostic design
  invariants or breaking GitHub remote transport.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.35  |
| impact_pred         | 75.0  |
| cost_pred           | 1.20  |
| learning_value_pred | 3.00  |
| ev_pred             | 71.45 |

### Step Metrics Rationale

- **P(success):** `0.95`. High probability as the test suite is deterministic and all modified assertions are known.
- **Entropy:** `0.35`. Low entropy verification step.
- **Impact:** `75.0`. Guarantees system integrity, preventing regressions and validating all acceptance criteria.
- **Cost:** `1.20`. Execution of targeted pytest runs and repository task suites.
- **Learning Value:** `3.00`. Validates test-suite coverage across flow logging and review consensus reporting.
- **Expected Value (EV):** `71.45`, derived as
  `EV = 0.95 * 75.0 + 0.5 * 3.00 - 0.3 * 0.35 - 1.20 = 71.25 + 1.50 - 0.105 - 1.20 = 71.445 -> 71.45`.
