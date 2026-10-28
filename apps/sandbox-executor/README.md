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
  `run_plan_stage`, `run_execute_stage`, `run_review_stage`, `run_calibrate_stage`). Stages 1-3 dispatch the same role
  containers as the per-stage commands (through `cli.dispatch_role_agent`) and adopt the branches they push; the flow
  never authors plans or code itself. Also manages atomic checkpointing, the empty-diff guard and Bean 0034 safety
  halts.
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
  temporary `0600` docker `--env-file` (in the `0700` directory `~/.holon/tmp/env-files`) that is removed after the run;
  the pre-flight readiness probe (`holon doctor`, and before `holon plan` / `execute` / `flow`) uses the same env-file
  and classifier for `HOLON_AGENT_KEY` and a credential-bearing `HOLON_AGENT_SETTINGS`, and forwards `HOLON_REPO_URL`
  without userinfo. `HOLON_REPO_URL` never embeds credentials: HTTP(S) userinfo is stripped from both the override and
  `remote.origin.url`, while SSH remotes are left untouched. A stripped credential (the password, the user name in the
  `<token>:x-oauth-basic` form, or a token-shaped bare user name) wins over the ambient host token, except that a
  plain-HTTP credential is discarded with a warning. In-container git authenticates through a host-scoped credential
  helper configured only in the environment (`GIT_CONFIG_*`), so the token is never written to `.git/config`. Every
  echoed command is masked through the shared `sandbox_executor.redaction` patterns, always before any length clamp.
  Known gap: the mitmweb dashboard password is still on the sidecar's command line. See
  [Agent Credentials](../../docs/executor/agent_credentials_requirements.md#repository-url-credentials).

---

## 3. CLI Subcommands Reference

The package provides the unified `holon` CLI command.

### `holon flow`

Runs the automated 5-stage Holon Flow lifecycle pipeline (Intent -> Plan -> Execute -> Review Loop -> Calibrate). A flow
run is equivalent to invoking `holon intent`, `holon plan`, `holon execute`, `holon review-loop` and `holon calibrate`
by hand: Stages 1-3 launch the same role containers with the resolved `--agent` and `--model`, then fetch `origin` to
adopt the branch each container pushed. The flow fails Stage 2 rather than fabricate a plan when no planner produced
`plans/P-*.md`, and fails Stage 3 (recording `status: "failed"`, opening no PR) when the execution changes nothing
outside `holon-knowledge/ledger/`, `plans/` and `executions/`. `--skip-push` is only accepted with `--dry-run`, which
validates without dispatching. See [the flow equivalence guarantee](../../docs/git_flow.md#flow-equivalence-guarantee).

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

The review loop fails closed:

- **Target resolution.** A PR number or GitHub PR URL is resolved through
  `gh pr view <target> --json number,headRefName,headRefOid,title,body` to its head branch and head SHA
  (`sandbox_executor/review_target.py`); a branch resolves to its fetched tip. The container entrypoint does this before
  any git operation, clones or fetches the head branch (never `git clone --branch 87`), checks every git return code and
  requires `HEAD` to equal the head SHA, fetching that SHA explicitly if the branch has moved. Before the clone, `gh`
  learns the repository from `GH_REPO`, derived from `HOLON_REPO_URL` without its credentials. On any resolution, clone,
  fetch or checkout failure it appends one `ACT-REV` failure row to `holon-knowledge/ledger/executions.jsonl`, prints a
  FAILED summary and exits 1 without computing or posting a consensus. Limitation: when the workspace has no
  `holon-knowledge/ledger/` directory, the failure is reported only on stderr and through the exit code.
- **Empty-diff guard.** The guard runs for every target, whatever `--auto-pr` is and whether or not `gh` is installed:
  an empty diff is `rejected` and an unverifiable one `failed`, before any PR, iteration or post. Execution branches are
  compared with their plan branch, PRs (by head SHA) and other branches with the intent's target branch (`main` by
  default).
- **Posting.** A consensus whose post fails or raises is `post_failed`, never approved; the `ACT-POST` failure row is
  still written.
- **Dry runs.** A dry run measures nothing, so a simulated approval reports `not_measured` and is never approved.
- **Exit code.** The command exits 0 only when the consensus is approved and posted; otherwise it exits 1 and prints the
  reason. It never merges and never enables auto-merge.
- **Bounded waits.** Pending CI checks are re-probed every `CI_REGISTRATION_POLL_SECONDS` (15 s) until they settle or
  the iteration's `review_iteration` budget runs out; checks still pending then count as a CI finding.

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
