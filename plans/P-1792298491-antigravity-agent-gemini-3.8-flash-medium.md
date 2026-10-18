# Plan for I-1792298443-proactive-conflict-resolution-pre-commit-and-audit

- **Plan ID:** P-1792298491-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.4.0)
- **Created At:** 2026-10-18T04:41:31.606Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical discovery for git merge-base divergence detection,
  append-only JSONL ledger union reconciliation, staged credential entropy matching, and en-AU spelling standardization.
- **Safety priority level:** elevated
- **Priority Justification:** Triggered by `docs/safety.md` §1 (Git as safety boundary: isolated branch execution and
  clean abort on non-auto-resolvable conflicts), §2 (Mandatory sandboxing), §3 (Trust is earned: hermetic credential
  validation), §4 (Entropy as safety signal: bounded blast radius), §5 (Human review as final gate: Bean 0034 Human-Only
  PR Merging), `holon-config/world/constraints.md` §1 (Git flow constraints: strict branch prefix isolation and commit
  boundaries), §2 (Sandbox containment tiers: credential leakage detection and ephemeral scratch containment), §3
  (Ledger immutability: zero modification policy for historical JSONL records), and `holon-config/world/ruleset.md` §1,
  §2, §3 (Python `==3.13.*`, monorepo workspace layout under `apps/sandbox-executor`, PEP 8, static typing, docstring
  integrity, pytest 9.1.1 hermetic test boundaries).

## Exploration

- **Proportion of steps that are exploratory:** 0.17
- **Justification:** Step 1 executes focused informational discovery covering existing ledger utilities
  (`reconcile_ledgers`, `reconcile_ledger_file`), git merge-tree conflict representations, current Makefile lint
  chaining, and documentation orthography baselines prior to modifying code.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.35  |
| impact_pred         | 95.0  |
| cost_pred           | 12.2  |
| learning_value_pred | 5.0   |
| ev_pred             | 79.50 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.94. Governed by the critical bottleneck step (Step 2: 0.94), which implements proactive git
  conflict detection (`check_and_resolve_stage_conflicts`) and the deterministic JSONL ledger union resolver
  (`reconcile_jsonl_ledger_conflict`). Multi-process git operations, merge-base detection, and handling concurrent
  branch advancements represent the principal technical failure surface.
- **entropy_pred**: 0.35. Evaluated as the maximum single-step risk profile (Step 2: 0.35), where git rebase/merge
  interactions and conflict abort logic introduce operational entropy. The cumulative sum of predicted step entropies
  across all six steps is 1.35 (`0.20 + 0.35 + 0.25 + 0.20 + 0.15 + 0.20 = 1.35`), well within the allocated entropy
  budget of 15.0.
- **impact_pred**: 95.0. High architectural, security, and developer experience impact fulfilling Beans 0121, 0110,
  0105, and 0106 across `holon-agentic-coder`: preventing pipeline deadlocks via proactive upstream conflict resolution
  across all 5 Holon flow stages, eliminating secret leaks and untracked scratch artefacts via pre-commit scanning,
  establishing orthographic consistency through Australian English documentation, and providing comprehensive auditing
  visibility via `docs/audit-report-2026-10.md`.
- **cost_pred**: 12.2. Calculated as the direct sum of predicted step costs
  (`1.2 + 2.8 + 2.2 + 2.0 + 1.6 + 2.4 = 12.2`).
- **learning_value_pred**: 5.0. Epistemic gain delivered to the system through empirical discovery of git tree states
  during upstream divergence, deterministic JSONL union semantics, pre-commit regex scanning performance, and hermetic
  CI test isolation.
- **ev_pred**: 79.50. Derived using the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system coefficients
  `lambda = 0.3` and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.94 * 95.0 + 0.5 * 5.0 - 0.3 * 0.35 - 12.2 = 89.30 + 2.50 - 0.105 - 12.2 = 79.495 -> 79.50`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: strict prefix isolation, commit boundaries
    scoped to active step branch, mandatory rebase discipline).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: container isolation, process safety, zero
    credential leakage).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: append-only policy for
    `holon-knowledge/ledger/*.jsonl`).
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python `==3.13.*`, uv workspace management
    under `apps/sandbox-executor`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, strict static type hints,
    docstring preservation).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: pytest 9.1.1, test hermeticity, preserve test boundaries in
    `apps/sandbox-executor/tests/`).
  - `docs/safety.md` §1 & §5 (Git as safety boundary, Human review as final gate: Bean 0034 Human-Only PR Merging rule
    requiring unconditional halt at consensus approval without merging to `main`).
- **Potential violations or edge cases:**
  - Non-ledger source code merge conflicts attempting to automatically resolve or corrupting source code files during
    rebase.
  - Silent rebase failures leaving dangling git lockfiles (`.git/index.lock` or `.git/rebase-merge/`) or detached HEAD
    states.
  - JSONL ledger resolution accidentally reordering historical entries or dropping valid local or upstream records.
  - Pre-commit scanning script flagging false positives in test mock files or documentation examples.
  - Documentation spelling replacement corrupting Python keywords, API contracts, or external JSON keys.
- **Mitigations built into the plan:**
  - Clean abort guard: If any non-ledger file has conflict markers or cannot be cleanly auto-merged, immediately execute
    `git merge --abort` or `git rebase --abort` and raise a structured `ConflictResolutionError` with explicit file
    paths.
  - Deterministic union algorithm: Preserve all upstream JSONL lines in exact chronological/order sequence followed by
    deduplicated local lines, staging the resolved ledger file via `git add`.
  - Path exclusion rules in `scripts/pre_commit_check.py`: Scan staged files while permitting test mock directories or
    test fixtures to include test patterns without false positives.
  - Orthography boundary rule: Scope Australian English normalisation strictly to markdown documentation text in `docs/`
    and root README, explicitly preserving code identifiers, imports, and schema keys.
- **Residual risk accepted (and why):**
  - Upstream branch advancing rapidly during live execution; accepted because proactive conflict checking runs before
    every single stage and at every iteration of Stage 4.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 1.35
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

This plan delivers a resilient, end-to-end implementation for Beans 0121, 0110, 0105, and 0106 in `holon-agentic-coder`:

1. **Bean 0121:** Proactive upstream conflict detection and automated resolution across all 5 Holon flow stages
   (`intent`, `plan`, `execute`, `review`, `calibrate`), featuring deterministic append-only JSONL ledger union
   reconciliation, clean git abort guards for source conflicts with structured diagnostic reporting, PR mergeable
   inspection in the review loop, and execution-calibration branch synchronization.
2. **Bean 0110:** Pre-commit secret credential leakage and ephemeral scratch file detection in
   `scripts/pre_commit_check.py`, wired into `Makefile` targets (`pre-commit-check` and composite `lint`), validated by
   comprehensive unit tests.
3. **Beans 0105 & 0106:** Comprehensive documentation orthography audit enforcing Australian English (en-AU) spelling
   across `docs/` and root documentation, and publication of the October 2026 Codebase Architecture,
   Security/Hermeticity, and DevEx Audit Report (`docs/audit-report-2026-10.md`).
4. **Verification:** Rigorous linting, code formatting, and hermetic pytest verification ensuring 100% test pass rates
   across the test suite.

---

## Step 1: Baseline Inspection & Conflict Mechanics Discovery

- **Sub‑intent recommendation:** NO
- **Reasoning:** Informational audit with zero modification to production code; low risk, low cost, providing epistemic
  grounding.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Inspecting existing conflict handling utilities (`reconcile_ledgers`,
  `reconcile_ledger_file`, `check_pr_mergeability`), git rebase/merge abort patterns, current Makefile lint chains,
  staged file hooks, and existing documentation spellings will identify exact integration touchpoints with zero
  regressions.
- **Learning target:** Characterise the git index state during branch divergence and verify regex patterns for secret
  tokens.
- **Maximum acceptable cost for this learning:** `cost <= 1.5`

### Intent & Git Integration

- **Step Intent:** Audit existing git conflict handling functions, ledger reconciliation routines, Makefile targets, and
  documentation baselines across `apps/sandbox-executor/src/sandbox_executor/flow.py`, `Makefile`, and `docs/`.
- **Git branch:** `I-1792298443-proactive-conflict-resolution-pre-commit-and-audit/audit-baseline`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Review `apps/sandbox-executor/src/sandbox_executor/flow.py` lines 430-800 covering `extract_valid_ledger_rows`,
   `reconcile_ledger_rows`, `reconcile_ledger_file`, `reconcile_ledgers`, and `check_pr_mergeability`.
2. Map the existing invocation points of `run_git` and rebase calls across `run_intent_stage`, `run_plan_stage`,
   `run_execute_stage`, `run_review_stage` / `run_review_loop`, and `run_calibrate_stage`.
3. Inspect `Makefile` to examine the `lint` target prerequisite chain and verify how new targets (`pre-commit-check`)
   should be added to `.PHONY` and `help`.
4. Check regex definitions for Google AI, GitHub PAT, OpenAI, and Anthropic secret credential patterns against standard
   secret leakage benchmarks.
5. Survey `docs/` for prevalent American English spellings (e.g., initialize, behavior, color, prioritize, optimize,
   artifact) to establish an automated normalisation dictionary.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1, `docs/safety.md` §1.
- **Potential failure modes for this step:** Misidentifying active flow call sites or existing rebase commands.
- **Guardrails and early‑abort checks:** Read-only inspection; abort if git working tree contains uncommitted
  modifications.

### Success & Discard Criteria

- **Success:** Detailed map of flow stage integration points, conflict marker patterns, and pre-commit check
  requirements established.
- **Discard:** Discard if core repository files are inaccessible or corrupt.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.20  |
| impact_pred         | 40.0  |
| cost_pred           | 1.2   |
| learning_value_pred | 4.5   |
| ev_pred             | 40.19 |

### Step Metrics Rationale

High probability (`p_success_pred = 0.98`) and low cost (`cost_pred = 1.2`). Formula derivation:
`EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
`EV = 0.98 * 40.0 + 0.5 * 4.5 - 0.3 * 0.20 - 1.2 = 39.20 + 2.25 - 0.06 - 1.2 = 40.19`.

---

## Step 2: Proactive Conflict Detection & JSONL Ledger Union Resolver

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core algorithmic implementation scoped strictly to `flow.py`; moderate complexity with low blast radius
  under unit test coverage.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement `ConflictResolutionError`, `reconcile_jsonl_ledger_conflict(file_path)`, and
  `check_and_resolve_stage_conflicts(context, stage, target_branch, pr_number=None)` in
  `apps/sandbox-executor/src/sandbox_executor/flow.py`.
- **Git branch:** `I-1792298443-proactive-conflict-resolution-pre-commit-and-audit/flow-conflict-resolver`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Define `class ConflictResolutionError(RuntimeError)` in `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - Store `conflicting_files: list[str]`, `stage: FlowStage`, `target_branch: str`, and a formatted diagnostic message
     listing all unresolvable files.
2. Implement `reconcile_jsonl_ledger_conflict(file_path: str) -> bool`:
   - Check if `file_path` contains git conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`).
   - Parse upstream and local lines: upstream records (between `<<<<<<<` and `=======`) and local records (between
     `=======` and `>>>>>>>`), or extract lines using existing `extract_valid_ledger_rows`.
   - Preserve all upstream JSONL records in exact chronological/sequence order.
   - Append unique local JSONL records that do not exist upstream (deduplicating by primary key: `branch`/`slug` for
     intents, `plan_id` for plans, `execution_id` for executions, or full payload hash).
   - Write reconciled lines back to `file_path` with atomic temporary file replacement, ensuring a terminating newline.
   - Run `run_git(["add", file_path], cwd=repo_dir)` to stage the resolved ledger file.
3. Implement
   `check_and_resolve_stage_conflicts(context: FlowContext, stage: FlowStage | str, target_branch: str = "main", pr_number: int | None = None) -> dict[str, Any]`:
   - Attempt `git fetch origin <target_branch>` with graceful fallback if remote is unreachable.
   - Compute merge-base between current HEAD and `origin/<target_branch>`.
   - Compare commit hash of `origin/<target_branch>` against merge-base to detect if upstream has advanced.
   - If not diverged (HEAD is up to date or ahead), return clean resolution status
     (`{"status": "up_to_date", "diverged": False}`).
   - If diverged, attempt automated rebase/merge against `origin/<target_branch>`
     (`git merge --no-commit --no-ff origin/<target_branch>` or `git rebase origin/<target_branch>`).
   - If conflicts occur, inspect `git diff --name-only --diff-filter=U` to determine unmerged paths.
   - If ALL unmerged paths match append-only ledgers (`holon-knowledge/ledger/*.jsonl`), resolve each using
     `reconcile_jsonl_ledger_conflict(file_path)` and complete the merge/rebase with standard git commit.
   - If ANY conflicting file is a non-ledger source code file, immediately abort cleanly (`git merge --abort` or
     `git rebase --abort`) and raise `ConflictResolutionError(conflicting_files, stage, target_branch)`.
4. Ensure comprehensive docstrings, PEP 8 compliance, and strict static type hints on all new functions.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Core engine component required by all pipeline stages)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (Git as safety boundary), `holon-config/world/constraints.md` §1 (Git Flow
  constraints), `holon-config/world/constraints.md` §3 (Ledger immutability).
- **Potential failure modes for this step:** Partial merge abort leaving dirty unmerged state or failing to stage
  resolved ledgers.
- **Guardrails and early‑abort checks:** Always enclose git operations in `try...except` blocks; execute
  `git merge --abort` or `git rebase --abort` in cleanup handlers upon non-ledger conflicts.

### Success & Discard Criteria

- **Success:** `check_and_resolve_stage_conflicts` correctly identifies divergence, cleanly resolves ledger-only
  conflicts by chronological union, stages the resolved files, and cleanly aborts on source file conflicts raising
  `ConflictResolutionError`.
- **Discard:** Discard step branch if ledger resolution drops records or source conflicts corrupt working tree files.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.35  |
| impact_pred         | 95.0  |
| cost_pred           | 2.8   |
| learning_value_pred | 4.0   |
| ev_pred             | 88.40 |

### Step Metrics Rationale

Bottleneck step with high impact (`95.0`) and focused complexity (`entropy_pred = 0.35`). Formula derivation:
`EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
`EV = 0.94 * 95.0 + 0.5 * 4.0 - 0.3 * 0.35 - 2.8 = 89.30 + 2.00 - 0.105 - 2.8 = 88.395 -> 88.40`.

---

## Step 3: Lifecycle Stage Integration & Review/Calibration Reconcilers

- **Sub‑intent recommendation:** NO
- **Reasoning:** Modular wiring of Step 2's conflict resolver into existing stage orchestration functions in `flow.py`.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Wire `check_and_resolve_stage_conflicts` across all 5 Holon Flow stages (`run_intent_stage`,
  `run_plan_stage`, `run_execute_stage`, `run_review_stage` / `run_review_loop`, `run_calibrate_stage`), inspect PR
  mergeability in Stage 4, and reconcile `/calibrated` in Stage 5.
- **Git branch:** `I-1792298443-proactive-conflict-resolution-pre-commit-and-audit/flow-stage-hooks`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `run_intent_stage`: Invoke `check_and_resolve_stage_conflicts(context, FlowStage.INTENT, target_branch)` prior to
   initialising or checking out the intent branch.
2. In `run_plan_stage`: Invoke `check_and_resolve_stage_conflicts(context, FlowStage.PLAN, target_branch)` prior to
   checking out the plan branch.
3. In `run_execute_stage`: Invoke `check_and_resolve_stage_conflicts(context, FlowStage.EXECUTE, target_branch)` prior
   to rebasing the execution branch.
4. In `run_review_stage` and `run_review_loop`:
   - At the beginning of each review iteration, invoke `check_pr_mergeability(context.repo_dir, branch, target_branch)`
     to evaluate mergeable status.
   - If status is `CONFLICTING` or `DIRTY`, trigger
     `check_and_resolve_stage_conflicts(context, FlowStage.REVIEW, target_branch, pr_number)`.
   - If conflicts are resolved automatically, record the resolution action in `executions.jsonl` and continue review
     loop iterations.
   - If non-resolvable conflicts exist, log structured diagnostic and transition review stage to appropriate
     halted/failed status.
5. In `run_calibrate_stage`:
   - Invoke `check_and_resolve_stage_conflicts(context, FlowStage.CALIBRATE, target_branch)`.
   - Ensure the `/calibrated` branch cleanly reconciles with the execution PR branch before generating calibration
     analysis reports.
6. Record conflict resolution telemetry in stage payloads and checkpoint metadata.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 & §5 (Bean 0034 Human-Only PR Merging: never merge PRs autonomously to
  `main`).
- **Potential failure modes for this step:** Unintended branch switching during active stage execution.
- **Guardrails and early‑abort checks:** Validate active git branch name before and after conflict resolution checks;
  halt if branch pointer diverges unexpectedly.

### Success & Discard Criteria

- **Success:** All 5 stages proactively check and resolve upstream conflicts before proceeding; Stage 4 handles
  `CONFLICTING` PR states; Stage 5 cleanly reconciles `/calibrated`.
- **Discard:** Discard if stage orchestration deadlocks or bypasses human approval gates.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.25  |
| impact_pred         | 90.0  |
| cost_pred           | 2.2   |
| learning_value_pred | 3.5   |
| ev_pred             | 84.98 |

### Step Metrics Rationale

High probability (`p_success_pred = 0.95`) integrating Step 2's validated helpers. Formula derivation:
`EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
`EV = 0.95 * 90.0 + 0.5 * 3.5 - 0.3 * 0.25 - 2.2 = 85.50 + 1.75 - 0.075 - 2.2 = 84.975 -> 84.98`.

---

## Step 4: Pre-Commit Credential Leakage & Scratch File Detection (Bean 0110)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Independent utility script and Makefile integration; well-defined regex pattern matching with low risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Create `scripts/pre_commit_check.py` scanning staged files for secret credential patterns and
  blocking disallowed ephemeral scratch paths; wire into `Makefile` (`pre-commit-check` target and `lint`).
- **Git branch:** `I-1792298443-proactive-conflict-resolution-pre-commit-and-audit/pre-commit-guard`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `scripts/pre_commit_check.py` with standalone CLI argument parsing:
   - Support `--staged` (default: inspect staged git files via `git diff --cached --name-only --diff-filter=ACM`) and
     `--all-files` (inspect all tracked files via `git ls-files`).
   - Define ephemeral scratch file blocklist patterns: `^tmp/.*`, `.*\.tmp$`, `.*\.log$`, `.*\.index/.*`.
   - If any staged file matches the scratch blocklist, reject with exit code 1 and print offending paths.
   - Define secret credential regular expressions:
     - Google AI API keys: `AIza[0-9A-Za-z\-_]{30,40}`
     - GitHub PATs: `ghp_[0-9A-Za-z]{30,50}` and `github_pat_[0-9A-Za-z_]{70,90}` (or `github_pat_[0-9A-Za-z_]+`)
     - OpenAI API keys: `sk-[0-9A-Za-z\-_]{20,}`
     - Anthropic API keys: `sk-ant-[0-9A-Za-z\-_]{20,}`
   - Implement file content scanner: inspect text files line by line, redacting matched secret substrings in output
     diagnostics.
   - Allow exclusion/whitelist for test mock fixtures (e.g., `tests/`, `test_pre_commit_check.py`,
     `scripts/pre_commit_check.py` itself) to avoid false positives on pattern definitions.
   - Exit with status 0 on clean scan, status 1 if any secret credential or disallowed scratch file is detected.
2. Update root `Makefile`:
   - Add `.PHONY` target `pre-commit-check:`.
   - Implement `pre-commit-check` target executing `uv run python3 scripts/pre_commit_check.py`.
   - Wire `pre-commit-check` into the `lint` target: `lint: pre-commit-check lint-json lint-yaml lint-shell lint-md`.
   - Update `help` target in `Makefile` with formatted description of `pre-commit-check`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §2 & §3, `holon-config/world/constraints.md` §2 (Zero credential leakage).
- **Potential failure modes for this step:** False positives blocking legitimate code commits or unhandled binary file
  decoding errors.
- **Guardrails and early‑abort checks:** Ignore binary files by checking for null bytes; provide clear, actionable
  diagnostic messages with redacted token values.

### Success & Discard Criteria

- **Success:** `scripts/pre_commit_check.py` detects all specified credential patterns and blocked paths, exits 0 on
  clean repository state, and `make pre-commit-check` integrates seamlessly into `make lint`.
- **Discard:** Discard if regex false-positive rate impedes normal developer workflow.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.20  |
| impact_pred         | 85.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 81.04 |

### Step Metrics Rationale

High success probability (`0.96`) and strong security impact (`85.0`). Formula derivation:
`EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
`EV = 0.96 * 85.0 + 0.5 * 3.0 - 0.3 * 0.20 - 2.0 = 81.60 + 1.50 - 0.06 - 2.0 = 81.04`.

---

## Step 5: Australian English Spelling Normalisation & Audit Report Publication (Beans 0105 & 0106)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Documentation orthography normalisation and audit report authoring; zero functional runtime risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Ensure documentation and guidelines across `docs/` follow Australian English (en-AU) spelling while
  preserving code symbols and API contracts, and publish `docs/audit-report-2026-10.md` (Beans 0105 and 0106).
- **Git branch:** `I-1792298443-proactive-conflict-resolution-pre-commit-and-audit/docs-en-au-audit`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Perform Australian English (en-AU) orthographic audit across `docs/` and root `README.md`:
   - Replace American English spelling variants with en-AU conventions in prose:
     - `-ize` -> `-ise` (initialize -> initialise, prioritize -> prioritise, standardize -> standardise, optimize ->
       optimise, centralize -> centralise, serialize -> serialise).
     - `-ization` -> `-isation` (initialization -> initialisation, optimization -> optimisation, standardization ->
       standardisation, serialization -> serialisation).
     - `-or` -> `-our` (behavior -> behaviour, color -> colour).
     - `artifact` -> `artefact` (in documentation prose).
     - `canceled` -> `cancelled`.
   - Strict preservation rule: Never modify code symbols, Python variable/function/class names, imports, URL endpoints,
     or JSON ledger schema fields.
2. Author and publish `docs/audit-report-2026-10.md`:
   - Follow standard audit report format established in `docs/audit_report.md`.
   - Document executive summary, audit scope, methodology, and detailed findings matrix across Architecture,
     Security/Hermeticity, and Developer Experience (DevEx).
   - Evaluate Bean 0121 (proactive conflict resolution and append-only ledger union mechanics).
   - Evaluate Bean 0110 (pre-commit secret detection and scratch file blocking).
   - Evaluate Bean 0105 (en-AU documentation standardisation).
   - Evaluate Bean 0106 (audit report delivery and architectural decoupling recommendations).
   - Detail hermetic sandbox isolation, credential redaction boundaries, and zero-trust verification posture.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Docstring and documentation integrity).
- **Potential failure modes for this step:** Accidental find-and-replace modifying code identifiers or test assertions.
- **Guardrails and early‑abort checks:** Run `git diff` inspection ensuring changes are strictly confined to markdown
  documentation files.

### Success & Discard Criteria

- **Success:** Documentation prose conforms to en-AU orthography, code symbols are preserved, and
  `docs/audit-report-2026-10.md` is published with comprehensive findings.
- **Discard:** Discard if code symbols or schema definitions are altered.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.15  |
| impact_pred         | 75.0  |
| cost_pred           | 1.6   |
| learning_value_pred | 3.0   |
| ev_pred             | 73.36 |

### Step Metrics Rationale

High certainty (`p_success_pred = 0.98`), low entropy (`0.15`). Formula derivation:
`EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
`EV = 0.98 * 75.0 + 0.5 * 3.0 - 0.3 * 0.15 - 1.6 = 73.50 + 1.50 - 0.045 - 1.6 = 73.355 -> 73.36`.

---

## Step 6: Comprehensive Unit Testing & System Verification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Validation step executing test suite and linters to guarantee zero regressions.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add comprehensive unit tests in `apps/sandbox-executor/tests/test_flow.py` and
  `apps/sandbox-executor/tests/test_pre_commit_check.py`, and verify 100% test pass rate across linters and test suites.
- **Git branch:** `I-1792298443-proactive-conflict-resolution-pre-commit-and-audit/verification`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_flow.py`:
   - Implement `test_check_and_resolve_stage_conflicts_up_to_date`: test behaviour when active branch is up to date with
     `origin/main`.
   - Implement `test_check_and_resolve_stage_conflicts_diverged_clean_merge`: test automated clean merge when upstream
     has advanced cleanly.
   - Implement `test_reconcile_jsonl_ledger_conflict_union`: test deterministic append-only union resolution verifying
     upstream records maintain exact sequence followed by unique local records without duplicates.
   - Implement `test_check_and_resolve_stage_conflicts_non_ledger_abort`: test clean abort (`git merge --abort` /
     `git rebase --abort`) and raising of `ConflictResolutionError` containing conflicting file paths when source files
     conflict.
   - Implement `test_review_loop_conflicting_pr_resolution`: test that Stage 4 detects `CONFLICTING` status and triggers
     automatic conflict resolution.
   - Implement `test_calibration_stage_reconciliation`: test that Stage 5 ensures `/calibrated` branch cleanly
     reconciles with execution PR branch.
2. In `apps/sandbox-executor/tests/test_pre_commit_check.py`:
   - Test detection of all secret credential patterns (Google AI API key, GitHub PAT, OpenAI key, Anthropic key).
   - Test blocking of disallowed scratch file paths (`tmp/scratch.txt`, `debug.tmp`, `run.log`, `.index/cache.db`).
   - Test clean staged files passing verification with exit code 0.
   - Test `--all-files` mode scanning tracked repository files.
3. Run complete verification commands:
   - `make lint` (verifying `pre-commit-check`, `lint-json`, `lint-yaml`, `lint-shell`, `lint-md`, and python linters).
   - `uv run ruff check .`
   - `uv run ruff format --check .`
   - `uv run pytest apps/sandbox-executor/tests -m "not integration_test and not stress"`
4. Verify all tests pass with 0 failures and 0 lint warnings.

### Dependencies & Criticality

- **Depends on:** Step 2, Step 3, Step 4, Step 5
- **Is Bottleneck:** YES (Final gatekeeper before intent merge)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Testing Constraints: pytest 9.1.1, test hermeticity).
- **Potential failure modes for this step:** Integration test timeouts or test assertion discrepancies.
- **Guardrails and early‑abort checks:** Run fast unit test subset first before full test suite.

### Success & Discard Criteria

- **Success:** 100% passing tests across unit test suite and clean execution of `make lint`.
- **Discard:** Discard if test failures cannot be resolved within predicted cost budget.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.20  |
| impact_pred         | 95.0  |
| cost_pred           | 2.4   |
| learning_value_pred | 3.5   |
| ev_pred             | 88.59 |

### Step Metrics Rationale

High impact (`95.0`) validating complete system behaviour. Formula derivation:
`EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
`EV = 0.94 * 95.0 + 0.5 * 3.5 - 0.3 * 0.20 - 2.4 = 89.30 + 1.75 - 0.06 - 2.4 = 88.59`.
