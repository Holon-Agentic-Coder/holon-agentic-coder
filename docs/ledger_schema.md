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

### Recommended format: JSON Lines

- Directory: `holon-knowledge/ledger/`
- Primary File: `holon-knowledge/ledger/events.jsonl`
- Encoding: UTF-8
- Each line: one JSON object (one event)
- Ordering: strictly increasing by `ts` then by `seq`

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

Appended when a plan execution begins/finishes:

- `execution_id`: Unique identifier for the execution record.
- `plan_branch`: Branch of the plan being executed.
- `agent`: The active agent name.
- `agent_version`: The resolved version string of the agent CLI.
- `model`: The model used for execution.
- `status`: Execution status (e.g. `success`, `failure`, `decomposed`).
- `summary`: Description of execution outcome (only for terminal states).
- `execution_file`: _(Deprecated / Omitted for new records)_ Historical relative path to execution markdown file.
- `agent_output_truncated`: Boolean flag indicating if captured agent logs exceeded byte budget and were truncated.
- `agent_output_bytes`: Byte length of captured (and potentially truncated/sanitized) agent output payload.
- `ledger_revision`: Integer revision number (defaults to `1` on creation).
- `sub_intents`: List of sub-intents generated upon decomposition (only for `decomposed` state).
- `created_at`: Creation timestamp.

**Reader Contract for Superseding Rows:** When a ledger row is superseded (e.g. `ledger_revision >= 2` due to
post-execution verification or git recovery failures), the superseding row must repeat all authoritative fields that
readers should rely on (including `agent_output_truncated` and `agent_output_bytes`). Readers must select the row with
the highest `ledger_revision` for a given `execution_id` without needing to merge fields backwards.

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
