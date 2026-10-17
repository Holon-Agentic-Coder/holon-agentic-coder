# Plan for I-1792220943-native-holon-review-loop-step

- **Plan ID:** P-1792220973-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.4.0)
- **Created At:** 2026-10-17T07:09:33.920Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with empirical discovery of GitHub CLI status checks, trailer appending mechanisms,
  and multi-pass iteration budget convergence under unattended pipeline execution.
- **Safety priority level:** elevated
- **Priority Justification:** Triggered by `docs/safety.md` §1 (Git as safety boundary: isolated branch execution), §2
  (Mandatory sandboxing), §4 (Entropy as safety signal: iteration budget bounding), §5 (Human review as final gate: Bean
  0034 Human-Only PR Merging prohibition on autonomous merge to `main`), `holon-config/world/constraints.md` §1 (Git
  flow constraints: strict branch prefix isolation and commit boundaries), §2 (Sandbox containment tiers: zero
  credential leakage), §3 (Ledger immutability: zero modification policy for append-only archives), and
  `holon-config/world/ruleset.md` §1, §2, §3 (Python `==3.13.*`, monorepo workspace layout under
  `apps/sandbox-executor`, PEP 8, static type annotations, docstring integrity, pytest 9.1.1 hermetic test boundaries).
  Elevated safety ensures that multi-pass review loop actions strictly preserve human gating prior to any branch
  integration with `main`.

## Exploration

- **Proportion of steps that are exploratory:** 0.17
- **Justification:** Step 1 performs a focused exploratory audit of external review loop skill patterns, GitHub CLI
  `gh pr checks` parsing schemas, trailer delimiter formatting rules, and iteration budget management strategies prior
  to committing core engine changes.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.35  |
| impact_pred         | 95.0  |
| cost_pred           | 11.2  |
| learning_value_pred | 5.4   |
| ev_pred             | 80.70 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.94. Governed by the critical bottleneck step (Step 3: 0.94), which implements the core
  multi-pass review loop engine, CI status polling via `gh pr checks`, iteration budget tracking, and git trailer
  appending within `sandbox_executor/flow.py`. Network polling latency and CI state transitions represent the primary
  failure surface.
- **entropy_pred**: 0.35. Evaluated as the maximum single-step risk profile (Step 3: 0.35), where iteration loops,
  subprocess execution, and trailer parsing introduce behavioral complexity. The cumulative sum of predicted step
  entropies across all six steps is 1.30 (`0.25 + 0.20 + 0.35 + 0.25 + 0.15 + 0.10 = 1.30`), remaining well within the
  allocated entropy budget of 15.0.
- **impact_pred**: 95.0. High architectural impact fulfilling Bean 0118 by promoting the PR review loop from an external
  agent harness skill into a first-class, explicit native CLI subcommand (`holon review-loop` with aliases `review` and
  `pr-review-loop`) and native pipeline Stage 4 in the 5-stage Holon flow.
- **cost_pred**: 11.2. Calculated as the direct sum of predicted step costs
  (`1.4 + 1.8 + 3.2 + 2.0 + 1.6 + 1.2 = 11.2`).
- **learning_value_pred**: 5.4. Substantial epistemic gain delivered to the system through empirical discovery of native
  review loop convergence dynamics, CI check polling resilience, git trailer parsing contracts, and ledger provenance
  recording.
- **ev_pred**: 80.70. Derived using the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system coefficients
  `lambda = 0.3` and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.94 * 95.0 + 0.5 * 5.4 - 0.3 * 0.35 - 11.2 = 89.30 + 2.70 - 0.105 - 11.2 = 80.695 -> 80.70`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: strict prefix isolation, commit boundaries
    scoped to active step branch, mandatory rebase discipline).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: container isolation, process safety, zero
    credential leakage).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: append-only policy for
    `holon-knowledge/ledger/executions.jsonl` and `actions.jsonl`).
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python `==3.13.*`, uv workspace management
    under `apps/sandbox-executor`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, strict static type hints,
    docstring preservation).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: pytest 9.1.1, test hermeticity, preserve test boundaries in
    `apps/sandbox-executor/tests/`).
  - `docs/safety.md` §1 & §5 (Git as safety boundary, Human review as final gate: Bean 0034 Human-Only PR Merging rule
    requiring unconditional halt at consensus approval).
  - Intent Q1 Answer: Primary CLI subcommand is `review-loop`, with aliases `review` and `pr-review-loop`.
  - Intent Q2 Answer: Review loop sits as Stage 4 in the 5-stage Holon flow (intent -> plan -> execute -> review ->
    calibrate).
- **Potential violations or edge cases:**
  - Autonomous merging of pull requests to `main` upon consensus approval, directly violating Bean 0034 and
    `docs/safety.md` §5.
  - Infinite review iteration loops consuming unbounded time and tokens when CI checks or review criteria fail to
    converge.
  - Subprocess blocking or deadlocks when executing `gh pr checks` in headless or offline container environments.
  - Mangled git commit trailers corrupting commit metadata or breaking commit signing.
  - Ledger format collisions or mutation of historical entries in `executions.jsonl`.
- **Mitigations built into the plan:**
  - Strict preservation of `StageStatus.HALTED_FOR_HUMAN` upon review consensus approval, emitting actionable manual
    approval instructions (`holon review approve <target>`) without merging.
  - Hard-capped iteration budget via `--max-iterations` (default: 25) with graceful exhaustion handling and fail-fast
    triggers (`--fail-fast`).
  - Robust timeouts and fallbacks for `gh pr checks` querying: fallback to local test results and git mergeability
    inspection when `gh` or network is unavailable.
  - Structured git trailer generation conforming to RFC 2822 / git-interpret-trailers standard
    (`Action-Origin: pr_review`, `Review-Iteration: N`, `Review-Finding-Ids: ...`, `Review-Severity: ...`).
  - Atomic ledger appending using `atomic_ledger_append` to guarantee append-only immutability.
- **Residual risk accepted (and why):**
  - Dependency on GitHub API availability for `gh pr checks` in live CI; accepted because the engine implements an
    offline local fallback mechanism analyzing local test suite outcomes and git diffs.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 1.30
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

This plan implements Bean 0118 across `apps/sandbox-executor`:

1. **CLI Interface (`apps/sandbox-executor/src/sandbox_executor/cli.py`):**
   - Register the primary subcommand `review-loop` with aliases `review` and `pr-review-loop` on the main argument
     parser.
   - Accept arguments: `target` (positional: execution branch or PR number/URL), `--max-iterations` (int, default: 25),
     `--ensemble-size` (int, default: 3), `--auto-pr` (boolean flag, default: True), `--fail-fast` (boolean flag),
     `--dry-run` (boolean flag), `--repo-dir` (default: current directory), and `--json` (boolean flag).
   - Wire the CLI command handler to invoke the review loop execution engine and format human-readable or JSON outputs.

2. **Pipeline Lifecycle Engine (`apps/sandbox-executor/src/sandbox_executor/flow.py`):**
   - Upgrade Stage 4 in the 5-stage Holon flow (intent -> plan -> execute -> review -> calibrate) to natively drive
     multi-pass review loop execution.
   - Implement CI check inspection via `gh pr checks` with structured status polling, timeout handling, and local
     fallback.
   - Implement iteration budget management ensuring loop execution halts cleanly when consensus is reached,
     `--fail-fast` is triggered, or `--max-iterations` is exhausted.
   - Format and append standardized git trailers (`Action-Origin: pr_review`, `Review-Iteration: N`,
     `Review-Finding-Ids: ...`, `Review-Severity: ...`) to review commits.
   - Enforce Bean 0034 (Human-Only PR Merging): upon consensus approval, halt execution with status `HALTED_FOR_HUMAN`
     and emit clear instructions without autonomously merging to `main`.
   - Append discrete review actions, iteration telemetry, and outcomes to `holon-knowledge/ledger/executions.jsonl`.

3. **Verification & Quality Gates:**
   - Add unit tests in `apps/sandbox-executor/tests/test_cli.py` covering the `review-loop` CLI subcommands, aliases
     (`review`, `pr-review-loop`), argument parsing, and command dispatch.
   - Add unit tests in `apps/sandbox-executor/tests/test_flow.py` covering multi-pass review iteration, CI status
     probing, trailer appending, iteration budgeting, fail-fast aborts, and ledger logging.
   - Ensure all linters, formatting checks, and test suites pass cleanly: `make lint`,
     `uv run pytest apps/sandbox-executor/tests -m 'not integration_test and not stress'`.

---

## Step 1: Audit review loop specifications, CLI arguments, and git trailer standards (Bean 0118 Discovery)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Informational exploration step to audit existing Stage 4 review implementation, review skill
  interfaces, GitHub CLI checks schemas, and trailer formatting rules without modifying production code.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The existing Stage 4 implementation in `flow.py` and CLI argument parser in `cli.py` can
  be cleanly refactored to support multi-pass iteration loops, `gh pr checks` polling, and git trailers while remaining
  backward compatible with existing single-pass review invocations.
- **Learning target:** Exact argument signatures, alias registration mechanisms in `argparse`, GitHub CLI check status
  JSON schemas, git trailer syntax conventions, and ledger entry schemas for review actions.
- **Maximum acceptable cost for this learning:** 1.5 cost units.

### Intent & Git Integration

- **Step Intent:** Audit existing review loop code in `flow.py`, inspect `argparse` subparser aliasing capabilities,
  review `gh pr checks --json` output formats, and define git trailer specifications.
- **Git branch:**
  `I-1792220943-native-holon-review-loop-step/P-1792220973-antigravity-agent-gemini-3.8-flash-medium/E-1792220980-audit-review-loop-specs/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Review existing `run_review_stage` in `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - Inspect `check_pr_mergeability` and how merge conflict states (`DIRTY`, `CONFLICTING`) are detected.
   - Examine `StageResult` payload structure and how `consensus` is currently recorded.
   - Identify where multi-pass looping and iteration budget tracking should be integrated.
2. Review `sandbox_executor/cli.py` argument parsing architecture:
   - Check how subparsers are registered and verify `aliases` parameter support in
     `subparsers.add_parser("review-loop", aliases=["review", "pr-review-loop"])`.
   - Determine parameter mappings for `target`, `--max-iterations`, `--ensemble-size`, `--auto-pr`, `--fail-fast`, and
     `--dry-run`.
3. Audit GitHub CLI CI check status formats:
   - Inspect JSON output of `gh pr checks` (`bucket`, `name`, `state`, `event`, `workflow`).
   - Define normalization rules for pending, successful, failed, and skipped CI check states.
4. Audit git trailer conventions:
   - Confirm standard trailer formatting conforming to `git interpret-trailers` (`Action-Origin: pr_review`,
     `Review-Iteration: N`, `Review-Finding-Ids: ...`, `Review-Severity: ...`).
5. Audit ledger logging requirements in `holon-knowledge/ledger/executions.jsonl`:
   - Verify fields for review execution records: `execution_id`, `plan_branch`, `review_target`, `iteration`,
     `findings`, `verdict`, `duration`, `status`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (Git boundary), §4 (Entropy limits), §5 (Human review boundary: Bean 0034),
  `holon-config/world/ruleset.md` §2.
- **Potential failure modes for this step:** Misunderstanding `argparse` alias behavior across Python versions or
  misinterpreting `gh pr checks` schema.
- **Guardrails and early‑abort checks:** Verify that `argparse.add_parser` supports `aliases` natively in Python 3.13;
  verify fallback when `gh` CLI is absent.

### Success & Discard Criteria

- **Success:** Detailed design specification established for CLI parser registration, multi-pass engine loop, trailer
  formatting, and ledger schemas.
- **Discard:** Discard if core specifications violate Bean 0034 or break existing `flow` stage contracts.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.25  |
| impact_pred         | 85.0  |
| cost_pred           | 1.4   |
| learning_value_pred | 4.0   |
| ev_pred             | 83.83 |

### Step Metrics Rationale

High probability (`0.98`) and low entropy (`0.25`) for a read-only audit. Cost is minimal (`1.4`), and learning value is
high (`4.0`) as it clarifies trailer standards and CLI alias mechanics. Expected value derivation:
`EV = 0.98 * 85.0 + 0.5 * 4.0 - 0.3 * 0.25 - 1.4 = 83.30 + 2.0 - 0.075 - 1.4 = 83.825 -> 83.83`.

---

## Step 2: Implement review-loop CLI subcommand with aliases and arguments in cli.py (CLI Interface)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Targeted CLI argument parser configuration and subcommand dispatching within `sandbox_executor/cli.py`.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add `review-loop` subcommand with aliases `review` and `pr-review-loop` to `cli.py`, parse all
  required arguments, and wire the handler to the review loop execution function.
- **Git branch:**
  `I-1792220943-native-holon-review-loop-step/P-1792220973-antigravity-agent-gemini-3.8-flash-medium/E-1792220995-cli-review-loop-subcommand/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Open `apps/sandbox-executor/src/sandbox_executor/cli.py`.
2. Locate the `subparsers` definition in `main()`:
   - Add the `review-loop` subparser with aliases `review` and `pr-review-loop`:
     `subparsers.add_parser("review-loop", aliases=["review", "pr-review-loop"], help="Run autonomous native review loop on an execution branch or pull request.")`.
3. Configure arguments for the `review-loop` parser:
   - Positional argument `target`: execution branch name, PR number, or PR URL.
   - Optional argument `--max-iterations`: integer with default 25, validating value >= 1.
   - Optional argument `--ensemble-size`: integer with default 3, validating value >= 1.
   - Optional argument `--auto-pr`: boolean flag with default True (using `--no-auto-pr` for disabling via
     `argparse.BooleanOptionalAction` or custom flags).
   - Optional argument `--fail-fast`: boolean action flag (`action="store_true"`), terminating review loop immediately
     upon first unrecoverable failure.
   - Optional argument `--dry-run`: boolean action flag (`action="store_true"`), evaluating diffs and checks without
     making git commits or pushing.
   - Optional argument `--repo-dir`: string default `"."`, defining repository working directory.
   - Optional argument `--json`: boolean action flag (`action="store_true"`), outputting structured JSON results to
     stdout.
   - Optional argument `--token-reduce`: boolean action flag with standard token reduction help.
4. Implement command dispatch handler in `main()`:
   - Check if `args.command in ("review-loop", "review", "pr-review-loop")`.
   - Validate that `max_iterations >= 1` and `ensemble_size >= 1`, exiting with code 1 on invalid values.
   - Resolve `target` branch or PR identifier.
   - Construct `FlowContext` or review runner configuration and call `run_review_loop_cli`.
   - Format terminal output: print iteration count, findings summary, CI check statuses, consensus approval state, and
     Bean 0034 human halt notice.
   - If `--json` is supplied, print serialised JSON payload to stdout.
   - Set process exit code based on review outcome: exit 0 on consensus approval or human halt, exit 1 on unrecovered
     failure.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (PEP 8, strict type hints, docstrings), `docs/safety.md` §5
  (Bean 0034 halt notification).
- **Potential failure modes for this step:** Alias resolution conflicts; missing default parameter handling; unhandled
  exception on invalid argument inputs.
- **Guardrails and early‑abort checks:** Validate input ranges for `--max-iterations` and `--ensemble-size` before
  calling the execution engine.

### Success & Discard Criteria

- **Success:** `holon review-loop --help`, `holon review --help`, and `holon pr-review-loop --help` all display the
  identical help message with all required arguments, and CLI argument parsing correctly sets parsed attributes.
- **Discard:** Discard if subcommand aliases fail to register or if required arguments (`target`, `--max-iterations`,
  `--ensemble-size`, `--auto-pr`, `--fail-fast`, `--dry-run`) are missing.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.20  |
| impact_pred         | 90.0  |
| cost_pred           | 1.8   |
| learning_value_pred | 2.5   |
| ev_pred             | 85.79 |

### Step Metrics Rationale

High probability (`0.96`) and low entropy (`0.20`) because standard `argparse` constructs are used. Delivers core CLI
usability impact (`90.0`). Expected value derivation:
`EV = 0.96 * 90.0 + 0.5 * 2.5 - 0.3 * 0.20 - 1.8 = 86.40 + 1.25 - 0.06 - 1.8 = 85.79`.

---

## Step 3: Implement native multi-pass review loop engine, CI probe via gh pr checks, and trailer formatting in flow.py (Pipeline Lifecycle)

- **Sub‑intent recommendation:** YES
- **Reasoning:** Core algorithmic enhancement involving multi-pass iteration management, CI status polling via
  `gh pr checks`, iteration budget enforcement, trailer formatting, and consensus evaluation. Isolated branch execution
  isolates blast radius.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** A native multi-pass review loop engine can iteratively evaluate diffs, probe GitHub CI
  statuses via `gh pr checks` (falling back to local test runs), append standardized git trailers, manage iteration
  budgets, and halt safely for human review under Bean 0034 without infinite loops or hanging.
- **Learning target:** Behavior of `gh pr checks` under varying network and authorization environments; convergence
  patterns across multiple review passes; trailer formatting robustness.
- **Maximum acceptable cost for this learning:** 4.0 cost units.

### Intent & Git Integration

- **Step Intent:** Implement `run_review_loop` and upgrade `run_review_stage` in
  `apps/sandbox-executor/src/sandbox_executor/flow.py` to natively execute the multi-pass review loop with CI check
  polling, iteration budget control, and git trailer formatting.
- **Git branch:**
  `I-1792220943-native-holon-review-loop-step/P-1792220973-antigravity-agent-gemini-3.8-flash-medium/E-179221010-flow-stage4-review-loop-engine/_`
- **Sub‑intent:** NEW

### Implementation Details (No code blocks, only logic/steps)

1. Open `apps/sandbox-executor/src/sandbox_executor/flow.py`.
2. Implement CI check status inspection helper
   `probe_ci_checks(repo_dir: str, branch_or_pr: str, timeout: float = 30.0) -> dict[str, Any]`:
   - Check if `gh` CLI binary is available on PATH.
   - If available, run `gh pr checks <branch_or_pr> --json bucket,name,state,event,workflow` with timeout.
   - Parse JSON output and categorize checks into: `total`, `passed`, `failed`, `pending`, and `all_passed` (True when
     `total > 0` and `failed == 0` and `pending == 0`).
   - If `gh` is unavailable, times out, or PR is not found, fallback gracefully: inspect local test results from
     execution stage payload, returning `source: "local_tests"` with status derived from local test exit codes.
3. Implement git trailer formatting helper
   `format_review_git_trailers(action_origin: str, iteration: int, finding_ids: list[str], severity: str) -> str`:
   - Format key-value trailers conforming to RFC 2822: `Action-Origin: pr_review` `Review-Iteration: N`
     `Review-Finding-Ids: id1, id2, ...` (or `none`) `Review-Severity: critical | important | nit | clean`
   - Validate that trailer lines do not contain unescaped line breaks.
4. Implement multi-pass review loop engine `run_review_loop(...)`:
   - Accept parameters: `context: FlowContext`, `target: str`, `max_iterations: int = 25`, `ensemble_size: int = 3`,
     `auto_pr: bool = True`, `fail_fast: bool = False`, `dry_run: bool = False`.
   - Track iteration counter starting from 1 up to `max_iterations`.
   - In each iteration: a. Query mergeability using `check_pr_mergeability`. If dirty/conflicting, fail fast or request
     auto-rebase. b. Probe CI checks using `probe_ci_checks`. c. Evaluate code diff and test suite pass rate. d.
     Aggregate findings from reviewer ensemble (or rule evaluation). e. If findings exist and iteration <
     max_iterations:
     - Record finding IDs and highest severity.
     - Create review iteration commit with formatted git trailers:
       `git commit --allow-empty -m "review: iteration N finding fixes\n\n<trailers>"`.
     - Re-run verification tests if code changes were applied. f. If all checks pass and no unresolved findings remain:
     - Declare `consensus_reached = True`.
     - Break out of iteration loop. g. If `fail_fast` is True and critical severity finding or failed CI check is
       observed:
     - Log fail-fast termination and break loop.
   - If iteration counter reaches `max_iterations` without consensus:
     - Mark iteration budget exhausted.
     - Record failure status `iteration_budget_exhausted`.
5. Upgrade `run_review_stage(context: FlowContext) -> StageResult`:
   - Invoke `run_review_loop` passing configured `context.max_review_iterations`, `dry_run`, and target branches.
   - Enforce Bean 0034 (Human-Only PR Merging):
     - When consensus is reached, set `StageResult.status = StageStatus.HALTED_FOR_HUMAN`.
     - Log explicit human halt notice:
       `"[BEAN 0034 SAFETY HALT] PR Review Loop reached consensus approval. Pipeline halted for human review. To approve manually, run: 'holon review approve <target>'"`
     - Never execute git merge to `main`.
   - Update `context.stage_results["review"]` with detailed telemetry payload.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (Core engine functionality; entire review loop depends on this implementation)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (Git boundary), §4 (Entropy budgets), §5 (Human review as final gate: Bean
  0034), `holon-config/world/constraints.md` §1 (Prefix isolation).
- **Potential failure modes for this step:** Infinite loop when CI checks remain pending; git commit trailer formatting
  error causing git hook rejection; accidental automated merge to `main`.
- **Guardrails and early‑abort checks:** Hard-cap iteration loop with `max_iterations`; enforce explicit Bean 0034
  guardrail preventing merge to `main`; provide local fallback when `gh` CLI fails.

### Success & Discard Criteria

- **Success:** Multi-pass review loop executes up to `max_iterations`, probes CI checks, formats git trailers, stops at
  consensus, halts under Bean 0034 without merging to `main`, and records comprehensive telemetry.
- **Discard:** Discard if review loop attempts automated merge to `main`, hangs indefinitely, or fails to respect
  iteration limits.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.35  |
| impact_pred         | 95.0  |
| cost_pred           | 3.2   |
| learning_value_pred | 5.0   |
| ev_pred             | 88.50 |

### Step Metrics Rationale

Bottleneck step with probability (`0.94`) and highest step entropy (`0.35`) due to subprocess interactions and iteration
logic. High impact (`95.0`) delivering core Bean 0118 functionality. Expected value derivation:
`EV = 0.94 * 95.0 + 0.5 * 5.0 - 0.3 * 0.35 - 3.2 = 89.30 + 2.5 - 0.105 - 3.2 = 88.495 -> 88.50`.

---

## Step 4: Record discrete review actions, git trailers, and outcomes in executions.jsonl (Ledger Integration)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Ledger serialization enhancement adhering to existing append-only conventions and schema specifications
  in `sandbox_executor/ledger.py` and `flow.py`.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Append discrete review actions, iteration counts, git trailers, findings summaries, and final review
  outcomes to `holon-knowledge/ledger/executions.jsonl` using `atomic_ledger_append`.
- **Git branch:**
  `I-1792220943-native-holon-review-loop-step/P-1792220973-antigravity-agent-gemini-3.8-flash-medium/E-1792221025-ledger-git-trailer-logging/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Open `apps/sandbox-executor/src/sandbox_executor/flow.py` and navigate to review stage completion handling.
2. In review loop iteration completion:
   - Construct review action entry for each iteration:
     - `action_type`: `"pr_review_iteration"`
     - `iteration`: iteration index `N`
     - `execution_id`: current execution ID or generated review run ID
     - `target`: branch or PR number
     - `ci_status`: result of `probe_ci_checks` (`passed`, `failed`, `pending`, `skipped`)
     - `findings_count`: number of findings detected
     - `finding_ids`: list of finding ID strings
     - `severity`: maximum severity observed (`critical`, `important`, `nit`, `clean`)
     - `trailers`: dictionary of git trailers appended to review commit
     - `duration_seconds`: iteration elapsed time
     - `created_at`: ISO-8601 UTC timestamp
3. In review loop final consensus / termination handling:
   - Construct review summary record for `holon-knowledge/ledger/executions.jsonl`:
     - `execution_id`: review execution ID
     - `plan_branch`: context plan branch
     - `execution_branch`: context execution branch or review target
     - `agent`: agent identifier
     - `model`: model identifier
     - `status`: `"success"` if consensus reached, `"halted_for_human"` if consensus approval reached under Bean 0034,
       `"failed"` if budget exhausted or fail-fast triggered
     - `summary`: narrative description of review outcome, iterations completed, and findings resolved
     - `total_iterations`: total review iterations executed
     - `max_iterations`: iteration budget ceiling
     - `ci_checks_summary`: summary of final CI status
     - `trailers`: list or dictionary of all trailers applied
     - `created_at`: ISO-8601 UTC timestamp
4. Use `atomic_ledger_append` to append entries to `holon-knowledge/ledger/executions.jsonl` safely with file locking.
5. In Git-enabled environments, stage `holon-knowledge/ledger/executions.jsonl` and commit with appropriate author and
   trailer metadata.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modifications to historical
  records, strictly append-only), `docs/safety.md` §1 & §6.
- **Potential failure modes for this step:** Concurrency collision during ledger write; serialization error with complex
  trailer objects.
- **Guardrails and early‑abort checks:** Wrap ledger append with `atomic_ledger_append`; ensure JSON serialization
  handles all data types cleanly.

### Success & Discard Criteria

- **Success:** Each review loop iteration and the overall review outcome are cleanly appended to
  `holon-knowledge/ledger/executions.jsonl` with valid JSON schemas, accurate git trailers, and no corrupted lines.
- **Discard:** Discard if ledger entries rewrite historical lines or if JSONL syntax is malformed.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.25  |
| impact_pred         | 90.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 85.83 |

### Step Metrics Rationale

High probability (`0.96`) and moderate entropy (`0.25`) because `atomic_ledger_append` is already established and proven
in the codebase. High impact (`90.0`) providing audit trail and provenance. Expected value derivation:
`EV = 0.96 * 90.0 + 0.5 * 3.0 - 0.3 * 0.25 - 2.0 = 86.40 + 1.5 - 0.075 - 2.0 = 85.825 -> 85.83`.

---

## Step 5: Add comprehensive unit tests in test_cli.py and test_flow.py (Testing)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Modular unit testing step in `apps/sandbox-executor/tests/` verifying all new CLI subcommands,
  arguments, and flow lifecycle logic.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add thorough unit test coverage in `test_cli.py` and `test_flow.py` asserting `review-loop`
  subcommands and aliases, argument parsing, multi-pass iteration flow, CI status probing, trailer generation, iteration
  budget limits, and ledger logging.
- **Git branch:**
  `I-1792220943-native-holon-review-loop-step/P-1792220973-antigravity-agent-gemini-3.8-flash-medium/E-1792221040-unit-tests-cli-flow/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Open `apps/sandbox-executor/tests/test_cli.py`:
   - Add `test_review_loop_cli_subcommand_registration`: assert `review-loop`, `review`, and `pr-review-loop` are all
     registered subcommands on the parser.
   - Add `test_review_loop_cli_argument_parsing`: parse test arguments
     `["holon", "review-loop", "my-exec-branch", "--max-iterations", "15", "--ensemble-size", "2", "--fail-fast", "--dry-run"]`
     and assert all attributes are set correctly.
   - Add `test_review_loop_cli_alias_invocations`: verify invoking with `review` and `pr-review-loop` dispatches
     identically.
   - Add `test_review_loop_cli_validation`: assert `--max-iterations 0` or `--ensemble-size 0` exits with code 1 and
     descriptive error message.
   - Add `test_review_loop_cli_json_output`: assert `--json` flag formats structured output to stdout.
2. Open `apps/sandbox-executor/tests/test_flow.py`:
   - Add `test_probe_ci_checks_github_cli_success`: mock `subprocess.run` returning `gh pr checks` JSON with passed
     checks; assert `all_passed=True`.
   - Add `test_probe_ci_checks_github_cli_failures`: mock `gh pr checks` returning failing checks; assert `failed > 0`
     and `all_passed=False`.
   - Add `test_probe_ci_checks_fallback_when_gh_missing`: patch `shutil.which` to return None; assert fallback to local
     test results.
   - Add `test_format_review_git_trailers`: assert trailer string formatting conforms to expected key-value pairs
     (`Action-Origin`, `Review-Iteration`, `Review-Finding-Ids`, `Review-Severity`).
   - Add `test_review_loop_consensus_approval_halts_under_bean_0034`: verify multi-pass review loop stops upon consensus
     approval, sets status `HALTED_FOR_HUMAN`, and never invokes `git merge main`.
   - Add `test_review_loop_max_iterations_exhaustion`: simulate unresolved findings and verify loop terminates when
     iteration count reaches `max_iterations` without infinite looping.
   - Add `test_review_loop_fail_fast_abort`: simulate critical finding with `--fail-fast` and verify immediate
     termination.
   - Add `test_review_loop_appends_executions_ledger`: assert `executions.jsonl` receives structured iteration and
     summary records with trailers.
3. Run tests using hermetic fixtures:
   `uv run pytest apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_flow.py -q`.

### Dependencies & Criticality

- **Depends on:** Step 2, Step 3, Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Testing constraints: pytest 9.1.1, test boundaries),
  `apps/sandbox-executor/docs/hermetic_testing.md` (hermetic fixtures, no real network calls, pin `HOLON_REPO_DIR`).
- **Potential failure modes for this step:** Non-hermetic test leaking real network call to `github.com` or deleting
  live workspace directories.
- **Guardrails and early‑abort checks:** Use mocks for `subprocess.run`, `gh` CLI, and git operations; pin
  `HOLON_REPO_DIR` to temporary directory fixtures.

### Success & Discard Criteria

- **Success:** All new unit tests in `test_cli.py` and `test_flow.py` pass cleanly with 100% pass rate.
- **Discard:** Discard if any test leaks external network requests or fails under pytest.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.15  |
| impact_pred         | 92.0  |
| cost_pred           | 1.6   |
| learning_value_pred | 3.0   |
| ev_pred             | 89.10 |

### Step Metrics Rationale

High probability (`0.97`) and low entropy (`0.15`) as unit tests validate defined deterministic interfaces. Delivers
high regression protection impact (`92.0`). Expected value derivation:
`EV = 0.97 * 92.0 + 0.5 * 3.0 - 0.3 * 0.15 - 1.6 = 89.24 + 1.5 - 0.045 - 1.6 = 89.095 -> 89.10`.

---

## Step 6: Execute repository quality gates, linting, formatting, and test suites (Goal 3 Verification)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Final quality gate execution verifying all linters, formatting, and test suites across the repository.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Run static analysis, linters, code formatters, documentation style validation, and the unit test
  suite across the repository to verify zero regressions.
- **Git branch:**
  `I-1792220943-native-holon-review-loop-step/P-1792220973-antigravity-agent-gemini-3.8-flash-medium/E-1792221055-verification-quality-gates/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Execute `make lint`, verifying:
   - `uv lock --check`
   - `git diff --exit-code uv.lock`
   - `ruff check .`
   - `ruff format --check .`
2. Execute the unit test suite:
   - `uv run pytest apps/sandbox-executor/tests -m 'not integration_test and not stress'`
3. Execute standalone Ruff linter and formatter checks:
   - `uv run ruff check .`
   - `uv run ruff format --check .`
4. Execute Prettier documentation formatting checks:
   - `npx prettier --check '**/*.md'`
5. Verify Git status and diff integrity:
   - Confirm `apps/sandbox-executor/src/sandbox_executor/cli.py` contains `review-loop` subcommands and aliases.
   - Confirm `apps/sandbox-executor/src/sandbox_executor/flow.py` contains native multi-pass review loop, CI check
     polling, and trailer formatting.
   - Confirm `apps/sandbox-executor/tests/test_cli.py` and `test_flow.py` contain all required unit tests.
   - Confirm `holon-knowledge/ledger/` files are not modified historically.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4, Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1, §2, §3, `docs/safety.md` §1, §5.
- **Potential failure modes for this step:** Unformatted markdown line or minor linter discrepancy.
- **Guardrails and early‑abort checks:** Run `ruff check` and `prettier` locally to catch formatting issues before final
  completion.

### Success & Discard Criteria

- **Success:** All verification commands (`make lint`, `uv run pytest`, `ruff check`, `ruff format --check`,
  `prettier --check`) complete with exit code 0.
- **Discard:** Discard if any quality gate fails.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.10  |
| impact_pred         | 90.0  |
| cost_pred           | 1.2   |
| learning_value_pred | 1.5   |
| ev_pred             | 88.62 |

### Step Metrics Rationale

Very high probability (`0.99`) and minimal entropy (`0.10`) as automated test and lint suites validate clean code
without introducing novel logic. Delivers final verification impact (`90.0`). Expected value derivation:
`EV = 0.99 * 90.0 + 0.5 * 1.5 - 0.3 * 0.10 - 1.2 = 89.10 + 0.75 - 0.03 - 1.2 = 88.62`.
