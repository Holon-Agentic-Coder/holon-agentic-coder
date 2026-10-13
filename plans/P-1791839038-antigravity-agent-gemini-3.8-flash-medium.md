# Plan for I-1791839030-append-every-action-to-execution-ledger

- **Plan ID:** P-1791839038-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-12T21:03:58.350Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with empirical validation of action-level execution logging schemas, git commit
  trailer ingestion, multi-criteria CLI filtering, and hermetic regression verification across existing calibration
  readers.
- **Safety priority level:** standard
- **Priority Justification:** Triggered by `holon-config/world/ruleset.md` §1 & §2 (Python runtime `==3.13.*`, workspace
  isolation under `apps/sandbox-executor`, strict PEP 8 formatting, static type annotations with `typing` module,
  docstring maintenance), `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test placement under
  `apps/sandbox-executor/tests/`, test modifications declared in planning), `holon-config/world/constraints.md` §1 (Git
  Flow & Branch Constraints: prefix-based branch isolation, rebase discipline, commit boundaries),
  `holon-config/world/constraints.md` §2 (Sandbox containment: subprocess and filesystem containment, no unwhitelisted
  subprocesses, process sandbox `< 10` entropy), `holon-config/world/constraints.md` §3 (Ledger immutability:
  append-only ledgers, zero modifications to historical lines), `docs/safety.md` §1 & §2 (Git as safety boundary,
  sandboxing mandatory for execution), and Bean 0077 (Action-level execution ledger, standardized action schema, action
  provenance trailers, and `holon ledger query`).

## Exploration

- **Proportion of steps that are exploratory:** 0.67
- **Justification:** Steps 1, 2, 4, and 6 incorporate balanced exploration to empirically validate the standardized
  action record schema, resolver commit trailer parsing across git history, multi-flag query filtering performance with
  JSON serialization, and backward compatibility of legacy calibration readers against interleaved action records
  without regression.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.60  |
| impact_pred         | 96.0  |
| cost_pred           | 10.3  |
| learning_value_pred | 5.5   |
| ev_pred             | 82.51 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.94. Dictated by the bottleneck step (Step 3: 0.94), where existing ledger consumers
  (`calibration.py`, `executor.py`, and `scaffold.py`) must remain completely robust and backward-compatible when
  parsing summary records from `executions.jsonl` in the presence of interleaved action records.
- **entropy_pred**: 0.60. Evaluated as the maximum single-step risk profile (Step 1: 0.60), where core schema models and
  execution logging hooks are established. The cumulative sum of predicted step entropies across all six steps is 2.60
  (`0.60 + 0.40 + 0.50 + 0.50 + 0.20 + 0.40 = 2.60`), strictly within the allocated entropy budget of 15.0.
- **impact_pred**: 96.0. Fulfills Bean 0077 by unlocking fine-grained action observability: discrete execution logging
  for code changes, doc updates, config edits, commands, test executions, and git mutations; standardized action schemas
  with error context and mistake lessons; review criticality and provenance tracking via commit trailers; a
  high-performance CLI query interface (`holon ledger query`); and zero regressions for existing tools.
- **cost_pred**: 10.3. Calculated as the direct sum of individual step costs
  (`2.0 + 1.5 + 1.8 + 2.2 + 0.8 + 2.0 = 10.3`).
- **learning_value_pred**: 5.5. Epistemic gain from establishing standardized action classification taxonomies,
  structured error context schemas, git commit trailer provenance pipelines, and efficient streaming JSONL query
  architectures.
- **ev_pred**: 82.51. Computed strictly via the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system constants `lambda = 0.3`
  and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.94 * 96.0 + 0.5 * 5.5 - 0.3 * 0.60 - 10.3 = 90.24 + 2.75 - 0.18 - 10.3 = 82.51`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python target `==3.13.*`, package
    management via `uv`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, static typing with `typing`
    module, docstring maintenance).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test files placed in
    `apps/sandbox-executor/tests/`, all test additions declared in the planning step).
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, rebase
    discipline, commit boundaries).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: filesystem containment, no unwhitelisted
    subprocesses, process sandbox `< 10` entropy).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modifications to historical ledger entries,
    append-only ledger operations).
  - `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory for execution).
  - Bean 0077 (Append every action to execution ledger, standardized action schema, action provenance, and
    `holon ledger query`).
- **Potential violations or edge cases:**
  - Existing callers in `calibration.py` failing when encountering interleaved `"entry_type": "action"` rows in
    `holon-knowledge/ledger/executions.jsonl`.
  - Malformed or partial JSONL rows in `executions.jsonl` causing crashes during CLI query filtering.
  - Commit trailer parsing errors when commit messages contain arbitrary multi-line text or non-standard formatting.
  - Unhandled filter combinations in `holon ledger query` returning inaccurate record subsets or crashing on null
    fields.
  - Inadvertent rewriting or truncation of existing records in `executions.jsonl` violating ledger immutability.
- **Mitigations built into the plan:**
  - Enforce explicit discrimination on `"entry_type"` across all readers: rows with missing `"entry_type"` default to
    `"summary"` for backward compatibility; `calibration.py` explicitly ignores rows where `entry_type != "summary"`.
  - Enforce strict append-only file operations (`mode="a"`) in `executor.py` and action logging utilities; forbid
    in-place writes.
  - Implement robust regex and standard trailer parsing for `Action-Origin`, `Review-Severity`, `Review-Iteration`, and
    `Review-Finding-Ids` with safe fallbacks (`initial_implementation`).
  - Add comprehensive unit tests verifying that interleaved action rows in `executions.jsonl` do not disrupt plan
    calibration or execution lookups.
  - Stream JSONL file lines during CLI queries with defensive JSON decode handling, gracefully ignoring empty lines.
- **Residual risk accepted (and why):**
  - Historical executions prior to Bean 0077 do not contain action records; accepted as backward compatibility
    gracefully handles summary-only executions.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 0.60
- **Budget Compliance:** The strategy fits within budget (`0.60 <= 15.0`).

## Plan Description & Strategy

This plan implements Bean 0077 across `holon-agentic-coder`:

1. **Standardized Action Schema & Execution Logging Infrastructure (`sandbox_executor/ledger.py` & `executor.py`):**
   - Create domain models and helper functions in `apps/sandbox-executor/src/sandbox_executor/ledger.py` representing
     discrete action records (`ActionRecord`) and execution summary records (`SummaryRecord`).
   - Define valid taxonomies: categories (`code`, `doc`, `config`, `test`, `build`, `dependency`), outcomes (`success`,
     `failure`, `reverted`, `blocked`), origins (`initial_implementation`, `pr_review`, `ci_fix`, `calibration`,
     `operator`), and review severities (`critical`, `important`, `nit`).
   - In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`, integrate action logging to record discrete
     actions (code modifications, test executions, git mutations, command runs) to
     `holon-knowledge/ledger/executions.jsonl` with `"entry_type": "action"` while preserving run summaries with
     `"entry_type": "summary"`.

2. **Commit Trailer Ingestion & Action Provenance Parsing:**
   - In `sandbox_executor/ledger.py`, implement resolver commit trailer extraction from git commit messages:
     `Action-Origin`, `Review-Severity`, `Review-Iteration`, `Review-Finding-Ids`.
   - Map extracted trailer values to action record provenance fields, defaulting `action_origin` to
     `initial_implementation` when absent.
   - For `action_origin == "pr_review"`, record `review_severity`, `review_iteration`, `review_finding_ids`,
     `review_disposition` (`applied` or `rejected`), `rejection_reason`, and `introduced_by_action_id`.

3. **Reader Backward Compatibility & Ledger Immutability Hardening (`calibration.py`, `executor.py`, `scaffold.py`):**
   - Update `apps/sandbox-executor/src/sandbox_executor/calibration.py` to filter records by `entry_type`: treat records
     without `entry_type` as `"summary"`, and skip all `"entry_type": "action"` records when matching execution
     metadata.
   - Ensure `scaffold.py` and `executor.py` maintain append-only semantics for `executions.jsonl`.

4. **CLI Query Interface Implementation (`holon ledger query`):**
   - In `apps/sandbox-executor/src/sandbox_executor/cli.py`, add the `ledger` command and `query` subcommand.
   - Support all required filter flags: `--ledger-file`, `--entry-type`, `--category`, `--labels`, `--target`,
     `--outcome`, `--origin`, `--review-severity`, `--query`/`--keyword`, `--json`, and `--limit`.
   - Provide formatted terminal output by default (with columns for timestamp, entry type, category, action/target,
     outcome, and origin) and structured JSON/JSONL output when `--json` is specified.

5. **Documentation & Agent Behavioral Instructions (`docs/ledger_schema.md` & `.agents/AGENTS.md`):**
   - Update `docs/ledger_schema.md` with the action schema specification, entry types, review provenance fields, commit
     trailer conventions, and CLI query usage examples.
   - Update `.agents/AGENTS.md` with guidelines on emitting commit trailers and using `holon ledger query` to inspect
     past actions.

6. **Comprehensive Hermetic Test Suite (`apps/sandbox-executor/tests/`):**
   - Add unit tests in `test_ledger_query.py` and `test_cli.py` covering action serialization, commit trailer parsing,
     query filtering, and CLI output formatting.
   - Add regression tests in `test_calibration.py` asserting that interleaved action records in `executions.jsonl` do
     not interfere with plan calibration.

---

## Step 1: Standardized Action Schema & Action-Level Execution Logging Infrastructure

- **Sub‑intent recommendation:** NO
- **Reasoning:** Encapsulated within `sandbox_executor/ledger.py` and `executor.py` with low blast radius and
  straightforward unit test verification.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Defining structured dataclasses for discrete action records and appending them alongside
  summary records with `"entry_type": "action"` provides fine-grained execution visibility while preserving append-only
  ledger invariants.
- **Learning target:** Determine the optimal serialization format and field defaults for discrete action records that
  support fast streaming reads and flexible metadata tagging.
- **Maximum acceptable cost for this learning:** 2.0

### Intent & Git Integration

- **Step Intent:** Create `sandbox_executor/ledger.py` with `ActionRecord` and `SummaryRecord` models, and integrate
  discrete action recording in `executor.py`.
- **Git branch:**
  `I-1791839030-append-every-action-to-execution-ledger/P-1791839038-antigravity-agent-gemini-3.8-flash-medium/E-1-action-schema-and-logging/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create module `apps/sandbox-executor/src/sandbox_executor/ledger.py`:
   - Define dataclass `ActionRecord`:
     - `entry_type`: Literal["action"] = "action"
     - `action_id`: str (e.g. `ACT-{timestamp}-{uuid}`)
     - `execution_id`: str (associating the action to the parent execution)
     - `parent_action_id`: str | None = None
     - `event_type`: str (e.g. `code_modification`, `doc_update`, `config_change`, `command_execution`, `test_run`,
       `git_mutation`)
     - `action`: str (concise description of the operation)
     - `category`: str (`code`, `doc`, `config`, `test`, `build`, `dependency`)
     - `labels`: list[str] (e.g. `["refactor", "ci", "core"]`)
     - `target`: str (path, command name, or target artifact)
     - `outcome`: str (`success`, `failure`, `reverted`, `blocked`)
     - `error_context`: str | None = None
     - `mistake_category`: str | None = None
     - `action_origin`: str (`initial_implementation`, `pr_review`, `ci_fix`, `calibration`, `operator`) with default
       `"initial_implementation"`
     - `review_severity`: str | None = None (`critical`, `important`, `nit`)
     - `review_iteration`: int | None = None
     - `review_finding_ids`: list[str] | None = None
     - `review_disposition`: str | None = None (`applied`, `rejected`)
     - `rejection_reason`: str | None = None
     - `introduced_by_action_id`: str | None = None
     - `created_at`: str (ISO 8601 UTC timestamp)
   - Define helper functions: `append_action_record(ledger_path: str, record: ActionRecord) -> None` and
     `append_summary_record(ledger_path: str, record: dict[str, Any]) -> None`.
   - Ensure `append_action_record` writes in append mode (`"a"`), validates required fields, and flushes to disk.
2. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`:
   - Ensure all existing summary ledger entries (lines 1240, 1385, 1490) explicitly include `"entry_type": "summary"`.
   - Add discrete action logging calls during plan execution:
     - Log command execution actions (e.g. running agent command, git setup, git status).
     - Log git mutations (e.g. `git add`, `git commit`, `git recovery`).
     - Log test execution runs when tests are executed.
     - Associate each action with `exec_id` as `execution_id`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Docstrings, static typing),
  `holon-config/world/constraints.md` §3 (Ledger immutability: append-only ledgers), `docs/safety.md` §1 (Git safety
  boundary).
- **Potential failure modes for this step:** Ledger write exceptions interrupting execution flow; schema validation
  rejecting optional fields.
- **Guardrails and early‑abort checks:** Wrap action writes in non-fatal try-except blocks with error logging; validate
  field constraints prior to serialization.

### Success & Discard Criteria

- **Success:** Discrete actions and overall summaries are appended to `executions.jsonl` with valid schemas and
  `"entry_type"` discrimination.
- **Discard:** Discard if action logging modifies or overwrites existing ledger lines.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.60  |
| impact_pred         | 94.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 89.62 |

### Step Metrics Rationale

Calculated via: `EV = 0.95 * 94.0 + 0.5 * 5.0 - 0.3 * 0.60 - 2.0 = 89.30 + 2.50 - 0.18 - 2.0 = 89.62`. High success
probability for clean dataclass definitions and modular append helpers.

---

## Step 2: Implement Git Commit Trailer Ingestion & Action Provenance Tracking

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standardized regex and git trailer extraction logic encapsulated within `sandbox_executor/ledger.py`.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Parsing standardized git commit trailers (`Action-Origin`, `Review-Severity`,
  `Review-Iteration`, `Review-Finding-Ids`) enables automated provenance tracking without manual input or external
  sidecars.
- **Learning target:** Evaluate regex pattern matching versus `git interpret-trailers` for resilient multi-line trailer
  parsing.
- **Maximum acceptable cost for this learning:** 1.5

### Intent & Git Integration

- **Step Intent:** Implement commit trailer parsing functions in `sandbox_executor/ledger.py` and connect them to action
  provenance enrichment.
- **Git branch:**
  `I-1791839030-append-every-action-to-execution-ledger/P-1791839038-antigravity-agent-gemini-3.8-flash-medium/E-2-commit-trailers-provenance/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/ledger.py`:
   - Implement `parse_commit_trailers(commit_message: str) -> dict[str, Any]`:
     - Parse case-insensitive trailers:
       - `Action-Origin`: map to valid `action_origin` (`initial_implementation`, `pr_review`, `ci_fix`, `calibration`,
         `operator`); default to `initial_implementation` if omitted or invalid.
       - `Review-Severity`: map to valid `review_severity` (`critical`, `important`, `nit`).
       - `Review-Iteration`: parse integer value for `review_iteration`.
       - `Review-Finding-Ids`: parse comma-separated or space-separated string into list of finding IDs.
   - Implement `extract_action_provenance_from_commit(repo_dir: str, commit_ref: str) -> dict[str, Any]`:
     - Run `git log -1 --pretty=%B <commit_ref>` to fetch full commit message.
     - Extract trailers using `parse_commit_trailers`.
     - Return structured dictionary matching provenance fields of `ActionRecord`.
   - Implement `format_commit_trailers(...) -> str` to generate trailer blocks for inclusion in resolver commit
     messages.
2. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`:
   - When staging and committing changes, support appending resolver trailers to commit messages if review metadata is
     present in execution context.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Docstrings, static typing),
  `holon-config/world/constraints.md` §1 (Git Flow constraints).
- **Potential failure modes for this step:** Commit messages without trailers raising KeyError; invalid trailer
  formatting causing parsing exceptions.
- **Guardrails and early‑abort checks:** Provide safe defaults (`initial_implementation`, empty lists); sanitize and
  validate extracted trailer values against enum sets.

### Success & Discard Criteria

- **Success:** Resolver commit trailers are cleanly parsed and mapped to `ActionRecord` provenance attributes.
- **Discard:** Discard if trailer parsing crashes on standard multi-line git commit messages lacking trailers.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.40  |
| impact_pred         | 90.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 4.5   |
| ev_pred             | 87.03 |

### Step Metrics Rationale

Calculated via: `EV = 0.96 * 90.0 + 0.5 * 4.5 - 0.3 * 0.40 - 1.5 = 86.40 + 2.25 - 0.12 - 1.5 = 87.03`. Straightforward
text parsing logic with high determinism.

---

## Step 3: Hardening Reader Backward Compatibility & Immutability Invariants

- **Sub‑intent recommendation:** NO
- **Reasoning:** Targeted updates to existing reader functions in `calibration.py`, `executor.py`, and `scaffold.py`
  with immediate regression test validation.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT
- **Hypothesis being tested:** Explicitly ignoring action records in summary consumers guarantees full backward
  compatibility for calibration and execution history.
- **Learning target:** N/A (EXPLOIT)
- **Maximum acceptable cost for this learning:** N/A

### Intent & Git Integration

- **Step Intent:** Update `calibration.py` to filter by `entry_type == "summary"`, preserving compatibility against
  interleaved action rows.
- **Git branch:**
  `I-1791839030-append-every-action-to-execution-ledger/P-1791839038-antigravity-agent-gemini-3.8-flash-medium/E-3-backward-compatibility-readers/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/calibration.py`:
   - In `extract_execution_metrics`:
     - In both git-show parsing loop (lines 691-706) and local disk fallback loop (lines 714-729), inspect
       `record.get("entry_type", "summary")`.
     - If `record.get("entry_type", "summary") != "summary"`, skip the record.
     - Ensure only records representing run summaries are evaluated for `best_record`, `status`, and actual execution
       telemetry.
   - In `run_calibrate` (lines 1097-1110):
     - When resolving missing `plan_id` from `executions.jsonl`, check `data.get("entry_type", "summary") == "summary"`.
     - Skip action records so action IDs are not confused with execution summaries.
2. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`:
   - Verify that all summary writes use `"entry_type": "summary"`.
   - Verify that superseding revision rows (`ledger_revision >= 2`) preserve `"entry_type": "summary"`.
3. In `apps/sandbox-executor/src/sandbox_executor/scaffold.py`:
   - Ensure scaffolding initializers verify `executions.jsonl` existence without modifying existing rows.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (If backward compatibility fails, existing calibration and flow tests break across the
  workspace).

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (Ledger immutability), `docs/safety.md` §5 (Core invariant
  preservation).
- **Potential failure modes for this step:** Misidentifying legacy summary rows without `entry_type` as invalid.
- **Guardrails and early‑abort checks:** Default missing `entry_type` to `"summary"` so all historical rows in
  `executions.jsonl` continue to be processed seamlessly.

### Success & Discard Criteria

- **Success:** Calibration module passes all existing unit and regression tests when executing against ledgers
  containing interleaved action records.
- **Discard:** Discard if legacy summary entries without `entry_type` fail to resolve in `calibration.py`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.50  |
| impact_pred         | 93.0  |
| cost_pred           | 1.8   |
| learning_value_pred | 5.0   |
| ev_pred             | 87.97 |

### Step Metrics Rationale

Calculated via: `EV = 0.94 * 93.0 + 0.5 * 5.0 - 0.3 * 0.50 - 1.8 = 87.42 + 2.50 - 0.15 - 1.8 = 87.97`. Critical
bottleneck step ensuring zero disruption to existing calibration workflows.

---

## Step 4: Implement CLI Query Interface (`holon ledger query`)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Adding CLI subparser and filtering engine directly within `cli.py` and `ledger.py`.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** A dedicated streaming query interface with rich filtering flags enables operators and
  autonomous agents to locate relevant actions and error lessons in sub-second time.
- **Learning target:** Determine the most ergonomic terminal formatting (columns, wrapping, colorization) and JSON
  output schema for both interactive and programmatic callers.
- **Maximum acceptable cost for this learning:** 2.2

### Intent & Git Integration

- **Step Intent:** Add `ledger` parser with `query` subcommand in `sandbox_executor/cli.py` supporting all 11 filter
  flags and formatting modes.
- **Git branch:**
  `I-1791839030-append-every-action-to-execution-ledger/P-1791839038-antigravity-agent-gemini-3.8-flash-medium/E-4-cli-ledger-query/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/ledger.py`:
   - Implement `query_ledger(ledger_path: str, filters: dict[str, Any]) -> list[dict[str, Any]]`:
     - Stream lines from `ledger_path`, safely skipping empty lines and parsing JSON.
     - Apply filters:
       - `entry_type`: filter by `"action"`, `"summary"`, or allow all if `"all"` or None.
       - `category`: match exact category string.
       - `labels`: match if any (or all) specified labels exist in record's `labels`.
       - `target`: case-insensitive substring match on `target`.
       - `outcome`: match exact outcome string.
       - `origin`: match `action_origin`.
       - `review_severity`: match `review_severity`.
       - `query` / `keyword`: case-insensitive search across `action`, `summary`, `target`, `error_context`, and
         `mistake_category`.
       - `limit`: stop streaming after reaching the specified count limit.
   - Implement `format_query_results_table(records: list[dict[str, Any]]) -> str`:
     - Format matching records into a clean human-readable table with headers: Timestamp, Type, Category,
       Action/Summary, Target, Outcome, Origin.
2. In `apps/sandbox-executor/src/sandbox_executor/cli.py`:
   - Register `ledger` subparser:
     - Add `ledger_parser = subparsers.add_parser("ledger", help="Query and inspect Holon ledgers.")`
     - Add sub-subparsers: `ledger_subparsers = ledger_parser.add_subparsers(dest="ledger_command", required=True)`
     - Add
       `query_parser = ledger_subparsers.add_parser("query", help="Query action and summary records from execution ledger.")`
     - Add arguments to `query_parser`:
       - `--ledger-file`: default `"holon-knowledge/ledger/executions.jsonl"`
       - `--entry-type`: choices `["action", "summary", "all"]`, default `"all"`
       - `--category`: choices `["code", "doc", "config", "test", "build", "dependency"]`
       - `--labels`: string of comma-separated tags
       - `--target`: string filter for target
       - `--outcome`: choices `["success", "failure", "reverted", "blocked"]`
       - `--origin`: choices `["initial_implementation", "pr_review", "ci_fix", "calibration", "operator"]`
       - `--review-severity`: choices `["critical", "important", "nit"]`
       - `--query`, `--keyword`: text search string
       - `--json`: boolean flag for JSON stdout output
       - `--limit`: integer limit
       - `--repo-dir`: default `"."`
   - In `main()` dispatch:
     - Handle `args.command == "ledger"` and `args.ledger_command == "query"`.
     - Resolve ledger file path relative to `repo_dir`.
     - Call `query_ledger` and print table or JSON to stdout.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Docstrings, typing), `holon-config/world/constraints.md` §3
  (Ledger immutability).
- **Potential failure modes for this step:** Non-existent ledger file path; invalid regex or search pattern crashing the
  CLI.
- **Guardrails and early‑abort checks:** Check file existence; print helpful message and exit code 0 with empty result
  set if no records match; exit 1 with descriptive error if file does not exist.

### Success & Discard Criteria

- **Success:** `holon ledger query` correctly filters execution records by all supported parameters and outputs clean
  tabular or JSON data.
- **Discard:** Discard if CLI query mutates the ledger or crashes on unknown fields.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.50  |
| impact_pred         | 95.0  |
| cost_pred           | 2.2   |
| learning_value_pred | 5.0   |
| ev_pred             | 90.40 |

### Step Metrics Rationale

Calculated via: `EV = 0.95 * 95.0 + 0.5 * 5.0 - 0.3 * 0.50 - 2.2 = 90.25 + 2.50 - 0.15 - 2.2 = 90.40`. High impact
feature delivering the primary user-facing capability of Bean 0077.

---

## Step 5: Update Documentation & Agent Behavioral Guidelines

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure documentation updates to `docs/ledger_schema.md` and `.agents/AGENTS.md`.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT
- **Hypothesis being tested:** N/A (EXPLOIT)
- **Learning target:** N/A (EXPLOIT)
- **Maximum acceptable cost for this learning:** N/A

### Intent & Git Integration

- **Step Intent:** Document action schema, provenance trailers, and `holon ledger query` CLI in `docs/ledger_schema.md`
  and `.agents/AGENTS.md`.
- **Git branch:**
  `I-1791839030-append-every-action-to-execution-ledger/P-1791839038-antigravity-agent-gemini-3.8-flash-medium/E-5-update-documentation/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `docs/ledger_schema.md`:
   - Under Sandbox Executor Ledgers § "Execution Ledger (`executions.jsonl`)":
     - Document two entry types: `"entry_type": "summary"` and `"entry_type": "action"`.
     - Specify the full action schema: `action_id`, `execution_id`, `parent_action_id`, `event_type`, `action`,
       `category`, `labels`, `target`, `outcome`, `error_context`, `mistake_category`, `action_origin`.
     - Specify PR review provenance fields: `review_severity`, `review_iteration`, `review_finding_ids`,
       `review_disposition`, `rejection_reason`, `introduced_by_action_id`.
     - Document resolver commit trailers: `Action-Origin`, `Review-Severity`, `Review-Iteration`, `Review-Finding-Ids`.
     - Document the `holon ledger query` CLI command, options, filtering behavior, and example invocations.
2. In `.agents/AGENTS.md`:
   - Add instructions for planning and execution agents:
     - Require execution agents to append discrete action records during runs.
     - Instruct agents to append commit trailers when resolving PR review findings or fixing CI.
     - Advise agents to query past failure modes and lessons using
       `holon ledger query --outcome failure --category <cat>` before attempting risky refactors.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (Documentation integrity), `holon-config/world/ruleset.md` §2 (Docstrings &
  documentation standards).
- **Potential failure modes for this step:** Prettier formatting inconsistencies across markdown files.
- **Guardrails and early‑abort checks:** Run prettier convergence checks on all updated markdown files.

### Success & Discard Criteria

- **Success:** `docs/ledger_schema.md` and `.agents/AGENTS.md` accurately document Bean 0077 specifications and pass all
  markdown lint checks.
- **Discard:** Discard if documentation contradicts implemented schemas or CLI options.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.20  |
| impact_pred         | 88.0  |
| cost_pred           | 0.8   |
| learning_value_pred | 4.0   |
| ev_pred             | 87.38 |

### Step Metrics Rationale

Calculated via: `EV = 0.98 * 88.0 + 0.5 * 4.0 - 0.3 * 0.20 - 0.8 = 86.24 + 2.00 - 0.06 - 0.8 = 87.38`. Low cost, minimal
entropy documentation update.

---

## Step 6: Hermetic Unit, Regression, and Backward Compatibility Test Suite

- **Sub‑intent recommendation:** NO
- **Reasoning:** Hermetic test suite added to `apps/sandbox-executor/tests/` to guarantee 100% test pass rate.
- **Step Type:** TEST
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Comprehensive unit and regression tests covering all query filters, trailer ingestion,
  action logging, and interleaved calibration parsing ensure permanent stability against regressions.
- **Learning target:** Verify edge-case performance for large interleaved ledgers and validate that all query filter
  combinations execute hermetically without external dependencies.
- **Maximum acceptable cost for this learning:** 2.0

### Intent & Git Integration

- **Step Intent:** Add `apps/sandbox-executor/tests/test_ledger_query.py` and regression test cases in
  `test_calibration.py` and `test_executor.py`.
- **Git branch:**
  `I-1791839030-append-every-action-to-execution-ledger/P-1791839038-antigravity-agent-gemini-3.8-flash-medium/E-6-hermetic-tests/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/tests/test_ledger_query.py`:
   - Test action record serialization and schema validation (required fields, enum constraints, optional fields).
   - Test commit trailer parsing:
     - Messages with standard trailers (`Action-Origin: pr_review`, `Review-Severity: critical`, `Review-Iteration: 2`,
       `Review-Finding-Ids: FIND-001, FIND-002`).
     - Messages with no trailers (defaults to `initial_implementation`).
     - Messages with messy formatting, empty lines, and mixed casing.
   - Test CLI query command via `CliRunner` or `subprocess`:
     - Filter by `--entry-type action` vs `--entry-type summary`.
     - Filter by `--category code`, `--category test`, etc.
     - Filter by `--labels`.
     - Filter by `--target` substring.
     - Filter by `--outcome success`, `--outcome failure`.
     - Filter by `--origin pr_review`, `--origin initial_implementation`.
     - Filter by `--review-severity critical`.
     - Filter by `--query` keyword search.
     - Output formatting: verify tabular output headers and `--json` array output.
     - Pagination limit: verify `--limit` truncates output correctly.
2. In `apps/sandbox-executor/tests/test_calibration.py`:
   - Add regression tests verifying that `run_calibrate` and `extract_execution_metrics` operate flawlessly when
     `executions.jsonl` contains interleaved action rows and summary rows.
   - Assert that action rows are ignored and summary rows are accurately matched.
3. In `apps/sandbox-executor/tests/test_executor.py`:
   - Add tests verifying that execution runs record discrete action entries and summary entries with `"entry_type"` into
     `executions.jsonl`.
4. Run full workspace test suite (`uv run pytest`) and lint suite (`uv run task check`) to ensure 100% pass rate.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4, Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, hermetic tests),
  `docs/safety.md` §2 (Sandboxing and hermetic fixtures).
- **Potential failure modes for this step:** Tests depending on live git repo or mutating real ledger files.
- **Guardrails and early‑abort checks:** Use temporary directories (`tmp_path`) and hermetic mock fixtures; never mutate
  the repo's real ledger files during testing.

### Success & Discard Criteria

- **Success:** 100% test pass rate across all new and existing tests; zero lint/formatting errors.
- **Discard:** Discard if tests fail or if test fixtures contaminate repository working state.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.40  |
| impact_pred         | 92.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 86.86 |

### Step Metrics Rationale

Calculated via: `EV = 0.94 * 92.0 + 0.5 * 5.0 - 0.3 * 0.40 - 2.0 = 86.48 + 2.50 - 0.12 - 2.0 = 86.86`. Comprehensive
testing delivering robust verification and preventing regressions.
