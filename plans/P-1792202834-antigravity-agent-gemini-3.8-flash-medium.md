# Plan for I-1792202073-action-kb-wisdom-consult-and-recording

- **Plan ID:** P-1792202834-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-17T02:07:14.461Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with empirical validation of dual-tier SQLite FTS5 index performance, byte-offset
  incremental ledger ingestion, Wilson score interval calibration, and shadow-mode deterministic failure veto ladder.
- **Safety priority level:** elevated
- **Priority Justification:** Triggered by `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory for
  execution), `holon-config/world/constraints.md` §2 (Sandbox containment tiers and escape detection: credential
  redaction before persistence), `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modifications to
  historical ledger entries, atomic append-only operations, offset-based streaming), `holon-config/world/ruleset.md` §1
  & §2 (Python runtime `==3.13.*`, workspace isolation under `apps/sandbox-executor`, strict typing with `typing`, PEP 8
  compliance, docstrings), and `holon-config/world/ruleset.md` §3 (Testing constraints: pytest 9.1.1, test placement in
  `apps/sandbox-executor/tests/`). Elevated safety guarantees credentials and secrets are scrubbed before ledger append
  and failure vetoes default to non-blocking shadow mode to prevent inadvertent pipeline deadlocks.

## Exploration

- **Proportion of steps that are exploratory:** 0.29
- **Justification:** Steps 2 and 4 incorporate balanced exploratory probing to benchmark conjunctive, disjunctive, and
  negated multi-label SQLite FTS5 query latency against strict sub-15ms p95 SLAs, and calibrate Wilson score interval
  thresholds and shadow veto decays across heterogeneous execution outcomes without destabilizing the pipeline.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.55  |
| impact_pred         | 96.0  |
| cost_pred           | 16.5  |
| learning_value_pred | 6.5   |
| ev_pred             | 74.91 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.92. Governed by the bottleneck step (Step 4: 0.92), where the asymmetric promotion engine must
  mathematically balance deterministic failure veto promotion (two identical context fingerprints with zero successes)
  against statistical Beta(alpha, beta) posteriors and Wilson 95% intervals for success advisories without emitting
  imperative vetoes for successes.
- **entropy_pred**: 0.55. Evaluated as the maximum single-step risk profile (Step 4: 0.55), where mathematical threshold
  tuning and veto decay rules introduce behavioral shifts in agent decision loops. The sum of predicted step entropies
  across all seven steps is 3.00 (`0.45 + 0.50 + 0.35 + 0.55 + 0.40 + 0.45 + 0.30 = 3.00`), well within the allocated
  entropy budget of 15.0.
- **impact_pred**: 96.0. Directly delivers Bean 0052: establishes continuous learning, sub-15ms multi-label wisdom
  consult, and outcome recording across all flow stages (`intent`, `plan`, `execute`, `review`, `calibrate`); enables
  late verdict reconciliation, shadow-mode failure vetoes, and evidence-weighted Beta-Wilson promotions; and surfaces
  real-time telemetry via `holon stats`.
- **cost_pred**: 16.5. Calculated as the direct sum of individual step costs
  (`2.6 + 2.4 + 2.2 + 2.8 + 2.3 + 2.5 + 1.7 = 16.5`).
- **learning_value_pred**: 6.5. High epistemic gain delivered from benchmarking SQLite FTS5 inverted indexing under
  byte-offset sync, formulating credential redaction filters for ledger writes, and establishing empirical Wilson
  confidence intervals for autonomous agent self-reflection.
- **ev_pred**: 74.91. Computed strictly via the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system constants `lambda = 0.3`
  and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.92 * 96.0 + 0.5 * 6.5 - 0.3 * 0.55 - 16.5 = 88.32 + 3.25 - 0.165 - 16.5 = 74.905 -> 74.91`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python target `==3.13.*`, package
    management via `uv`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, static typing with `typing`
    module, docstring maintenance).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test files placed in
    `apps/sandbox-executor/tests/`, all test changes explicitly declared in the planning step).
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, rebase
    discipline, commit boundaries).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: filesystem containment, no unwhitelisted
    subprocesses, process sandbox `< 10` entropy).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modifications to historical ledger entries,
    append-only operations, streaming offset tracking).
  - `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory for execution).
  - Bean 0052 (Consult and record ledger/KB/wisdom per agent action).
- **Potential violations or edge cases:**
  - Replay scans of large ledger JSONL files degrading consult latency beyond the 15ms p95 target on cold or warm
    lookups.
  - Veto rules prematurely blocking agent execution stages during early test iterations or exploratory phases.
  - Secrets, API keys, or bearer tokens inadvertently leaking into append-only ledger files (`actions.jsonl`) without
    reversibility.
  - Stale `unknown` action records permanently polluting Beta posteriors or biasing Wilson confidence intervals.
  - Unbounded payload injection exceeding agent context windows during planning or execution loops.
- **Mitigations built into the plan:**
  - Implement durable SQLite FTS5 index with file modification time (`mtime`) and byte-offset checkpointing so queries
    never scan JSONL files from beginning.
  - Default veto enforcement to shadow mode with diagnostic warnings, requiring explicit stage configuration flags for
    blocking enforcement.
  - Integrate a multi-pattern credential redactor (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, Bearer
    tokens, GitHub PATs) into the write path before disk persistence.
  - Implement automated TTL expiration (14 days) transitioning `unknown` verdicts to `unresolved` so they are excluded
    from posterior and veto calculations.
  - Cap retrieval payloads strictly to top-k `<= 6` records and `<= 1.2k` tokens, with session-level ID deduplication to
    prevent repetitive prompting.
- **Residual risk accepted (and why):**
  - Minor initial index construction overhead on the very first cold startup; accepted because subsequent queries are
    accelerated to sub-15ms p95 via persistent SQLite FTS5 storage.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 3.00
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

This plan implements Bean 0052 across `holon-agentic-coder`:

1. **Shared Ledger & Incremental Index (Goal 1):** Create `apps/sandbox-executor/src/sandbox_executor/knowledge/` with
   an incremental SQLite indexing engine (`index.py`). Store indices under `holon-knowledge/.index/action_index.db`
   (project tier) and `~/.holon/index/action_index.db` (global tier). Use FTS5 for full-text search, normalized inverted
   tables for labels and action signatures, and byte-offset tracking to ingest new ledger records without full file
   scans.
2. **Multi-Label Retrieval (`holon consult`) (Goal 2):** Implement `consult.py` and CLI command `holon consult`
   supporting conjunctive (`--all`), disjunctive (`--any`), and negated (`--not`) multi-label queries. Guarantee
   sub-15ms p95 latency on warm index and sub-50ms cold. Enforce payload caps (top-k `<= 6`, `<= 1.2k` tokens) and
   session-level deduplication.
3. **Outcome Logging State Machine (`holon record` & `holon reconcile`) (Goal 3):** Define the action state machine in
   `records.py` with states `attempted`, `success`, `failure`, `unknown`, `inconclusive`, `abandoned`, and `superseded`.
   Append discrete records to `holon-knowledge/ledger/actions.jsonl` with atomic locking and secret redaction. Provide
   `holon reconcile` for late verdict attachment and 14-day TTL expiration for `unknown` records.
4. **Asymmetric Promotion Engine (Veto vs Evidence) (Goal 4):** Implement `promotion.py` separating failure vetoes from
   success evidence. Deterministic veto ladder for failures (`observed` -> `repeated` -> `contested` -> `proven-fail`),
   requiring 2 reproductions with identical context fingerprint and zero successes, with decay and automatic lifting on
   contrary observation. Statistical Beta(alpha, beta) posteriors with Wilson 95% confidence intervals for successes
   (`observed` -> `repeated` -> `likely-success` -> `proven-success`), strictly emitting non-imperative advisory hints.
   Default vetoes to shadow mode.
5. **Wisdom & KB Persistence (Goal 5):** Implement `wisdom.py` persisting descriptive facts to `holon-knowledge/kb/` and
   prescriptive rules to `holon-knowledge/wisdom/` with posterior stats, trial counts, context fingerprints, and source
   ledger IDs. Implement CLI commands `holon promote --pending` and `holon veto list|lift`.
6. **Lifecycle Stage Integration & Self-Metrics (Goal 6):** Instrument `flow.py` across `intent`, `plan`, `execute`,
   `review`, and `calibrate` stages to consult wisdom before acting and echo consulted IDs in artifacts. Implement
   `holon stats` reporting hit rate, consult latency, and injected token counts.
7. **Hermetic Verification & Quality Gates (Goal 7):** Provide exhaustive hermetic unit and benchmark tests in
   `apps/sandbox-executor/tests/test_knowledge.py`. Verify sub-15ms latency, Wilson intervals, veto transitions, atomic
   ledger writes, `make lint`, `make tools`, `pytest`, `ruff`, and `prettier`.

---

## Step 1: Implement durable SQLite index engine with FTS5, inverted label index, and byte-offset incremental synchronization (Bean 0052 Goal 1)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Modular foundational package under `sandbox_executor.knowledge` with clear interfaces; low blast
  radius, highly testable in isolation.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Build the persistent dual-tier SQLite index engine with FTS5 full-text search, normalized inverted
  label tables, action signature mapping, and byte-offset incremental synchronization for ledger append operations.
- **Git branch:**
  `I-1792202073-action-kb-wisdom-consult-and-recording/P-1792202834-antigravity-agent-gemini-3.8-flash-medium/E-1792202850-index-engine/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create package directory `apps/sandbox-executor/src/sandbox_executor/knowledge/` with `__init__.py`.
2. In `apps/sandbox-executor/src/sandbox_executor/knowledge/index.py`, design `ActionIndexDatabase`:
   - Support dual-tier database paths: project tier at `holon-knowledge/.index/action_index.db` and global tier at
     `~/.holon/index/action_index.db`. Automatically ensure parent directories exist.
   - Initialize SQLite schema with WAL journal mode (`PRAGMA journal_mode=WAL;`), synchronous normal, and foreign keys
     enabled for high read concurrency and sub-millisecond transactions.
   - Create table `ledger_sync_state` storing `ledger_path`, `last_mtime`, `last_byte_offset`, and `last_sync_ts`.
   - Create table `action_records` storing `record_id`, `action_id`, `execution_id`, `action`, `category`, `target`,
     `outcome`, `verdict`, `context_fingerprint`, `created_at`, `payload_json`, and `raw_byte_offset`.
   - Create FTS5 virtual table `action_records_fts` using `content="action_records"`, indexing `action`, `target`,
     `context_fingerprint`, and error context for fast text queries.
   - Create table `record_labels` storing `record_id`, `label`, with a compound primary key `(record_id, label)` and an
     index on `label` for sub-millisecond inverted label lookups.
   - Create table `action_signatures` storing `signature_hash`, `context_fingerprint`, `action_origin`, `success_count`,
     `failure_count`, and `last_seen_ts`.
3. Implement `sync_ledger_incremental(db_path, ledger_path)`:
   - Read `last_byte_offset` and `last_mtime` from `ledger_sync_state`.
   - If ledger file does not exist, return cleanly.
   - If file size is less than `last_byte_offset` (e.g. truncated or rotated), reset offset to 0.
   - Open ledger file, seek directly to `last_byte_offset`, and stream new JSONL lines until end-of-file.
   - For each valid line, insert record into `action_records`, tokenize labels into `record_labels`, update FTS5 virtual
     table, and update `action_signatures`.
   - Persist updated `last_byte_offset` and `mtime` inside a single atomic SQLite transaction.
4. Ensure backward compatibility: parse existing summary rows and action records from `executions.jsonl`,
   `intents.jsonl`, and `plans.jsonl` without errors.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (Downstream consult and recording features depend on the storage and indexing engine)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (Ledger Immutability), `holon-config/world/ruleset.md` §1 &
  §2.
- **Potential failure modes for this step:** SQLite database lock contention during concurrent agent runs; index
  corruption on process interrupt.
- **Guardrails and early‑abort checks:** Enable WAL mode with a busy timeout of 5000ms; use atomic SQLite transactions
  with try-finally blocks to roll back partial syncs.

### Success & Discard Criteria

- **Success:** Incremental sync ingests 10,000 JSONL rows in under 200ms, tracks byte offsets accurately, and enables
  instant label lookups without rescanning the file.
- **Discard:** Incremental sync requires full-file scans or causes lock contention exceeding 1000ms.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.45  |
| impact_pred         | 88.0  |
| cost_pred           | 2.6   |
| learning_value_pred | 4.8   |
| ev_pred             | 82.39 |

### Step Metrics Rationale

`EV = 0.94 * 88.0 + 0.5 * 4.8 - 0.3 * 0.45 - 2.6 = 82.72 + 2.40 - 0.135 - 2.6 = 82.385 -> 82.39`. High probability of
success due to standard SQLite WAL and FTS5 capabilities; delivers strong foundational impact for fast retrieval.

---

## Step 2: Implement multi-label retrieval engine and CLI command holon consult with token budget caps and session deduplication (Bean 0052 Goal 2)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped retrieval component under `sandbox_executor.knowledge` with direct CLI interface; low risk and
  fast benchmark validation.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Conjunctive, disjunctive, and negated label set intersections using SQLite index set
  operations achieve sub-15ms p95 query latency on warm databases while capping payload token overhead to 1.2k tokens.
- **Learning target:** Measure p50 and p95 retrieval latency across varying ledger sizes (1k to 50k rows) and determine
  optimal SQL query plans for complex multi-label queries.
- **Maximum acceptable cost for this learning:** 2.4 normalized cost units.

### Intent & Git Integration

- **Step Intent:** Build multi-label retrieval engine and `holon consult` CLI command answering `--all`, `--any`, and
  `--not` queries within sub-15ms p95 on warm index, with strict payload caps and session deduplication.
- **Git branch:**
  `I-1792202073-action-kb-wisdom-consult-and-recording/P-1792202834-antigravity-agent-gemini-3.8-flash-medium/E-1792202870-consult-cli/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/knowledge/consult.py`, implement `ConsultEngine`:
   - Initialize with project-tier and global-tier database connections. Synchronize incremental ledger state before
     querying if file `mtime` has changed.
   - Implement
     `consult_records(all_labels=None, any_labels=None, not_labels=None, query_text=None, top_k=6, max_tokens=1200, session_seen_ids=None)`:
     - Build optimized SQL query combining label set operations:
       - For `--all` (conjunctive): select records having all specified labels using
         `GROUP BY record_id HAVING COUNT(DISTINCT label) = len(all_labels)`.
       - For `--any` (disjunctive): filter by `label IN (any_labels)`.
       - For `--not` (negated): exclude records using
         `NOT EXISTS (SELECT 1 FROM record_labels WHERE record_labels.record_id = action_records.record_id AND label IN (not_labels))`.
       - For `query_text`: join with `action_records_fts` using `action_records_fts MATCH :query_text ORDER BY rank`.
     - Filter out any record IDs present in `session_seen_ids` to guarantee session-level deduplication and prevent
       prompt bloat.
     - Enforce `top_k <= 6`.
     - Calculate approximate token counts for each matched record payload; truncate or drop records that would exceed
       the 1,200 token budget cap.
     - Return structured `ConsultResult` containing matched records, total count, latency milliseconds, and token count.
2. In `apps/sandbox-executor/src/sandbox_executor/cli.py`, add subcommand `consult`:
   - Add arguments `--all` (nargs="+"), `--any` (nargs="+"), `--not` (nargs="+"), `--query` (string), `--top-k` (default
     6), `--max-tokens` (default 1200), `--json` (flag), and `--format` (table or json).
   - Wire arguments to `ConsultEngine.consult_records()`.
   - Output formatted results with ANSI styling for interactive terminal use or clean JSON for programmatic consumption.
3. Record latency metrics for every consult call into local session telemetry.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2, Bean 0052 Goal 2.
- **Potential failure modes for this step:** SQL injection via unescaped label or search terms; query latency spikes on
  large unindexed tables.
- **Guardrails and early‑abort checks:** Use parameterized SQL queries exclusively; assert index usage via
  `EXPLAIN QUERY PLAN` in unit tests.

### Success & Discard Criteria

- **Success:** `holon consult` executes conjunctive, disjunctive, and negated queries with warm p95 latency under 15ms
  and enforces the 1.2k token payload ceiling.
- **Discard:** Warm query latency exceeds 15ms or token payloads exceed 1.2k tokens.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.50  |
| impact_pred         | 86.0  |
| cost_pred           | 2.4   |
| learning_value_pred | 5.2   |
| ev_pred             | 80.03 |

### Step Metrics Rationale

`EV = 0.93 * 86.0 + 0.5 * 5.2 - 0.3 * 0.50 - 2.4 = 79.98 + 2.60 - 0.15 - 2.4 = 80.03`. Validates latency constraints
under real workloads; high learning value for query optimization and token bounding.

---

## Step 3: Implement outcome logging state machine, credential redactor, and late-verdict reconciliation CLI (Bean 0052 Goal 3)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Extends existing ledger infrastructure in `sandbox_executor.ledger` and adds secure state transitions;
  self-contained and verifiable.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement the action outcome logging state machine with discrete verdicts, multi-pattern credential
  redactor, atomic appending to `holon-knowledge/ledger/actions.jsonl`, late-verdict attachment via `holon reconcile`,
  and 14-day TTL expiration for `unknown` records.
- **Git branch:**
  `I-1792202073-action-kb-wisdom-consult-and-recording/P-1792202834-antigravity-agent-gemini-3.8-flash-medium/E-1792202890-outcome-logging/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/knowledge/records.py`:
   - Define discrete verdict enums: `attempted`, `success`, `failure`, `unknown`, `inconclusive`, `abandoned`,
     `superseded`, and `unresolved`.
   - Define state transition validation rules:
     - `attempted` can transition to `success`, `failure`, `unknown`, `inconclusive`, or `abandoned`.
     - `unknown` can transition to `success`, `failure`, or `superseded` via late reconciliation, or expire to
       `unresolved` after TTL.
     - `success` and `failure` are terminal states (unless explicitly superseded by an audit event).
   - Implement `CredentialRedactor`:
     - Compile regex patterns covering API keys and tokens: OpenAI (`sk-[a-zA-Z0-9_-]{20,}`), Anthropic
       (`sk-ant-[a-zA-Z0-9_-]{20,}`), Google Gemini/AI Studio (`AIza[0-9A-Za-z-_]{35}`), GitHub tokens
       (`ghp_[a-zA-Z0-9]{36}`, `github_pat_[a-zA-Z0-9_]{82}`), generic bearer tokens, and private keys.
     - Replace matched secrets with `[REDACTED_SECRET]` before converting records to JSON or appending to disk.
   - Implement `append_action_event(record, ledger_path="holon-knowledge/ledger/actions.jsonl")`:
     - Apply credential redaction to all string fields and error contexts.
     - Use existing `atomic_ledger_append` with file locking (`fcntl.flock` or lockfile) to prevent write interleaving.
     - Immediately invoke incremental index sync to keep the SQLite index hot.
2. Implement `reconcile_records(ledger_path, max_age_days=14)`:
   - Identify records with verdict `unknown`.
   - If record age exceeds `max_age_days` (14 days), append a transition event updating verdict to `unresolved`.
   - Match pending `unknown` actions against subsequent execution outcomes or git commit SHAs, attaching late verdicts
     (`success` or `failure`).
3. In `apps/sandbox-executor/src/sandbox_executor/cli.py`, add CLI subcommands:
   - `holon record`: accepts `--action`, `--category`, `--target`, `--verdict`, `--labels`, `--context`, records event
     to `actions.jsonl`, and prints action ID.
   - `holon reconcile`: accepts `--action-id`, `--verdict`, `--reason`, `--expire-ttl` (default 14 days), resolves
     pending outcomes, and reports reconciliation statistics.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §2 (Sandbox containment and secret redaction),
  `holon-config/world/constraints.md` §3 (Ledger Immutability: reconciliation appends new transition events rather than
  editing past lines).
- **Potential failure modes for this step:** Secret leakage into immutable ledger files; race conditions during
  concurrent record appending.
- **Guardrails and early‑abort checks:** Run redactor unit tests against a comprehensive test suite of fake credential
  strings; assert file locking wraps all disk write operations.

### Success & Discard Criteria

- **Success:** Actions are appended atomically to `actions.jsonl` with all secrets redacted, late reconciliation
  attaches terminal verdicts via appended events, and records older than 14 days cleanly transition to `unresolved`.
- **Discard:** Raw API keys escape redaction or append operations corrupt the JSONL structure.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.35  |
| impact_pred         | 85.0  |
| cost_pred           | 2.2   |
| learning_value_pred | 4.2   |
| ev_pred             | 80.55 |

### Step Metrics Rationale

`EV = 0.95 * 85.0 + 0.5 * 4.2 - 0.3 * 0.35 - 2.2 = 80.75 + 2.10 - 0.105 - 2.2 = 80.545 -> 80.55`. High probability and
strong safety compliance; prevents credential leakage and ensures forensic integrity.

---

## Step 4: Implement asymmetric promotion engine with deterministic failure veto ladder and statistical Beta posterior / Wilson score success advisories (Bean 0052 Goal 4)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core statistical and deterministic decision logic isolated in `sandbox_executor.knowledge.promotion`;
  high algorithmic value, verified with hermetic mathematical tests.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Asymmetric promotion logic (deterministic failure veto ladder based on identical context
  fingerprints vs statistical Beta-Wilson posteriors for successes) eliminates repetitive agent regressions while
  avoiding false-positive execution blocks.
- **Learning target:** Calibrate Wilson 95% lower bound thresholds and decay schedules across simulated failure and
  success distributions to ensure smooth promotion transitions.
- **Maximum acceptable cost for this learning:** 2.8 normalized cost units.

### Intent & Git Integration

- **Step Intent:** Implement the asymmetric promotion engine in `promotion.py` featuring a deterministic failure veto
  ladder with automatic lifting and decay, alongside statistical Beta(alpha, beta) posteriors with Wilson 95% confidence
  intervals for successes, strictly running vetoes in shadow mode by default.
- **Git branch:**
  `I-1792202073-action-kb-wisdom-consult-and-recording/P-1792202834-antigravity-agent-gemini-3.8-flash-medium/E-1792202910-promotion-engine/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/knowledge/promotion.py`:
   - Implement `ContextFingerprint`: compute deterministic hash from action target, tool name, command prefix,
     normalized error type, and relevant git file extensions.
   - Implement Failure Veto Promotion Ladder:
     - States: `observed` (1 failure), `repeated` (2 failures with identical context fingerprint, 0 successes),
       `contested` (failures with 1+ intervening successes or manual dispute), `proven-fail` (3+ reproducible failures
       without contrary evidence).
     - Veto Promotion Rule: when an action reaches `repeated` or `proven-fail`, generate an active Veto record.
     - Veto Decay & Lifting: calculate exponential time decay on veto weights; automatically lift veto if a subsequent
       execution with matching fingerprint yields a confirmed `success`.
     - Veto Execution Mode: default to `shadow` mode (logs diagnostic warning and emits telemetry without blocking);
       allow explicit per-stage configuration (`enforce=True`) to hard-block execution.
   - Implement Success Evidence Promotion Ladder:
     - States: `observed` (1 success), `repeated` (2-4 successes), `likely-success` (5+ successes with Wilson 95% lower
       bound >= 0.70), `proven-success` (10+ successes with Wilson 95% lower bound >= 0.85).
     - Compute Beta posterior distribution `Beta(alpha + successes, beta + failures)` with uninformative prior
       `alpha=1, beta=1`.
     - Compute Wilson score 95% confidence interval `(lower_bound, upper_bound)` for the binomial success proportion.
     - Invariant: never emit imperative commands or vetoes for success patterns; success outputs must strictly be
       formulated as non-imperative advisory guidance.
   - Exclude `unknown` and `unresolved` records from both failure veto and success posterior calculations to prevent
     bias.
2. Integrate promotion evaluation function `evaluate_action_promotion(signature_hash, index_db)` returning current
   ladder state, posterior stats, Wilson interval, and veto status.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 3
- **Is Bottleneck:** YES (Governs the intelligence and safety of wisdom consult and promotion across all agent flows)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §3 (Trust model) & §4 (Failure containment), Bean 0052 Goal 4.
- **Potential failure modes for this step:** Vetoes erroneously deadlocking valid execution paths; numerical instability
  or zero division in Wilson interval formulas.
- **Guardrails and early‑abort checks:** Default veto enforcement to shadow mode with comprehensive diagnostic logging;
  clamp Wilson interval denominators and probabilities strictly to `[0.0, 1.0]`.

### Success & Discard Criteria

- **Success:** Failure ladder accurately triggers vetoes on 2 identical failures with 0 successes, lifts vetoes on
  contrary success, calculates Wilson 95% intervals correctly, and emits non-imperative advisories for successes.
- **Discard:** Mathematical calculation errors in Wilson score or failure vetoes hard-blocking stages while in shadow
  mode.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.55  |
| impact_pred         | 90.0  |
| cost_pred           | 2.8   |
| learning_value_pred | 5.8   |
| ev_pred             | 82.74 |

### Step Metrics Rationale

`EV = 0.92 * 90.0 + 0.5 * 5.8 - 0.3 * 0.55 - 2.8 = 82.80 + 2.90 - 0.165 - 2.8 = 82.735 -> 82.74`. Step 4 is the
algorithmic core of Bean 0052; delivers high impact and high learning value while containing risk via shadow mode.

---

## Step 5: Implement Knowledge Base (KB) and Wisdom Base (WB) persistence, promotion, and veto management CLI (Bean 0052 Goal 5)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard filesystem persistence conforming to existing `docs/knowledgebase_schema.md` and
  `docs/wisdombase_schema.md`; low risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Build KB and WB persistence logic writing descriptive facts to `holon-knowledge/kb/` and prescriptive
  rules to `holon-knowledge/wisdom/`, and implement CLI subcommands `holon promote --pending` and
  `holon veto list|lift`.
- **Git branch:**
  `I-1792202073-action-kb-wisdom-consult-and-recording/P-1792202834-antigravity-agent-gemini-3.8-flash-medium/E-1792202930-kb-wb-persistence/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/knowledge/wisdom.py`:
   - Implement `persist_kb_fact(fact_data, kb_dir="holon-knowledge/kb/")`:
     - Validate against KB schema: descriptive facts, patterns, and failure modes.
     - Store metadata including `kb_id`, `version`, `context_fingerprint`, `trial_count`, `posterior`,
       `source_record_ids`, and `created_at`.
     - Write formatted JSON file to appropriate subcategory (e.g. `patterns/` or `failure_modes/`).
   - Implement `persist_wb_rule(rule_data, wb_dir="holon-knowledge/wisdom/")`:
     - Validate against WB schema: prescriptive heuristics and universal invariants.
     - Store metadata including `wb_id`, `scope="global"`, `ladder_state`, `wilson_interval`, `context_fingerprint`, and
       source evidence links.
     - Write formatted JSON file to `holon-knowledge/wisdom/heuristics/` or `invariants/`.
   - Implement `promote_pending_candidates(index_db, dry_run=False)`:
     - Scan action signatures meeting promotion criteria (`likely-success`, `proven-success`, `repeated` failure,
       `proven-fail`).
     - Generate candidate KB facts or WB rules. If `dry_run` is False, persist to disk and index in SQLite.
2. In `apps/sandbox-executor/src/sandbox_executor/cli.py`, add CLI subcommands:
   - `holon promote`: accepts `--pending`, `--dry-run`, `--min-trials`, executes promotion evaluation, and displays
     summary table of promoted knowledge items.
   - `holon veto`: with nested subcommands:
     - `holon veto list`: displays active and shadowed vetoes, context fingerprints, reproduction counts, and decay
       status.
     - `holon veto lift --context <fingerprint>`: manually lifts a veto, records operator audit event, and updates
       SQLite index state.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/knowledgebase_schema.md`, `docs/wisdombase_schema.md`, `holon-config/world/constraints.md`
  §3.
- **Potential failure modes for this step:** Schema validation failures during JSON serialization; file overwrite
  collisions.
- **Guardrails and early‑abort checks:** Validate every candidate entry against dataclass schemas prior to disk write;
  use deterministic unique IDs (`KB-fact-...`, `WB-rule-...`).

### Success & Discard Criteria

- **Success:** `holon promote --pending` identifies qualified candidates and writes valid JSON files to `kb/` and
  `wisdom/`; `holon veto list|lift` accurately inspects and lifts veto entries.
- **Discard:** Invalid JSON written to `kb/` or failure to lift active vetoes upon command.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.40  |
| impact_pred         | 88.0  |
| cost_pred           | 2.3   |
| learning_value_pred | 4.6   |
| ev_pred             | 82.60 |

### Step Metrics Rationale

`EV = 0.94 * 88.0 + 0.5 * 4.6 - 0.3 * 0.40 - 2.3 = 82.72 + 2.30 - 0.12 - 2.3 = 82.60`. Standardized schema persistence
and operator CLI commands provide clear operational control over system learning.

---

## Step 6: Instrument lifecycle stages (Intent, Plan, Execute, Review, Calibrate) with pre-action consult, consulted-ID echoing, and self-metrics CLI holon stats (Bean 0052 Goal 6)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Integration across existing stage functions in `flow.py` and telemetry in `stats.py`; well-defined
  hooks, regression-tested against full flow pipeline.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Instrument all Holon lifecycle stages (`run_intent_stage`, `run_plan_stage`, `run_execute_stage`,
  `run_review_stage`, `run_calibrate_stage`) to consult wisdom before acting, echo consulted IDs in outputs and commit
  trailers, and surface real-time self-metrics via `holon stats`.
- **Git branch:**
  `I-1792202073-action-kb-wisdom-consult-and-recording/P-1792202834-antigravity-agent-gemini-3.8-flash-medium/E-1792202950-stage-instrumentation/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - In `run_intent_stage`: before creating or validating intent, invoke `ConsultEngine.consult_records` with intent
     domain labels; append relevant wisdom summaries to `FlowContext.logs` and echo consulted record IDs in intent
     metadata.
   - In `run_plan_stage`: consult wisdom for known failure modes and proven tactics matching intent labels; inject
     non-imperative advisory hints into planner prompt context (respecting `<= 1.2k` token cap); echo consulted IDs in
     plan variant metadata.
   - In `run_execute_stage`: check context fingerprint against veto engine; if veto active in shadow mode, log
     diagnostic warning; if enforce mode active, block execution with explanatory error; record action execution events
     in `actions.jsonl`.
   - In `run_review_stage`: consult review-specific heuristics and previous review findings; echo consulted IDs in PR
     review comments; record review dispositions and findings.
   - In `run_calibrate_stage`: reconcile pending action verdicts against actual execution success/failure; trigger
     `promote_pending_candidates` if confidence thresholds are met.
   - In `format_commit_trailers`: append `Consulted-Knowledge-IDs: <comma-separated-ids>` to git commit trailers for
     full git-addressable provenance.
2. In `apps/sandbox-executor/src/sandbox_executor/knowledge/stats.py`:
   - Implement `TelemetryCollector` tracking consult counts, cache hits, consult latency histogram (p50, p90, p95, p99),
     total injected tokens, and veto trigger counts.
   - Store rolling window telemetry in SQLite table `consult_telemetry`.
3. In `apps/sandbox-executor/src/sandbox_executor/cli.py`, add subcommand `holon stats`:
   - Display formatted telemetry dashboard: Total Consults, Hit Rate (%), Warm/Cold Consult Latency (p50/p95 ms), Total
     Injected Tokens, Shadow Veto Warnings, and Active Veto Blocks.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4, Step 5
- **Is Bottleneck:** YES (Integrates continuous learning across all operational flow stages)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 (Git Flow & Commit Boundaries), `docs/safety.md` §1 & §4,
  Bean 0052 Goal 6.
- **Potential failure modes for this step:** Pre-action consult failures crashing flow stages; unbounded prompt token
  bloat during planning.
- **Guardrails and early‑abort checks:** Wrap stage consults in non-fatal try-except blocks; strictly enforce top-k
  `<= 6` and `<= 1.2k` token limits before injecting context.

### Success & Discard Criteria

- **Success:** All 5 flow stages consult wisdom before acting, echo consulted IDs in outputs and commit trailers, and
  `holon stats` displays real-time telemetry metrics.
- **Discard:** Consult failure halts pipeline or injected tokens exceed 1.2k tokens.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.45  |
| impact_pred         | 89.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 4.5   |
| ev_pred             | 82.39 |

### Step Metrics Rationale

`EV = 0.93 * 89.0 + 0.5 * 4.5 - 0.3 * 0.45 - 2.5 = 82.77 + 2.25 - 0.135 - 2.5 = 82.385 -> 82.39`. Completes end-to-end
integration across the agent lifecycle, turning passive execution logs into an active, self-improving feedback loop.

---

## Step 7: Comprehensive hermetic unit and benchmark test suite, latency verification, and repository quality gates (Bean 0052 Goal 7)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Hermetic test suite and standard validation gates; ensures 100% adherence to repository invariants and
  zero regressions.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Author comprehensive unit and benchmark tests in `apps/sandbox-executor/tests/test_knowledge.py` and
  execute full repository verification gates (`pytest`, `ruff`, `prettier`, `make lint`, `make tools`).
- **Git branch:**
  `I-1792202073-action-kb-wisdom-consult-and-recording/P-1792202834-antigravity-agent-gemini-3.8-flash-medium/E-1792202970-verification-gates/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create test suite `apps/sandbox-executor/tests/test_knowledge.py` with hermetic test cases:
   - `test_sqlite_fts5_inverted_index`: Verify schema creation, inverted label queries, and FTS5 ranking in temporary
     directories.
   - `test_incremental_sync_byte_offsets`: Assert byte-offset tracking seeks directly to new content without full-file
     scans when records are appended.
   - `test_consult_multi_label_roundtrip`: Test conjunctive (`--all`), disjunctive (`--any`), and negated (`--not`)
     queries against known fixture data.
   - `test_consult_latency_benchmark`: Measure warm query latency across 100 iterations, asserting p95 latency is
     strictly `< 15ms`.
   - `test_consult_payload_capping_and_dedup`: Verify top-k `<= 6` and token cap `<= 1200` are strictly enforced, and
     repeated records in same session are deduplicated.
   - `test_outcome_logging_state_machine`: Verify transitions across `attempted`, `success`, `failure`, `unknown`,
     `reconcile`, and TTL expiration to `unresolved`.
   - `test_credential_redaction`: Verify OpenAI, Anthropic, Gemini, and GitHub token strings are redacted prior to
     ledger write.
   - `test_veto_ladder_and_decay`: Verify 2 reproductions with identical context fingerprint trigger veto, contrary
     success lifts veto, and shadow mode prevents execution block.
   - `test_wilson_score_and_non_imperative_advisories`: Verify Wilson 95% confidence interval values match reference
     statistical calculations, and success advisories contain non-imperative language.
   - `test_flow_stage_integration_and_stats`: Mock flow stages to verify consulted IDs are echoed in metadata and commit
     trailers, and `holon stats` aggregates telemetry correctly.
2. Run test suite:
   `uv run pytest apps/sandbox-executor/tests/test_knowledge.py apps/sandbox-executor/tests/test_ledger_query.py -m "not integration_test and not stress"`.
3. Run full test suite: `uv run pytest apps/sandbox-executor/tests -m "not integration_test and not stress"`.
4. Run static analysis: `uv run ruff check .` and format check: `uv run ruff format --check .`.
5. Run markdown formatting check: `npx prettier --check "**/*.md"`.
6. Run Makefile targets: `make lint` and `make tools`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4, Step 5, Step 6
- **Is Bottleneck:** YES (Final gate for intent completion and merge eligibility)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (pytest standard runner, no modification to existing passing
  tests), `docs/safety.md` §1.
- **Potential failure modes for this step:** Benchmark tests failing due to noisy neighbor VM latency; Prettier
  formatting oscillations on newly authored markdown docs.
- **Guardrails and early‑abort checks:** Use isolated temporary SQLite files in `tempfile.TemporaryDirectory` for all
  tests; run `npx prettier --write` on markdown files before final verification.

### Success & Discard Criteria

- **Success:** All test cases in `test_knowledge.py` pass, warm consult p95 latency is verified `< 15ms`, and all lint,
  format, and Makefile verification commands return exit code 0.
- **Discard:** Any test failure, latency violation > 15ms, or lint/formatting regression.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.30  |
| impact_pred         | 92.0  |
| cost_pred           | 1.7   |
| learning_value_pred | 3.8   |
| ev_pred             | 88.43 |

### Step Metrics Rationale

`EV = 0.96 * 92.0 + 0.5 * 3.8 - 0.3 * 0.30 - 1.7 = 88.32 + 1.90 - 0.09 - 1.7 = 88.43`. Comprehensive verification
guarantees hermeticity, regression prevention, and full adherence to repository standards.
