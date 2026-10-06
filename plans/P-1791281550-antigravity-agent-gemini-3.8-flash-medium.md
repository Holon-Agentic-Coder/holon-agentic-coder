# Plan for I-1791281538-stop-ledger-collisions

- **Plan ID:** P-1791281550-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-06T10:12:30.845Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical validation of deterministic ledger union reconciliation
  semantics, pre-push remote divergence synchronization, and PR mergeability guardrails without compromising repository
  immutability or CI test visibility.
- **Safety priority level:** elevated
- **Priority Justification:** Triggered by `holon-config/world/ruleset.md` §1 & §2 (Python runtime `==3.13.*`, workspace
  isolation under `apps/sandbox-executor`, strict PEP 8 formatting, explicit static typing with `typing` module,
  docstring maintenance), `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test file placement
  under `apps/sandbox-executor/tests/`, test changes declared in planning), `holon-config/world/constraints.md` §1 (Git
  Flow & Branch Constraints: prefix-based branch isolation, rebase discipline, commit boundaries),
  `holon-config/world/constraints.md` §2 (Sandbox containment: subprocess and filesystem containment),
  `holon-config/world/constraints.md` §3 (Ledger immutability: zero modification of historical records, append-only
  union semantics), `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory for execution), and Bean 0034
  / Bean 0062 / Bean 0019 (human-only merge to main, preventing false green consensus when merge conflicts suppress CI
  test execution).

## Exploration

- **Proportion of steps that are exploratory:** 0.80
- **Justification:** Steps 1 through 4 incorporate balanced exploration to test specific hypotheses on deterministic
  union reconciliation across three distinct ledger schemas, safe pre-push three-way conflict isolation in git, and
  non-blocking mergeability inspection under both local and remote execution environments.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.2   |
| impact_pred         | 95.0  |
| cost_pred           | 11.5  |
| learning_value_pred | 6.0   |
| ev_pred             | 78.54 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.92. Dictated by the bottleneck step (Step 2: 0.92), where pre-push remote divergence inspection,
  three-way merge resolution, and conflict isolation strictly to `holon-knowledge/ledger/*.jsonl` must succeed reliably
  across diverse git branch states without corrupting the repository index or masking genuine code conflicts.
- **entropy_pred**: 1.2. Derived as the maximum single-step risk profile (Step 2: 1.2), where git merge execution and
  automatic conflict resolution are performed. The sum of predicted step entropies is 4.2
  (`0.8 + 1.2 + 1.0 + 0.8 + 0.4 = 4.2`), well within the allocated entropy budget of 15.0.
- **impact_pred**: 95.0. Directly resolves the critical silent CI test drop vulnerability observed in Bean 0019 (PR #59)
  and Bean 0062 (PR #64), ensuring concurrent flows automatically reconcile append-only JSONL ledgers before push, and
  preventing false consensus approvals when PRs enter DIRTY or CONFLICTING merge states.
- **cost_pred**: 11.5. Calculated as the direct sum of individual step costs (`2.0 + 3.0 + 2.5 + 2.5 + 1.5 = 11.5`).
- **learning_value_pred**: 6.0. Epistemic gain from establishing formal union semantics for append-only JSONL ledgers in
  distributed multi-agent systems and formalizing PR mergeability detection patterns in CI pipeline review loops.
- **ev_pred**: 78.54. Computed strictly via the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system constants `lambda = 0.3`
  and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.92 * 95.0 + 0.5 * 6.0 - 0.3 * 1.2 - 11.5 = 87.40 + 3.00 - 0.36 - 11.5 = 78.54`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python target `==3.13.*`, workspace
    isolation under `apps/sandbox-executor`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, explicit static typing with
    `typing` module, docstring maintenance).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test files located under
    `apps/sandbox-executor/tests/`, test changes declared in planning).
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, commit
    boundaries).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: strict filesystem containment, prevention of
    escape actions).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modifications to historical ledger entries,
    append-only union semantics).
  - `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory for execution).
  - Bean 0034 (Human-Only PR Merging: review loop halts unconditionally with status `HALTED_FOR_HUMAN` and never merges
    to `main`).
- **Potential violations or edge cases:**
  - Accidental loss of intent, plan, or execution records during conflict reconciliation due to aggressive deduplication
    or improper primary key resolution.
  - Mistakenly auto-merging non-ledger conflicts (e.g. source code or configuration files), leading to silent build or
    logic breakage.
  - Inadvertently suppressing review approval for clean PRs when git or gh CLI commands encounter transient network or
    auth errors.
  - Ledger file corruption caused by unparsed git conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) leaking into
    `.jsonl` files.
- **Mitigations built into the plan:**
  - Define strict canonical primary keys for each ledger type (`branch` or `slug` for `intents.jsonl`, `plan_id` for
    `plans.jsonl`, `execution_id` for `executions.jsonl`), preserving all valid JSON rows and sorting chronologically by
    `created_at`.
  - Implement a loud abort mechanism (`git merge --abort`) in `executor.py` whenever any non-ledger file appears in
    `git diff --name-only --diff-filter=U`.
  - Filter conflict markers explicitly when reading conflicted ledger files, ensuring only well-formed JSON rows are
    unioned and emitted.
  - Provide fallback mergeability checks using local `git merge-tree` or `git merge-base` inspection when remote `gh`
    CLI status is unavailable.
- **Residual risk accepted (and why):**
  - Minor clock skew between agent hosts when ordering records by `created_at`: chronological ordering is stable, and
    subsequent flow runs append deterministically without affecting row integrity.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 4.2
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy sum of 4.2 is well below the 15.0
  allocated budget).

## Plan Description & Strategy

This plan implements robust ledger reconciliation and merge conflict guards across
`apps/sandbox-executor/src/sandbox_executor/flow.py` and
`apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`, ensuring concurrent Holon flows never drop the
GitHub Actions CI signal:

1. **Automatic Ledger Union Reconciliation Utility:** Implement deterministic reconciliation functions
   (`reconcile_ledger_content`, `reconcile_ledger_file`, and `reconcile_ledgers`) that resolve JSONL conflicts by union.
   The utility extracts all valid JSON objects from conflicted files or string buffers (skipping git conflict markers),
   deduplicates entries using canonical primary keys (`branch`/`slug` for `intents.jsonl`, `plan_id` for `plans.jsonl`,
   and `execution_id` for `executions.jsonl`), preserves latest revisions or superseding metadata, and outputs cleanly
   formatted JSONL ordered chronologically by `created_at`.
2. **Executor Pre-Push Synchronization Guard:** In `executor.py`, prior to executing `git push origin <exec_branch>`,
   detect if `origin/main` (or the target base branch) has diverged. If diverged, attempt a three-way merge
   (`git merge origin/main --no-commit --no-ff`). If merge conflicts arise, inspect the unmerged file list. If conflicts
   are strictly confined to `holon-knowledge/ledger/*.jsonl`, resolve each conflicted ledger using the union utility,
   stage the resolved files, verify that no unmerged paths remain, and commit the merge. If any non-ledger file is in
   conflict, immediately abort the merge via `git merge --abort` and fail loudly, preventing the push and logging an
   explicit failure record.
3. **Flow & Review PR Mergeability Guards:** In `flow.py` (`run_review_stage` and `PipelineEngine`), inspect the pull
   request mergeability state. Detect whether `mergeStateStatus` is `DIRTY` or `mergeable` is `CONFLICTING` (via GitHub
   CLI status or local `git merge-tree` analysis). When a dirty/conflicting state is detected, surface an explicit
   critical warning that CI test workflows on `refs/pull/<PR>/merge` are suppressed by GitHub Actions, set review
   consensus to rejected (`approved = False`), and prevent the pipeline from falsely declaring consensus approval.
4. **Unit Test Suite Expansion:** Add comprehensive test coverage in `apps/sandbox-executor/tests/test_flow.py` and
   `apps/sandbox-executor/tests/test_executor.py` covering: ledger union deduplication and chronological ordering,
   multi-file ledger conflict resolution, loud abort on non-ledger code conflicts, and DIRTY merge state detection.
5. **Static Analysis & End-to-End Verification:** Validate that `uv run ruff check .`, `uv run ruff format --check .`,
   `uv run pytest -m "not integration_test and not stress"`, and `npx --yes prettier@3.8.4 --check "**/*.md"` all pass
   cleanly.

---

## Step 1: Implement Deterministic Ledger Union Reconciliation Utility

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped, deterministic data structure transformation utility with zero external network dependencies and
  immediate unit-testability.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** A robust parser that ignores git conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) while
  extracting all valid JSON rows guarantees zero data loss and deterministic union idempotency across all three ledger
  files.
- **Learning target:** Verify whether extracting valid JSON objects directly from conflict marker buffers is completely
  equivalent to three-way git blob extraction (`:1:`, `:2:`, `:3:`) while remaining resilient to uncommitted working
  tree states.
- **Maximum acceptable cost for this learning:** `cost <= 2.5` (utility design and test validation).

### Intent & Git Integration

- **Step Intent:** Implement `reconcile_ledger_content`, `reconcile_ledger_file`, and `reconcile_ledgers` in
  `apps/sandbox-executor/src/sandbox_executor/flow.py` (and expose for import in `executor.py`) to deterministically
  resolve JSONL ledger conflicts by union.
- **Git branch:**
  `I-1791281538-stop-ledger-collisions/P-1791281550-antigravity-agent-gemini-3.8-flash-medium/E-reconcile-ledger-utility`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Open `apps/sandbox-executor/src/sandbox_executor/flow.py` and declare ledger primary key mappings:
  - `intents.jsonl`: primary key selector checking `'branch'` then `'slug'`.
  - `plans.jsonl`: primary key selector `'plan_id'`.
  - `executions.jsonl`: primary key selector `'execution_id'`.
- Implement `extract_valid_ledger_rows(content: str) -> list[dict[str, Any]]`:
  - Split content into lines.
  - Skip git conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`, `|||||||`).
  - For each non-marker line, strip whitespace and attempt `json.loads(line)`.
  - Collect all successfully parsed JSON dictionaries, logging or ignoring malformed non-JSON lines.
- Implement `reconcile_ledger_rows(rows: list[dict[str, Any]], ledger_type: str) -> list[dict[str, Any]]`:
  - Identify primary key extractor based on `ledger_type` (`intents`, `plans`, `executions`).
  - Iterate through rows and group/deduplicate by primary key value.
  - For duplicate entries with the same primary key:
    - If `ledger_revision` is present, select the entry with the higher revision.
    - Otherwise, select the entry with the later `created_at` timestamp or preserve the first valid entry if identical.
  - Sort the deduplicated entries chronologically ascending by `'created_at'` (using ISO-8601 parsing or string
    comparison).
- Implement `reconcile_ledger_file(file_path: str, ledger_type: str | None = None) -> bool`:
  - Determine `ledger_type` from the file basename (`intents.jsonl`, `plans.jsonl`, `executions.jsonl`).
  - Read existing file contents from disk (which may contain git conflict markers).
  - Extract valid rows and reconcile them.
  - Write back the serialized entries as newline-terminated JSON lines
    (`json.dumps(row, separators=(', ', ': ')) + "\n"`).
  - Return `True` if reconciliation succeeded, `False` on IO failure.
- Implement `reconcile_ledgers(repo_dir: str) -> list[str]`:
  - Iterate through `holon-knowledge/ledger/{intents,plans,executions}.jsonl` in `repo_dir`.
  - If the file exists and contains conflict markers or needs synchronization, invoke `reconcile_ledger_file`.
  - Return the list of reconciled ledger relative paths.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 & §2 (PEP 8, typing, docstrings),
  `holon-config/world/constraints.md` §3 (Ledger Immutability: append-only union semantics, zero row loss).
- **Potential failure modes for this step:** Misidentifying primary keys causing entries to be dropped or overwritten;
  failing to parse multiline JSON if formatted non-standardly.
- **Guardrails and early‑abort checks:** Validate that all records present in both input branches exist in the output
  union, and verify that the count of unique primary keys equals the output line count.

### Success & Discard Criteria

- **Success:** Given mock conflicted file contents with overlapping and distinct rows across `intents.jsonl`,
  `plans.jsonl`, and `executions.jsonl`, `reconcile_ledger_file` produces a deterministic, sorted, deduplicated JSONL
  file with zero conflict markers.
- **Discard:** If row extraction drops valid historical records or violates ledger immutability constraints.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.8   |
| impact_pred         | 90.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 86.66 |

### Step Metrics Rationale

- **p_success_pred (0.96):** Pure in-memory data processing logic with explicit schema keys; highly predictable.
- **entropy_pred (0.8):** Scoped to utility functions in `flow.py`; no external side effects or process spawning.
- **impact_pred (90.0):** Provides the foundational union reconciliation engine required by both executor pre-push sync
  and flow merge guards.
- **cost_pred (2.0):** Implementing parsing, deduplication, sorting, and file IO helper functions (~70 LOC).
- **learning_value_pred (5.0):** Establishes canonical union rules and key selectors for all Holon ledger schemas.
- **ev_pred (86.66):** Derived as `EV = 0.96 * 90.0 + 0.5 * 5.0 - 0.3 * 0.8 - 2.0 = 86.40 + 2.50 - 0.24 - 2.0 = 86.66`.

---

## Step 2: Implement Executor Pre-Push Sync and Ledger-Only Conflict Resolution Guard

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core enhancement to git synchronization in
  `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py` with clear git command boundaries and automated
  abort guards.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Attempting an in-flight merge of target base (`origin/main`) prior to push and isolating
  conflict status strictly to ledger files enables 100% clean remote merges while preventing any accidental merge of
  divergent source code.
- **Learning target:** Determine the exact set of git status filters and conflict inspection commands needed to reliably
  distinguish between ledger-only conflicts and code conflicts during pre-push synchronization.
- **Maximum acceptable cost for this learning:** `cost <= 3.5` (git command choreography and failure handling).

### Intent & Git Integration

- **Step Intent:** In `executor.py`, before pushing `exec_branch` to origin, inspect whether `origin/main` has advanced,
  attempt a merge, auto-resolve conflicts strictly when confined to `holon-knowledge/ledger/*.jsonl`, and abort loudly
  on any non-ledger conflict.
- **Git branch:**
  `I-1791281538-stop-ledger-collisions/P-1791281550-antigravity-agent-gemini-3.8-flash-medium/E-executor-pre-push-sync`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Open `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py` and locate the pre-push block around line
  1340-1440.
- Import `reconcile_ledger_file` from `sandbox_executor.flow`.
- Define helper `sync_and_reconcile_pre_push(repo_dir: str, target_branch: str = "main") -> tuple[bool, str]`:
  - Check if remote `origin` exists via `git remote get-url origin`. If no remote is configured (e.g. offline test
    environment), return `(True, "No remote origin configured")`.
  - Fetch target branch tip: `git fetch origin {target_branch}`.
  - Determine if `origin/{target_branch}` has advanced past merge-base with HEAD:
    - Run `git merge-base HEAD origin/{target_branch}`.
    - Check if the merge base equals `origin/{target_branch}` (up to date) or if divergence exists.
  - If diverged:
    - Attempt merge: `git merge origin/{target_branch} --no-commit --no-ff`.
    - If merge returncode is 0 (clean fast-forward or clean recursive merge):
      - Run `git commit -m "chore(sync): sync target branch {target_branch} before push"` if staged changes exist.
      - Return `(True, "Cleanly merged target branch")`.
    - If merge returncode is non-zero (conflicts detected):
      - Query conflicted paths: `git diff --name-only --diff-filter=U`.
      - Inspect the list of conflicted files:
        - Separate files into ledger conflicts (`holon-knowledge/ledger/*.jsonl`) and non-ledger conflicts.
      - If any non-ledger file is present in the conflict list:
        - Execute `git merge --abort`.
        - Log a critical error:
          `Non-ledger merge conflicts detected with origin/{target_branch} in files: {non_ledger_files}. Aborting auto-merge and refusing push.`
        - Return `(False, f"Non-ledger conflicts detected in {non_ledger_files}")`.
      - If and only if ALL conflicted files are strictly within `holon-knowledge/ledger/`:
        - For each conflicted ledger path, invoke `reconcile_ledger_file(full_path)`.
        - Stage the reconciled files: `git add holon-knowledge/ledger/*.jsonl`.
        - Verify `git diff --name-only --diff-filter=U` is now completely empty.
        - Commit the resolved merge:
          `git commit -m "chore(ledger): reconcile concurrent ledger updates with origin/{target_branch}"`.
        - Return `(True, "Successfully reconciled ledger conflicts and committed merge")`.
- Integrate `sync_and_reconcile_pre_push` into `executor.py` right before the `git push` call:
  - If `sync_and_reconcile_pre_push` returns `False`, set `can_push = False`, update the execution summary with the sync
    failure reason, record the failure in `executions.jsonl`, and refuse to push.
  - If it returns `True`, proceed with ancestry verification and push to origin.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Pre-push synchronization directly dictates whether execution branches can be pushed cleanly
  without dropping CI signals)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 (Git Flow: commit boundaries, branch isolation),
  `holon-config/world/constraints.md` §2 (containment), `docs/safety.md` §1 (Git as safety boundary).
- **Potential failure modes for this step:** Git merge leaving an uncommitted or broken index if `git merge --abort`
  fails; accidentally committing conflict markers if ledger reconciliation fails.
- **Guardrails and early‑abort checks:** Wrap merge operations in try/finally blocks to guarantee `git merge --abort` is
  executed if any unexpected exception occurs; assert `git diff --name-only --diff-filter=U` is empty before committing.

### Success & Discard Criteria

- **Success:** When `origin/main` advances with concurrent ledger updates, `executor.py` successfully merges, reconciles
  all 3 ledgers by union, commits the merge, and pushes a cleanly mergeable branch. When non-ledger code conflicts
  occur, the merge is cleanly aborted and push is refused.
- **Discard:** If auto-merge modifies non-ledger files or fails to cleanly abort on code conflicts.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.2   |
| impact_pred         | 95.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 87.04 |

### Step Metrics Rationale

- **p_success_pred (0.92):** Involves multi-step git CLI execution and index state handling; bottleneck of the plan.
- **entropy_pred (1.2):** Interacts with git working tree and remote fetch; controlled by explicit merge-abort
  guardrails.
- **impact_pred (95.0):** Directly eliminates PR merge conflicts for concurrent flow runs, ensuring PRs remain clean and
  CI test workflows run.
- **cost_pred (3.0):** Implementing git divergence checks, conflict filtering, and auto-merge orchestration (~110 LOC).
- **learning_value_pred (6.0):** Establishes an automated synchronization and ledger reconciliation pattern for
  multi-agent branches.
- **ev_pred (87.04):** Derived as `EV = 0.92 * 95.0 + 0.5 * 6.0 - 0.3 * 1.2 - 3.0 = 87.40 + 3.00 - 0.36 - 3.0 = 87.04`.

---

## Step 3: Implement Flow & Review PR Mergeability Guards (DIRTY / CONFLICTING Detection)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Targeted logic addition within `run_review_stage` and `PipelineEngine` in
  `apps/sandbox-executor/src/sandbox_executor/flow.py`.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Explicitly checking PR `mergeStateStatus` and `mergeable` attributes before consensus
  evaluation prevents false positive approvals caused by GitHub Actions dropping test jobs on unmergeable PRs.
- **Learning target:** Determine the most reliable method for detecting dirty/conflicting merge states across both
  GitHub API/CLI responses and local git worktrees.
- **Maximum acceptable cost for this learning:** `cost <= 3.0` (PR review loop verification).

### Intent & Git Integration

- **Step Intent:** In `flow.py`, enhance `run_review_stage` and `PipelineEngine` to inspect git/PR mergeability status,
  detect `mergeStateStatus == "DIRTY"` or `mergeable == "CONFLICTING"`, surface explicit errors, and halt or reject
  review consensus when CI signals are suppressed.
- **Git branch:**
  `I-1791281538-stop-ledger-collisions/P-1791281550-antigravity-agent-gemini-3.8-flash-medium/E-review-mergeability-guards`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Open `apps/sandbox-executor/src/sandbox_executor/flow.py` and inspect `run_review_stage`.
- Define helper `check_pr_mergeability(repo_dir: str, branch: str, target_branch: str = "main") -> dict[str, Any]`:
  - Check for GitHub CLI availability: run
    `gh pr view {branch} --json number,mergeable,mergeStateStatus,statusCheckRollup`.
  - If `gh` command succeeds:
    - Parse JSON output.
    - Extract `mergeable` (e.g. `MERGEABLE`, `CONFLICTING`, `UNKNOWN`) and `mergeStateStatus` (e.g. `CLEAN`, `DIRTY`,
      `BLOCKED`, `BEHIND`).
    - Check if `statusCheckRollup` contains completed check runs or if test runs were skipped.
  - If `gh` is unavailable or fails (e.g. offline sandbox or branch has no active PR yet):
    - Fallback to local git inspection:
      - Run `git merge-tree $(git merge-base HEAD origin/{target_branch}) HEAD origin/{target_branch}` or test merge
        dry-run.
      - If conflicts are detected in the merge tree, flag `mergeable="CONFLICTING"` and `mergeStateStatus="DIRTY"`.
      - Otherwise flag `mergeable="MERGEABLE"` and `mergeStateStatus="CLEAN"`.
  - Return mergeability metadata dictionary:
    `{"mergeable": mergeable, "merge_state_status": merge_state_status, "is_dirty": is_dirty}`.
- In `run_review_stage`:
  - Call `check_pr_mergeability` using `context.execution_branch` or `context.intent_branch`.
  - If `merge_state_status == "DIRTY"` or `mergeable == "CONFLICTING"`:
    - Log a prominent warning:
      `[CRITICAL: CI SIGNAL SUPPRESSED] Pull Request has merge conflicts against {target_branch} (mergeStateStatus={merge_state_status}, mergeable={mergeable}). GitHub Actions suppresses test workflows on refs/pull/<PR>/merge. Halting consensus approval.`
    - Force `consensus_reached = False`.
    - Set review package consensus status to `conflicted_dirty`.
    - Return `StageResult` with status `StageStatus.FAILED` (or halt with explicit conflict diagnostic error).
  - Include mergeability diagnostics in `review_package` payload under key `"mergeability"`.
- In `PipelineEngine`:
  - Ensure that when `run_review_stage` reports a dirty merge conflict failure, the pipeline does not proceed to
    auto-calibration or false completion, maintaining strict visibility of the failure.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Bean 0034 (Human-Only PR Merging), `docs/safety.md` §5 (Human review boundary), `docs/safety.md`
  §1 (Git as safety boundary).
- **Potential failure modes for this step:** False negative if PR state is temporarily `UNKNOWN` while GitHub calculates
  mergeability; false positive if network timeout occurs during `gh` execution.
- **Guardrails and early‑abort checks:** Implement a brief polling retry (up to 3 attempts with 2s backoff) if GitHub
  reports `mergeable="UNKNOWN"`; fallback gracefully to local git merge-tree simulation if network/auth fails.

### Success & Discard Criteria

- **Success:** When PR has `mergeStateStatus="DIRTY"` or `mergeable="CONFLICTING"`, `run_review_stage` explicitly
  rejects consensus approval, logs the CI signal suppression alert, and records the failure in the stage result.
- **Discard:** If clean PRs are falsely flagged as dirty or if `gh` errors cause uncaught crashes in offline
  environments.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 1.0   |
| impact_pred         | 92.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 5.5   |
| ev_pred             | 86.43 |

### Step Metrics Rationale

- **p_success_pred (0.94):** Standard flow review logic enhancement with local git fallback.
- **entropy_pred (1.0):** Confined to review stage evaluation logic in `flow.py`.
- **impact_pred (92.0):** Guarantees that unmergeable PRs with dropped CI test signals cannot slip past review as
  falsely green.
- **cost_pred (2.5):** Adding mergeability inspection helper and review stage guard checks (~80 LOC).
- **learning_value_pred (5.5):** Formalizes PR mergeability and CI test ref validation within the review lifecycle.
- **ev_pred (86.43):** Derived as `EV = 0.94 * 92.0 + 0.5 * 5.5 - 0.3 * 1.0 - 2.5 = 86.48 + 2.75 - 0.30 - 2.5 = 86.43`.

---

## Step 4: Expand Unit Test Suites in test_flow.py and test_executor.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard unit test expansion directly covering newly introduced utilities and guardrails in
  `test_flow.py` and `test_executor.py`.
- **Step Type:** TEST
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Synthetic git repository fixtures with simulated merge conflicts and divergent branches
  comprehensively validate all auto-reconciliation and abort paths without flaky external dependencies.
- **Learning target:** Measure test execution time for git conflict simulations in temporary fixtures to ensure unit
  tests remain fast (<5s).
- **Maximum acceptable cost for this learning:** `cost <= 3.0` (fixture development and assertion tuning).

### Intent & Git Integration

- **Step Intent:** Add thorough unit test coverage in `apps/sandbox-executor/tests/test_flow.py` and
  `apps/sandbox-executor/tests/test_executor.py` covering ledger union deduplication, chronological ordering, executor
  auto-merge, loud abort on non-ledger conflict, and DIRTY merge state detection.
- **Git branch:**
  `I-1791281538-stop-ledger-collisions/P-1791281550-antigravity-agent-gemini-3.8-flash-medium/E-ledger-conflict-unit-tests`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/tests/test_flow.py`, add `TestLedgerReconciliation`:
  - `test_reconcile_intents_ledger_deduplication_and_order`:
    - Create mock `intents.jsonl` content with overlapping slugs/branches and out-of-order `created_at` timestamps.
    - Run `reconcile_ledger_content` or `reconcile_ledger_file`.
    - Assert rows are deduplicated by `branch`/`slug` and ordered chronologically by `created_at`.
  - `test_reconcile_plans_ledger_deduplication`:
    - Create mock `plans.jsonl` with duplicate `plan_id` entries having differing timestamps.
    - Assert higher revision or latest entry is retained.
  - `test_reconcile_executions_ledger_deduplication`:
    - Create mock `executions.jsonl` with duplicate `execution_id` entries.
    - Assert entries are deduplicated by `execution_id` and correctly sorted.
  - `test_reconcile_ledger_with_git_conflict_markers`:
    - Format text containing `<<<<<<< HEAD`, `=======`, and `>>>>>>> origin/main` interleaved with JSONL lines.
    - Assert all valid JSON lines from both branches are preserved and all conflict markers are eliminated.
  - `test_review_stage_flags_dirty_merge_state`:
    - Mock `check_pr_mergeability` returning
      `{"is_dirty": True, "mergeStateStatus": "DIRTY", "mergeable": "CONFLICTING"}`.
    - Execute `run_review_stage`.
    - Assert `result.status == StageStatus.FAILED`.
    - Assert `consensus["approved"] is False`.
    - Assert error message explicitly highlights CI signal suppression.
- In `apps/sandbox-executor/tests/test_executor.py`, add `TestExecutorPrePushSync`:
  - `test_pre_push_sync_reconciles_ledger_only_conflicts`:
    - Create temporary git repo fixture with a base commit containing ledger files.
    - Create a branch and append to `holon-knowledge/ledger/executions.jsonl`.
    - Create a divergent commit on main appending different rows to `executions.jsonl`.
    - Trigger `sync_and_reconcile_pre_push`.
    - Assert merge succeeds, conflict is resolved via union, changes are committed, and return value is `(True, ...)`.
  - `test_pre_push_sync_aborts_loudly_on_non_ledger_conflict`:
    - Create temporary git repo fixture with a conflicting change in `pyproject.toml` or `flow.py`.
    - Trigger `sync_and_reconcile_pre_push`.
    - Assert return value is `(False, ...)`.
    - Assert `git merge --abort` was executed and working directory is clean.
    - Assert `can_push` evaluates to `False`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (pytest constraints),
  `apps/sandbox-executor/docs/hermetic_testing.md` (hermetic repo fixtures).
- **Potential failure modes for this step:** Git fixtures leaking temporary directories or failing to clean up on
  assertion failure.
- **Guardrails and early‑abort checks:** Use pytest `tmp_path` fixture for all temporary repositories to ensure
  automatic cleanup and complete isolation.

### Success & Discard Criteria

- **Success:** All new tests pass cleanly with 100% assertions satisfied and execution time <5 seconds.
- **Discard:** If tests require external network access or exhibit non-deterministic behavior.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.8   |
| impact_pred         | 88.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 5.0   |
| ev_pred             | 83.36 |

### Step Metrics Rationale

- **p_success_pred (0.95):** Synthetic unit tests in hermetic fixtures; fully deterministic.
- **entropy_pred (0.8):** Test files only; zero production risk.
- **impact_pred (88.0):** Guarantees long-term regression protection against ledger collision and dirty PR regressions.
- **cost_pred (2.5):** Authoring comprehensive test classes across `test_flow.py` and `test_executor.py` (~120 LOC).
- **learning_value_pred (5.0):** Validates hermetic testing patterns for git conflict resolution and dirty state
  simulations.
- **ev_pred (83.36):** Derived as `EV = 0.95 * 88.0 + 0.5 * 5.0 - 0.3 * 0.8 - 2.5 = 83.60 + 2.50 - 0.24 - 2.5 = 83.36`.

---

## Step 5: End-to-End Suite Verification, Static Analysis & Markdown Hygiene Compliance

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard integration and hygiene verification step executing project validation tools.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT
- **Hypothesis being tested:** NONE
- **Learning target:** NONE
- **Maximum acceptable cost for this learning:** `cost <= 1.5`

### Intent & Git Integration

- **Step Intent:** Run the entire test suite, linter, formatter, and markdown hygiene checks to ensure full project
  conformance.
- **Git branch:**
  `I-1791281538-stop-ledger-collisions/P-1791281550-antigravity-agent-gemini-3.8-flash-medium/E-verification-hygiene`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Run `uv run ruff check .` across the workspace and verify zero lint errors.
- Run `uv run ruff format --check .` and verify full formatting compliance.
- Run `uv run pytest -m "not integration_test and not stress"` and confirm all unit tests pass cleanly.
- Run `npx --yes prettier@3.8.4 --check "**/*.md"` and confirm all markdown files adhere strictly to Prettier formatting
  standards.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1, §2, §3; `apps/sandbox-executor/docs/hermetic_testing.md`.
- **Potential failure modes for this step:** Prettier formatting failure due to unescaped math expressions; lint
  failures due to unused imports.
- **Guardrails and early‑abort checks:** Format files using automated tools prior to final check.

### Success & Discard Criteria

- **Success:** All four verification commands exit with code 0.
- **Discard:** If any check fails and cannot be resolved through standard lint/formatting fixes.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.4   |
| impact_pred         | 85.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 83.18 |

### Step Metrics Rationale

- **p_success_pred (0.98):** Verification of tested code and standardized formatters.
- **entropy_pred (0.4):** Read-only verification execution; minimal entropy.
- **impact_pred (85.0):** Guarantees repository cleanliness and CI pipeline green status.
- **cost_pred (1.5):** Running test runner and formatters (~3 minutes).
- **learning_value_pred (3.0):** Confirms end-to-end compatibility of modified components.
- **ev_pred (83.18):** Derived as `EV = 0.98 * 85.0 + 0.5 * 3.0 - 0.3 * 0.4 - 1.5 = 83.30 + 1.50 - 0.12 - 1.5 = 83.18`.

---
