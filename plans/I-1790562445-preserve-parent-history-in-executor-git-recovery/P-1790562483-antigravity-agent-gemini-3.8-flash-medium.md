# Plan for I-1790562445-preserve-parent-history-in-executor-git-recovery

- **Plan ID:** P-1790562483-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-09-28T02:28:03.466Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical validation of git recovery mechanics, environment
  sanitization, and hermetic fixture tests to guarantee non-destructive recovery and parent commit preservation under
  diverse repository corruption scenarios.
- **Safety priority level:** critical
- **Priority Justification:** Triggered by `docs/safety.md` §1 (Git is the safety boundary), `docs/safety.md` Invariant
  1 (Git discipline), and `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints). The executor's
  defective git recovery path was actively destroying commit ancestry, generating orphaned root commits, wiping
  repository history, and pushing invalid repositories to remote branches.

## Exploration

- **Proportion of steps that are exploratory:** 0.40
- **Justification:** Steps 2 and 4 incorporate balanced exploration to test specific hypotheses regarding mixed reset
  preservation of agent working tree modifications across re-initialized repositories and hermetic local bare git
  simulation of diverse repository corruption and network failure modes without external dependencies.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.86  |
| entropy_pred        | 2.4   |
| impact_pred         | 95.0  |
| cost_pred           | 15.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 68.98 |

### Strategy Rationale

The overall plan metrics were derived from the individual step-level metrics as follows:

- **p_success_pred**: 0.86. Sourced from the integration bottleneck across Step 2 (non-destructive history preservation
  and mixed reset) and Step 4 (hermetic local git regression test suite covering subtle corruptions and edge cases).
- **entropy_pred**: 2.4. Calculated from the maximum step-level entropy (Step 4: 2.0) plus a 0.4 integration margin for
  cross-module git state interactions. The total sum of individual step entropies is 7.0 (1.0 + 1.8 + 1.4 + 2.0 + 0.8),
  well within the allocated entropy budget of 15.0.
- **impact_pred**: 95.0. Permanently eliminates the Bean 0019 disaster pattern where post-agent git recovery erased
  parent history and pushed orphaned root commits, securing historical continuity, auditability, and code preservation
  across the entire Holon ecosystem.
- **cost_pred**: 15.0. Computed as the direct sum of individual step costs (2.5 + 3.5 + 3.0 + 4.0 + 2.0 = 15.0).
- **learning_value_pred**: 6.0. Epistemic gain from establishing reusable non-destructive disaster recovery patterns in
  sandboxed agent environments, safe environment sanitization, and local bare git hermetic testing methodologies.
- **ev_pred**: 68.98. Derived strictly using the canonical config-driven Expected Value formula:
  $$EV = P(\text{success}) \times \text{Impact} + \mu \times \text{LearningValue} - \lambda \times \Delta S_{\text{intent}} - \text{Cost}$$
  With system constants $\lambda = 0.3$ and $\mu = 0.5$ from `holon-config/metrics/ev_config.json`:
  $$EV = 0.86 \times 95.0 + 0.5 \times 6.0 - 0.3 \times 2.4 - 15.0 = 81.70 + 3.00 - 0.72 - 15.00 = 68.98$$

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python target `==3.13.*`, package
    isolation, workspace structure under `apps/sandbox-executor`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, explicit static typing with
    `typing`, docstring requirements, no wildcard imports).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: unit tests located in `apps/sandbox-executor/tests/`,
    changes to test files explicitly planned).
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, rebase
    discipline, commit boundaries).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: strict filesystem containment, prevention of
    escape actions and unwhitelisted modifications).
  - `docs/safety.md` §1 & Invariant 1 (Git is the safety boundary: sub-intents never merge directly to main, branch
    parentage must be preserved).
  - `docs/safety.md` §2 & Invariant 2 (Sandboxing mandatory for execution, containment to intent workspace).
  - `docs/safety.md` §3 (Trust model: changes to safety-critical execution flows must maintain strict invariants).
  - `apps/sandbox-executor/docs/hermetic_testing.md` (Mandatory rules for tests: fixture pinning, local bare remotes, no
    network access, sandbox safety rail).
- **Potential violations or edge cases:**
  - Running pytest discovery directly in `/home/holon/.holon-sandbox/workspace`, triggering the sandbox safety hazard.
  - Residual environment variables (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`) leaking from
    child agent processes into the executor's git commands, causing git commands to target improper repository paths.
  - Accidental removal of working tree modifications authored by the coding agent during recovery resets (e.g. using
    `git reset --hard` or `git checkout -f` instead of mixed `git reset`).
  - Unintended staging of backup directories (`.git-unusable-*`) by `git add -A` if exclusion is misconfigured or
    committed to tracked `.gitignore`.
  - Pushing orphan commits when the remote origin branch cannot be fetched or resolved.
- **Mitigations built into the plan:**
  - Mandatory sandbox safety rail: all testing and verification must occur strictly within an isolated scratch directory
    (`/tmp/holon-fixture`) with `HOLON_REPO_DIR=/tmp/holon-test-fixture`.
  - Implementation of an environment scrubbing helper that removes all git-specific environment variables (`GIT_DIR`,
    `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`) before any executor git command runs.
  - Enforcing mixed-mode `git reset <tip>` exclusively, guaranteeing that index state matches the fetched base commit
    while preserving all working tree files created by the agent.
  - Enforcing non-destructive backups using `os.replace` / `shutil.move` to `.git-unusable-<utc-timestamp>` and adding
    the backup path strictly to `.git/info/exclude` (never `.gitignore`).
  - Strict pre-push ancestry assertion (`git merge-base --is-ancestor <base_tip> HEAD`) and non-empty repository tree
    assertion (`git ls-tree -r HEAD`), with hard refusal to push and ledger failure logging if parentage cannot be
    proven.
- **Residual risk accepted (and why):**
  - Unrecoverable corrupted remote: if the remote origin repository itself has deleted the `plan_branch`, the executor
    cannot re-attach parent history; this failure is safely handled by marking the ledger entry as failed, retaining the
    backup directory, and refusing to push.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 7.0
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy sum of 7.0 is well below the 15.0
  allocated budget).

## Plan Description & Strategy

This plan transforms the post-agent git recovery mechanism in
`apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py` from a destructive fallback that deletes `.git` and
creates orphan root commits into a robust, non-destructive, history-preserving recovery pipeline. It also replaces the
flawed assertion in `apps/sandbox-executor/tests/test_executor.py` with comprehensive hermetic regression tests.

The plan is structured into five cohesive steps:

1. **Git Environment Scrubbing and Benign Probe/Repair Helpers:** Create dedicated helpers to sanitize the git
   environment from leaked agent runner variables and diagnose/repair benign repository probe failures (dubious
   ownership, stale locks, dangling HEAD) before triggering any rebuild.
2. **Non-Destructive Sibling Backup and History-Preserving Base Re-Attachment:** Replace `shutil.rmtree` of `.git` with
   an atomic rename to `.git-unusable-<utc-timestamp>`, exclude the backup via `.git/info/exclude`, and re-attach the
   base commit using bounded-retry `git fetch`, `git update-ref`, `git symbolic-ref`, and mixed-mode `git reset`.
3. **Pre-Push Parentage Verification and Hard Publication Guardrails:** Implement verification checks confirming that
   the base tip is an ancestor of HEAD and the committed tree contains repository files, refusing to push and recording
   failure in the execution ledger if verification fails.
4. **Hermetic Regression Test Suite and Removal of Misleading Assertions:** Remove the invalid `git symbolic-ref HEAD`
   check in `test_executor.py` and implement comprehensive hermetic tests covering benign repairs, corrupted `.git`
   recovery with parentage preservation, unpushable orphan handling, and backup exclusion.
5. **Code Style Conformance and Sandbox Safety Rail Verification:** Ensure full compliance with ruff linting and
   formatting, prettier documentation standards, and run test verification from an isolated scratch copy under `/tmp`.

---

## Step 1: Git Environment Scrubbing and Benign Probe/Repair Helpers

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped refactoring within `executor.py` creating pure, deterministic helper functions with high success
  probability.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement `_get_clean_git_env()`, `_probe_git_repo(repo_dir: str)`, and
  `_repair_git_repo(repo_dir: str, probe_err: str)` in
  `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py` to strip process-leaked git variables and repair
  benign failures (dubious ownership, stale index locks, dangling symrefs) without rebuilding the repository.
- **Git branch:** I-1790562445-preserve-parent-history-in-executor-git-recovery/step1-probe-repair-helpers
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`:
  - Define `_CLEAN_GIT_ENV_VARS` containing keys known to leak from child processes or subagents: `GIT_DIR`,
    `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, and `GIT_ALTERNATE_OBJECT_DIRECTORIES`.
  - Implement `_get_clean_git_env(base_env: dict[str, str] | None = None) -> dict[str, str]` that copies the environment
    and strips all matching variables.
  - Update `run_cmd` or add a specialized `run_git_cmd` helper that ensures `_get_clean_git_env` is consistently applied
    to all executor git invocations.
  - Implement `_probe_git_repo(repo_dir: str) -> tuple[bool, str]`:
    - Executes `git rev-parse --is-inside-work-tree` within `repo_dir` using the clean environment.
    - If successful, executes `git rev-parse --verify HEAD` to check for dangling or missing HEAD symref.
    - Returns a tuple of `(is_healthy, error_output)`.
  - Implement `_repair_git_repo(repo_dir: str, probe_err: str, default_branch: str) -> bool`:
    - Case (a) "detected dubious ownership":
      - Check if `probe_err` contains "detected dubious ownership" or "safe.directory".
      - Run `git -c safe.directory=<repo_dir> status` to verify access.
      - Persist the configuration strictly to the local repository config using
        `git config --local --add safe.directory <repo_dir>` (or fallback to passing `-c safe.directory` if local config
        cannot be written directly due to ownership).
    - Case (b) Stale index lock or locked ref:
      - Check for the existence of `.git/index.lock` or error string containing "cannot lock ref" / "index.lock".
      - Verify if any active git process is holding the lock; if none, safely remove the stale `.git/index.lock` file.
    - Case (c) Dangling or missing HEAD symref:
      - Inspect `.git/HEAD`. If `.git/HEAD` is missing, dangling, or unreadable, re-point it to
        `refs/heads/<default_branch>` using `git symbolic-ref HEAD refs/heads/<default_branch>`.
    - Case (d) Leaked git environment variables:
      - Re-probe with the sanitized environment from `_get_clean_git_env()`.
    - Following the attempted repairs, re-run `_probe_git_repo(repo_dir)`.
    - Return `True` if the repository is restored to a usable state, or `False` if structural corruption persists.
  - In `executor.main()`, replace the raw `run_cmd(["git", "rev-parse", "--is-inside-work-tree"])` call with an
    invocation of `_probe_git_repo` followed by `_repair_git_repo` prior to any rebuild logic.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Typing and docstrings), `docs/safety.md` §1 (Git safety
  boundary).
- **Potential failure modes for this step:**
  - Writing `safe.directory` globally instead of locally, which would pollute the container or host git config.
  - Removing `.git/index.lock` while a git subprocess is still actively running.
- **Guardrails and early‑abort checks:**
  - Restrict `git config` modifications to `--local`.
  - Ensure process lookup or sanity timeout before unlinking lock files.

### Success & Discard Criteria

- **Success:** Unit tests confirm that simulated dubious ownership, stale `.git/index.lock`, leaked `GIT_DIR`, and
  dangling `HEAD` are diagnosed and repaired in-place without triggering repository re-initialization.
- **Discard:** Discard if repair attempts cause side-effects outside of the designated `repo_dir`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.0   |
| impact_pred         | 75.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 69.95 |

### Step Metrics Rationale

High probability of success (0.95) and low entropy (1.0) because environment sanitization and deterministic diagnosis of
standard git failure modes are well-understood software patterns. EV is strongly positive at 69.95.

---

## Step 2: Non-Destructive Sibling Backup and History-Preserving Base Re-Attachment

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tightly coupled core recovery logic inside `executor.py`. Best executed as a focused branch with direct
  unit verification.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Mixed-mode `git reset <tip>` after bounded-retry `git fetch` across a freshly initialized
  repository successfully re-attaches parent commit history while preserving 100% of agent-authored working tree files.
- **Learning target:** Confirmation that `git reset <tip>` without `--hard` preserves untracked and modified files left
  behind by an agent runner, even when the original `.git` directory was completely replaced.
- **Maximum acceptable cost for this learning:** 3.5 cost units.

### Intent & Git Integration

- **Step Intent:** Replace `shutil.rmtree` on `.git` with atomic relocation to a sibling directory
  (`.git-unusable-<utc-timestamp>`), append the backup path to `.git/info/exclude`, and re-attach the base commit using
  bounded-retry `git fetch`, `git update-ref`, `git symbolic-ref`, and mixed-mode `git reset`.
- **Git branch:** I-1790562445-preserve-parent-history-in-executor-git-recovery/step2-nondestructive-rebuild
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`:
  - Eliminate all calls to `shutil.rmtree(git_dot)` on the post-agent recovery path.
  - Implement non-destructive backup relocation:
    - Generate a unique timestamped backup directory name within `repo_dir`: `.git-unusable-<utc-timestamp>` using
      integer epoch seconds or ISO UTC format.
    - If `.git` exists, atomically move it using `os.replace` (or `shutil.move` across filesystems).
    - Log the exact backup path to stderr and execution output:
      `Warning: unrepairable git repository moved aside to <backup_path>`.
  - Re-initialize repository:
    - Run `git init` within `repo_dir`.
    - Create `.git/info/exclude` if missing and append the basename of `.git-unusable-*` followed by a newline, ensuring
      `git add -A` will never stage the corrupted backup.
    - Re-add or repoint `origin` to `get_repo_url()`.
  - Implement resilient base commit re-attachment:
    - Read retry limit from environment variable `HOLON_GIT_FETCH_RETRIES` (default: 3).
    - Read timeout configuration or apply a standard 30-second per-fetch timeout.
    - Execute bounded-retry fetch: `git fetch --no-tags origin <plan_branch>`.
    - If fetch fails on all retries, capture the diagnostic output and abort base re-attachment, proceeding to the
      failure guardrail.
    - If fetch succeeds:
      - Resolve the fetched commit hash: tip = `git rev-parse FETCH_HEAD`.
      - Create and point the execution branch ref directly at the tip: `git update-ref refs/heads/<exec_branch> <tip>`.
      - Set HEAD symref to the execution branch: `git symbolic-ref HEAD refs/heads/<exec_branch>`.
      - Execute mixed-mode reset to the base tip: `git reset <tip>` (strictly forbid `--hard` or `checkout -f`).
      - This aligns the git index and commit parentage with the fetched `plan_branch` tip while leaving every working
        tree modification authored by the agent intact.
  - Return the resolved `tip` SHA (or `None` if unrecoverable) to be consumed by Step 3.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Core fix for Bean 0019 history destruction and orphan commits)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (Git safety boundary), `holon-config/world/constraints.md` §1 (Git Flow
  constraints).
- **Potential failure modes for this step:**
  - Accidentally using `git reset --hard` or `git checkout -f`, which would erase the agent's work.
  - Adding the backup directory to the tracked `.gitignore` file instead of `.git/info/exclude`.
- **Guardrails and early‑abort checks:**
  - Explicit assertion in code and tests that no `--hard` flag is passed to `git reset`.
  - Verify `.git/info/exclude` contains the backup path and `.gitignore` remains unmodified by recovery.

### Success & Discard Criteria

- **Success:** When `.git` is wiped or corrupted, executor re-initializes, excludes the backup in `.git/info/exclude`,
  fetches `plan_branch`, resets to tip in mixed mode, and staging produces a commit that lists the plan tip as its first
  parent.
- **Discard:** Discard if any agent-created working tree files are deleted or overwritten during the recovery reset.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.8   |
| impact_pred         | 88.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 5.0   |
| ev_pred             | 77.66 |

### Step Metrics Rationale

Probability of success is 0.90 due to subtleties in git ref resolution and network fetch retry semantics. Entropy is 1.8
reflecting interaction with git internals. Learning value is high (5.0) as it proves non-destructive repo resuscitation.
EV is 77.66.

---

## Step 3: Pre-Push Parentage Verification and Hard Publication Guardrails

- **Sub‑intent recommendation:** NO
- **Reasoning:** Straightforward assertion logic and state machine updates for ledger recording and push prevention.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement verification logic checking that the committed HEAD is a descendant of the recovered base
  commit and that the committed tree contains repository files; if unverified or base is missing, refuse to push, write
  failure execution records, and update the ledger entry.
- **Git branch:** I-1790562445-preserve-parent-history-in-executor-git-recovery/step3-parentage-guardrail
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`:
  - After committing changes locally via `git commit`, execute parentage and tree verification:
    - If recovery was triggered, verify that the base commit `base_tip` was resolved and run:
      `git merge-base --is-ancestor <base_tip> HEAD`.
    - Inspect the root tree of the resulting commit using `git ls-tree --name-only HEAD`.
    - Verify that the tree contains repository files beyond just the execution record and ledger (e.g. codebase
      directories or files present in `base_tip`).
    - Confirm that HEAD has at least one parent (`git rev-parse --verify HEAD^`).
  - Handling verification failures or missing base tip:
    - If `base_tip` could not be fetched/resolved, or `is-ancestor` returns non-zero, or tree verification fails:
      - Log an actionable error naming the specific cause, the backup path, and the failed ref.
      - Override `exec_status = "failure"`.
      - Construct an updated summary:
        `Git recovery failure: unable to preserve parent history from <plan_branch>. Corrupted repo backed up at <backup_path>. Remote push aborted.`
      - Rewrite the execution record in `executions/<exec_id>.md` and the append-only ledger in
        `holon-knowledge/ledger/executions.jsonl` with `status: "failure"` and the diagnostic summary.
      - Commit the updated execution record and ledger locally if git is functional.
      - Explicitly skip `git push`, overriding any `HOLON_SKIP_PUSH=0` setting.
      - Print a prominent warning: `Refusing to push execution branch: branch shares no history with plan base commit.`
  - Healthy repository path preservation:
    - If the repository was healthy or benignly repaired without rebuild, maintain the existing `git add`, `git commit`,
      and `git push` flow without modification.
    - Respect `HOLON_SKIP_PUSH` as before.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (Enforces the safety invariant that orphan commits must never be published to remote)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (Git safety boundary), `holon-config/world/constraints.md` §3 (Ledger
  immutability).
- **Potential failure modes for this step:**
  - Accidental push despite failed ancestry verification.
  - Rewriting existing ledger entries instead of appending new entries.
- **Guardrails and early‑abort checks:**
  - Condition `git push` invocation strictly on `ancestry_verified is True` and `exec_status != "failure"`.
  - Verify ledger writes strictly use append mode (`"a"`).

### Success & Discard Criteria

- **Success:** Any execution where base history cannot be verified halts before `git push`, logs actionable diagnostics,
  and records `status: failure` in `executions.jsonl`.
- **Discard:** Discard if healthy executions are falsely marked as failures or blocked from pushing.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.4   |
| impact_pred         | 85.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 76.78 |

### Step Metrics Rationale

High probability (0.92) and low entropy (1.4) as ancestry checking with `git merge-base` is a deterministic git
primitive. Impact is high (85.0) because it prevents toxic commits from polluting upstream branches. EV is 76.78.

---

## Step 4: Hermetic Regression Test Suite and Removal of Misleading Assertions

- **Sub‑intent recommendation:** NO
- **Reasoning:** Test additions and assertion corrections confined to `apps/sandbox-executor/tests/test_executor.py`.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** A local bare git fixture initialized on the filesystem can completely simulate corrupted
  git states (dubious ownership, missing `.git`, missing remote branches, stale locks) deterministically without network
  calls or docker dependencies.
- **Learning target:** Proven patterns for testing git disaster recovery hermetically within pytest fixtures without
  risking container workspace leakage.
- **Maximum acceptable cost for this learning:** 4.0 cost units.

### Intent & Git Integration

- **Step Intent:** Delete the misleading `git symbolic-ref HEAD` assertion from
  `apps/sandbox-executor/tests/test_executor.py::test_main_git_recovery_on_corrupted_repo` and add comprehensive
  regression tests covering history preservation, non-destructive backup, benign repair, and push refusal using local
  bare repositories.
- **Git branch:** I-1790562445-preserve-parent-history-in-executor-git-recovery/step4-hermetic-regression-tests
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/tests/test_executor.py`:
  - Locate `test_main_git_recovery_on_corrupted_repo`:
    - Remove the defective assertion at line 342:
      `self.assertTrue(any(cmd[:3] == ["git", "symbolic-ref", "HEAD"] for cmd in called_cmds))`.
    - Update the test to assert that `shutil.rmtree` was NOT called on `.git`.
    - Assert that the backup directory `.git-unusable-*` was created.
    - Assert that `.git/info/exclude` contains the backup directory name.
  - Implement new hermetic test cases using temporary local repositories and local bare remotes:
    - Test 1: Full corrupted `.git` recovery with history preservation:
      - Set up a bare remote in a temporary directory with an initial commit on `plan_branch` containing codebase files.
      - Clone the workspace from the bare remote.
      - Simulate agent modifications in the working tree.
      - Corrupt or delete the workspace `.git`.
      - Invoke `executor.main()`.
      - Assert that the resulting commit on the execution branch has `plan_branch` tip as its parent
        (`git rev-parse HEAD^ == base_tip`).
      - Assert that the commit tree contains the original codebase files alongside `executions/<id>.md` and
        `ledger/executions.jsonl`.
      - Assert that no `shutil.rmtree` was called on `.git`.
    - Test 2: Benign probe failures repaired without re-initialization:
      - Sub-case (a): Dubious ownership error: simulate safe.directory failure; assert executor repairs config locally
        and reuses repository without rebuild.
      - Sub-case (b): Stale `.git/index.lock`: create index.lock; assert executor clears stale lock and completes
        without rebuild.
      - Sub-case (c): Leaked `GIT_DIR`: set `GIT_DIR` in environment; assert executor strips it and executes normally.
    - Test 3: Unrecoverable remote branch refusal to push:
      - Set up bare remote where `plan_branch` does not exist or fetch fails.
      - Corrupt `.git`.
      - Invoke `executor.main()`.
      - Assert that no `git push` is executed.
      - Assert that `holon-knowledge/ledger/executions.jsonl` contains `status: "failure"`.
      - Assert that the backup directory is preserved.
  - Strictly follow `apps/sandbox-executor/docs/hermetic_testing.md`:
    - Every test must pin `HOLON_REPO_DIR` to a temporary fixture.
    - Patch `get_workspace_dir` and `cleanup_repo_dir`.
    - Zero network or SSH calls.

### Dependencies & Criticality

- **Depends on:** Steps 1, 2, 3
- **Is Bottleneck:** YES (Validates all acceptance criteria of Bean 0019)

### Safety & Constraint Considerations

- **Relevant rules:** `apps/sandbox-executor/docs/hermetic_testing.md` (Rules 1-5, Sandbox Safety Rail),
  `holon-config/world/ruleset.md` §3 (Testing Constraints).
- **Potential failure modes for this step:**
  - Running tests without mocking `cleanup_repo_dir`, potentially deleting the live workspace.
  - Flaky tests due to timestamp collisions in backup directory naming.
- **Guardrails and early‑abort checks:**
  - Enforce `tempfile.TemporaryDirectory` wrapping in all test cases.
  - Verify all git commands in tests use `--git-dir` or isolated `cwd`.

### Success & Discard Criteria

- **Success:** All new and modified tests pass cleanly under both `not integration_test and not stress` and
  `integration_test` marker selections, confirming 100% of recovery requirements.
- **Discard:** Discard if tests require real network connections or docker privileges.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.0   |
| impact_pred         | 90.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 5.5   |
| ev_pred             | 77.35 |

### Step Metrics Rationale

Step 4 has entropy of 2.0 and cost of 4.0 because constructing realistic git corruption scenarios hermetically requires
mocking diverse failure modes. Learning value is 5.5 and EV is 77.35.

---

## Step 5: Code Style Conformance and Sandbox Safety Rail Verification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Routine quality assurance and execution of sandbox safety rail verification checks.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Verify formatting, linting, and test execution conformance using `ruff`, `pytest`, and `prettier`
  strictly within an isolated scratch copy under `/tmp` per `apps/sandbox-executor/docs/hermetic_testing.md`.
- **Git branch:** I-1790562445-preserve-parent-history-in-executor-git-recovery/step5-conformance-verification
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Code Style & Static Analysis:
  - Run `uv run ruff check .` within `apps/sandbox-executor` to ensure no linting regressions.
  - Run `uv run ruff format --check .` to guarantee PEP 8 formatting compliance.
  - Run `npx prettier --write "**/*.md"` to format all modified markdown documentation and plans.
- Sandbox Safety Rail Verification:
  - Create an isolated test scratch copy: copy `/home/holon/.holon-sandbox/workspace` to `/tmp/holon-fixture`.
  - Set `export HOLON_REPO_DIR=/tmp/holon-test-fixture`.
  - Switch to `/tmp/holon-fixture/apps/sandbox-executor`.
  - Run the unit selection: `uv run pytest -m "not integration_test and not stress"`.
  - Run the integration selection: `uv run pytest -m "integration_test"`.
  - Confirm all tests pass with zero failures and that `/tmp/holon-fixture` remains completely intact.
  - Clean up temporary directories under `/tmp` after verification.

### Dependencies & Criticality

- **Depends on:** Steps 1, 2, 3, 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `apps/sandbox-executor/docs/hermetic_testing.md` §3 (Sandbox Safety Rail),
  `holon-config/world/ruleset.md` §1 & §2.
- **Potential failure modes for this step:**
  - Accidentally running pytest discovery directly from `/home/holon/.holon-sandbox/workspace`.
- **Guardrails and early‑abort checks:**
  - Verify current working directory is explicitly `/tmp/holon-fixture` before issuing `uv run pytest`.

### Success & Discard Criteria

- **Success:** Zero ruff lint errors, clean formatting, and 100% test pass rate in both unit and integration selections
  in isolated scratch environment.
- **Discard:** Discard if linting or formatting checks fail.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.8   |
| impact_pred         | 70.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.5   |
| ev_pred             | 66.21 |

### Step Metrics Rationale

High success probability (0.96) and low entropy (0.8) as this step involves standard automated quality gates and
verification. EV is 66.21.
