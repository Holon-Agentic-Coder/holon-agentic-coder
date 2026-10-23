# Holon Sandbox Executor (`sandbox-executor`)

The `sandbox-executor` package is the core execution and orchestration engine of Holon Agentic Coder. It manages
containerised and process-isolated sandboxes for autonomous agents, powers the 5-stage Holon Flow pipeline, enforces
safety boundaries, and maintains append-only ledger audit logs.

---

## 1. Package Architecture & Submodules

```
apps/sandbox-executor/
├── src/
│   └── sandbox_executor/
│       ├── flow.py              # 5-stage lifecycle pipeline engine & checkpointing
│       ├── cli.py               # Unified CLI interface (holon subcommands)
│       ├── git.py               # Centralised Git repository abstraction & operations (Bean 0124)
│       ├── calibration.py       # Post-execution calibration engine (Bean 0038)
│       ├── agent_runner.py      # Docker container execution & runner lifecycle
│       ├── ledger.py            # Canonical 3-ledger operations & LedgerLock concurrency (Bean 0127)
│       ├── formatting.py        # Prettier formatting convergence utilities
│       ├── context_cleaner.py   # Context and git workspace sanitisation
│       ├── entrypoint/          # Sandbox role entrypoints & Stage 4 review loop
│       │   ├── planner.py       # Sandboxed plan generation entrypoint
│       │   ├── executor.py      # Sandboxed execution entrypoint & credential scrubber
│       │   └── review_loop.py   # Autonomous native PR review loop runner (Bean 0118)
│       ├── knowledge/           # Knowledge & Wisdom consult engine (Bean 0052)
│       │   ├── consult.py       # Multi-label consult engine & payload token budgeter
│       │   ├── index.py         # SQLite FTS5 action index database & sync
│       │   ├── records.py       # Knowledge record ingestion, validation & redaction
│       │   ├── promotion.py     # Qualification heuristic & KB/WB promotion
│       │   ├── stats.py         # Self-telemetry & operational consultation metrics
│       │   └── wisdom.py        # Wisdom base axiom ingestion & synchronisation
│       └── token_reduction/     # MITM proxy context compression & caching sidecar
└── tests/                       # Hermetic pytest suite
```

### Key Submodules

- **`flow.py`**: Houses `PipelineEngine`, `FlowContext`, `FlowCheckpoint`, and stage runners (`run_intent_stage`,
  `run_plan_stage`, `run_execute_stage`, `run_review_stage`, `run_calibrate_stage`). Manages stage dispatch, atomic
  checkpointing, and Bean 0034 safety halts.
- **`cli.py`**: Entrypoint for all `holon` commands (`flow`, `plan`, `execute`, `review-loop`, `calibrate`, `consult`,
  `record`, `reconcile`, `promote`, `veto`, `stats`, `ledger`). Handles argument parsing, proxy setup, and process
  dispatch.
- **`git.py`**: Centralised repository abstraction providing typed Git interactions (`run_git`, `get_diff_numstat`,
  `resolve_ref`, `get_head_commit`, `branch_exists`) with automatic credential scrubbing and timeout enforcement.
- **`calibration.py`**: Executes post-execution calibration analysis comparing predicted EV and step metrics against
  actual test results and git logs, committing markdown reports to `/calibrated` branches.
- **`agent_runner.py`**: Manages isolated Docker container invocations with CPU/memory limits, volume mounts, and
  hardened security policies for planner and executor agents.
- **`ledger.py`**: Provides structured querying, deduplication, conflict resolution, and filtering across
  `intents.jsonl`, `plans.jsonl`, and `executions.jsonl`. Implements `LedgerLock` (POSIX advisory file locking with
  exponential backoff) for concurrent append safety.
- **`entrypoint/review_loop.py`**: Powers autonomous Stage 4 review loops with multi-agent ensemble consensus
  evaluations, dry-run reviews, and automated PR issue resolution.
- **`knowledge/`**: Implements Knowledge and Wisdom consultation (`ConsultEngine`, `ActionIndexDatabase`), indexing
  discrete actions into SQLite FTS5 with sub-50ms query SLAs, semantic deduplication, and candidate promotion.
- **`token_reduction/`**: Runs a locally-contained mitmproxy sidecar performing prompt cache optimisation, local body
  caching, and wire log sanitisation.

---

## 2. Container Security Hardening

Agent execution within sandboxes is strictly isolated using non-root container containment and minimal privilege
enforcement:

- **Capability Dropping (`--cap-drop=ALL`)**: All Linux kernel capabilities are stripped by default from agent
  containers, preventing administrative escalation, network raw socket manipulation, and device tampering.
- **Privilege Boundary (`--security-opt=no-new-privileges`)**: Disables `setuid` and `setgid` binaries from acquiring
  elevated privileges inside the container environment.
- **Non-Root Execution (`holon` user)**: Agent processes execute as an unprivileged system user (`holon`, UID `10001`,
  GID `10001`) with isolated user directories and sandboxed workspace access.
- **Network Containment**: Agent outbound traffic is routed through the local MITM proxy sidecar, stripping
  authentication credentials and enforcing strict egress filtering.
- **Credentials Off the Command Line**: The GitHub token and other credential variables reach the container through a
  temporary `0600` docker `--env-file` that is removed after the run. `HOLON_REPO_URL` never embeds credentials.
  In-container git authenticates through a credential helper configured only in the environment (`GIT_CONFIG_*`), so the
  token is never written to `.git/config`. Every echoed command is masked through the shared
  `sandbox_executor.redaction` patterns. See
  [Agent Credentials](../../docs/executor/agent_credentials_requirements.md#how-the-github-token-reaches-the-container).

---

## 3. CLI Subcommands Reference

The package provides the unified `holon` CLI command.

### `holon flow`

Runs the automated 5-stage Holon Flow lifecycle pipeline (Intent -> Plan -> Execute -> Review Loop -> Calibrate):

```bash
# Run complete lifecycle
./holon flow intents/my-task.json

# Run with token reduction and dashboard
./holon flow intents/my-task.json --token-reduce --mitm-web

# Resume pipeline from a specific stage
./holon flow --from-stage execute --checkpoint .holon/flow/checkpoint-my-task.json

# Skip review and calibration stages
./holon flow intents/quick-task.json --skip-review --skip-calibrate
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

### `holon review-loop` (aliases: `review`, `pr-review-loop`)

Runs the autonomous native review loop on an execution branch or pull request:

```bash
# Review an execution branch with 3-agent ensemble
./holon review-loop I-1784983150-build-execution/P-1784983200-antigravity-agent/E-1784983300-impl/_

# Review a pull request by number or URL with max 10 iterations
./holon review-loop 87 --max-iterations 10 --ensemble-size 3

# Run in dry-run mode without applying changes or pushing commits
./holon review-loop 87 --dry-run
```

### `holon calibrate`

Runs post-execution calibration on an execution branch without rerunning tests:

```bash
./holon calibrate I-1784983150-build-execution/P-1784983200-antigravity-agent/E-1784983300-impl/_
```

### `holon consult`

Consults historical knowledge records and wisdom using multi-label queries and full-text search:

```bash
# Conjunctive query: match records with all specified labels
./holon consult --all python git

# Disjunctive query with full-text search
./holon consult --any cli docker -q "permission denied" --top-k 5

# JSON formatted query output
./holon consult --all test pytest --json
```

### `holon record`

Records a discrete action event to the executions ledger with automatic credential scrubbing:

```bash
./holon record --action "Applied fix for timeout jitter" --category test --verdict success --labels "test,ci"
```

### `holon reconcile`

Reconciles pending action outcomes and expires stale unconfirmed records:

```bash
# Reconcile specific action outcome
./holon reconcile --action-id ACT-1792384779-621819d7 --verdict success --reason "Verified in test run"

# Expire stale records older than TTL days
./holon reconcile --expire-ttl 7
```

### `holon promote`

Evaluates qualified knowledge candidates and promotes them to the Knowledge Base (KB) or Wisdom Base (WB):

```bash
# Dry-run promotion evaluation
./holon promote --dry-run --min-trials 3

# Promote all qualified pending candidates
./holon promote --pending
```

### `holon veto`

Inspects and manages deterministic failure vetoes to prevent repeated failures:

```bash
# List active and shadowed failure vetoes
./holon veto list

# Lift a failure veto by context fingerprint
./holon veto lift --context 7d7d77ee670a4c23 --reason "Upstream bug fixed in v1.2"
```

### `holon stats`

Displays operational self-metrics and consult telemetry:

```bash
# Display telemetry table
./holon stats

# Output telemetry in structured JSON
./holon stats --json
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

## 4. Development Workflows & Testing

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
