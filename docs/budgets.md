# Turn and Stage Budgets (Bean 0139)

A single runner turn on a local model used to be able to run without any bound, and a pipeline stage had no ceiling at
all. Holon now bounds both, preserves whatever partial work exists when a budget fires, and leaves an auditable ledger
action whenever a budget failure is not recovered.

The implementation lives in `apps/sandbox-executor/src/sandbox_executor/budgets.py` (configuration, timers, summary
lines) and `apps/sandbox-executor/src/sandbox_executor/turns.py` (turn supervision, salvage, resume and the ACT-BUDGET
record). The incremental artefact protocol these budgets rely on is described in
[Creating a Plan in Sandbox](sandbox/create_plan.md#incremental-plan-artefact-bean-0138) and
[Running Execution via Docker](sandbox/execute_plan.md#incremental-execution-notes-bean-0138).

## Budgets and defaults

The defaults are the real measurements recorded in Beans 0138 to 0140. They are not derived from synthetic throughput
data, and the tests that exercise them use injected clocks only to drive control flow.

| Budget                | Default | Variable                                      | Bounds                                               |
| --------------------- | ------- | --------------------------------------------- | ---------------------------------------------------- |
| Turn                  | 300 s   | `HOLON_TURN_TIMEOUT_SECONDS`                  | One runner invocation (one agent CLI process).       |
| Stall                 | 90 s    | `HOLON_TURN_STALL_SECONDS`                    | Silence within a turn; `0` disables stall detection. |
| Intent stage          | 10 min  | `HOLON_STAGE_BUDGET_INTENT_MINUTES`           | The intent stage.                                    |
| Plan stage            | 35 min  | `HOLON_STAGE_BUDGET_PLAN_MINUTES`             | The plan stage, including the resume turn.           |
| Execute stage         | 45 min  | `HOLON_STAGE_BUDGET_EXECUTE_MINUTES`          | The execute stage, including verification tests.     |
| Review iteration      | 15 min  | `HOLON_STAGE_BUDGET_REVIEW_ITERATION_MINUTES` | Each review-loop iteration.                          |
| Calibration stage     | 10 min  | `HOLON_STAGE_BUDGET_CALIBRATION_MINUTES`      | The calibration stage.                               |
| Stage ceiling (exact) | (unset) | `HOLON_STAGE_BUDGET_SECONDS`                  | Replaces every per-stage value above, in seconds.    |

`HOLON_STAGE_BUDGET_SECONDS` is meant for handing one exact ceiling to a single sandbox role; set it only for that
container, because wherever it is set it applies to every stage. Non-numeric, non-finite, zero or negative values are
ignored with a warning and the default is kept (except that `HOLON_TURN_STALL_SECONDS=0` is accepted and disables stall
detection). All of these variables start with `HOLON_`, so the host CLI forwards them into the sandbox container
automatically. `holon doctor` prints the effective values.

> [!NOTE] The turn budget applies to every runner invocation, including executor turns that implement a whole plan in
> one agent session. Raise `HOLON_TURN_TIMEOUT_SECONDS` for plans whose single agent turn legitimately runs longer.

## Turn supervision

Agent turns are started with `start_new_session=True`, so each agent leads its own process group. Reader threads drain
stdout and stderr continuously, so an agent can never block on a full pipe. A turn counts as live while any of these
change:

- streamed stdout or stderr output (partial lines count);
- the size or modification time of the artefact being written (`plans/P-*.md` or `executions/E-*.md`);
- for executor turns, the newest file modification time in the working tree (scanned at most every 15 s, skipping
  `.git`, `.venv` and `node_modules`), because an executor edits many files besides its notes.

When a budget fires, the supervisor sends `SIGTERM` to the agent's process group, waits a 10 s grace period, then sends
`SIGKILL`. It asserts first that the agent's process group differs from its own; if they were ever equal it signals only
the agent process, so the entrypoint and its salvage path always survive.

## Salvage, resume and the ACT-BUDGET record

1. After a breached turn, whatever the agent already wrote to its artefact is kept on disk (salvage).
2. Exactly one resume turn is attempted. Its prompt repeats the task context, states that the file is partial, and lists
   only the missing sections. It is bounded by both the turn budget and what is left of the stage budget; if the stage
   has no time left, no resume is attempted.
3. If the artefact is usable after the resume (it exists, exceeds the size floor and its metrics table parses), the
   stage carries on normally and nothing is recorded.
4. Otherwise one action record is appended to `holon-knowledge/ledger/executions.jsonl` through `append_action_record()`
   (which takes `LedgerLock`), and the entrypoint exits non-zero. No other ledger file is written and no new ledger is
   created.

The record has these fields in addition to the standard action fields (see [Ledger Schema](ledger_schema.md)):

| Field             | Value                                                                   |
| ----------------- | ----------------------------------------------------------------------- |
| `entry_type`      | `action`                                                                |
| `action_id`       | `ACT-BUDGET-{epoch}-{8 hex}`                                            |
| `execution_id`    | The plan id (planner) or execution id (executor) the failure belongs to |
| `event_type`      | `budget_breach`                                                         |
| `category`        | `budget`                                                                |
| `outcome`         | `failure`                                                               |
| `stage`           | `plan` or `execute`                                                     |
| `budget_name`     | The budget that fired last: `turn`, `stall` or `stage`                  |
| `budget_seconds`  | That budget's limit in seconds                                          |
| `elapsed_seconds` | Seconds elapsed against that budget when it fired                       |
| `error_context`   | Human-readable description, including the stage's total elapsed time    |
| `target`          | The artefact path (`plans/P-*.md` or `executions/E-*.md`)               |

The record is written in one place only, once per stage failure. The planner commits it together with the salvaged
partial plan on the plan branch (no `plans.jsonl` entry is made for an unusable plan); the executor commits it with the
failed execution summary and stages the partial notes, but not the code changes.

Host-side stage ceilings in `holon flow` (below) never write an ACT-BUDGET record, so a failure is not recorded twice.

## Stage summary lines

Every stage reports elapsed time against its budget in the form `stage: status (elapsed min / budget min)`, for example
`plan: ok (4.1 min / 35 min)`. Elapsed minutes carry one decimal; whole-minute budgets are printed as integers.

- The planner and executor entrypoints print the line when they finish (`plan: ok ...`, `execute: failed ...`).
- `holon flow` logs it for every stage and prints it in the flow summary. The review stage's budget is one review
  iteration budget per iteration it ran; each iteration also logs `iteration N review: ok (... / 15 min)`.
- A `holon flow` stage that returns after its ceiling is marked failed with an error naming the budget, and a review
  iteration that overruns stops the review loop without consensus.

The verification test run in the flow execute stage uses the remaining execute-stage budget as its timeout (never less
than 30 s), replacing the former hard-coded 300 s.
