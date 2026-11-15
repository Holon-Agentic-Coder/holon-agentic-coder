### architecture.md

This document describes the **Holon / FIEE (Fractal Intent Evolution Engine)** architecture: a self-evolving, git-native
multi-agent system that plans competitively, executes in sandboxes, and improves via measured feedback loops.

#### Scope

- What the system is made of (components + responsibilities)
- How work flows (intent → plans → execution → measurement → learning)
- Where invariants live (git discipline, metric “physics”, autonomy boundaries)
- How routing works (different models for different work, as a selection pressure)

---

### Core invariants (non-negotiable)

#### 1) Git is the universal state machine

- All intents, plans, executions, and outcomes are represented as git artifacts.
- Agents never “just do work”; they **propose diffs**.

#### 2) Fractal intent (recursive, depth-capped)

- Any intent may spawn sub-intents recursively, in practice through the plan that decides the split, up to
  `MAX_SUB_INTENT_DEPTH` levels below the root intent.
- Sub-intents are first-class and follow the same rules as root intents.

#### 3) Competitive planning variants per intent

- Multiple plan variants compete as hypotheses.
- Plans are scored using shared “physics” metrics (P(success), ΔS, EV, Cost, Impact, LearningValue).

#### 4) Strict rebase discipline; deciding-node-only merges

- **Sub-intents rebase from their deciding node's branch (in practice the plan branch) before and after execution.**
- **Sub-intents may merge ONLY into that branch** (the plan branch, or the execution branch when an execution decided
  the split).
- **No direct merges from sub-intents to `main`.**
- Below the root no human intervention is needed: the agent raises and merges every sub-intent's PR automatically, and
  only the root intent's PR to `main` stops for a human. `holon flow` cuts each child from the plan branch tip, merges
  its PR into the plan branch with a merge commit (a squash on a squash-only repository) after the child's review
  consensus and calibration, and raises one combined PR from the parent execution branch (see [Git Flow](git_flow.md)
  Stage 3 under the flow equivalence guarantee).

#### 5) Sandboxed exploration is allowed (including risky)

- Risky exploration is allowed because it happens in disposable branches + sandboxes.
- The system is designed to **learn from failures**, not just avoid them.

#### 6) Metrics are “physics”

- Metric definitions are stable contracts.
- Estimators may evolve, but definitions, ranges, and ledger recording are immutable without explicit approval.

---

### Core architecture: The Stateless Engine

Holon operates as a **Stateless Engine**. It separates project-specific **Governance** (Priors) from **Experience**
(Memory) using two mandatory directories.

#### 1) `holon-config/` (The Static Priors)

Consolidates human-authored rules, agent identities, and project "physics".

- `prompts/`: Agent identities, missions, and persona templates.
- `schemas/`: Validation rules for the Ledger, KB, and Wisdom Base.
- `world/`: The project "Physics" context (`ruleset.md` and `constraints.md`).
- `rules/`: Programmatic safety and autonomy boundaries.
- `metrics/`: EV weights, coefficients, and estimator constants.

#### 2) `holon-knowledge/` (The Dynamic Experience)

Consolidates machine-authored history and the intelligence evolved from it.

- `ledger/`: The canonical 3-ledger append-only audit trail (`intents.jsonl`, `plans.jsonl`, `executions.jsonl`)
  protected by `LedgerLock`. Auxiliary ledgers (`actions.jsonl`, `reviews.jsonl`) are strictly retired. Micro-actions
  (`entry_type: "action"`, e.g. `ACT-REV`, `ACT-FIND`, `ACT-POST`) are chronologically interleaved alongside lifecycle
  summaries (`entry_type: "summary"`) in `executions.jsonl`.
- `kb/`: (Knowledge Base) Project-specific patterns, tactics, and failure modes.
- `wb/`: (Wisdom Base) Universal invariants and cross-project heuristics.

Agents and system components are **config-driven**; they are initialised with a `config_path` (defaulting to
`holon-config/`) and a `knowledge_path` (defaulting to `holon-knowledge/`).

### Agent CLI Versioning in the Validation Cycle

To guarantee reproducibility and full provenance, the stateless runner does not assume static agent environments.
Instead, when executing plans, it dynamically resolves the exact agent CLI version. The runner tries to query the binary
directly (using `--version`, `-v`, or `version` flags). If the execution environment does not have the binary or the
invocation fails, it falls back to the exact version declared in the orchestrator build environment (defined in
`apps/sandbox-executor/Dockerfile`). The resolved `agent_version` is embedded in the schema validation cycle for both
`plans.jsonl` and `executions.jsonl`, linking the exact binary version with predictions, calibration errors, and final
outputs.

---

### High-level architecture

The components of the Holon engine are organised around the Orchestrator (Meta-Agent), which coordinates the flow of
information between static configurations, dynamic data products, and execution runtimes.

```mermaid
flowchart TD
    subgraph ConfigAndKnowledge [Configs & Knowledge Stores]
        HC["holon-config/ (Static Priors)"]
        Ledger[("Evolution Ledger (JSONL)")]
        KB[("Knowledge Base (KB)")]
        WB[("Wisdom Base (WB)")]
    end

    subgraph SystemCore [Core Holon Engine]
        MA["Orchestrator (Meta-Agent)"]
        IR["Intent Registry"]
        PL["Planner Agent"]
        EV["Plan Evaluator (with Convergence)"]
        RT["Model Router"]
        EX["Sandbox Executor"]
        CU["Curator Agent"]
    end

    %% Seeding configuration & knowledge
    HC --> MA
    Ledger -.-> MA
    KB -.-> MA
    WB -.-> MA

    %% Orchestration Flow
    MA -->|"1. Register Intent"| IR
    MA -->|"2. Request Plans"| PL
    PL -->|"3. Query KB/WB"| KB & WB
    PL -->|"4. Return Plan Variants"| MA
    MA -->|"5. Evaluate & Converge"| EV
    EV -->|"6. Rank & Select Plan"| MA
    MA -->|"7. Select Model"| RT
    MA -->|"8. Execute Plan"| EX
    EX -->|"9. Isolate Container"| SB["Sandbox (Docker/gVisor)"]
    EX -->|"10. Log Events"| Ledger

    %% Curation Loop
    CU -->|"11. Scan & Extract"| Ledger
    CU -->|"12. Propose & Validate"| KB
    KB -->|"13. Ascend Axioms"| WB
```

#### Component map

- **Config Loader**
  - The entry point for the "Static Priors". Provides a unified API to access prompts, schemas, and the world definition
    from `holon-config/`.
- **Knowledge Loader**
  - The entry point for the "Dynamic Experience". Provides agents with the Ledger, KB, and WB from `holon-knowledge/`.
- **Intent Registry**
  - Stores intent metadata: IDs, parent/child links, state, constraints, trust requirements.
- **Planner (Variant Generator)**
  - Config-driven: Loads missions and project context from `holon-config/`.
  - Knowledge-driven: Utilises local patterns (KB) and universal reasoning (WB) from `holon-knowledge/`.
  - Produces multiple candidate **Plan Graphs** for the same intent.
- **Plan Evaluator**
  - Config-driven: Loads metric weights from `holon-config/metrics/`.
  - Scores plan variants using metrics + constraints defined in `holon-config/world/`.
- **Convergence Policy**
  - Config-driven: Loads thresholds from `holon-config/rules/`.
  - Decides when to stop generating new plan variants.
- **Router**
  - Selects model tiers per task, recorded as metadata.
- **Executor (Sandbox Runner)**
  - Config-driven: Loads safety rules from `holon-config/rules/`.
  - Knowledge-driven: Adheres to universal safety invariants from `holon-knowledge/wb/`.
  - Security-hardened: Dispatches containers with `--cap-drop=ALL` (stripping all kernel capabilities),
    `--security-opt=no-new-privileges` (preventing privilege escalation), and non-root execution (`holon` user, UID
    10001).
  - Git abstraction: Routes all repository interactions through `sandbox_executor.git` (`run_git`, `get_diff_numstat`,
    `resolve_ref`, `get_head_commit`, `branch_exists`) with automatic credential sanitisation and timeout enforcement.
  - Runs a chosen plan variant in an isolated environment.
  - One host worktree for every stage: the intent creator, planner, executor and review-loop containers all work in the
    flow's bind-mounted `flow-<slug>` worktree and only commit. The host fetches the base before each container starts
    and pushes the committed branch after it returns, so no container clones or touches the remote and no `ssh-agent` or
    repository URL variable is needed.
- **Review Ensemble (Stage 4)**
  - `sandbox_executor/review_ensemble.py` runs independent reviewer agent turns, each in its own scratch directory under
    `tmp/review/` and blind to the other reviewers' reports, parses the strict JSON trailer that ends each report, and
    approves only by unanimity: every configured reviewer completed with `APPROVED` and zero findings, plus mergeable
    and green CI. Any failed reviewer, zero completed reviewers or unknown CI can never approve. `run_review_loop()`
    calls it once per iteration on the real diff (`--ensemble-size`, default 3), stops at the first iteration that is
    not approved (there is no automatic resolver), and `sandbox_executor/review_report.py` renders the posted report
    from the real reviewer results. Reviewer turns run only inside the sandbox: on the host the loop refuses before any
    PR, iteration or post. See the [sandbox-executor README](../apps/sandbox-executor/README.md#reviewer-ensemble).
- **Evolution Ledger (Canonical 3-Ledger Architecture)**
  - Knowledge-driven: Strictly maintains three append-only ledgers in `holon-knowledge/ledger/`:
    1. `intents.jsonl`: Intent registrations, goals, constraints, and hierarchy links.
    2. `plans.jsonl`: Competing plan proposals, predictions, and model routing metadata.
    3. `executions.jsonl`: Execution summaries, Stage 4 PR review summaries, and fine-grained micro-actions
       (`entry_type: "action"`, e.g. `ACT-REV`, `ACT-FIND`, `ACT-POST`, historical `ACT-RES` rows and the Stage 5
       `ACT-CAL`). See the flow stage record matrix in `docs/ledger_schema.md`.
  - Retirement of auxiliary ledgers: Auxiliary ledgers such as `actions.jsonl` and `reviews.jsonl` are completely
    retired and prohibited.
  - Concurrency & Interleaving Protection: All ledger appends are guarded by `LedgerLock` (POSIX advisory file locking
    via `fcntl.flock` with exponential backoff and explicit fsync), preventing race conditions or interleaved torn
    writes.
  - Validates events against `holon-config/schemas/`.
- **Knowledge Base (Read/write store)**
  - Knowledge-driven: Stores patterns and tactics in `holon-knowledge/kb/`.
  - Validates entries against `holon-config/schemas/`.
- **Wisdom Base (Global Store)**
  - Knowledge-driven: Stores universal axioms in `holon-knowledge/wb/`.
  - Meta-evolving: Universal knowledge that "ascends" from local KBs.
- **Meta-Agent / Orchestrator**
  - Initialised with `config_path` and `knowledge_path`.
  - Watches for new work, dispatches planner/executor jobs, enforces git discipline and budgets.
- **Human Review Boundary**
  - Where humans approve promotions (only the root intent's PR to `main`; see
    [Branch topology and authority boundaries](#branch-topology-and-authority-boundaries) for the current
    implementation).

---

### Data products: Ledger vs Knowledge Base

#### Evolution Ledger (append-only, forensic)

- Purpose: auditability and measurement.
- Stores:
  - intent_id, plan_id, variant_id
  - predicted metrics (P(success), ΔS, Impact, Cost, LearningValue, EV)
  - routing decision (model_id, tier, reason)
  - execution logs and outcomes
  - git commit SHAs / diffs
  - post-execution measurements + calibration errors

#### Knowledge Base (curated, reusable)

- Purpose: accelerate future planning and improve estimators.
- Stores:
  - proven tactics/patterns (“playbooks”)
  - common failure modes + mitigations
  - reusable code snippets/modules
  - estimator proposals, comparisons, and approvals
  - routing heuristics and observed ROI

#### Instrumentation for Selection Pressure

The system treats the `model` metadata as an evolvable trait. By recording architectural attributes (e.g.,
`tuning_type`, `parameter_class`, `reasoning_mode`), the **Researcher Agent** can extract "Wisdom" that generalises
across model providers. This ensures that the system evolves toward the most efficient **Architecture + Tactic**
pairings, rather than just chasing specific model IDs. Detailed instrumentation of modalities and runtime configurations
enables the system to penalise inefficient resource usage (e.g., using vision for text-only tasks) and reward
high-fitness pairings.

---

### End-to-end lifecycle (intent → learning)

#### 1) Intent creation

An intent is created by one of:

- **Human root intent** (bootstrap and high-level goals)
- **Trusted agent root intent** (autonomous goal-setting; gated by trust)
- **Reactive/system intent** (triggered by failures, conflicts, regressions)

Each intent includes:

- goal statement
- constraints (entropy budget, cost budget, time budget, trust requirements)
- scope boundaries (allowed modules, forbidden modules, sandbox requirements)
- parent pointer (unless root)

#### 2) Competitive planning (plan variants)

For each intent, the planner generates plan variants:

- Each variant is a structured plan graph (steps + dependencies + sub-intents)
- Each variant includes predicted:
  - P(success)
  - ΔS (entropy)
  - Impact
  - LearningValue
  - Cost
  - EV

Variants are recorded even if rejected.

#### 3) Plan evaluation + selection

The evaluator:

- validates plan feasibility (dependency availability, tool availability, sandbox constraints)
- scores each variant via EV
- chooses the current best variant
- triggers **convergence** if further planning is unlikely to improve EV meaningfully

#### 4) Routing (model selection)

The router selects model tier per role/task:

- Low-entropy, low-novelty tasks → fast/cheap model (e.g., “Flash” class)
- High-entropy, high-novelty, high-impact tasks → deeper model (e.g., “DeepThink” class)

Routing decisions are logged with full "DNA" metadata:

- model architecture, tuning type, and parameter class
- active modalities and reasoning modes
- routing_reason and expected_roi
- selection_method (e.g., `autonomous_router` or `explicit_manual`)

Routing becomes a **selection pressure**: better routing improves ROI. The system learns to associate specific task
characteristics with the architectural traits that yield the highest EV.

#### 5) Execution (sandboxed)

Executor:

- creates a sandbox environment
- checks out the correct branch
- enforces git discipline (rebase rules)
- runs plan steps (may spawn sub-intents where permitted)
- produces code changes, tests, artifacts, logs

#### 6) Measurement (post-execution)

After execution:

- measure actual P(success) (binary success/failure per intent definition)
- measure actual ΔS from diff + dependency changes + conflicts + coverage deltas
- measure actual Impact (domain-specific)
- measure actual LearningValue (epistemic gain)
- compute calibration error for predictions

All results are appended to the ledger.

#### 7) Learning / KB update

Curate:

- if successful: extract reusable patterns into KB
- if failed: extract failure modes and mitigation steps
- if predictions were off: propose estimator improvements (requires approval)

---

### Branch topology and authority boundaries

#### Principle: merges flow up to the deciding node

- A sub-intent branch nests under, is cut from and merges into the branch of its deciding node: the intent, plan or
  execution branch whose stage decided the split (in practice the plan branch; `target_branch` equals
  `deciding_branch`).
- Any level (intent, plan, execution, review) may decide a split, but in practice the plan does. A split decided during
  review may nest under the reviewed plan or execution.
- The deciding node's branch is the integration surface for its sub-intents.
- Siblings under one deciding node always have different slugs, so their ids cannot collide.
- Only the root intent's PR merges into `main`.
- `holon flow` implements this for plan-decided splits: each child is cut from the plan branch tip after the previous
  child merged, records the plan branch as its `target_branch` and `parent_intent_id`, and is merged into it by the
  guarded `flow/github/merge.py` (merge commit when allowed, otherwise squash, never `main`, `master` or the default
  branch). The plan branch is then merged into the parent execution branch, whose PR is the combined PR (see
  [Git Flow](git_flow.md) Stage 3).

#### Human review boundary

Human review occurs at:

- root intent completion (its PR to `main`, the only human stop in the flow)
- estimator definition updates
- trust/autonomy level changes
- changes to “physics” or governance modules

---

### Planning convergence policy (architecture role)

Convergence prevents infinite planning loops. For a detailed breakdown of convergence triggers, mathematical
definitions, and termination thresholds, see the
[Convergence & Termination Policies in planning_and_evaluation.md](planning_and_evaluation.md#L186).

Convergence triggers may include:

- **EV plateau:** incremental EV gains fall below threshold for N variants
- **entropy budget exhausted:** predicted ΔS exceeds remaining budget
- **dominant plan:** a plan wins by margin and confidence is high
- **planning cost cap:** planning spend exceeds configured cost/time limits

Convergence outcome:

- select best-known plan variant
- record convergence reason + final plan set

---

### Autonomy and trust levels (corrected: intent proposal is privileged)

Holon autonomy is staged such that **work is broadly allowed**, but **goal-setting is gated**.

#### Baseline (low trust / default agent)

- Agent can generate **plans** (variants) for assigned intents.
- Agent can execute **actions** inside sandbox for assigned intents, within budgets.
- Agent cannot create new root intents.
- Agent can propose sub-intents only if the parent intent policy allows it (often “no” at first).

Rationale: executing bounded work in a sandbox is safer than inventing new goals.

#### Medium trust (decomposition allowed)

- Agent can spawn **sub-intents** during planning/execution, subject to:
  - parent intent constraints
  - entropy/cost budgets
  - mandatory rebase + deciding-node-only merge rules
- Agent still cannot propose new root intents.

#### High trust (autonomous goal-setting)

- Agent can propose **new root intents** (agent-generated intents) that enter the same validation pipeline:
  - predicted metrics (P(success), ΔS, Impact, Cost, LearningValue, EV)
  - justification + alignment constraints
  - explicit scope boundaries
- Root-intent proposals may still require human acceptance depending on configuration.

#### Highest trust (governance proposals; still human-approved)

- Agent can propose changes to:
  - estimator implementations
  - routing heuristics
  - governance policies
- Human approval remains required for any changes that affect metric definitions (“physics”) or system invariants.

#### Trust is earned via measurement

Trust level is influenced by:

- calibration quality (P(success), ΔS, Impact)
- rate of successful intent completion
- compliance with git discipline
- safety violations (immediate penalties)

---

### Minimal module map (conceptual)

- `core/`
  - intent model, plan model, IDs, invariants
- `planning/`
  - variant generation, plan graph, convergence policy
- `routing/`
  - model router, routing evaluation
- `execution/`
  - sandbox runner, tool adapters, rebase enforcer
- `metrics/`
  - estimators, post-measurement, calibration
- `holon-knowledge/ledger/`
  - append-only event logging, schemas
- `holon-knowledge/kb/`
  - curated knowledge, retrieval APIs, write rules
- `holon-knowledge/wb/`
  - universal invariants, cross-project heuristics
- `orchestrator/`
  - meta-agent loop, scheduling, work queue

### Flow engine module map

The Holon Flow engine is the `sandbox_executor.flow` package in `apps/sandbox-executor/src/sandbox_executor/flow/`. Each
module owns one cluster, so a change to one stage or helper touches a small, separate set of files. The package was
split from the former single `flow.py` by moving code only. Three tests guard the structure that remains:

- `tests/flow/test_patch_targets.py` checks that patch targets resolve where the code under test looks them up.
- `tests/flow/test_package_exports.py` parses each submodule and requires every public top-level function and class to
  be re-exported by `flow/__init__.py`, listed in `__all__` and the same object as in its owning module. A
  temporary-package test proves it fails when a definition is added without being re-exported. A shrink-only
  `KNOWN_UNEXPORTED` allowlist in that test covers public definitions that were never re-exported (reachable only
  through their owning module); a new definition cannot join it, and an entry must be removed once it is re-exported.
  The test also fails if two submodules define the same public name.
- `tests/flow/test_module_budgets.py` enforces the module size budgets in `flow_module_budgets.json`.

| Module                         | Responsibility                                                                                              |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| `flow/__init__.py`             | Re-exports every engine name through `__all__`, so `from sandbox_executor.flow import ...` still works.     |
| `flow/constants.py`            | Constants shared by two or more modules (`CURRENT_CONTEXT_SCHEMA_VERSION`, `CI_REGISTRATION_POLL_SECONDS`). |
| `flow/context.py`              | The data model: `FlowStage`, `StageStatus`, `StageResult` and `FlowContext`.                                |
| `flow/checkpoint.py`           | Checkpoint schema migration, `FlowCheckpoint`, and saving and loading checkpoints.                          |
| `flow/ledger_reconcile.py`     | Resolves conflicts in the append-only JSONL ledgers by a union under the ledger lock.                       |
| `flow/ledger_interleave.py`    | Resolves ledger-only merge conflicts: merge-base rows kept as a prefix, appended rows interleaved.          |
| `flow/child_progress.py`       | Per-child progress of a decomposed Stage 3, origin reconciliation and the next-action decision.             |
| `flow/stage_conflicts.py`      | The proactive conflict check a stage runs before it pushes.                                                 |
| `flow/intent_questions.py`     | Validates an intent's clarifying questions before Stage 1 accepts it.                                       |
| `flow/dispatch.py`             | Role-agent dispatch shared with `holon intent`, `holon plan` and `holon execute`.                           |
| `flow/host_remote.py`          | Host-side fetch of the base and push of the committed branch; containers never touch the remote.            |
| `flow/git_refs.py`             | Branch fetches, child-branch discovery, ledger readers, execution refs and the effective diff.              |
| `flow/github/pr.py`            | Mergeability checks, review git trailers, semantic PR metadata and creating the PR.                         |
| `flow/github/ci.py`            | Probes CI checks and waits for them to register and settle.                                                 |
| `flow/github/review_post.py`   | Posts the Stage 4 consensus review to the pull request.                                                     |
| `flow/github/merge.py`         | The guarded child merge into the plan branch and its fail-closed refusal check.                             |
| `flow/review/workspace.py`     | The sandbox guard for reviewer turns and the persistent review session volume.                              |
| `flow/review/records.py`       | Refused-review results, review action rows in the ledger and the reviewer summary line.                     |
| `flow/review/ensemble.py`      | Dispatches the reviewer ensemble for one review iteration.                                                  |
| `flow/review/resolver.py`      | The resolver pass: filters adjudicated findings and records finding dispositions.                           |
| `flow/review/loop.py`          | The review-resolve iteration loop and the engine's Stage 4 pass window.                                     |
| `flow/review/cli.py`           | The `holon review-loop` CLI entry and the review loop summary helpers.                                      |
| `flow/stages/intent.py`        | Stage 1: dispatches the intent creator and verifies its branch.                                             |
| `flow/stages/plan.py`          | Stage 2: dispatches the planner, adopts its plan and makes the entropy budget decision.                     |
| `flow/stages/decomposition.py` | Stage 3 decomposition: validates the sub-intent list, delegates to the child loop and summarises it.        |
| `flow/stages/children.py`      | Stage 3 child loop: runs, resumes and merges each sub-intent under the plan branch, one after another.      |
| `flow/stages/integration.py`   | Stage 3 integration: merges the plan branch into the parent execution branch for the combined PR.           |
| `flow/stages/execute.py`       | Stage 3: dispatches the executor, adopts its branch and resumes failed partial executions.                  |
| `flow/stages/review.py`        | Stage 4: runs the review loop and halts at consensus approval without merging.                              |
| `flow/stages/calibrate.py`     | Stage 5: runs calibration and commits the calibrated artefacts.                                             |
| `flow/pipeline.py`             | `PipelineEngine` and `run_flow_pipeline`, which run the five stages in order with checkpoints.              |

The `flow/github/`, `flow/review/` and `flow/stages/` packages each have an `__init__.py` holding only a docstring.

Rules that keep the package healthy:

- **Import direction.** Imports run from the leaves (`constants`, `context`, `checkpoint`) through `dispatch`,
  `git_refs`, `github/*`, `review/*` and `stages/*` to `pipeline`. No submodule imports `pipeline` at runtime. The one
  back-edge is bound late: `stages/children.py` runs child flows through `PipelineEngine`, but `pipeline` imports the
  stages. So `flow/__init__.py` sets `stages.children.PipelineEngine` after it has imported `pipeline`, and
  `children.py` imports the name only under `TYPE_CHECKING`. `tests/flow/test_package_exports.py` checks the binding.
- **Tests patch the submodule, not the package.** A name such as `run_git` is looked up in each submodule that imports
  it, so a test patches `sandbox_executor.flow.<module>.<name>` at each submodule its code path crosses. It uses
  `tests/flow/lookup_patch.py` `patch_lookup_sites` when there is more than one, so they share one mock.
  `tests/flow/test_patch_targets.py` rejects package-level patch targets. The only exception is a name that a caller
  outside the package imports from `sandbox_executor.flow` inside a function body.
- **Size ratchet.** `apps/sandbox-executor/flow_module_budgets.json` records each module's line count at the split.
  `scripts/check_flow_module_budgets.py` runs in `uv run task lint` and through `tests/flow/test_module_budgets.py`, and
  fails when a module grows past its budget, lacks a budget, or a budget names a missing module. Budgets only go down,
  by hand.

---

### Interfaces (what components exchange)

#### Intent → Planner

- intent_id
- goal + constraints
- context pointers (KB/WB refs + ledger summaries)
- allowed tool/model set

#### Planner → Evaluator

- plan_variants[] (each with predicted metrics + structure)

#### Evaluator → Router

- chosen plan variant + task decomposition with complexity/entropy cues

#### Router → Executor

- model assignments per subtask
- routing metadata

#### Executor → Ledger

- diffs, SHAs, logs
- predicted vs actual metrics
- outcomes and errors

#### Ledger/K.B./W.B. → Planner

- retrieved patterns and universal invariants
- prior similar intents + outcomes
- estimator versions and calibration stats

---

### Notes on extensibility

This architecture is intentionally modular:

- you can replace planners, routers, estimators without rewriting the whole system
- the ledger remains the stable audit backbone
- KB evolves slowly and deliberately to avoid poisoning

---

### What this architecture optimises for

- auditability (every action has a trace)
- safety through containment (sandbox + branch discipline)
- continuous improvement (calibration + selection pressure)
- scalable delegation (fractal intent, authority boundaries)
- efficient inference spend (routing as evolution)

---

### Complexity Gate and Ratchet Baseline

To safeguard long-term maintainability and prevent structural decay across `apps/sandbox-executor/src`, function
complexity is continuously gated via Ruff complexity lints and an automated test ratchet.

#### Complexity Thresholds and Rules

The following complexity rules and thresholds are enforced:

- **McCabe Cyclomatic Complexity (`C901`)**: `max-complexity = 10`. Restricts cyclomatic path complexity within any
  single function.
- **Pylint Return Statements (`PLR0911`)**: Maximum `6` return statements per function (Ruff default).
- **Pylint Branch Count (`PLR0912`)**: Maximum `12` branches per function (Ruff default).
- **Pylint Argument Count (`PLR0913`)**: Maximum `5` arguments per function definition (Ruff default).
- **Pylint Statement Count (`PLR0915`)**: Maximum `50` statements per function body (Ruff default).

#### Architectural Rationale: Inline Baseline vs. Per-File Ignores

Rather than configuring blanket `per-file-ignores` in `pyproject.toml`, existing legacy functions that exceed these
limits are annotated with inline `# noqa: <codes>` comments directly on their `def` lines:

1. **Function-Level Granularity**: `per-file-ignores` exempt whole files, allowing new or unannotated functions in those
   files to accumulate unchecked complexity. Inline annotations localize suppressions strictly to offending functions.
2. **Explicit Technical Debt Visibility**: Placing `# noqa` comments directly at the function declaration makes
   complexity immediately visible during code reading and PR reviews, signaling to developers where refactoring is
   needed.
3. **Configuration Stability**: Keeping `pyproject.toml` declarative and minimal avoids configuration churn and merge
   conflicts in project configuration files.

#### Ratchet Enforcement

The complexity gate is protected by an automated ratchet test at `apps/sandbox-executor/tests/test_complexity_gate.py`:

- The test scans all Python files in `apps/sandbox-executor/src` and counts the inline complexity `# noqa` directives
  (`C901`, `PLR0911`, `PLR0912`, `PLR0913`, `PLR0915`) on function definitions.
- The total count cannot exceed the established baseline (`BASELINE_COMPLEXITY_NOQA_COUNT = 187`,
  `BASELINE_ANNOTATED_FUNCTIONS_COUNT = 91`), and every annotation must match an entry in the frozen identity snapshot
  (`BASELINE_SNAPSHOT`, a multiset of `(relative path, rule codes)` pairs), so removing a legacy suppression never
  licenses adding or relocating another one.
- Within the ratchet's scope (`apps/sandbox-executor/src`), any new complex function or new complexity `# noqa`
  directive fails CI. Code outside that tree (for example `tests/`) is not covered by the ratchet and is gated by
  `ruff check` alone; bare `# noqa` directives without rule codes are rejected repo-wide via the `PGH004` lint.
- When legacy functions are simplified or refactored, the baseline constants and snapshot must be ratcheted down,
  ensuring codebase complexity only decreases over time.
- The snapshot names the module that carries each annotation. Since the flow engine split, its entries name
  `sandbox_executor/flow/<module>.py` paths rather than the former `sandbox_executor/flow.py`.
