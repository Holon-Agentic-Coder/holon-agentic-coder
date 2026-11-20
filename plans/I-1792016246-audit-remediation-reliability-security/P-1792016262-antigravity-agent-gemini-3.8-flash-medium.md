# Plan for I-1792016246-audit-remediation-reliability-security

- **Plan ID:** P-1792016262-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-14T22:17:42.971Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with empirical validation of atomic advisory file locking with exponential backoff,
  schema version migration dispatch tables, unprivileged container network isolation and cloud metadata sinkholing, and
  read-only host credential containment.
- **Safety priority level:** critical
- **Priority Justification:** Triggered by `docs/safety.md` §1 (Git as safety boundary), §2 (Sandboxing is mandatory for
  execution), §4 (Failure containment and blast radius limits), `holon-config/world/constraints.md` §2 (Sandbox
  Containment Tiers: process and container sandbox boundary enforcement, network isolation, preventing escape actions),
  §3 (Ledger Immutability: zero modification policy, append-only operations under concurrent execution), and
  `holon-config/world/ruleset.md` §1, §2, §3 (Runtime `==3.13.*`, monorepo workspace isolation under
  `apps/sandbox-executor`, strict typing with `typing`, PEP 8, test placement and assertions in `tests/`). Critical
  safety priority is mandated because this intent remediates core architecture and security vulnerabilities identified
  in the platform audit (Bean 0044) across ledger write safety, state serialization integrity, host D-Bus socket
  isolation, and network egress containment.

## Exploration

- **Proportion of steps that are exploratory:** 0.38
- **Justification:** Steps 1, 3, and 5 adopt a balanced exploratory stance to empirically validate file-locking
  contention behavior across POSIX and non-POSIX fallback paths, versioned schema migration dispatch transformations
  across disparate pipeline states, and container egress restrictions (metadata endpoint blocking and `NET_RAW`
  dropping) without impairing git remotes or authorized LLM provider communication.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.55  |
| impact_pred         | 96.0  |
| cost_pred           | 15.6  |
| learning_value_pred | 6.5   |
| ev_pred             | 75.81 |

### Strategy Rationale

The overall plan metrics were derived from the individual step-level metrics as follows:

- **p_success_pred**: 0.92. Dictated by the bottleneck step (Step 5: 0.92), where container network isolation and egress
  filtering must block unauthorized scanning and cloud metadata access (`169.254.169.254`) while ensuring zero
  disruption to git remotes and authorized LLM endpoints across varying Docker daemon and host networking environments.
- **entropy_pred**: 0.55. Evaluated as the maximum single-step risk profile (Step 5: 0.55). The sum of predicted step
  entropies across all eight steps is 2.95 (`0.45 + 0.30 + 0.40 + 0.25 + 0.55 + 0.40 + 0.30 + 0.30 = 2.95`), well within
  the allocated entropy budget of 15.0.
- **impact_pred**: 96.0. Directly fulfills Batch K and remediates four critical audit findings from Bean 0044: atomic
  ledger file locking prevents corruption during concurrent flows (Bean 0093); checkpoint schema versioning and
  migration prevents deserialization crashes across version boundaries (Bean 0094); sandbox network firewalling prevents
  rogue egress and cloud metadata exfiltration (Bean 0092); and session credential isolation eliminates host D-Bus
  exposure and enforces read-only mounts (Bean 0095).
- **cost_pred**: 15.6. Calculated as the direct sum of individual step costs
  (`2.2 + 1.8 + 2.0 + 1.6 + 2.5 + 2.1 + 1.8 + 1.6 = 15.6`).
- **learning_value_pred**: 6.5. High epistemic gain delivered from testing POSIX advisory locking under high concurrency
  and failure simulation, establishing a reusable schema migration dispatch framework, formulating robust container
  egress controls without requiring external proxy dependencies, and hardening host session state isolation.
- **ev_pred**: 75.81. Computed strictly via the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system constants `lambda = 0.3`
  and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.92 * 96.0 + 0.5 * 6.5 - 0.3 * 0.55 - 15.6 = 88.32 + 3.25 - 0.165 - 15.6 = 75.805 -> 75.81`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, commit
    scoping, rebase discipline).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: container resource bounds, filesystem
    containment, network egress restrictions, zero tolerance for escape actions).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modification policy; ledger files in
    `holon-knowledge/ledger/` are strictly append-only).
  - `holon-config/world/ruleset.md` §1 (Runtime: Python `==3.13.*`, package management via `uv`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions: PEP 8 compliance, strict static typing via `typing` module,
    comprehensive docstrings).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1` as standard test runner, tests located in
    `apps/sandbox-executor/tests/`, test hermeticity enforced).
  - `docs/safety.md` §1 & §2 (Git as safety boundary, mandatory sandboxing, blast radius containment).
  - `docs/audit_report.md` Findings SEC-01, SEC-02, BUG-01, BUG-02 (Beans 0092, 0095, 0093, 0094).
- **Potential violations or edge cases:**
  - File locking deadlocks or unbounded lock wait times during high-frequency concurrent ledger writes.
  - Non-POSIX environments (Windows or environments lacking `fcntl`) crashing if advisory locking is imported or called
    unconditionally.
  - Incomplete filesystem flush where metadata is written but content is not synced to physical disk (`f.flush()`
    without `os.fsync(f.fileno())`).
  - Schema migration crashes on corrupted, half-written, or future schema versions.
  - Network isolation blocking necessary communication with git remotes (e.g. GitHub) or legitimate LLM provider
    endpoints (Anthropic, Google, OpenAI).
  - Container failure when agent runner tries to write configuration into a read-only (`:ro`) session directory without
    an ephemeral scratch overlay.
  - Host environment variable leakage or lingering credentials in temporary working files.
- **Mitigations built into the plan:**
  - Implement non-blocking `fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)` with a strict timeout (e.g. 10.0 seconds),
    exponential backoff, and randomized jitter to prevent lock starvation and deadlocks.
  - Wrap `fcntl` in platform checks (`sys.platform != "win32"` and `hasattr(fcntl, "flock")`), providing a graceful
    fallback to standard append with actionable diagnostic logging.
  - Enforce explicit `f.flush()` followed by `os.fsync(f.fileno())` before releasing any lock.
  - Schema version validation rejects unsupported future versions (`schema_version > CURRENT_SCHEMA_VERSION`) with an
    explicit `ValueError`, while legacy v0 checkpoints pass through a migration dispatch table that provides
    backward-compatible default fields.
  - Container network isolation explicitly blocks link-local/cloud metadata (`169.254.169.254`) and drops `NET_RAW`
    capability while preserving DNS resolution and standard HTTPS egress for git and LLM endpoints.
  - Eliminate the host D-Bus socket mount completely; mount host session directories as `:ro` or clone them into
    ephemeral temporary scratch directories, cleaning up any credentials upon container exit.
  - Run full hermetic test suites, linting (`ruff`), and markdown validation (`prettier`) at every step.
- **Residual risk accepted (and why):**
  - Minor I/O latency overhead (a few milliseconds) per ledger write from `os.fsync`; accepted because ledger integrity
    and concurrency safety under multi-agent execution far outweigh negligible write latency.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 2.95
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

This plan implements Batch K remediating key architecture, reliability, and security audit findings from Bean 0044
across `holon-agentic-coder`:

1. **Bean 0093 (Atomic Ledger Locking & Concurrent Write Safety):**
   - In `apps/sandbox-executor/src/sandbox_executor/ledger.py` and `flow.py`, implement an atomic context manager for
     ledger writes (`atomic_ledger_append` / `atomic_ledger_lock`).
   - Use advisory file locking (`fcntl.flock` with `LOCK_EX`) with non-blocking checks, exponential backoff, and
     configurable timeout.
   - Enforce explicit `f.flush()` and `os.fsync(f.fileno())` before releasing the lock.
   - Provide graceful fallback for non-POSIX environments and diagnostic error logging.
   - Replace all uncoordinated `open(..., "a")` append operations in `ledger.py` and `flow.py` with this atomic context
     manager.
   - Add unit tests in `tests/test_ledger_query.py` verifying concurrency safety, timeout handling, and error recovery.

2. **Bean 0094 (Pipeline Checkpoint Schema Versioning & Migration):**
   - In `apps/sandbox-executor/src/sandbox_executor/flow.py`, introduce explicit schema versioning
     (`schema_version: int = 1`) for `FlowCheckpoint` and `FlowContext`.
   - Implement a migration dispatch table mapping `(from_version, to_version)` to deterministic transformation
     functions.
   - Ensure legacy v0 checkpoints (lacking `schema_version`) load cleanly and upgrade automatically with standard
     defaults.
   - Enforce rejection of unsupported future schema versions with a descriptive `ValueError`.
   - Add unit tests in `tests/test_flow.py` verifying legacy loading, multi-version migration, and future version
     rejection.

3. **Bean 0092 (Sandbox Network Firewalling & Egress Filtering):**
   - In `apps/sandbox-executor/src/sandbox_executor/cli.py` and `flow.py`, implement network isolation and egress
     filtering for container sandboxes.
   - When direct or fallback egress is used, apply `--cap-drop=NET_RAW` to prevent raw socket packet crafting and
     network scanning.
   - Block cloud metadata endpoints (`169.254.169.254`, `metadata.google.internal`, `instance-data`) via container host
     sinkholes (`--add-host`) to prevent instance credential exfiltration.
   - Ensure git remotes and authorized LLM endpoints remain accessible.
   - Add unit tests in `tests/test_cli.py` verifying network isolation argument construction.

4. **Bean 0095 (Agent Runner Session State & Credential Isolation):**
   - In `apps/sandbox-executor/src/sandbox_executor/cli.py` (`get_agent_session_mounts`) and `agent_runner.py`:
     - Completely eliminate the host D-Bus session socket mount (`/run/user/*/bus`).
     - Enforce read-only mounts (`:ro`) for all host session directories (Antigravity, Claude, Codex, Pi, Gemini).
     - Where writable configuration is required by the agent CLI, use ephemeral temporary directories or tmpfs mounts.
     - Ensure ephemeral tokens and temporary config directories are wiped on container exit via robust `finally`
       cleanup.
   - Add unit tests in `tests/test_cli.py` and `tests/test_agent_runner.py` verifying no D-Bus socket is mounted,
     session mounts are `:ro`, and ephemeral credentials clean up reliably.

5. **Verification & Quality Gates:**
   - Execute all repository checks: `make lint`, `make tools`,
     `uv run pytest apps/sandbox-executor/tests -m "not integration_test and not stress"`, `uv run ruff check .`,
     `uv run ruff format --check .`, and `npx prettier --check "**/*.md"`.

---

## Step 1: Implement atomic ledger locking context manager and append safety in ledger.py and flow.py (Bean 0093)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core concurrency utility localized to `ledger.py` and callers in `flow.py`; well-defined scope with
  rapid feedback.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Non-blocking advisory file locking (`fcntl.flock` with `LOCK_EX | LOCK_NB`) combined with
  bounded exponential backoff and `os.fsync` eliminates interleaving and corruption during concurrent JSONL ledger
  appends without introducing deadlocks.
- **Learning target:** Measure lock contention backoff intervals and verify non-POSIX fallback behavior under synthetic
  file descriptor contention.
- **Maximum acceptable cost for this learning:** 2.2 normalized cost units.

### Intent & Git Integration

- **Step Intent:** Implement atomic file locking with `fcntl.flock`, retry backoff, `os.fsync`, and non-POSIX fallback
  in `sandbox_executor.ledger`, and update all ledger write operations in `ledger.py` and `flow.py`.
- **Git branch:**
  `I-1792016246-audit-remediation-reliability-security/P-1792016262-antigravity-agent-gemini-3.8-flash-medium/E-1792016270-atomic-ledger-locking/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/ledger.py`, define
   `atomic_ledger_append(filepath: str, timeout: float = 10.0, initial_delay: float = 0.02, max_delay: float = 0.5)` as
   an atomic context manager.
2. Ensure directory existence for `filepath` via
   `os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)`.
3. Detect platform support for POSIX file locking: check for `fcntl` module and `fcntl.flock`. If unavailable (e.g.
   Windows), emit diagnostic debug log and fall back to standard `open(filepath, "a", encoding="utf-8")`.
4. On POSIX systems:
   - Open file in append mode (`"a"`, encoding `"utf-8"`).
   - Enter a retry loop tracking elapsed time against `timeout`.
   - Attempt `fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)`.
   - If `BlockingIOError` or `OSError` (errno `EAGAIN` or `EWOULDBLOCK`) occurs, compute backoff with jitter and
     `time.sleep(delay)`.
   - If timeout is reached, raise `TimeoutError(f"Timed out waiting for exclusive lock on ledger file: {filepath}")`.
5. Inside the context manager:
   - Yield the open file handle to the caller.
   - Upon block completion, execute `f.flush()` followed by `os.fsync(f.fileno())` before releasing the lock.
   - Release lock via `fcntl.flock(f.fileno(), fcntl.LOCK_UN)` in a `finally` block, ensuring the lock is always
     released even on exception.
6. Refactor `append_action_record` and `append_summary_record` in `ledger.py` to write through `atomic_ledger_append`.
7. In `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - Import `atomic_ledger_append` from `sandbox_executor.ledger`.
   - In `run_intent_stage`: replace direct `with open(intents_file, "a") as f:` with
     `atomic_ledger_append(intents_file)`.
   - In `run_plan_stage`: replace direct `with open(plans_file, "a") as f:` with `atomic_ledger_append(plans_file)`.
   - In `run_execute_stage`: replace direct `with open(exec_file, "a") as f:` with `atomic_ledger_append(exec_file)`.
8. Maintain strict type annotations and docstrings conforming to repository standards.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (Downstream stages and tests depend on stable, lock-safe ledger operations)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (Ledger immutability and append-only operations),
  `holon-config/world/ruleset.md` §1 & §2, `docs/safety.md` §4, Bean 0093.
- **Potential failure modes for this step:** Indefinite deadlocks if locks are acquired without timeout; file descriptor
  leak if unlock is omitted in error paths.
- **Guardrails and early‑abort checks:** Use non-blocking `LOCK_NB` inside a bounded loop with hard timeout; wrap lock
  release in a `finally` block.

### Success & Discard Criteria

- **Success:** All ledger writes in `ledger.py` and `flow.py` acquire an exclusive lock, flush and fsync content before
  unlocking, and successfully append valid JSONL records.
- **Discard:** Lock acquisition blocks indefinitely or causes uncaught exceptions during normal serial appends.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.45  |
| impact_pred         | 88.0  |
| cost_pred           | 2.2   |
| learning_value_pred | 4.8   |
| ev_pred             | 82.79 |

### Step Metrics Rationale

`EV = 0.94 * 88.0 + 0.5 * 4.8 - 0.3 * 0.45 - 2.2 = 82.72 + 2.40 - 0.135 - 2.2 = 82.785 -> 82.79`. High impact by
eliminating JSONL corruption risk during concurrent multi-agent executions.

---

## Step 2: Add unit tests verifying concurrent write safety, timeouts, and fallback error handling in test_ledger_query.py (Bean 0093)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard unit test expansion within `apps/sandbox-executor/tests/test_ledger_query.py`.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add hermetic unit tests verifying atomic ledger locking, lock contention handling, timeout
  exceptions, non-POSIX fallbacks, and concurrent write integrity.
- **Git branch:**
  `I-1792016246-audit-remediation-reliability-security/P-1792016262-antigravity-agent-gemini-3.8-flash-medium/E-1792016280-test-atomic-ledger/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_ledger_query.py`, create a new test case class
   `TestAtomicLedgerLocking(unittest.TestCase)`:
   - `test_atomic_ledger_append_success`: Verify standard append creates file, locks, writes valid JSONL, and releases
     lock.
   - `test_atomic_ledger_append_fsync_called`: Mock `os.fsync` and verify it is called with file descriptor before file
     close.
   - `test_atomic_ledger_contention_and_backoff`: Mock `fcntl.flock` to simulate initial `BlockingIOError` followed by
     success; assert retry logic backoff succeeds without error.
   - `test_atomic_ledger_timeout`: Mock `fcntl.flock` to consistently raise `BlockingIOError`; assert
     `atomic_ledger_append` raises `TimeoutError` when elapsed time exceeds timeout parameter.
   - `test_atomic_ledger_non_posix_fallback`: Mock `fcntl` or `fcntl.flock` as missing (`AttributeError` /
     `ImportError`); verify append proceeds cleanly with warning logged.
   - `test_concurrent_multithreaded_appends`: Spawn multiple concurrent threads appending distinct records to a
     temporary JSONL file using `atomic_ledger_append`; verify all records are present, no lines are interleaved, and
     each line parses as valid JSON.
2. Execute `uv run pytest apps/sandbox-executor/tests/test_ledger_query.py` to confirm all tests pass cleanly.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Hermetic tests, standard test runner), Bean 0093.
- **Potential failure modes for this step:** Thread deadlocks in test suite causing test execution timeout.
- **Guardrails and early‑abort checks:** Use short timeouts (`timeout=0.5`) in contention and timeout test cases.

### Success & Discard Criteria

- **Success:** All new test cases pass in under 5 seconds with zero race conditions or assertion failures.
- **Discard:** Flaky concurrency tests or unhandled deadlocks.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.30  |
| impact_pred         | 85.0  |
| cost_pred           | 1.8   |
| learning_value_pred | 4.2   |
| ev_pred             | 81.81 |

### Step Metrics Rationale

`EV = 0.96 * 85.0 + 0.5 * 4.2 - 0.3 * 0.30 - 1.8 = 81.60 + 2.10 - 0.09 - 1.8 = 81.81`. High confidence and immediate
verification of concurrency safety guarantees.

---

## Step 3: Implement pipeline checkpoint schema versioning and migration dispatch table in flow.py (Bean 0094)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Modular schema versioning and migration layer in `flow.py`; localized scope with high architectural
  value.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Explicit schema versioning with an extensible `(from_version, to_version)` dispatch table
  allows seamless forward migration of legacy v0 checkpoints while strictly preventing undefined state ingestion from
  unsupported future versions.
- **Learning target:** Validate transformation edge cases when upgrading partially completed legacy checkpoints with
  missing nested fields.
- **Maximum acceptable cost for this learning:** 2.0 normalized cost units.

### Intent & Git Integration

- **Step Intent:** Add `schema_version = 1` to `FlowCheckpoint` and `FlowContext`, implement migration dispatch table,
  support legacy v0 auto-upgrades, and reject unsupported future versions with `ValueError`.
- **Git branch:**
  `I-1792016246-audit-remediation-reliability-security/P-1792016262-antigravity-agent-gemini-3.8-flash-medium/E-1792016290-checkpoint-schema-versioning/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - Define constants: `CURRENT_CHECKPOINT_SCHEMA_VERSION: int = 1` and `CURRENT_CONTEXT_SCHEMA_VERSION: int = 1`.
   - Update `FlowContext`:
     - Add field `schema_version: int = CURRENT_CONTEXT_SCHEMA_VERSION`.
     - In `FlowContext.to_dict()`: serialize `"schema_version": self.schema_version`.
     - In `FlowContext.from_dict(data)`: default `schema_version` to 0 if absent, ensuring all modern fields
       (`max_review_iterations`, `mitm_web`, `token_reduce`, `skip_review`, `skip_calibrate`, `skip_push`,
       `auto_calibrate`) receive appropriate default values when loaded from legacy dictionaries.
   - Update `FlowCheckpoint`:
     - Add field `schema_version: int = CURRENT_CHECKPOINT_SCHEMA_VERSION`.
     - In `FlowCheckpoint.to_dict()`: serialize `"schema_version": self.schema_version`.
     - In `FlowCheckpoint.from_dict(data)`: check incoming `schema_version`.
       - If `schema_version > CURRENT_CHECKPOINT_SCHEMA_VERSION`, raise
         `ValueError(f"Unsupported checkpoint schema_version {data['schema_version']}. Supported version is {CURRENT_CHECKPOINT_SCHEMA_VERSION}.")`.
       - If `schema_version < CURRENT_CHECKPOINT_SCHEMA_VERSION`, invoke migration dispatch.
2. Implement migration dispatch architecture:
   - Define type alias: `MigrationTransform = Callable[[dict[str, Any]], dict[str, Any]]`.
   - Define registry: `CHECKPOINT_MIGRATION_REGISTRY: dict[tuple[int, int], MigrationTransform] = {}`.
   - Register transform `_migrate_v0_to_v1(data: dict[str, Any]) -> dict[str, Any]`:
     - Ingests legacy v0 checkpoint dictionary (missing `schema_version`).
     - Normalizes `intent_slug`, `current_stage`, and `completed_stages`.
     - Ensures `context` dict exists and contains all required v1 keys with defaults.
     - Sets `schema_version = 1` and returns migrated dictionary.
     - Register as `CHECKPOINT_MIGRATION_REGISTRY[(0, 1)] = _migrate_v0_to_v1`.
   - Implement
     `migrate_checkpoint_data(data: dict[str, Any], target_version: int = CURRENT_CHECKPOINT_SCHEMA_VERSION) -> dict[str, Any]`:
     - Reads current version from `data.get("schema_version", 0)`.
     - Sequentially applies registered transitions `(v, v+1)` until `target_version` is reached.
     - Raises `ValueError` if an intermediate transition path is missing.
3. In `load_checkpoint(filepath: str)`:
   - Load raw JSON dictionary.
   - Pass dictionary through `migrate_checkpoint_data(data)`.
   - Construct and return `FlowCheckpoint.from_dict(migrated_data)`.
4. Ensure all public functions, classes, and methods have strict type annotations and docstrings.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 & §2, `docs/safety.md` §4, Bean 0094.
- **Potential failure modes for this step:** Breaking existing checkpoint loading during active pipeline runs; migration
  corrupting stage execution state.
- **Guardrails and early‑abort checks:** Defensive field extraction with fallback defaults in `_migrate_v0_to_v1`.

### Success & Discard Criteria

- **Success:** Legacy v0 checkpoint dictionaries load into fully populated v1 `FlowCheckpoint` objects, new checkpoints
  save with `schema_version: 1`, and future schema versions raise `ValueError`.
- **Discard:** Checkpoint serialization fails to round-trip or corrupts `FlowContext`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.40  |
| impact_pred         | 86.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 4.5   |
| ev_pred             | 81.83 |

### Step Metrics Rationale

`EV = 0.95 * 86.0 + 0.5 * 4.5 - 0.3 * 0.40 - 2.0 = 81.70 + 2.25 - 0.12 - 2.0 = 81.83`. Establishes robust forward and
backward compatibility for pipeline checkpoints across software releases.

---

## Step 4: Add unit tests for legacy loading, migration transforms, and future version rejection in test_flow.py (Bean 0094)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Targeted unit testing in `apps/sandbox-executor/tests/test_flow.py`; fast feedback loop.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add thorough unit test coverage in `test_flow.py` verifying legacy v0 checkpoint upgrades, schema
  migration dispatch, and unsupported future schema rejection.
- **Git branch:**
  `I-1792016246-audit-remediation-reliability-security/P-1792016262-antigravity-agent-gemini-3.8-flash-medium/E-1792016300-test-checkpoint-migration/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_flow.py`:
   - `test_checkpoint_schema_version_serialized`: Verify `save_checkpoint` writes `schema_version: 1` in the output
     JSON.
   - `test_load_legacy_v0_checkpoint_auto_migration`:
     - Write a synthetic legacy v0 checkpoint (lacking `schema_version` and lacking several v1 context fields like
       `mitm_web`, `token_reduce`) to temporary file.
     - Call `load_checkpoint`.
     - Assert returned `FlowCheckpoint.schema_version == 1`.
     - Assert `FlowCheckpoint.context.schema_version == 1`.
     - Assert default values are cleanly populated (`max_review_iterations == 25`, `token_reduce == False`).
   - `test_migration_dispatch_table_execution`:
     - Test calling `migrate_checkpoint_data` directly on v0 data and verify it returns upgraded v1 schema.
   - `test_future_schema_version_rejection`:
     - Write a checkpoint with `"schema_version": 999`.
     - Call `load_checkpoint` inside `pytest.raises(ValueError)`.
     - Assert error message indicates unsupported future version.
2. Run `uv run pytest apps/sandbox-executor/tests/test_flow.py` to confirm all existing and new tests pass.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3, Bean 0094.
- **Potential failure modes for this step:** Test writing outside fixture temp directory.
- **Guardrails and early‑abort checks:** Use `tmp_path` fixture for all temporary checkpoint files.

### Success & Discard Criteria

- **Success:** All new checkpoint versioning and migration unit tests pass with 100% assertions satisfied.
- **Discard:** Incomplete assertion coverage or failure on legacy checkpoint loading.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.25  |
| impact_pred         | 82.0  |
| cost_pred           | 1.6   |
| learning_value_pred | 3.8   |
| ev_pred             | 79.77 |

### Step Metrics Rationale

`EV = 0.97 * 82.0 + 0.5 * 3.8 - 0.3 * 0.25 - 1.6 = 79.54 + 1.90 - 0.075 - 1.6 = 79.765 -> 79.77`. Solidifies state
migration reliability and ensures backward compatibility guarantees hold.

---

## Step 5: Implement container network isolation, egress filtering, and cloud metadata blocking in cli.py and flow.py (Bean 0092)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core security enhancement in `cli.py` container spawning logic; critical impact, scoped changes.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Dropping `NET_RAW` capability and sinkholing link-local cloud metadata addresses
  (`169.254.169.254`) prevents unauthorized network probing and cloud credential exfiltration while preserving normal
  git remote syncing and authorized LLM API calls.
- **Learning target:** Determine the optimal combination of Docker host flags and network capability drops that enforce
  egress boundaries without requiring Docker-in-Docker or root iptables manipulation.
- **Maximum acceptable cost for this learning:** 2.5 normalized cost units.

### Intent & Git Integration

- **Step Intent:** Implement network isolation arguments in `cli.py` and `flow.py`, dropping `NET_RAW` and sinkholing
  metadata IP addresses (`169.254.169.254`, `metadata.google.internal`) in container sandboxes.
- **Git branch:**
  `I-1792016246-audit-remediation-reliability-security/P-1792016262-antigravity-agent-gemini-3.8-flash-medium/E-1792016310-network-isolation/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/cli.py`:
   - Define network security constants:
     - `BLOCKED_METADATA_HOSTS = ["169.254.169.254", "metadata.google.internal", "instance-data"]`
     - `SINKHOLE_IP = "127.0.0.1"`
   - Implement helper function
     `build_network_isolation_args(token_reduce: bool = False, network_mode: str | None = None) -> list[str]`:
     - Construct baseline security arguments:
       - Drop raw packet socket capability: `["--cap-drop=NET_RAW"]` (prevents ARP spoofing, packet sniffing, and raw
         port scanning inside sandbox).
     - Block cloud metadata access:
       - For each host in `BLOCKED_METADATA_HOSTS`, append `["--add-host", f"{host}:{SINKHOLE_IP}"]`. This overrides
         in-container `/etc/hosts`, routing attempts to access instance identity tokens or cloud IAM metadata back to
         container loopback where no service is listening.
     - When `token_reduce` is False (direct/fallback egress):
       - If `network_mode` is explicitly provided, apply `["--network", network_mode]`.
       - Ensure proxy-less direct egress containers still inherit the capability drop and metadata sinkhole flags.
     - Return the compiled argument list.
   - In `run_docker_container()`:
     - Call `build_network_isolation_args(token_reduce=token_reduce)` and extend `docker_cmd` with the resulting
       isolation arguments before appending image name.
2. In `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - In any container execution helpers (such as `run_flow_pipeline` container invocations), ensure
     `build_network_isolation_args` flags are consistently passed to container runs.
3. Validate that standard outbound HTTPS egress to git remotes (e.g. `github.com`) and authorized LLM provider APIs
   (`generativelanguage.googleapis.com`, `api.anthropic.com`, `api.openai.com`) is unimpaired by loopback sinkholing.
4. Add comprehensive docstrings and strict type annotations.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (Remediates High-severity security finding SEC-01)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §2 (Sandbox containment tiers and escape prevention),
  `docs/safety.md` §2, Bean 0092.
- **Potential failure modes for this step:** Sinkholing legitimate hostnames or breaking container DNS resolution.
- **Guardrails and early‑abort checks:** Sinkhole only specific cloud metadata endpoints (`169.254.169.254` and known
  metadata hostnames); do not block public DNS or wildcard domains.

### Success & Discard Criteria

- **Success:** Docker run commands for all agent roles include `--cap-drop=NET_RAW` and `--add-host` mappings sinkholing
  `169.254.169.254`, while normal DNS and git/API traffic remain functional.
- **Discard:** In-container git fetch or LLM API calls fail due to overly aggressive network restrictions.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.55  |
| impact_pred         | 92.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 5.5   |
| ev_pred             | 84.73 |

### Step Metrics Rationale

`EV = 0.92 * 92.0 + 0.5 * 5.5 - 0.3 * 0.55 - 2.5 = 84.64 + 2.75 - 0.165 - 2.5 = 84.725 -> 84.73`. High impact and
learning value by hardening container isolation against metadata exfiltration.

---

## Step 6: Eliminate host D-Bus socket mount, enforce read-only session mounts, and ensure ephemeral credential cleanup (Bean 0095)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped credential and mount security hardening in `cli.py` and `agent_runner.py`.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Remove host D-Bus socket mounting from `get_agent_session_mounts()`, enforce read-only (`:ro`)
  session mounts, and ensure ephemeral credential scrubbing upon container exit.
- **Git branch:**
  `I-1792016246-audit-remediation-reliability-security/P-1792016262-antigravity-agent-gemini-3.8-flash-medium/E-1792016320-credential-isolation/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/cli.py`:
   - Inspect `get_agent_session_mounts(agent_id: str) -> list[str]`:
     - Delete the Linux host D-Bus session socket mount block (`/run/user/{uid}/bus` mounted to `/run/user/1000/bus`)
       completely. No D-Bus socket should ever be mounted into the container sandbox.
     - For Antigravity session directory:
       - Update macOS and Linux session mounts so that `dedicated_session` (`~/.holon/sessions/antigravity`) and host
         `~/.gemini/antigravity-cli` are mounted read-only (`:ro`) instead of read-write (`:rw`):
         `-v {path}:/home/holon/.gemini/antigravity-cli:ro`
       - Ensure `--tmpfs /home/holon/.gemini/config:uid=1000,gid=1000` is retained for runtime temporary state that the
         CLI writes during execution.
     - For other agent session mappings (Claude, Codex, Gemini):
       - Ensure all mounts explicitly specify `:ro` suffix to prevent container processes from modifying host session
         files.
2. In `apps/sandbox-executor/src/sandbox_executor/agent_runner.py`:
   - In `StandardAgentRunner.resolve_credentials()`:
     - Ensure any unpacked ephemeral configuration files or secret tokens are tracked for cleanup.
   - Implement `cleanup_ephemeral_credentials()`:
     - Securely wipe temporary secret bundles or session artifacts generated during the run upon runner termination.
   - In `cli.py` `run_docker_container()`:
     - In the `finally` block, verify all ephemeral mounts and generated agent directories (such as `local_agent_dir`)
       are removed with `shutil.rmtree(..., ignore_errors=True)`.
3. Verify that removing the D-Bus socket does not impede agent CLI execution in headless container environments.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (Remediates High-severity security finding SEC-02)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §2, `docs/safety.md` §2 & §4, Bean 0095.
- **Potential failure modes for this step:** Agent CLI failing to launch if it expects write access to read-only mounted
  session directory.
- **Guardrails and early‑abort checks:** Provide writable tmpfs mounts for CLI configuration directories (e.g.
  `/home/holon/.gemini/config`) so local cache writes succeed without mutating host files.

### Success & Discard Criteria

- **Success:** `get_agent_session_mounts()` returns zero D-Bus mounts and all directory mounts specify `:ro`, while
  agents run without write-permission crashes.
- **Discard:** Agent process crashes due to missing D-Bus or unwritable session cache directories.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.40  |
| impact_pred         | 90.0  |
| cost_pred           | 2.1   |
| learning_value_pred | 4.6   |
| ev_pred             | 84.68 |

### Step Metrics Rationale

`EV = 0.94 * 90.0 + 0.5 * 4.6 - 0.3 * 0.40 - 2.1 = 84.60 + 2.30 - 0.12 - 2.1 = 84.68`. Closes a major host privilege
boundary exposure by decoupling the container from host session IPC and preventing host state mutation.

---

## Step 7: Add unit tests for network isolation, D-Bus elimination, and read-only session mounts in test_cli.py (Beans 0092 & 0095)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard unit test expansion in `apps/sandbox-executor/tests/test_cli.py`.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add and update unit tests in `test_cli.py` asserting network isolation flags, metadata sinkholing,
  absence of D-Bus mounts, and read-only session mount flags.
- **Git branch:**
  `I-1792016246-audit-remediation-reliability-security/P-1792016262-antigravity-agent-gemini-3.8-flash-medium/E-1792016330-test-network-and-creds/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_cli.py`:
   - Network Isolation Tests (Bean 0092):
     - `test_build_network_isolation_args_contains_cap_drop`: Verify `build_network_isolation_args()` contains
       `"--cap-drop=NET_RAW"`.
     - `test_build_network_isolation_args_sinkholes_metadata`: Verify `build_network_isolation_args()` maps
       `169.254.169.254:127.0.0.1` and `metadata.google.internal:127.0.0.1` via `--add-host`.
     - `test_run_docker_container_includes_network_isolation`: Verify `run_docker_container` includes the network
       isolation arguments in the executed Docker command line.
   - Session State & D-Bus Elimination Tests (Bean 0095):
     - Update `test_antigravity_macos_existing_session_mounts`: Assert mount contains `...:ro` instead of `...:rw`.
     - Update `test_antigravity_linux_mounts_gemini_cli`: Assert mount contains `...:ro` instead of `...:rw`.
     - Replace `test_antigravity_linux_dbus_mount`: Assert that under Linux, even when `/run/user/1001/bus` exists on
       the host, `get_agent_session_mounts` does NOT include any D-Bus mount.
     - `test_all_agent_session_mounts_are_read_only`: Iterate across all supported agents and verify any `-v` directory
       mounts contain `:ro`.
2. Run `uv run pytest apps/sandbox-executor/tests/test_cli.py` and ensure 100% test pass rate.

### Dependencies & Criticality

- **Depends on:** Step 5, Step 6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3, Beans 0092 & 0095.
- **Potential failure modes for this step:** Outdated assertions expecting `:rw` or D-Bus socket mounts causing test
  failures.
- **Guardrails and early‑abort checks:** Update test assertions in tandem with source code modifications.

### Success & Discard Criteria

- **Success:** All new and updated tests in `test_cli.py` pass cleanly without mock leaks.
- **Discard:** Test regressions or failing assertions in `test_cli.py`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.30  |
| impact_pred         | 85.0  |
| cost_pred           | 1.8   |
| learning_value_pred | 4.0   |
| ev_pred             | 81.71 |

### Step Metrics Rationale

`EV = 0.96 * 85.0 + 0.5 * 4.0 - 0.3 * 0.30 - 1.8 = 81.60 + 2.00 - 0.09 - 1.8 = 81.71`. Provides solid regression
prevention for network and credential isolation.

---

## Step 8: End-to-end repository verification, formatting, and quality gates (Beans 0093, 0094, 0092, 0095)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard end-to-end validation step ensuring zero regressions across all repository quality gates.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Execute full verification suite (`pytest`, `ruff`, `prettier`, `make lint`, `make tools`) and ensure
  all quality gates pass cleanly.
- **Git branch:**
  `I-1792016246-audit-remediation-reliability-security/P-1792016262-antigravity-agent-gemini-3.8-flash-medium/E-1792016340-verification-gates/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run unit test suite: `uv run pytest apps/sandbox-executor/tests -m "not integration_test and not stress"` and verify
   all tests pass with zero failures.
2. Run linter checks: `uv run ruff check .` and format checks: `uv run ruff format --check .`.
3. Run markdown formatting validation: `npx prettier --check "**/*.md"` across all documentation, plans, and source
   markdown files.
4. Run Makefile targets: `make lint` and `make tools`.
5. Check `git status` to verify working tree is clean and no untracked artifacts remain.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4, Step 5, Step 6, Step 7
- **Is Bottleneck:** YES (Final gate for intent completion)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1, §2, §3, `docs/safety.md` §1.
- **Potential failure modes for this step:** Prettier formatting failure on modified documentation or test files; lint
  regressions.
- **Guardrails and early‑abort checks:** Run `npx prettier --write` on any touched markdown files before final
  verification check.

### Success & Discard Criteria

- **Success:** All test, lint, format, and Makefile verification commands return exit code 0.
- **Discard:** Any test failure or unformatted markdown file fails verification gate.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.35  |
| impact_pred         | 90.0  |
| cost_pred           | 1.6   |
| learning_value_pred | 3.5   |
| ev_pred             | 85.55 |

### Step Metrics Rationale

`EV = 0.95 * 90.0 + 0.5 * 3.5 - 0.3 * 0.35 - 1.6 = 85.50 + 1.75 - 0.105 - 1.6 = 85.545 -> 85.55`. Validates complete
alignment with repository invariants and intent goals.
