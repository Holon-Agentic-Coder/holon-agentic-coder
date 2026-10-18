# Plan for I-1792302568-review-loop-agent-conflict-guard-and-pre-commit

- **Plan ID:** P-1792302582-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.4.0)
- **Created At:** 2026-10-18T05:49:42.863Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical investigation into containerised role dispatching for review
  loops, proactive diff3 git conflict handling, JSONL primary key collision protection, and pre-commit secret detection.
- **Safety priority level:** elevated
- **Priority Justification:** Triggered by `docs/safety.md` §1 (Git as safety boundary: isolated branch execution), §2
  (Mandatory execution sandboxing: Docker container isolation and unprivileged `holon` execution), §3 (Trust boundaries:
  strict adherence to agent credentials and roles), §4 (Entropy as safety signal: bounding iteration cycles and blast
  radius), §5 (Human review as final gate: Bean 0034 Human-Only PR Merging prohibition on autonomous merge to `main`),
  `holon-config/world/constraints.md` §1 (Git flow constraints: strict branch prefix isolation and commit boundaries),
  §2 (Sandbox containment tiers: zero credential leakage, network isolation), §3 (Ledger immutability: zero modification
  policy for append-only archives), and `holon-config/world/ruleset.md` §1, §2, §3 (Python `==3.13.*`, monorepo
  workspace layout under `apps/sandbox-executor`, PEP 8, static type annotations, docstring integrity, pytest 9.1.1
  hermetic test boundaries). Elevated safety ensures that the autonomous review loop strictly preserves human gating
  prior to any branch integration with `main`, while securing credentials against pre-commit leakage.

## Exploration

- **Proportion of steps that are exploratory:** 0.14
- **Justification:** Step 1 performs a focused informational audit of existing entrypoint patterns (`intent_creator.py`,
  `planner.py`, `executor.py`), `role_dispatcher.sh` role routing, diff3 conflict marker formats (`|||||||`), ledger
  primary key collision behaviors, and pre-commit regex path normalisation prior to modifying production code.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.40  |
| impact_pred         | 96.0  |
| cost_pred           | 14.2  |
| learning_value_pred | 5.5   |
| ev_pred             | 77.71 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.93. Governed by the critical bottleneck step (Step 4: 0.93), which implements the full
  containerised Review Loop Agent entrypoint (`review_loop.py`). Orchestrating isolated git workspace cloning, upstream
  fetching, proactive conflict detection, iterative verification passes (ruff, pytest, pre-commit, make lint),
  multi-pass AI agent reviewer and resolver invocations, and trailer-annotated ledger commit generation within a
  container constitutes the primary execution risk surface.
- **entropy_pred**: 0.40. Evaluated as the maximum single-step risk profile (Step 4: 0.40), where sub-process chaining,
  container filesystem mutations, and multi-pass agent execution introduce behavioral entropy. The cumulative sum of
  predicted step entropies across all seven steps is 1.75 (`0.20 + 0.30 + 0.25 + 0.40 + 0.25 + 0.15 + 0.20 = 1.75`),
  remaining well below the allocated entropy budget of 15.0.
- **impact_pred**: 96.0. High architectural and operational impact fulfilling Beans 0118, 0121, 0110, 0105, and 0106:
  establishing the fourth first-class containerised agent role (`Holon Review Loop Agent`), eliminating JSONL merge
  conflict stalls across all flow stages, enforcing rigorous credential and scratch file hygiene at commit boundaries,
  and standardising documentation under Australian English.
- **cost_pred**: 14.2. Calculated as the direct sum of predicted step costs
  (`1.2 + 2.4 + 1.8 + 3.2 + 2.0 + 1.4 + 2.2 = 14.2`).
- **learning_value_pred**: 5.5. Epistemic gain delivered to the system through empirical discovery of containerised
  reviewer/resolver feedback loops, diff3 ledger reconciliation semantics, deterministic pre-commit path matching across
  hidden directories, and Australian English linguistic standardisation.
- **ev_pred**: 77.71. Derived using the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system coefficients
  `lambda = 0.3` and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.93 * 96.0 + 0.5 * 5.5 - 0.3 * 0.40 - 14.2 = 89.28 + 2.75 - 0.12 - 14.2 = 77.71`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: strict prefix isolation, commit boundaries
    scoped to active step branch, mandatory rebase discipline).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: container isolation, process safety, zero
    credential leakage).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: append-only policy for
    `holon-knowledge/ledger/executions.jsonl`, `plans.jsonl`, `intents.jsonl`).
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python `==3.13.*`, uv workspace management
    under `apps/sandbox-executor`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, strict static type hints,
    docstring preservation).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: pytest 9.1.1, test hermeticity, preserve test boundaries in
    `apps/sandbox-executor/tests/`).
  - `docs/safety.md` §1 & §5 (Git as safety boundary, Human review as final gate: Bean 0034 Human-Only PR Merging rule
    requiring unconditional halt at consensus approval without merging to `main`).
  - Intent Q1 Answer: Stage 4 runs the first-class containerised 'Holon Review Loop Agent'
    (`user.name='Holon Review Loop Agent'`, `user.email='review-loop-agent@holon-agentic-coder.com'`) via
    `HOLON_ROLE='review-loop'` dispatched by `role_dispatcher.sh` to
    `python3 -m sandbox_executor.entrypoint.review_loop`, invoked by `cli.py` via
    `run_docker_container('review-loop', image_name, container_args, ...)`.
  - Intent Q2 Answer: `reconcile_jsonl_ledger_conflict` prioritises `action_id` before `execution_id` so action records
    do not collide, and resets section flags on diff3 `|||||||` markers. `scripts/pre_commit_check.py` uses
    `.removeprefix('./')` rather than `.lstrip('./')` so leading dots on `.index/` and `.agents/` are preserved.
- **Potential violations or edge cases:**
  - Autonomous merge attempts to `main` upon consensus approval, violating Bean 0034 and `docs/safety.md` §5.
  - Primary key collisions in `executions.jsonl` where discrete action entries (`action_id`) share an `execution_id`
    with summary entries or peer actions, leading to unintentional deduplication dropping.
  - Path normalisation using `.lstrip('./')` stripping leading dots from directories like `.index/` or `.agents/` and
    erroneously treating them as scratch files.
  - Unhandled `diff3` markers (`|||||||`) corrupting JSONL ledger reconciliation.
  - Subprocess mocking in unit tests returning non-string stdout (e.g. `MagicMock`), causing type errors during conflict
    inspection.
  - Leaking temporary test files (`.test_tmp.py`) into the workspace during pre-commit unit tests.
- **Mitigations built into the plan:**
  - Strict preservation of `StageStatus.HALTED_FOR_HUMAN` upon review consensus approval, emitting manual approval
    commands without merging.
  - Refactoring `get_pk` in `reconcile_jsonl_ledger_conflict` to check `action_id` before `execution_id`, ensuring
    discrete action records never collide.
  - Implementing `.removeprefix('./')` rather than `.lstrip('./')` in `scripts/pre_commit_check.py`, and whitelisting
    legitimate test files (`^tests/test_.*\.py$`, `^apps/sandbox-executor/tests/test_.*\.py$`).
  - Parsing diff3 conflict markers (`<<<<<<<`, `|||||||`, `=======`, `>>>>>>>`) with section flag resets in ledger
    reconcilers.
  - Adding `isinstance(..., str)` guards on git command stdout within `check_and_resolve_stage_conflicts` for seamless
    `MagicMock` test compatibility.
  - Using `tempfile.TemporaryDirectory` with robust try/finally cleanup in all unit tests.
- **Residual risk accepted (and why):**
  - Reliance on Docker daemon availability when executing containerised review loops from the host CLI; accepted because
    `--local` and `--dry-run` provide seamless host fallback execution pathways.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 1.75
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

This plan implements the complete first-class containerised Holon Review Loop Agent ecosystem, proactive conflict
detection, pre-commit security guards, and Australian English documentation standards across four core beans:

1. **Bean 0118 (First-Class Containerised Holon Review Loop Agent):**
   - Update `apps/sandbox-executor/entrypoint/role_dispatcher.sh` to route `HOLON_ROLE` values in (`review-loop`,
     `pr-review-loop`, `reviewer`) to `python3 -m sandbox_executor.entrypoint.review_loop "$@"`.
   - Author `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py` following the architectural pattern
     of `intent_creator.py`, `planner.py`, and `executor.py`.
   - Clone the target `execution_branch` into `get_workspace_dir()` (`/home/holon/.holon-sandbox/workspace`), fetch
     `origin/main`, and execute proactive conflict resolution.
   - Configure local git identity: `user.name='Holon Review Loop Agent'`,
     `user.email='review-loop-agent@holon-agentic-coder.com'`.
   - Automate PR creation or update via `generate_semantic_pr_metadata` and `create_or_update_pr`.
   - Iteratively execute verification checks (`ruff`, `pytest`, `pre_commit_check.py`, `make lint`, CI checks) alongside
     AI agent reviewer and resolver passes (`get_runner(agent_name)`).
   - Commit code fixes and ledger entries (`ACT-REV`, `ACT-FIND`, `ACT-RES`, `ACT-POST`, `REV-summary`) with RFC 2822
     trailers, push to origin `execution_branch`, dual-post consensus to GitHub, and halt for human review under
     Bean 0034.
   - Update `cli.py` to support `--agent`, `--model`, `--mitm-web`, and `--local` for review commands, dispatching
     `run_docker_container('review-loop', ...)`.
   - Update `flow.py` review stage helpers to set `GIT_AUTHOR_NAME` and `GIT_COMMITTER_NAME` to
     `Holon Review Loop Agent` and `review-loop-agent@holon-agentic-coder.com`.

2. **Bean 0121 (Proactive Conflict Detection & Resolution):**
   - Define `ConflictResolutionError` in `flow.py`.
   - Implement `reconcile_jsonl_ledger_conflict` in `flow.py` prioritizing `action_id` before `execution_id` in `get_pk`
     and parsing diff3 `|||||||` base markers.
   - Implement `check_and_resolve_stage_conflicts` in `flow.py` with `isinstance(..., str)` checks on git stdout for
     test mock safety.
   - Wire proactive conflict checks into all 5 flow stages (`intent`, `plan`, `execute`, `review`, `calibrate`) and
     entrypoints.

3. **Bean 0110 (Pre-Commit Credential & Scratch Guard):**
   - Implement `scripts/pre_commit_check.py` to scan staged files for secrets and uncommitted scratch files.
   - Use `.removeprefix('./')` to safely preserve leading dots on `.index/*` and `.agents/*`.
   - Whitelist standard test paths (`^tests/test_.*\.py$`, `^apps/sandbox-executor/tests/test_.*\.py$`).
   - Wire `pre-commit-check` into `Makefile` and `.PHONY`.
   - Add `pythonpath = ['.', 'apps/sandbox-executor/src']` to `pyproject.toml`.
   - Untrack tracked scratch files `tmp/doc_improvements.md` and `tmp/pr_46.diff`.

4. **Beans 0105 & 0106 (Australian English Audit & Documentation):**
   - Author `docs/audit-report-2026-10.md` detailing ecosystem health and architectural remediations.
   - Standardise `docs/audit_report.md` using strict Australian English spelling (`standardised`, `optimise`,
     `Serialisation`, `behaviour`).
   - Ensure zero errors across `ruff check`, `ruff format --check`, `make lint`, and `pytest`.

---

## Step 1: Reconnaissance and Audit of Container Entrypoints, Ledger Key Schemas, and Pre-Commit Guard Specifications (Discovery & Exploration)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only inspection of entrypoints, role dispatching, diff3 conflict marker behavior, ledger row
  primary keys, and pre-commit regex matching without mutating production code.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Review loop agent containerisation and proactive diff3 ledger conflict resolution can be
  integrated into the existing entrypoint architecture without breaking legacy flow runners or corrupting ledger
  provenance.
- **Learning target:** Exact CLI signatures and container argument expectations for `review-loop`, diff3 marker handling
  in git merge conflicts, primary key collision patterns between action and summary records, and path normalisation
  requirements in pre-commit filtering.
- **Maximum acceptable cost for this learning:** 1.5 cost units.

### Intent & Git Integration

- **Step Intent:** Inspect `role_dispatcher.sh`, `intent_creator.py`, `planner.py`, `executor.py`, `flow.py`, `cli.py`,
  `Makefile`, and `pyproject.toml` to baseline exact integration boundaries.
- **Git branch:**
  `I-1792302568-review-loop-agent-conflict-guard-and-pre-commit/P-1792302582-antigravity-agent-gemini-3.8-flash-medium/E-1792302584-audit-entrypoints-and-schemas/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Audit container entrypoint conventions:
   - Inspect `role_dispatcher.sh` environment variables (`HOLON_ROLE`, `HOLON_AGENT_ID`, secret bundle ingestion, CA
     bundle synthesis).
   - Compare entrypoint lifecycles in `intent_creator.py`, `planner.py`, and `executor.py` (workspace resolution via
     `get_workspace_dir()`, repository URL via `get_repo_url()`, clean git environment via `_get_clean_git_env()`, git
     identity configuration, ledger appending).
2. Audit conflict detection and ledger primary key dynamics:
   - Analyze `reconcile_ledger_rows` and `extract_valid_ledger_rows` in `flow.py`.
   - Identify how `action_id` vs `execution_id` behaves in `executions.jsonl` when multiple actions share the same
     execution context.
   - Verify diff3 conflict syntax (`<<<<<<<`, `|||||||`, `=======`, `>>>>>>>`) and state machine resets.
3. Audit pre-commit guard and file path normalisation:
   - Identify secret patterns and file extension rules required for `scripts/pre_commit_check.py`.
   - Analyze path stripping behavior: contrast `.lstrip('./')` (which strips leading dots from `.index` and `.agents`)
     with `.removeprefix('./')`.
   - Inspect tracked scratch files in `tmp/` (`doc_improvements.md`, `pr_46.diff`).
4. Audit documentation language conventions:
   - Identify American English spellings in `docs/audit_report.md` requiring Australian English standardisation
     (`optimization` -> `optimisation`, `standardized` -> `standardised`, `serialization` -> `serialisation`, `behavior`
     -> `behaviour`).

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (Git boundary), §2 (Sandboxing), `holon-config/world/constraints.md` §3
  (Ledger immutability).
- **Potential failure modes for this step:** Misidentifying key schemas or overlooking role dispatcher environment
  variables.
- **Guardrails and early‑abort checks:** Validate findings directly against existing codebase source files without
  applying mutations.

### Success & Discard Criteria

- **Success:** Detailed architectural blueprints finalized for `review_loop.py`, `reconcile_jsonl_ledger_conflict`,
  `scripts/pre_commit_check.py`, and documentation updates.
- **Discard:** Discard if entrypoint design breaks container isolation or conflicts with immutable ledger rules.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.20  |
| impact_pred         | 82.0  |
| cost_pred           | 1.2   |
| learning_value_pred | 4.5   |
| ev_pred             | 81.35 |

### Step Metrics Rationale

High probability (`0.98`) and low entropy (`0.20`) for a read-only reconnaissance step. Cost is low (`1.2`), and
learning value is high (`4.5`) for formalising diff3 and entrypoint mechanics. Expected value derivation:
`EV = 0.98 * 82.0 + 0.5 * 4.5 - 0.3 * 0.20 - 1.2 = 80.36 + 2.25 - 0.06 - 1.2 = 81.35`.

---

## Step 2: Proactive Conflict Detection & JSONL Ledger Reconciliation Engine across Flow Stages (Bean 0121)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core engine enhancements in `flow.py` establishing robust ledger conflict reconciliation and proactive
  conflict resolution across all flow stages.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement `ConflictResolutionError`, `reconcile_jsonl_ledger_conflict`, and
  `check_and_resolve_stage_conflicts` in `flow.py`, and wire proactive conflict resolution into all 5 flow stages and
  entrypoints.
- **Git branch:**
  `I-1792302568-review-loop-agent-conflict-guard-and-pre-commit/P-1792302582-antigravity-agent-gemini-3.8-flash-medium/E-1792302585-proactive-conflict-engine/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - Define custom exception: `class ConflictResolutionError(RuntimeError): pass`.
   - Implement `reconcile_jsonl_ledger_conflict(content: str, ledger_type: str) -> str`:
     - Parse diff3 conflict markers: support `<<<<<<<`, `|||||||` (common ancestor in diff3), `=======`, and `>>>>>>>`.
     - Ensure section state flags correctly reset when encountering `|||||||` so ancestor blocks are processed cleanly.
     - In primary key resolution `get_pk(row)`: prioritize `action_id` before `execution_id` so discrete action entries
       do not collide with summary rows or other actions sharing the same execution ID.
     - Deterministically deduplicate rows based on revision and timestamp, and return valid sorted JSONL.
   - Implement
     `check_and_resolve_stage_conflicts(repo_dir: str, target_branch: str = "main", stage_name: str = "") -> dict[str, Any]`:
     - Run `git status --porcelain` to detect uncommitted or conflicting files. Guard stdout parsing with
       `isinstance(stdout, str)` so test `MagicMock` instances returning mock objects do not cause runtime type errors.
     - Run git merge-tree or merge base inspection against `origin/<target_branch>` to detect upstream divergence
       proactively.
     - If conflicts exist in `holon-knowledge/ledger/*.jsonl`, apply `reconcile_jsonl_ledger_conflict` to resolve them
       automatically.
     - If non-ledger code conflicts cannot be cleanly resolved automatically, raise `ConflictResolutionError` detailing
       conflicting files.
2. Wire `check_and_resolve_stage_conflicts` into all 5 pipeline stages:
   - Stage 1 (`run_intent_stage`): check before checkout and commit.
   - Stage 2 (`run_plan_stage`): check before branching from intent.
   - Stage 3 (`run_execute_stage`): check before test execution and commit.
   - Stage 4 (`run_review_stage` and `run_review_loop`): check before review passes.
   - Stage 5 (`run_calibrate_stage`): check before calibration branch updates.
3. Wire into existing entrypoints (`intent_creator.py`, `planner.py`, `executor.py`) to run proactive checks upon
   workspace initialization.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 (Git Flow constraints), §3 (Ledger immutability),
  `docs/safety.md` §1 (Git as safety boundary).
- **Potential failure modes for this step:** Prematurely discarding non-conflicting ledger rows or failing to handle
  diff3 markers.
- **Guardrails and early‑abort checks:** Verify round-trip parsing of valid JSONL lines; ensure zero loss of historical
  records.

### Success & Discard Criteria

- **Success:** `reconcile_jsonl_ledger_conflict` handles standard and diff3 conflict blocks, distinguishes `action_id`
  from `execution_id`, and `check_and_resolve_stage_conflicts` proactively resolves ledger conflicts across all stages.
- **Discard:** Discard if ledger records are dropped or if non-string stdout in tests raises unhandled exceptions.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.30  |
| impact_pred         | 94.0  |
| cost_pred           | 2.4   |
| learning_value_pred | 4.0   |
| ev_pred             | 88.81 |

### Step Metrics Rationale

High probability (`0.95`) and moderate entropy (`0.30`) for pure Python text and git state reconciliation. Cost is
`2.4`, and learning value is `4.0`. Expected value derivation:
`EV = 0.95 * 94.0 + 0.5 * 4.0 - 0.3 * 0.30 - 2.4 = 89.30 + 2.00 - 0.09 - 2.4 = 88.81`.

---

## Step 3: Pre-Commit Security, Credential Scrubbing, and Scratch File Guard (Bean 0110)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Self-contained pre-commit validation script, root Makefile target integration, and workspace
  configuration cleanup.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement `scripts/pre_commit_check.py`, wire `pre-commit-check` into `Makefile`, update
  `pyproject.toml` `pythonpath`, and untrack temporary scratch files.
- **Git branch:**
  `I-1792302568-review-loop-agent-conflict-guard-and-pre-commit/P-1792302582-antigravity-agent-gemini-3.8-flash-medium/E-1792302586-pre-commit-guard/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Implement `scripts/pre_commit_check.py`:
   - Inspect staged or working tree files for sensitive tokens, unredacted credentials, and disallowed scratch files.
   - Use path normalisation with `.removeprefix('./')` (never `.lstrip('./')`), ensuring that directories starting with
     a dot (e.g. `.index/*`, `.agents/*`) preserve their leading dot.
   - Enforce file pattern whitelisting:
     - Whitelist standard test file patterns: `^tests/test_.*\.py$` and `^apps/sandbox-executor/tests/test_.*\.py$`.
     - Block unwhitelisted test scratch files (e.g. `.test_tmp.py`, `test_scratch.py` outside test directories).
     - Block credential patterns: private keys, API key literals (`sk_live_`, `ghp_`, `github_pat_`, `AIza`, etc.).
   - Support CLI arguments: `--repo-dir`, `--staged-only`, `--fix` (optional).
   - Exit with code 0 on clean scan, code 1 on detected violations.
2. Wire into root `Makefile`:
   - Add `pre-commit-check` to `.PHONY`.
   - Add `pre-commit-check` target running `python3 scripts/pre_commit_check.py`.
   - Update `help` target in `Makefile` to document `pre-commit-check`.
3. Update `pyproject.toml`:
   - In `[tool.pytest.ini_options]`, set `pythonpath = ['.', 'apps/sandbox-executor/src']`.
4. Clean up tracked scratch files:
   - Run `git rm --cached tmp/doc_improvements.md tmp/pr_46.diff` (or remove them if untracked).
   - Ensure `tmp/` remains ignored or clean in git index.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §2 (Sandbox containment: zero credential leakage),
  `holon-config/world/ruleset.md` §1 (Python environment), Intent Q2 Answer.
- **Potential failure modes for this step:** Breaking `.index/` or `.agents/` path checks due to improper prefix
  stripping.
- **Guardrails and early‑abort checks:** Verify path handling unit tests cover hidden directories and nested paths.

### Success & Discard Criteria

- **Success:** `scripts/pre_commit_check.py` accurately identifies secrets and scratch files while preserving `.index`
  paths, `make pre-commit-check` runs cleanly, and `pyproject.toml` contains updated `pythonpath`.
- **Discard:** Discard if valid test files are falsely flagged or if `.index` files are improperly categorized.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.25  |
| impact_pred         | 90.0  |
| cost_pred           | 1.8   |
| learning_value_pred | 3.0   |
| ev_pred             | 86.03 |

### Step Metrics Rationale

High probability (`0.96`) and low entropy (`0.25`) for regex and filesystem inspection. Cost is `1.8`, and learning
value is `3.0`. Expected value derivation:
`EV = 0.96 * 90.0 + 0.5 * 3.0 - 0.3 * 0.25 - 1.8 = 86.40 + 1.50 - 0.075 - 1.8 = 86.025 -> 86.03`.

---

## Step 4: First-Class Containerised Holon Review Loop Agent Entrypoint & Role Dispatcher (Bean 0118 - Part 1)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Modular container entrypoint implementation and shell dispatcher update establishing the autonomous
  review loop agent.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Creating `review_loop.py` following the exact lifecycle of `intent_creator.py`,
  `planner.py`, and `executor.py` enables seamless containerised review loop execution with isolated git operations and
  trailer-annotated ledger recording.
- **Learning target:** Interaction between automated verification tools (ruff, pytest, make lint) and multi-pass agent
  runner passes inside the container sandbox.
- **Maximum acceptable cost for this learning:** 3.5 cost units.

### Intent & Git Integration

- **Step Intent:** Update `role_dispatcher.sh` to route review roles, and create
  `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`.
- **Git branch:**
  `I-1792302568-review-loop-agent-conflict-guard-and-pre-commit/P-1792302582-antigravity-agent-gemini-3.8-flash-medium/E-1792302587-container-review-loop-entrypoint/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/entrypoint/role_dispatcher.sh`:
   - Update case statement for `ROLE` to match `review-loop | pr-review-loop | reviewer)`:
     - Dispatch `exec python3 -m sandbox_executor.entrypoint.review_loop "$@"`
2. Implement `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`:
   - Parse CLI arguments: `execution_branch`, optional `agent_name`, `model_name`, `--max-iterations`, `--auto-pr`,
     `--dry-run`.
   - Setup isolated workspace directory using `get_workspace_dir()` (`/home/holon/.holon-sandbox/workspace`) and clean
     prior state using `cleanup_repo_dir()`.
   - Clone `execution_branch` from `get_repo_url()` using single-branch depth 1.
   - Fetch `origin/main` to enable diff and mergeability evaluation.
   - Run `check_and_resolve_stage_conflicts()` to proactively detect and resolve ledger conflicts.
   - Configure local git identity:
     - `git config --local user.name "Holon Review Loop Agent"`
     - `git config --local user.email "review-loop-agent@holon-agentic-coder.com"`
   - Raise or update GitHub PR if `auto_pr` is enabled and `gh` is available, using `generate_semantic_pr_metadata()`
     and `create_or_update_pr()`.
   - Execute iterative review loop:
     - Run automated verification checks: `ruff check`, `ruff format --check`, `pytest`,
       `python3 scripts/pre_commit_check.py`, `make lint`, and GitHub CI check probes.
     - If findings occur, execute AI agent review pass via `get_runner(agent_name)` to analyze root causes.
     - Execute AI agent resolver pass via `get_runner(agent_name)` inside the container workspace to apply code fixes.
     - Converge formatting using `converge_prettier()`.
     - Record discrete ledger records in `holon-knowledge/ledger/executions.jsonl` using `atomic_ledger_append()`:
       `ACT-REV` (iteration review), `ACT-FIND` (findings detected), `ACT-RES` (resolver applied), `ACT-POST` (PR
       comments), and `REV-summary` (consensus outcome).
     - Commit any code fixes as `'Holon Review Loop Agent <review-loop-agent@holon-agentic-coder.com>'` with RFC 2822
       trailers and push to origin `execution_branch`.
   - Dual-post consensus review to GitHub (`gh pr comment` for discussion tab + `gh pr review` for official submission
     with HTTP 422 fallback).
   - Strictly halt for human merge under Bean 0034 (`HALTED_FOR_HUMAN`).

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** YES (Core autonomous review loop implementation; failure blocks containerised Stage 4 execution)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (Git boundary), §2 (Sandboxing), §5 (Human review boundary: Bean 0034), Intent
  Q1 Answer.
- **Potential failure modes for this step:** Git push failure due to missing authentication, or attempting autonomous
  merge to `main`.
- **Guardrails and early‑abort checks:** Unconditionally halt at consensus approval without issuing merge commands;
  respect `HOLON_SKIP_PUSH` and `dry_run`.

### Success & Discard Criteria

- **Success:** `review_loop.py` executes end-to-end within container workspace, commits fixes as 'Holon Review Loop
  Agent', records ledger actions, dual-posts review, and halts for human merge.
- **Discard:** Discard if agent attempts an autonomous merge to `main` or if secret credentials are exposed in commit
  messages.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.40  |
| impact_pred         | 96.0  |
| cost_pred           | 3.2   |
| learning_value_pred | 5.0   |
| ev_pred             | 88.46 |

### Step Metrics Rationale

Bottleneck step with probability (`0.93`) and highest single-step entropy (`0.40`) reflecting multi-subprocess and
container lifecycle orchestration. Cost is `3.2`, and learning value is high (`5.0`). Expected value derivation:
`EV = 0.93 * 96.0 + 0.5 * 5.0 - 0.3 * 0.40 - 3.2 = 89.28 + 2.50 - 0.12 - 3.2 = 88.46`.

---

## Step 5: CLI Subcommand Integration, Container Dispatch, and Flow Review Stage Identity Harmonisation (Bean 0118 - Part 2)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Straightforward parameter wiring in `cli.py` and git environment variable alignment in `flow.py`.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update `cli.py` to dispatch `run_docker_container('review-loop', ...)` when on host, and update
  `GIT_AUTHOR_NAME`/`GIT_COMMITTER_NAME` in `flow.py` review stage helpers.
- **Git branch:**
  `I-1792302568-review-loop-agent-conflict-guard-and-pre-commit/P-1792302582-antigravity-agent-gemini-3.8-flash-medium/E-1792302588-cli-and-flow-review-dispatch/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/cli.py`:
   - Update `review_parser` arguments to accept:
     - `--agent`: agent ID (defaulting to `antigravity-agent`).
     - `--model`: model ID (defaulting to `gemini-3.8-flash-medium`).
     - `--mitm-web`: launch mitmweb dashboard on port 8081.
     - `--local`: bypass container execution and run review loop directly on the host.
   - Update command dispatch logic for `args.command in ("review-loop", "review", "pr-review-loop")`:
     - If `--local` or `--dry-run` is passed, or if `HOLON_IN_SANDBOX` / `HOLON_ROLE` is set: invoke
       `run_review_loop_cli(args, ...)` directly.
     - Otherwise (when invoked on host): dispatch container execution via
       `run_docker_container("review-loop", image_name, container_args, **kwargs)` passing `target`, `agent`, `model`,
       `token_reduce`, `mitm_web`, and `repo_dir`.
2. In `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - In review stage commit helpers and `run_review_stage` / `run_review_loop`:
     - Set `GIT_AUTHOR_NAME` to `"Holon Review Loop Agent"`.
     - Set `GIT_AUTHOR_EMAIL` to `"review-loop-agent@holon-agentic-coder.com"`.
     - Set `GIT_COMMITTER_NAME` to `"Holon Review Loop Agent"`.
     - Set `GIT_COMMITTER_EMAIL` to `"review-loop-agent@holon-agentic-coder.com"`.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §2 (Sandbox containment tiers),
  `apps/sandbox-executor/README.md`.
- **Potential failure modes for this step:** Passing unrecognized flags to container entrypoints or Docker run commands.
- **Guardrails and early‑abort checks:** Provide seamless fallback to host execution when `--local` is specified or
  Docker is absent.

### Success & Discard Criteria

- **Success:** `holon review-loop` dispatches container by default on the host while `--local` executes in-process; all
  review stage commits carry the 'Holon Review Loop Agent' identity.
- **Discard:** Discard if CLI invocation fails or crashes when Docker is unavailable.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.25  |
| impact_pred         | 92.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 3.5   |
| ev_pred             | 88.00 |

### Step Metrics Rationale

High probability (`0.96`) and low entropy (`0.25`) for argument parsing and environment variable updates. Cost is `2.0`,
and learning value is `3.5`. Expected value derivation:
`EV = 0.96 * 92.0 + 0.5 * 3.5 - 0.3 * 0.25 - 2.0 = 88.32 + 1.75 - 0.075 - 2.0 = 87.995 -> 88.00`.

---

## Step 6: Australian English Ecosystem Audit Report & Documentation Standardisation (Beans 0105 & 0106)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Comprehensive documentation authoring and spelling standardisation across existing audit records.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add `docs/audit-report-2026-10.md` and update `docs/audit_report.md` using strict Australian English
  (`en-AU`).
- **Git branch:**
  `I-1792302568-review-loop-agent-conflict-guard-and-pre-commit/P-1792302582-antigravity-agent-gemini-3.8-flash-medium/E-1792302589-audit-report-en-au/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Author `docs/audit-report-2026-10.md`:
   - Detail ecosystem posture as of October 2026, documenting architectural remediations:
     - Review Loop Agent containerisation (Bean 0118).
     - Proactive conflict detection and ledger primary key reconciliation (Bean 0121).
     - Pre-commit security, credential hygiene, and scratch file protection (Bean 0110).
     - Token reduction proxy and mutual TLS trust store injection.
     - Adherence to Bean 0034 (Human-Only PR Merging).
   - Use strict Australian English throughout: `standardised`, `optimise`, `Serialisation`, `behaviour`, `artefacts`,
     `prioritise`, `harmonise`.
2. Update `docs/audit_report.md`:
   - Replace American English variants with strict Australian English:
     - `optimization` -> `optimisation`
     - `standardized` -> `standardised`
     - `serialization` / `Serialization` -> `serialisation` / `Serialisation`
     - `behavior` -> `behaviour`
   - Preserve existing audit findings, findings matrix, and architectural analysis.
3. Converge Prettier formatting on modified markdown documents.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §6 (Documentation integrity), `holon-config/world/ruleset.md` §2.
- **Potential failure modes for this step:** Prettier formatting oscillations or unescaped markdown tables.
- **Guardrails and early‑abort checks:** Run `npx prettier --check` across modified docs to ensure syntax stability.

### Success & Discard Criteria

- **Success:** `docs/audit-report-2026-10.md` created, `docs/audit_report.md` updated with strict Australian English,
  and all Prettier checks pass cleanly.
- **Discard:** Discard if American English spellings remain or if markdown linting fails.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.15  |
| impact_pred         | 86.0  |
| cost_pred           | 1.4   |
| learning_value_pred | 2.5   |
| ev_pred             | 84.09 |

### Step Metrics Rationale

High probability (`0.98`) and minimal entropy (`0.15`) for markdown documentation. Cost is `1.4`, and learning value is
`2.5`. Expected value derivation:
`EV = 0.98 * 86.0 + 0.5 * 2.5 - 0.3 * 0.15 - 1.4 = 84.28 + 1.25 - 0.045 - 1.4 = 84.085 -> 84.09`.

---

## Step 7: Comprehensive Test Suite Additions & Multi-Linter Quality Gate Validation (Verification & Testing)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Verification and hermetic unit testing validating all newly added modules, entrypoints, and lint rules.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add `apps/sandbox-executor/tests/test_review_loop.py`, `test_pre_commit_check.py`, update
  `test_flow.py` with `Path` imports, and run `ruff`, `make lint`, and `pytest`.
- **Git branch:**
  `I-1792302568-review-loop-agent-conflict-guard-and-pre-commit/P-1792302582-antigravity-agent-gemini-3.8-flash-medium/E-1792302590-verification-and-tests/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/tests/test_review_loop.py`:
   - Test `review_loop.py` argument parsing and role dispatching.
   - Test workspace cloning, upstream main fetching, and conflict check invocation.
   - Test git identity configuration (`Holon Review Loop Agent <review-loop-agent@holon-agentic-coder.com>`).
   - Test iterative verification runs, trailer generation, ledger appending (`ACT-REV`, `ACT-FIND`, `ACT-RES`,
     `ACT-POST`, `REV-summary`), and human merge halt under Bean 0034.
2. Create `apps/sandbox-executor/tests/test_pre_commit_check.py`:
   - Test `scripts/pre_commit_check.py` path normalisation: verify `.removeprefix('./')` preserves leading dots on
     `.index/` and `.agents/`.
   - Test whitelisting of standard test files (`^tests/test_.*\.py$`, `^apps/sandbox-executor/tests/test_.*\.py$`).
   - Test detection and blocking of staged credentials and unwhitelisted scratch files.
   - Guarantee hermeticity: clean up all temporary test files immediately; never leave any `.test_tmp.py` file on disk.
3. Update `apps/sandbox-executor/tests/test_flow.py`:
   - Use `from pathlib import Path` (never unimported `pathlib.Path`).
   - Test `ConflictResolutionError` raising on unresolvable conflicts.
   - Test `reconcile_jsonl_ledger_conflict` handling diff3 `|||||||` markers and checking `action_id` before
     `execution_id` in `get_pk`.
   - Test `check_and_resolve_stage_conflicts` with `isinstance(..., str)` stdout guard on `MagicMock`.
4. Run complete quality gates:
   - Run `ruff check .`
   - Run `ruff format --check .`
   - Run `make lint` (JSON, YAML, shell, Markdown, Python).
   - Run `uv run pytest apps/sandbox-executor/tests -m 'not integration_test and not stress'`.

### Dependencies & Criticality

- **Depends on:** Step 2, Step 3, Step 4, Step 5, Step 6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (pytest 9.1.1, test hermeticity), Intent Q1 & Q2 Answers.
- **Potential failure modes for this step:** Flaky subprocess mocking or lingering temporary test files failing
  pre-commit checks.
- **Guardrails and early‑abort checks:** Use `tempfile.TemporaryDirectory()` context managers to ensure complete
  filesystem cleanup.

### Success & Discard Criteria

- **Success:** 100% of unit tests in `test_review_loop.py`, `test_pre_commit_check.py`, and `test_flow.py` pass cleanly;
  `ruff check`, `ruff format --check`, and `make lint` report zero errors.
- **Discard:** Discard if any regressions occur across existing flow tests or if temporary test files remain on disk.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.20  |
| impact_pred         | 92.0  |
| cost_pred           | 2.2   |
| learning_value_pred | 3.0   |
| ev_pred             | 87.56 |

### Step Metrics Rationale

High probability (`0.96`) and low entropy (`0.20`) for hermetic unit testing. Cost is `2.2`, and learning value is
`3.0`. Expected value derivation: `EV = 0.96 * 92.0 + 0.5 * 3.0 - 0.3 * 0.20 - 2.2 = 88.32 + 1.50 - 0.06 - 2.2 = 87.56`.
