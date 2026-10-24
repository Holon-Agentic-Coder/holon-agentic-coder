# ledger_schema.md

This document defines the **Evolution Ledger** schema for Holon. The ledger is an **append-only, write-once** event log
that captures every intent, plan, action, result, and measurement with full traceability back to git.

The ledger enables:

- auditability (“why did the system do this?”)
- calibration (“how accurate were predictions?”)
- evolution (“what patterns should be promoted to the KB?”)
- routing ROI (“did deeper models pay off?”)

---

## Core principles

### 1) Append-only, immutable

- Ledger records are never updated in place.
- Corrections are recorded as **new events** (e.g., `ledger_correction`).

### 2) Event-sourced

- System state is derived from the ordered sequence of events.
- The ledger is the authoritative history of what happened.

### 3) Git-addressable

Every meaningful artifact is traceable to git:

- branch names
- commit SHAs
- diffs
- tags
- rebase/merge actions

### 4) Metrics are first-class

For each plan/execution we record:

- predicted metrics
- actual metrics
- calibration errors
- estimator versions used

### 5) Deterministic IDs

Ledger entries refer to stable IDs:

- `intent_id`
- `plan_id`
- `variant_id`
- `agent_id`
- `run_id`
- `sandbox_id`

---

## Storage format

### Canonical 3-Ledger Architecture (JSON Lines)

Holon maintains strictly three append-only ledgers under `holon-knowledge/ledger/`:

1. **`intents.jsonl`**: Intent registration, goals, constraints, entropy budgets, and hierarchy links.
2. **`plans.jsonl`**: Competing plan variants, predictions ($P(\text{success})$, $\Delta S$, EV), and model routing
   metadata.
3. **`executions.jsonl`**: Execution summaries, Stage 4 PR review summaries and actions, Stage 5 calibration actions,
   and chronologically interleaved micro-actions (see the flow stage record matrix below).

- Directory: `holon-knowledge/ledger/`
- Encoding: UTF-8
- Each line: one valid JSON object (one event or record)
- Ordering: strictly chronological append-only order under `LedgerLock`

#### Retirement of Auxiliary Ledgers

Auxiliary ledgers such as `actions.jsonl` and `reviews.jsonl` are **strictly retired and prohibited**. Previously,
discrete action logging or review feedback was occasionally split into separate files. This fragmented the provenance
trail, complicated atomic multi-process writes, and broke unified downstream query engines. In the canonical
architecture, all discrete micro-actions (`entry_type: "action"`, such as `ACT-REV`, `ACT-FIND`, `ACT-POST`, tool
operations) and lifecycle summaries (`entry_type: "summary"`, such as `E-...`, `REV-...`) MUST be interleaved
chronologically inside `executions.jsonl`.

#### Concurrency & Interleaving Protection: `LedgerLock`

To ensure thread- and process-safe append operations across concurrent agent containers, subagents, and tools, all
ledger appends are guarded by `LedgerLock` (`sandbox_executor.ledger.LedgerLock`).

- **Mechanism**: POSIX advisory file locking (`fcntl.flock` with `LOCK_EX | LOCK_NB`) on dedicated lock descriptors.
- **Contention Handling**: Bounded exponential backoff with randomised jitter and a configurable timeout (default:
  10.0s).
- **Durability**: Explicit `flush()` and `os.fsync()` calls upon release before closing the file descriptor,
  guaranteeing on-disk persistence without torn writes or interleaving.
- **Graceful Fallback**: On platforms or filesystems without flock support, logs a warning and proceeds with
  uncoordinated appends.

#### Flow Stage Record Matrix

Every `holon flow` stage writes to the target repository's own `holon-knowledge/ledger/`. The table lists the rows each
stage function in `sandbox_executor/flow.py` appends. In Stage 3 the in-sandbox executor entrypoint
(`sandbox_executor/entrypoint/executor.py`) appends further rows of its own, listed below the table.

| Stage             | Ledger file        | Rows appended                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ----------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1. Intent         | `intents.jsonl`    | One intent row (skipped when the branch or slug is already recorded).                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 2. Plan           | `plans.jsonl`      | One plan row (skipped when the `plan_id` is already recorded).                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| 3. Execute        | `executions.jsonl` | One execution summary (`entry_type: "summary"`, `execution_id` `E-...`) and one test-run action event (`entry_type: "action"`).                                                                                                                                                                                                                                                                                                                                                                                                      |
| 4. PR review loop | `executions.jsonl` | Per iteration: `ACT-REV` (evaluation), `ACT-FIND` (when findings exist) and `ACT-RES` (resolver, iterations 2 and later). Once per loop: `ACT-POST` (consensus posting, even when posting fails), a `REV-...` summary and an action event.                                                                                                                                                                                                                                                                                           |
| 5. Calibration    | `executions.jsonl` | One `ACT-CAL` action (`action_origin: "calibration"`), committed with `plans/{plan_id}_calibration.md` on the `/calibrated` branch. Then reconciliation events (`event_type: "reconciliation"` for the execution branch, plus `ttl_expiration` events for stale `unknown` actions), committed in a follow-up commit on the same `/calibrated` branch. When calibration fails, an `ACT-CAL` row with `outcome: "failure"` and the reconciliation events are appended but left uncommitted (see Known gap). No summary row is written. |

Stage 3 executor entrypoint rows (all in `executions.jsonl`, under the same `E-...` `execution_id`):

- Its own execution summary (`entry_type: "summary"`), plus a superseding failure summary when the branch integrity
  check or the pre-push sync fails.
- When action logging is enabled (`is_action_logging_enabled()`): a `command_execution` action for the agent run
  ("Executed agent ...") and a `git_mutation` action for the commit ("Committed execution changes: ..."), plus a
  `doc_update` action when markdown files are formatted and a `git_mutation` action when a corrupted repository is
  re-initialised.
- When the plan is decomposed instead of executed: sub-intent rows in `intents.jsonl`, a summary and, with action
  logging enabled, a `plan_decomposition` action.

Stage 4 and Stage 5 action rows keep their historical field order and identifier formats:

- `ACT-REV`, `ACT-FIND`, `ACT-RES` and `ACT-POST` use `execution_id` values `REV-ITR-{iteration}-{ts}`,
  `REV-FIND-{iteration}-{ts}`, `REV-RES-{iteration}-{ts}` and `REV-POST-{ts}`, with `action_origin: "pr_review"`.
- `ACT-CAL` uses `execution_id` `CAL-{plan_id}-{ts}`, `category: "doc"` and labels
  `["calibration", "stage-5", plan_id]`. Beyond the common action fields it carries `plan_branch`, `execution_branch`,
  `evaluated_commit_sha`, `predicted_metrics`, `actual_metrics`, `calibration_deltas` and `calibration_report_path`.
  Each committed calibration run is a new calibration event, so re-running calibration appends a new `ACT-CAL` row.

Write rules for every stage:

- **Serialised and validated**: all rows are appended under `LedgerLock`. The Stage 4 `ACT-REV`, `ACT-FIND`, `ACT-RES`
  and `ACT-POST` rows and the Stage 5 `ACT-CAL` row go through
  `sandbox_executor.ledger.try_append_action_record(..., preserve_shape=True)`, which passes every string in the row
  (including `error_context`) through the canonical `CredentialRedactor`, validates the row against `ActionRecord` and
  then writes it verbatim. Invalid rows are rejected before anything is written. Other action-shaped rows -- the Stage 3
  and Stage 4 action events written by `append_action_event` and the reconciliation events written by
  `reconcile_records` -- use their own writers and are not validated against `ActionRecord`. Stage 4 integrations should
  call `sandbox_executor.flow.record_review_action()` rather than writing to the file directly; integrations without a
  `FlowContext` call the context-free `sandbox_executor.ledger.try_append_action_record(..., preserve_shape=True)`,
  which applies the same validation, LedgerLock and fail-loud reporting.
- **Fail loud**: when a row a `flow.py` stage function appends fails, the failure is logged at `WARNING` with the ledger
  path and exception, and mirrored into the flow context log shown to the operator. No fallback ledger file is ever
  written. A failed Stage 1 intent row, Stage 2 plan row or Stage 3 execution summary then fails the stage, which halts
  the pipeline. The other rows in the table (action rows, the Stage 4 `REV-...` summary and the Stage 5 `ACT-CAL` row)
  never abort their stage.
- **Suppression**: dry runs append nothing, including the Stage 5 reconciliation events (the review loop logs how many
  rows it suppressed). `holon calibrate` with `skip_commit` appends no `ACT-CAL` row.
- **No backfill**: only new executions gain these rows. Historical records are never synthesised.
- **Known gap**: the review loop does not commit `executions.jsonl`. Stage 4 rows only reach git when a later commit
  includes the ledger, for example the Stage 5 calibration commit in `holon flow`. A review run in a throwaway container
  clone leaves them uncommitted. Likewise, when `run_calibrate()` raises, Stage 5 writes a fallback
  `plans/{plan_id}_calibration.md` and appends an `ACT-CAL` row with `outcome: "failure"` and the reconciliation events
  to the working-tree `executions.jsonl`, but commits none of them, even when HEAD already sits on the `/calibrated`
  branch, and neither pushes nor fast-forwards that branch, because `calibrate` is the last stage and the `/calibrated`
  branch may not exist. The operator must commit or discard them.

**Why JSONL?**

- streaming-friendly
- simple to diff
- append-only naturally
- easy to query with tools (`jq`, Python)

---

## Common envelope (required fields)

Every ledger event MUST contain:

- `schema_version` (string)  
  Example: `"1.0"`
- `event_type` (string)  
  Example: `"intent_created"`
- `ts` (string, ISO-8601 UTC)  
  Example: `"2026-02-10T03:12:45Z"`
- `seq` (integer, monotonically increasing within a ledger file)  
  Example: `1842`
- `run_id` (string, unique per orchestrator run / session)  
  Example: `"R-20260210-0312-8c1d"`
- `agent_id` (string)  
  Example: `"agent-planner-07"`
- `host` (object)
  - `hostname` (string)
  - `platform` (string)
  - `repo_root` (string)
- `git` (object)
  - `repo` (string) — optional remote URL or logical name
  - `branch` (string)
  - `head_sha` (string)
  - `dirty` (boolean)

**Minimal envelope example**

```json
{
  "schema_version": "1.0",
  "event_type": "intent_created",
  "ts": "2026-02-10T03:12:45Z",
  "seq": 1,
  "run_id": "R-20260210-0312-8c1d",
  "agent_id": "agent-orchestrator-01",
  "host": {
    "hostname": "runner-01",
    "platform": "linux",
    "repo_root": "/repo"
  },
  "git": {
    "repo": "origin",
    "branch": "intent/I-root-001-bootstrap-metrics",
    "head_sha": "abc123",
    "dirty": false
  },
  "payload": {}
}
```

---

## Identity and naming fields

### Required identifiers in relevant events

- `intent_id` (string)  
  Example: `"I-root-001-bootstrap-metrics"`
- `parent_intent_id` (string or null)  
  Example: `"I-root-000-project-init"`
- `plan_id` (string)  
  Example: `"P-I-root-001-bootstrap-metrics-v3-flash"`
- `variant_id` (string)  
  Example: `"v3"`
- `sandbox_id` (string)  
  Example: `"S-20260210-0315-2f93"`
- `model` (object) Full "DNA" of the execution engine to enable high-resolution evolutionary analysis.

```json
{
  "identity": {
    "provider": "anthropic",
    "model_id": "claude-sonnet-4.6",
    "version": "2026-02-24",
    "tier": "deep"
  },
  "architecture": {
    "type": "moe", // moe, dense, ssm, hybrid, dit
    "quantisation": "fp16", // null, 4-bit, fp8, etc.
    "parameter_class": "frontier", // small, medium, large, frontier
    "tuning_type": "rlhf" // rlhf, dpo, sft, distilled
  },
  "capabilities": {
    "primary_specialisation": "coding", // coding, reasoning, creative, tool-use
    "reasoning_mode": true, // true if using specific "thinking" / CoT paths
    "long_context_ready": true, // supports 1M+ tokens
    "structured_output": "strict_json", // regex, strict_json, constrained, none
    "safety_governance": "censored" // censored, uncensored, custom_guard
  },
  "modalities": {
    "supported": ["text", "image", "audio", "video"],
    "active_in_session": ["text", "image"], // Which "senses" were used in this turn
    "sensory_premium": 1.25 // Cost multiplier for multimodal use
  },
  "runtime_config": {
    "hosting": "api", // api, local, edge, private_cluster
    "temperature": 0.0,
    "top_p": 1.0,
    "deterministic": true, // true if greedy search/seed used
    "context_window_used": 15000,
    "max_tokens_budget": 4096
  },
  "routing": {
    "routing_reason": "high_novelty_ui_debug", // Why the choice was made
    "routing_policy_version": "v2.1",
    "expected_roi": 0.85, // Predicted value gain vs token cost
    "selection_method": "explicit_manual", // explicit_manual, autonomous_router, human_override
    "assigned_by": "human@user.com" // User or Agent ID who made the choice
  }
}
```

---

## Metric objects

### Predicted metrics (required for plan selection)

- `predicted` (object)
  - `p_success` (number, 0..1)
  - `entropy` (number, >= 0) — predicted ΔS
  - `impact` (number, >= 0) — project-specific scale
  - `learning_value` (number, 0..10)
  - `cost` (number, >= 0) — normalised cost units
  - `ev` (number) — computed EV
  - `lambda` (number, >= 0) — entropy penalty weight
  - `mu` (number, >= 0) — learning value weight
  - `estimator_versions` (object)
    - `p_success` (string)
    - `entropy` (string)
    - `impact` (string)
    - `learning_value` (string)
    - `ev` (string)

### Actual metrics (required after execution)

- `actual` (object)
  - `p_success` (number, 0..1) — typically 0 or 1
  - `entropy` (number, >= 0)
  - `impact` (number, >= 0)
  - `learning_value` (number, 0..10)
  - `cost` (number, >= 0)
  - `measurements` (object) — supporting evidence
    - `files_modified` (integer)
    - `modules_modified` (integer)
    - `lines_added` (integer)
    - `lines_deleted` (integer)
    - `new_dependencies` (integer)
    - `removed_dependencies` (integer)
    - `coverage_delta` (number)
    - `rebase_conflicts` (integer)
    - `tests_passed` (boolean)

### Calibration errors

- `calibration` (object)
  - `p_success_error` (number) — abs(pred - actual)
  - `entropy_error` (number) — normalised or absolute
  - `impact_error` (number)
  - `learning_value_error` (number)

---

## Event types (canonical)

### Intent lifecycle events

#### 1) `intent_created`

Payload:

- `intent_id`
- `parent_intent_id` (nullable)
- `goal` (string)
- `constraints` (object)
  - `entropy_budget` (number)
  - `cost_budget` (number)
  - `time_budget_sec` (number)
  - `trust_required` (string)
- `scope` (object)
  - `allowed_paths` (array of strings)
  - `forbidden_paths` (array of strings)
  - `sandbox_required` (boolean)

```json
{
  "schema_version": "1.0",
  "event_type": "intent_created",
  "ts": "2026-02-10T03:12:45Z",
  "seq": 12,
  "run_id": "R-20260210-0312-8c1d",
  "agent_id": "agent-orchestrator-01",
  "host": {
    "hostname": "runner-01",
    "platform": "linux",
    "repo_root": "/repo"
  },
  "git": {
    "repo": "origin",
    "branch": "intent/I-root-001-bootstrap-metrics",
    "head_sha": "abc123",
    "dirty": false
  },
  "payload": {
    "intent_id": "I-root-001-bootstrap-metrics",
    "parent_intent_id": null,
    "goal": "Bootstrap naive metrics estimators and ledger logging.",
    "constraints": {
      "entropy_budget": 80,
      "cost_budget": 400,
      "time_budget_sec": 7200,
      "trust_required": "human"
    },
    "scope": {
      "allowed_paths": ["holon/"],
      "forbidden_paths": ["holon/core/invariants.py"],
      "sandbox_required": true
    }
  }
}
```

#### 2) `intent_state_changed`

Payload:

- `intent_id`
- `from_state` (string)
- `to_state` (string)
- `reason` (string)

States (suggested): `proposed`, `planning`, `ready`, `executing`, `rebasing`, `testing`, `complete`, `merged`,
`promoted`, `abandoned`

#### 3) `intent_abandoned`

Payload:

- `intent_id`
- `reason` (string)
- `blocking_issue` (string, optional)

---

### Planning and plan selection events

#### 4) `plan_variant_created`

Payload:

- `intent_id`
- `plan_id`
- `variant_id`
- `plan_graph` (object) — structured steps and dependencies
- `predicted` (object) — predicted metrics (see above)
- `model` (object) — routing decision used to generate the plan (planner model)

#### 5) `plan_variant_scored`

Payload:

- `intent_id`
- `plan_id`
- `variant_id`
- `score_breakdown` (object)
  - `p_success_components` (object)
  - `entropy_components` (object)
  - `impact_components` (object)
  - `cost_components` (object)
- `predicted` (object)

#### 6) `planning_converged`

Payload:

- `intent_id`
- `variants_considered` (array of `plan_id`)
- `winner_plan_id` (string)
- `reason` (string) — e.g., `ev_plateau`, `dominant_plan`, `budget_exhausted`
- `planning_cost_total` (number)

#### 7) `plan_selected`

Payload:

- `intent_id`
- `plan_id`
- `variant_id`
- `predicted` (object)
- `selection_reason` (string) — usually “highest EV under constraints”

---

### Routing events

#### 8) `model_routed`

Payload:

- `intent_id`
- `plan_id`
- `task_kind` (string) — e.g., `planning`, `coding`, `review`, `tests`
- `model` (object)
- `signals` (object)
  - `novelty` (number)
  - `complexity` (number)
  - `predicted_entropy` (number)
  - `expected_cost` (number)
- `routing_policy_version` (string)

---

### Execution events

#### 9) `sandbox_created`

Payload:

- `intent_id`
- `sandbox_id`
- `sandbox_type` (string) — e.g., `docker`, `venv`, `nix`
- `base_image` (string, optional)
- `isolation_guarantees` (array of strings)

#### 10) `execution_started`

Payload:

- `intent_id`
- `plan_id`
- `sandbox_id`
- `entrypoint` (string)
- `predicted` (object)

#### 11) `tool_call`

Payload:

- `intent_id`
- `plan_id`
- `tool_name` (string) — e.g., `git`, `pytest`, `python`, `bash`
- `command` (string)
- `exit_code` (integer)
- `stdout_trunc` (string)
- `stderr_trunc` (string)
- `duration_ms` (integer)

#### 12) `git_rebase_started`

Payload:

- `intent_id`
- `branch` (string)
- `parent_branch` (string)
- `parent_head_sha` (string)

#### 13) `git_rebase_completed`

Payload:

- `intent_id`
- `branch` (string)
- `status` (string) — `success` or `conflict`
- `conflict_files` (array of strings)
- `duration_ms` (integer)

#### 14) `git_merge_attempted`

Payload:

- `intent_id`
- `from_branch` (string)
- `to_branch` (string)
- `status` (string) — `success` or `failed`
- `merge_sha` (string, optional)
- `failure_reason` (string, optional)

#### 15) `execution_completed`

Payload:

- `intent_id`
- `plan_id`
- `sandbox_id`
- `status` (string) — `success` or `failure`
- `failure_reason` (string, optional)
- `artifacts` (array of objects)
  - `kind` (string) — `diff`, `report`, `log`, `test_results`
  - `path` (string)
  - `sha256` (string, optional)
- `actual` (object) — actual metrics (see above)
- `calibration` (object)

---

### Promotion and human review events

#### 16) `human_review_requested`

Payload:

- `intent_id`
- `branch` (string)
- `review_kind` (string) — `promotion`, `governance`, `estimator_change`
- `summary` (string)
- `diff_range` (object)
  - `from_sha` (string)
  - `to_sha` (string)

#### 17) `human_review_decision`

Payload:

- `intent_id`
- `review_kind` (string)
- `reviewer` (string)
- `decision` (string) — `approved` or `rejected`
- `notes` (string)

#### 18) `intent_promoted_to_main`

Payload:

- `intent_id`
- `from_branch` (string)
- `to_branch` (string) — usually `main`
- `merge_sha` (string)
- `release_tag` (string, optional)

---

### Governance and estimator evolution events

#### 19) `estimator_proposed`

Payload:

- `estimator_name` (string) — e.g., `p_success`
- `new_version` (string) — e.g., `p_success_v2`
- `based_on_version` (string)
- `proposal_intent_id` (string)
- `evidence` (object)
  - `backtest_window` (string)
  - `calibration_improvement` (number)
  - `failure_cases` (array)
- `human_approval_required` (boolean)

#### 20) `estimator_approved`

Payload:

- `estimator_name`
- `approved_version`
- `reviewer`
- `decision_ts`

#### 21) `policy_changed`

Payload:

- `policy_name` (string) — e.g., `routing`, `convergence`, `trust`
- `from_version` (string)
- `to_version` (string)
- `change_intent_id` (string)
- `summary` (string)
- `human_approved` (boolean)

---

## Required event relationships (consistency rules)

These are invariants that validators should enforce:

1. Every `intent_created` must precede any other event referencing that `intent_id`.
2. Every `plan_selected` must reference an existing `plan_variant_created`.
3. Every `execution_started` must reference a `plan_selected`.
4. Every `execution_completed` must follow `execution_started`.
5. Every merge attempt must be recorded (`git_merge_attempted`), regardless of success.
6. Every rebase attempt must be recorded (`git_rebase_started` + `git_rebase_completed`).
7. If `execution_completed.status == "success"`, then `actual.p_success` must be `1.0`.
8. Metric ranges must hold:
   - `0 <= predicted.p_success <= 1`
   - `0 <= predicted.learning_value <= 10`
   - `predicted.entropy >= 0`
   - `actual.entropy >= 0`
9. If intent merges into parent, parent branch must match hierarchy (no merges to `main` from sub-intents).

---

## Minimal viable ledger (MVL) schema subset

To start, Holon can operate with a minimal event set:

- `intent_created`
- `plan_variant_created`
- `plan_selected`
- `execution_started`
- `execution_completed`
- `git_rebase_started`
- `git_rebase_completed`
- `git_merge_attempted`

Everything else can be layered later.

### Sandbox Executor Ledgers

The sandbox executor logs planning events in `plans.jsonl` and execution events in `executions.jsonl`.

#### Plan Selection Ledger (`plans.jsonl`)

Appended when a plan is proposed by the planner:

- `plan_id`: Unique identifier for the plan.
- `intent_branch`: Target intent branch.
- `agent`: The active agent name (e.g. `antigravity-agent`).
- `agent_version`: The resolved version string of the agent CLI.
- `model`: The model used for generation.
- `p_success`, `entropy`, `impact`, `cost`, `learning_value`, `ev`: Numerical metrics.
- `created_at`: Creation timestamp.
- `plan_file`: Relative path to the plan markdown file.
- `status`: Plan status (e.g. `proposed`).

#### Execution Ledger (`executions.jsonl`)

The execution ledger contains two entry types discriminated by `entry_type`:

##### 1. Summary Records (`"entry_type": "summary"`)

Appended when a plan execution begins, completes, fails, or decomposes into sub-intents:

- `entry_type`: Always `"summary"` (defaults to `"summary"` for backward compatibility if omitted).
- `execution_id`: Unique identifier for the execution record.
- `plan_branch`: Branch of the plan being executed.
- `agent`: The active agent name.
- `agent_version`: The resolved version string of the agent CLI.
- `model`: The model used for execution.
- `status`: Execution status (e.g. `success`, `failure`, `decomposed`).
- `summary`: Description of execution outcome (only for terminal states).
- `execution_file`: _(Deprecated / Omitted for new records)_ Historical relative path to execution markdown file.
- `agent_output_truncated`: Boolean flag indicating if captured agent logs exceeded byte budget and were truncated.
- `agent_output_bytes`: Byte length of captured (and potentially truncated/sanitised) agent output payload.
- `ledger_revision`: Integer revision number (defaults to `1` on creation).
- `sub_intents`: List of sub-intents generated upon decomposition (only for `decomposed` state).
- `created_at`: Creation timestamp.

**Reader Contract for Superseding Rows:** When a ledger row is superseded (e.g. `ledger_revision >= 2` due to
post-execution verification or git recovery failures), the superseding row must repeat all authoritative fields that
readers should rely on (including `agent_output_truncated` and `agent_output_bytes`). Readers must select the row with
the highest `ledger_revision` for a given `execution_id` without needing to merge fields backwards.

##### 2. Discrete Action Records (`"entry_type": "action"`)

Appended as fine-grained, discrete execution actions occur during a plan run (code modifications, doc updates,
config/env changes, command executions, test runs, git mutations):

- `entry_type`: Always `"action"`.
- `action_id`: Unique identifier for the action (e.g. `ACT-{timestamp}-{suffix}`).
- `execution_id`: Identifier associating the action with the parent execution.
- `parent_action_id`: Optional parent action ID if nested under another action.
- `event_type`: Type of event (e.g. `code_modification`, `doc_update`, `config_change`, `command_execution`, `test_run`,
  `git_mutation`).
- `action`: Concise description of the operation.
- `category`: Classification category (`code`, `doc`, `config`, `test`, `build`, `dependency`).
- `labels`: List of searchable tags/labels (e.g. `["refactor", "ci", "core"]`). Ingestion also supports comma-separated
  strings (e.g. `"refactor, ci"`), which are automatically normalised to arrays.
- `target`: Target path, command name, or artifact.
- `outcome`: Outcome status (`success`, `failure`, `reverted`, `blocked`).
- `error_context`: Optional error message or stderr excerpt if the action failed.
- `mistake_category`: Optional mistake category or lesson learned for evolutionary adaptation.
- `action_origin`: Provenance origin (`initial_implementation`, `pr_review`, `ci_fix`, `calibration`, `operator`).
  Defaults to `initial_implementation`.
- `created_at`: Creation timestamp (ISO 8601 UTC).

###### PR Review Provenance & Review Criticality

When actions originate from pull request reviews (`action_origin == "pr_review"`), provenance fields provide granular
review tracking:

- `review_severity`: Severity of finding (`critical`, `important`, `nit`).
- `review_iteration`: Integer iteration or review round (>= 1).
- `review_finding_ids`: List of review finding identifiers addressed by this action.
- `review_disposition`: Disposition of finding (`applied`, `rejected`).
- `rejection_reason`: Justification if review suggestion was rejected.
- `introduced_by_action_id`: Action ID that originally introduced the issue being resolved.

###### Resolver Commit Trailers

When committing fixes, agents and operators include standardised commit trailers to record provenance directly in git
history:

- `Action-Origin`: Origin category (e.g. `pr_review`, `ci_fix`).
- `Review-Severity`: Severity level (`critical`, `important`, `nit`).
- `Review-Iteration`: Round number (`1`, `2`, ...).
- `Review-Finding-Ids`: Comma-separated list of finding IDs.
- `Review-Disposition`: Outcome disposition (`applied`, `rejected`).
- `Rejection-Reason`: Optional reason for rejection.
- `Introduced-By-Action-Id`: Causative action ID when linking defects to initial implementation.

Example commit message:

```text
fix: resolve null check exception in flow runner

Action-Origin: pr_review
Review-Severity: critical
Review-Iteration: 2
Review-Finding-Ids: FIND-001, FIND-002
Review-Disposition: applied
Introduced-By-Action-Id: ACT-1791839487-a1b2c3d4
```

##### CLI Query Interface (`holon ledger query`)

The execution ledger can be queried and filtered via `holon ledger query`:

```bash
# Query recent actions in formatted table view
holon ledger query --entry-type action --limit 20

# Query failed actions in JSON format
holon ledger query --outcome failure --json

# Query review fixes for critical findings
holon ledger query --origin pr_review --review-severity critical

# Search for specific keywords across errors and action summaries
holon ledger query --query "git recovery"
```

---

## Validation and tooling: Config-Driven Schemas

The ledger is validated against schemas defined in `holon-config/schemas/ledger.json`. This ensures that all events
appended to the ledger conform to the required structure and business rules.

### Ledger validator

A validator should:

- Load the current schema from `holon-config/schemas/ledger.json`.
- Enforce envelope presence.
- Enforce schema version compatibility.
- Enforce range constraints (e.g., `p_success` between 0 and 1).
- Enforce event relationship constraints (ordering and references).
- Report anomalies as events (`ledger_validation_error`) rather than mutating history.

### Query patterns

Common queries:

- calibration per agent/model over last N executions
- routing ROI by model tier for given entropy bands
- top failure modes for intents of a given type
- mean entropy vs predicted entropy by estimator version
- conflict rates by repo area / module

---

## Related documents

- [`metrics.md`](metrics.md) — definitions for predicted/actual metrics
- [`git_flow.md`](git_flow.md) — rebase + merge constraints reflected in events
- [`architecture.md`](architecture.md) — how ledger fits into the system
- [`naming_conventions.md`](naming_conventions.md) — intent_id and plan_id formats
