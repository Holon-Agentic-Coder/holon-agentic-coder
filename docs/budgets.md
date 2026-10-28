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

| Budget                | Default | Variable                                      | Bounds                                                              |
| --------------------- | ------- | --------------------------------------------- | ------------------------------------------------------------------- |
| Turn                  | 300 s   | `HOLON_TURN_TIMEOUT_SECONDS`                  | One assistant response (see below), not a process.                  |
| Stall                 | 90 s    | `HOLON_TURN_STALL_SECONDS`                    | Silence within a streaming turn; `0` disables it.                   |
| Intent stage          | 10 min  | `HOLON_STAGE_BUDGET_INTENT_MINUTES`           | The intent stage.                                                   |
| Plan stage            | 35 min  | `HOLON_STAGE_BUDGET_PLAN_MINUTES`             | The plan stage, including the resume turn.                          |
| Execute stage         | 45 min  | `HOLON_STAGE_BUDGET_EXECUTE_MINUTES`          | The execute stage, including the executor container's own test run. |
| Review iteration      | 15 min  | `HOLON_STAGE_BUDGET_REVIEW_ITERATION_MINUTES` | Each review-loop iteration.                                         |
| Calibration stage     | 10 min  | `HOLON_STAGE_BUDGET_CALIBRATION_MINUTES`      | The calibration stage.                                              |
| Stage ceiling (exact) | (unset) | `HOLON_STAGE_BUDGET_SECONDS`                  | Replaces every per-stage value above, in seconds.                   |

`HOLON_STAGE_BUDGET_SECONDS` is meant for handing one exact ceiling to a single sandbox role; set it only for that
container, because wherever it is set it applies to every stage. The host does this itself: while `holon flow` runs a
timed stage (intent, plan, execute or calibration; `PipelineEngine.timed_execute_stage`), that stage's timer is the
active host stage timer (`budgets.active_stage_timer`), and every role container started through
`cli.run_docker_container` during the stage (the flow dispatches Stages 1-3 through `cli.dispatch_role_agent`, which
calls it) receives `HOLON_STAGE_BUDGET_SECONDS` set to the stage's remaining time minus
`budgets.CONTAINER_OVERHEAD_AND_HOST_CHECKS_MARGIN_SECONDS` (60 s, at most a tenth of the stage budget), which covers
the container overhead (start-up, teardown and the push after the agent stops) and, in the execute stage, the host's
post-dispatch checks (execution-branch discovery and the empty-diff guard), floored to whole seconds and never less than
1 s. The container's own stage ceiling therefore fires before the host's, and the host ceiling stays the stage budget
itself. The handed-down value replaces a forwarded host value; outside a timed stage (a standalone `holon plan` or
`holon execute`) nothing is added. The review stage is excluded because its ceiling is one review-iteration budget per
iteration, and the review loop starts no role container. Non-numeric, non-finite, zero or negative values, and values
above `budgets.MAX_BUDGET_SECONDS` (7 days, checked after the minute variables are converted to seconds, so `1e308`
minutes cannot overflow to an infinite budget), are ignored with a warning that names the variable, never the value, and
the default is kept (except that `HOLON_TURN_STALL_SECONDS=0` is accepted and disables stall detection); an invalid
`HOLON_STAGE_BUDGET_SECONDS` is ignored, so the per-stage budgets apply. Out-of-range values are rejected, never
clamped, so no `inf`, `nan` or absurd value reaches the timers, the summary lines or the handed-down
`HOLON_STAGE_BUDGET_SECONDS`. The model-envelope integers (`HOLON_LOCAL_CONTEXT_WINDOW`, `HOLON_CONTEXT_RESERVE_TOKENS`)
are rejected the same way above 2147483647, but as a configuration error that stops the run, because a wrong envelope
cannot fall back to a safe default. All of these variables start with `HOLON_`, so the host CLI forwards them into the
sandbox container automatically. `holon doctor` prints the effective values.

> [!NOTE] A turn is a single assistant response (Bean 0139), not a runner process. One `claude -p` or `pi -p` invocation
> performs a whole plan or execution as many responses and legitimately runs for tens of minutes (31.4 min for a real
> planner run), so the runner process as a whole is bounded only by its stage ceiling and the stall window. The
> process's own elapsed time is never compared with the turn budget.

## Turn supervision

Agent turns are started with `start_new_session=True`, so each agent leads its own process group. Reader threads drain
stdout and stderr continuously, so an agent can never block on a full pipe. A turn counts as live while any of these
change:

- streamed stdout or stderr output (partial lines count);
- the size or modification time of the artefact being written (`plans/P-*.md` or `executions/E-*.md`);
- for executor turns, the newest file modification time in the working tree (scanned at most every 15 s, skipping
  `.git`, `.venv` and `node_modules`), because an executor edits many files besides its notes;
- _tool progress_: at least 0.5 s of CPU time consumed between two samples (taken every 10 s, so 5 % of one core) by the
  runner's descendant processes, excluding the agent's own CPU: that of the runner and, for wrapper CLIs, of the agent
  binary the wrapper spawns as its direct child. In `holon/agent-codex`, `codex` is a Node script that spawns the native
  `codex` binary, which runs the model loop and parents every tool, so a direct child named `codex` is treated like the
  runner (only the CPU of the tools it has reaped counts); the list of such names is `WRAPPED_AGENT_PROCESS_NAMES` in
  `turns.py`. Descendants are every process below the runner in the parent tree plus anything left in its process group.
  On Linux each sampled process also carries the CPU of the children it has already reaped (`cutime` and `cstime` in
  `/proc/<pid>/stat`, for the runner and every descendant), so a tool made of many short processes (a shell loop of
  quick commands, `make`, pre-commit hooks) counts through its still-running parent. The process table is still read
  once per sample.

The runners print nothing per response, so a single response is observed through its side effects: each change of the
artefact (or, for executor turns, of the working tree) closes the current response. A tool call is not an assistant
response either: while the runner waits on a tool (`uv run pytest`, `uv sync`), nothing is written and a non-streaming
runner prints nothing, yet the tool's runtime must not be charged to the turn. Tool progress is therefore treated
exactly like an artefact change: it resets the turn clock and the stall clock and closes the current response. The three
budgets are checked as follows:

- **Turn** fires when no artefact or tree change and no tool progress has happened for `HOLON_TURN_TIMEOUT_SECONDS`.
  Streamed output keeps a turn out of the stall window but does not end a response, so a model streaming one degenerate
  response without writing anything is still stopped. Without a watched artefact or tree no response boundary is
  observable and the turn budget is not applied.
- **Stall** fires when nothing at all (no output, no artefact or tree change, no tool progress) has happened for the
  stall window. The window is `HOLON_TURN_STALL_SECONDS` only once the runner has streamed stdout or stderr output in
  the current response (since the last artefact or tree change); otherwise it is
  `max(HOLON_TURN_STALL_SECONDS, HOLON_TURN_TIMEOUT_SECONDS)`.
- **Stage** fires when the stage ceiling is reached, however much progress the process is making.

The stall window only means something for a runner that can signal liveness mid-response. The default runners
(`claude -p` and `pi -p` in text mode) print nothing until they exit, so for them the stall clock and the turn clock are
the same clock; applying the 90 s stall window would make it the real per-response limit and kill a legitimate
local-model response (Bean 0139 sizes the turn budget for one full 16,384-token response at 82 tok/s, about 5 min). The
effective limits are therefore:

| Runner behaviour in the current response  | Watched artefact or tree | Silence limit                       |
| ----------------------------------------- | ------------------------ | ----------------------------------- |
| Silent (non-streaming runner, or prefill) | Yes (plan, execute)      | Turn budget, 300 s (`turn` fires)   |
| Silent                                    | No                       | `max(stall, turn)`, 300 s (`stall`) |
| Has streamed output                       | Either                   | Stall window, 90 s (`stall`)        |
| Streams output but never writes           | Yes                      | Turn budget, 300 s (`turn` fires)   |

A tool call that is still busy when the response clock would otherwise run out is the normal executor path: the prompt
asks the agent to run the verification suite before its final `## Tests Run` write, and this repository's suite takes
about 190 s on a developer host and longer in a container. With tool progress, a 190 s test run inside a turn is not a
breach, while a runner whose descendants are idle (or that has none) is judged exactly as above, so a genuinely idle or
degenerate runner still breaches the turn budget. Limits of the signal:

- a tool that is blocked without using CPU (a `sleep`, a network wait) does not count as progress;
- a busy tool is bounded only by the stage ceiling, so a tool that never finishes is stopped by the stage budget, not
  the turn budget;
- descendants are sampled from `/proc` in Linux containers and from `ps -A -o pid=,ppid=,pgid=,time=` on macOS; when
  neither is available, sampling is skipped and only artefact and tree progress count. No dependency is added;
- `ps` on macOS reports no CPU of reaped children, so there a descendant that starts and exits between two 10 s samples
  is not seen, and a tool made of many sub-second processes may still breach the turn budget;
- on Linux, when a sampled descendant exits and its parent reaps it, the CPU it had already used is counted once more
  through the parent, so a finished tool can register progress one extra time; an idle runner is not affected.

Any stdout or stderr output marks the current response as streaming, including a one-off warning a non-streaming runner
prints at start-up; that response is then held to the 90 s stall window until its first artefact or tree change.

When a budget fires, the supervisor sends `SIGTERM` to the agent's process group, waits a 10 s grace period, then sends
`SIGKILL`. It asserts first that the agent's process group differs from its own; if they were ever equal it signals only
the agent process, so the entrypoint and its salvage path always survive. The process group is also stopped when
supervision itself fails or is interrupted, and when the agent has exited but a descendant still holds its output pipes
open; in that case the group gets `SIGTERM`, then `SIGKILL` if the pipes are still held after a bounded wait, and the
output read so far is returned after a second bounded wait.

## Salvage, resume and the ACT-BUDGET record

1. After a breached turn, whatever the agent already wrote to its artefact is kept on disk (salvage).
2. Exactly one resume turn is attempted, but only when the salvaged artefact is incomplete (not usable, or missing a
   required section) and the stage still has time left: a breach that left a complete artefact needs none. Its prompt
   repeats the task context, states that the file is partial, and lists only the missing sections. It is bounded by both
   the turn budget and what is left of the stage budget; if the stage has no time left, no resume is attempted.
3. If the artefact is usable after the resume (it exists, exceeds the size floor and its metrics table parses), the plan
   stage carries on normally and nothing is recorded. The executor is different: its resume may only touch the execution
   notes, so it can never finish code that was cut off. Any breach of the executor's implementation turn is therefore a
   failure, even when the notes are usable, and continues with step 4. A breach confined to the executor's notes-only
   re-prompt after a clean implementation turn is only logged: the code is complete and is committed.
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
failed execution summary (`status: failure`) and stages the partial notes, but not the code changes, in a commit titled
`execute: E-... failed for plan ...` rather than `completed`.

Host-side stage ceilings in `holon flow` (below) never write an ACT-BUDGET record, so a failure is not recorded twice.

## Stage summary lines

Every stage reports elapsed time against its budget in the form `stage: status (elapsed min / budget min)`, for example
`plan: ok (4.1 min / 35 min)`. Elapsed minutes carry one decimal; whole-minute budgets are printed as integers.

- The planner and executor entrypoints print the line when they finish (`plan: ok ...`, `execute: failed ...`).
- `holon flow` logs it for every stage and prints it in the flow summary. The review stage's budget is one review
  iteration budget per iteration it ran; each iteration also logs `iteration N review: ok (... / 15 min)`. Within an
  iteration, pending GitHub CI checks are re-probed every `CI_REGISTRATION_POLL_SECONDS` (15 s) for at most the rest of
  that iteration's `review_iteration` budget; checks still pending at that deadline count as a CI finding, never a pass.
- A `holon flow` intent, plan or execute stage that returns after its ceiling is marked failed with an error naming the
  budget, which stops the pipeline. All reviewer agent turns of one iteration share that iteration's `review_iteration`
  budget (one stage timer per ensemble), and a reviewer whose turn breaches it is `FAILED`. A review iteration that
  overruns stops the review loop without consensus (an iteration that reaches consensus is kept). Any iteration that is
  not approved stops the loop as well, because no automatic resolver runs, so the review stage normally runs one
  iteration. The review and calibration stages are never downgraded after the fact: a review that reached consensus has
  already posted its approval and must still be calibrated before the merge, and calibration is the last stage. Their
  overrun is logged as a `WARNING` and recorded as `budget_overrun` in the stage payload. Both review checks use the
  same source, so `HOLON_STAGE_BUDGET_SECONDS` replaces the per-iteration budget in the loop and in the stage ceiling
  (that value times the iterations run).

The flow execute stage runs no host-side test suite: the executor container runs and records its own tests within the
handed-down ceiling, and writes the stage's `elapsed_seconds` and `budget_seconds` on its execution summary row (see
[Ledger Schema](ledger_schema.md)). A superseding empty-diff row written by the flow repeats those fields.

## Entropy budget

The entropy budget is the blast-radius limit a plan must respect (see [Safety](safety.md)). It is not a wall-clock
budget, but it lives in the same module so that there is exactly one source of truth for it.

- **One shared default.** `budgets.DEFAULT_ENTROPY_BUDGET` (`15.0`) is used by both the planner and the executor; there
  are no other entropy-budget literals.
- **One resolver.** `budgets.resolve_entropy_budget(plan_row, intent_row)` returns a finite, non-negative
  `entropy_budget` from the plan's `plans.jsonl` row, else from the intent's `intents.jsonl` row, else the shared
  default. Missing, `null`, non-numeric, NaN, infinite or negative values fall through to the next source.
- **Persisted once.** The planner resolves the budget from the intent, shows it to the planning agent
  (`{budget_from_intent}` in the planner template) and writes the same value as `entropy_budget` on the plan row. The
  executor and the flow read it back through the resolver, so historic plan rows without the field fall back to the
  intent's budget.
- **Bounded re-planning.** A plan above its budget that lists no sub-intent list is sent back to Stage 2 at most
  `budgets.MAX_REPLAN_ATTEMPTS` (`2`) times, with a short re-plan request passed to the planner container through
  `HOLON_REPLAN_FEEDBACK` (never written to a ledger). After that, Stage 3 fails with a message naming the entropy, the
  budget and the number of attempts. Under `holon flow`, when the plan row can be read before dispatch, an over-budget
  plan is refused before the executor container starts: that attempt creates no execution branch and appends no
  `executions.jsonl` row. Each rejected plan keeps its `plans.jsonl` row on its plan branch, and those refusals, the
  re-plan count and the final failure are recorded only in the flow's stage results, log and checkpoint
  (`.holon/flow/checkpoint-<slug>.json`; a sub-intent's is keyed on its intent branch instead). When the plan row cannot
  be read before dispatch, the executor refuses the plan itself and the flow reads the `over_budget` row it records on
  an execution branch. The executor's `over_budget` summary row is written only when the executor itself refuses the
  plan, as in that case or a direct `holon execute` run, which then exits with code 3 (`OVER_BUDGET_EXIT_CODE`) only
  when that row is committed and then either pushed or kept local because pushing is disabled, and with code 1 otherwise
  (see
  [Entropy Budget and Decomposition Evaluation](executor/execution_architecture_specification.md#5-entropy-budget-and-decomposition-evaluation)).
  For an intent already at `budgets.MAX_SUB_INTENT_DEPTH`, the re-plan request asks only for a smaller plan, since a
  sub-intent list would be refused there.
- **Bounded nesting.** Child intents created from a sub-intent list carry `sub_intent_depth`; the flow refuses to
  recurse beyond `budgets.MAX_SUB_INTENT_DEPTH` (`2`).
