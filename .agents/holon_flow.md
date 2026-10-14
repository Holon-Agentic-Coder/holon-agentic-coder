# Holon Flow Lifecycle Architecture & Developer Guide

Holon Flow (`holon flow`) is the unified orchestration pipeline that coordinates autonomous AI coding agents across the
5-stage software delivery lifecycle in Holon Agentic Coder. It automates intent ingestion, plan generation, sandboxed
execution, diff review verification, and post-execution calibration while maintaining strict safety invariants and
ledger provenance.

---

## 1. The 5-Stage Lifecycle Architecture

The Holon Flow pipeline executes sequentially through five distinct lifecycle stages.

```
+-----------------------------------------------------------------------------------+
|                                 Holon Flow Pipeline                               |
|                                                                                   |
|  [Stage 1: Intent] --> [Stage 2: Plan] --> [Stage 3: Execute]                    |
|                                                  |                                |
|                                                  v                                |
|                           +------------- [Stage 4: Review] <---+                  |
|                           |                      |             | (revisions)      |
|          (--skip-review)  |       [Consensus]    v             |                  |
|                           |             [Stage 5: Calibrate] --+                  |
|                           |                      |                                |
|                           |                      v                                |
|                           |              +---------------+                        |
|                           |              | Bean 0034     |                        |
|                           |              | Human Halt    |                        |
|                           |              | (Merge Sign)  |                        |
|                           |              +---------------+                        |
|                           |                      |                                |
|                           +--------------------->v                                |
|                                             [Completed]                           |
+-----------------------------------------------------------------------------------+
```

### Stage 1: Intent Creation (`FlowStage.INTENT`)

- **Purpose**: Ingest and validate the intent specification, initialize isolated branch namespaces, and append
  provenance records to the ledger.
- **Git Branch**: `I-{timestamp}-{slug}/_` created from target base (usually `main`).
- **Validation**: Schema validation on `slug`, `title`, `category`, `priority`, `labels`, and clarification questions
  under Bean 0085.
- **Ledger Record**: Append-only row written to `holon-knowledge/ledger/intents.jsonl`.
- **Prettier Convergence**: Enforces formatting on updated markdown and ledger structures.

### Stage 2: Plan Generation (`FlowStage.PLAN`)

- **Purpose**: Invoke an autonomous planning agent to produce a structured, step-by-step markdown plan with quantitative
  metrics.
- **Git Branch**: `I-{timestamp}-{slug}/P-{timestamp}-{agent}-{model}/_` branched from the intent branch.
- **Plan File**: Persisted to `plans/P-{timestamp}-{agent}-{model}.md`.
- **Telemetry & EV**: Extracts predicted plan metrics (`p_success_pred`, `entropy_pred`, `impact_pred`, `cost_pred`,
  `learning_value_pred`) and computes Expected Value (`ev_pred`).
- **Ledger Record**: Append-only row written to `holon-knowledge/ledger/plans.jsonl`.

### Stage 3: Plan Execution (`FlowStage.EXECUTE`)

- **Purpose**: Execute code modifications and tests inside an isolated sandbox conforming to the plan.
- **Git Branch**: `I-{timestamp}-{slug}/P-{timestamp}-{agent}-{model}/E-{timestamp}-{action}/_` branched from the plan
  branch.
- **Rebase Discipline**: Automatically rebases from the plan branch before execution to prevent divergent histories.
- **Test Execution**: Executes `pytest` test suites and records test pass rates and exit codes.
- **Ledger Record**: Append-only row written to `holon-knowledge/ledger/executions.jsonl`.

### Stage 4: PR Review Loop (`FlowStage.REVIEW`)

- **Purpose**: Perform automated review of execution diffs, verify mergeability against target branch, and evaluate
  safety invariants.
- **Diff Analysis**: Computes `shortstat` diff metrics (files changed, insertions, deletions) between plan and execution
  branches.
- **Mergeability Check**: Runs git merge-tree or GitHub CLI checks to detect merge conflicts early.
- **Review Iterations**: Bounded by `max_review_iterations` (default: 3) to prevent unbounded loops.
- **Bean 0034 Human-Only PR Merging Invariant**:
  > **SAFETY HALT**: When the review loop achieves consensus approval (100% test pass rate, no merge conflicts, clean
  > diffs), the pipeline unconditionally transitions to `HALTED_FOR_HUMAN`. Autonomous merging to `main` is strictly
  > prohibited. The system presents manual approval commands for operator sign-off.
- **Bypass**: Can be bypassed using `--skip-review` for rapid prototyping or emergency out-of-band testing.

### Stage 5: Post-Execution Calibration (`FlowStage.CALIBRATE`)

- **Purpose**: Run post-execution calibration analysis (Bean 0038) comparing predicted vs actual outcomes.
- **Git Branch**: Execution branch appended with `/calibrated` (e.g. `.../E-.../_/calibrated`).
- **Artifacts**: Generates calibration report markdown in `plans/` containing variance analysis, Brier score
  calibration, and entropy deltas.
- **Ledger Record**: Updates or appends calibration outcomes for continuous agent learning.
- **Bypass**: Can be bypassed using `--skip-calibrate`.

---

## 2. Checkpoint & Resumption Mechanics

The pipeline provides crash resilience and state preservation via atomic checkpoints:

- **Location**: `.holon/flow/checkpoint-{slug}.json`
- **Atomic Persistence**: Checkpoints are written to a temporary file in the target directory and atomically moved into
  place (`os.replace`) to guard against partial writes during crashes or terminations.
- **Schema**: Preserves current stage, completed stages list, full `FlowContext` configuration, branch names, stage
  results, and timestamped logs.
- **Resumption**:
  ```bash
  # Resume from last checkpoint stage
  ./holon flow --checkpoint .holon/flow/checkpoint-my-task.json

  # Resume from specific stage override
  ./holon flow --checkpoint .holon/flow/checkpoint-my-task.json --from-stage review
  ```
- **CLI Overrides**: Explicit CLI flags (such as `--skip-review`, `--skip-calibrate`, `--token-reduce`) override loaded
  checkpoint defaults during resumption.

---

## 3. CLI Runner Reference (`holon flow`)

### Syntax

```bash
./holon flow [intent_file] [OPTIONS]
```

### Options Reference

| Flag                      | Type       | Default                   | Description                                                                           |
| ------------------------- | ---------- | ------------------------- | ------------------------------------------------------------------------------------- |
| `intent_file`             | Positional | `None`                    | Path to local intent JSON/YAML file (e.g., `intents/my-task.json`).                   |
| `--from-stage`            | Choice     | `None`                    | Stage to start/resume from: `intent`, `plan`, `execute`, `review`, `calibrate`.       |
| `--checkpoint`            | String     | `None`                    | Path to existing `.json` checkpoint file for state resumption.                        |
| `--agent`                 | String     | `antigravity-agent`       | Agent runner identifier (e.g., `antigravity-agent`, `claude-agent`, `pi-agent`).      |
| `--model`                 | String     | `gemini-3.8-flash-medium` | Model identifier passed to the agent.                                                 |
| `--dry-run`               | Flag       | `False`                   | Validate stage inputs and branch naming without executing external agents or commits. |
| `--json`                  | Flag       | `False`                   | Output pipeline status and stage payloads in structured JSON format to stdout.        |
| `--repo-dir`              | String     | `.`                       | Target repository directory path.                                                     |
| `--skip-push`             | Flag       | `False`                   | Skip pushing local git branches to remote origin during stages.                       |
| `--skip-review`           | Flag       | `False`                   | Skip Stage 4 (PR review loop) and proceed directly to calibration.                    |
| `--skip-calibrate`        | Flag       | `False`                   | Skip Stage 5 (post-execution calibration report generation).                          |
| `--max-review-iterations` | Integer    | `25`                      | Maximum autonomous review loop iterations in Stage 4. Must be `>= 1` (default: `25`). |
| `--token-reduce`          | Flag       | `False`                   | Enable mitmproxy token reduction sidecar for caching and context compression.         |
| `--mitm-web`              | Flag       | `False`                   | Launch mitmweb web dashboard on port 8081 (configurable via `HOLON_MITM_WEB_PORT`).   |

---

## 4. Usage Patterns & Workflows

### Standard End-to-End Run

```bash
./holon flow intents/my-feature.json
```

### Rapid Prototype Run (Skip Review & Calibration)

```bash
./holon flow intents/quick-fix.json --skip-review --skip-calibrate
```

### Dry-Run Validation

```bash
./holon flow intents/my-feature.json --dry-run --json
```

### Resuming After Review Feedback

```bash
./holon flow --checkpoint .holon/flow/checkpoint-my-feature.json --from-stage review --max-review-iterations 5
```

### Token Reduction with Live Traffic Inspection

```bash
./holon flow intents/complex-task.json --token-reduce --mitm-web
```

---

## 5. Safety Invariants & Troubleshooting

1. **Human-Only Merging (Bean 0034)**:
   - The flow runner will never execute `git merge` into `main`.
   - On consensus, the pipeline outputs `[BEAN 0034 SAFETY HALT]` with instructions for human operator review.
2. **Rebase Collisions**:
   - If upstream changes conflict during execution branch rebase, the pipeline halts with `FAILED` in the execute stage
     and saves checkpoint state.
   - Resolve conflicts in the working branch and resume using `./holon flow --checkpoint <file> --from-stage execute`.
3. **Dirty Workspace Invariant**:
   - Stage 4 verifies PR mergeability and branch dirtiness. Uncommitted changes or unresolved conflict markers trigger
     immediate review rejection.
