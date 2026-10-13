# Holon Sandbox Executor (`sandbox-executor`)

The `sandbox-executor` package is the core execution and orchestration engine of Holon Agentic Coder. It manages
containerized and process-isolated sandboxes for autonomous agents, powers the 5-stage Holon Flow pipeline, enforces
safety boundaries, and maintains append-only ledger audit logs.

---

## 1. Package Architecture & Submodules

```
apps/sandbox-executor/
├── src/
│   └── sandbox_executor/
│       ├── flow.py              # 5-stage lifecycle pipeline engine & checkpointing
│       ├── cli.py               # Unified CLI interface (holon subcommands)
│       ├── calibration.py       # Post-execution calibration engine (Bean 0038)
│       ├── agent_runner.py      # Docker container execution & runner lifecycle
│       ├── ledger.py            # Immutable ledger parsing, query, & reconciliation
│       ├── formatting.py        # Prettier formatting convergence utilities
│       ├── context_cleaner.py   # Context and git workspace sanitization
│       ├── entrypoint/          # Sandbox entrypoint scripts (planner, executor)
│       └── token_reduction/     # MITM proxy context compression & caching sidecar
└── tests/                       # Hermetic pytest suite
```

### Key Submodules

- **`flow.py`**: Houses `PipelineEngine`, `FlowContext`, `FlowCheckpoint`, and stage runners (`run_intent_stage`,
  `run_plan_stage`, `run_execute_stage`, `run_review_stage`, `run_calibrate_stage`). Manages stage dispatch, atomic
  checkpointing, and Bean 0034 safety halts.
- **`cli.py`**: Entrypoint for all `holon` commands (`flow`, `plan`, `execute`, `calibrate`, `ledger`). Handles argument
  parsing, proxy setup, and process dispatch.
- **`calibration.py`**: Executes post-execution calibration analysis comparing predicted EV and step metrics against
  actual test results and git logs, committing markdown reports to `/calibrated` branches.
- **`agent_runner.py`**: Manages isolated Docker container invocations with CPU/memory limits and volume mounts for
  planner and executor agents.
- **`ledger.py`**: Provides structured querying, deduplication, conflict resolution, and filtering across
  `intents.jsonl`, `plans.jsonl`, and `executions.jsonl`.
- **`token_reduction/`**: Runs a locally-contained mitmproxy sidecar performing prompt cache optimization, local body
  caching, and wire log sanitization.

---

## 2. CLI Subcommands Reference

The package provides the `holon` CLI command.

### `holon flow`

Runs the automated 5-stage Holon Flow lifecycle pipeline:

```bash
# Run complete lifecycle
./holon flow intents/my-task.json

# Run with token reduction and dashboard
./holon flow intents/my-task.json --token-reduce --mitm-web

# Skip review and calibration stages
./holon flow intents/quick-task.json --skip-review --skip-calibrate

# Resume from existing checkpoint
./holon flow --checkpoint .holon/flow/checkpoint-my-task.json
```

### `holon plan`

Runs the Plan Generator inside a sandboxed container to produce a structured plan branch and markdown file:

```bash
./holon plan I-1784983150-build-execution/_ --agent antigravity-agent --model gemini-3.8-flash-medium
```

### `holon execute`

Runs the Sandbox Executor to implement plan changes and run tests within container containment:

```bash
./holon execute I-1784983150-build-execution/P-1784983200-antigravity-agent/_ --agent antigravity-agent
```

### `holon calibrate`

Runs post-execution calibration on an execution branch without rerunning tests:

```bash
./holon calibrate I-1784983150-build-execution/P-1784983200-antigravity-agent/E-1784983300-impl/_
```

### `holon ledger query`

Queries and filters immutable audit ledger entries:

```bash
# Query failures in executions ledger
./holon ledger query --outcome failure --category bugfix

# Query with free-text keyword
./holon ledger query --query "merge conflict" --limit 10
```

---

## 3. Development Workflows & Testing

All commands must be executed strictly from the repository root using `uv`:

### Running Tests

```bash
# Run unit tests (excluding integration and stress)
uv run task test

# Run full pytest suite
uv run pytest apps/sandbox-executor/tests

# Run a specific test file
uv run pytest apps/sandbox-executor/tests/test_flow.py
```

### Code Formatting & Linting

```bash
# Run complete validation checks (ruff + prettier)
uv run task check

# Automatically format Python and markdown files
uv run task format
```
