# Execution Record: E-1792788550-claude-agent-claude-opus-5-5

- Plan: `P-1792788153-claude-agent-claude-opus-5-5`
- Intent: `I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope`
- Agent: `claude-agent` (`claude-opus-5-5`)

## Summary

Implemented Beans 0138 to 0140 in one execution branch (no sub-intent was spawned for Step 5): incremental planner and
executor artefacts with a shared validator, turn and stage budgets with a supervised runner turn, salvage, a single
resume and an `ACT-BUDGET` ledger action, elapsed-against-budget stage summary lines, and an explicit local-LLM model
envelope with a minimal `holon doctor`. Documentation was added in `docs/budgets.md` and extended in the sandbox,
executor and ledger documents.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 1     |
| entropy_actual        | 13.8  |
| impact_actual         | 85.0  |
| cost_actual           | 18.0  |
| learning_value_actual | 5.5   |

## Steps Executed

1. **Baseline.** `uv` was not installed in the sandbox and was installed with `pip install --user uv`. Baseline from the
   workspace root: `673 passed, 2 failed`; ruff check and format clean. Both failures are environmental: the sandbox
   shell exports `CLAUDE_CODE_OAUTH_TOKEN` / `HOLON_AGENT_SETTINGS`, which the `claude` runner turns into a `--settings`
   argument (`test_agent_runner.py::test_standard_runner_cmd[claude]`, and the hermetic guard that re-runs the suite).
   Runner classification: no runner is started in a streaming mode by `agent_runner.py`, so all are treated as buffered;
   liveness therefore also counts artefact growth (and working-tree changes for executor turns).
2. **Incremental plan artefact.** New `artefacts.py` (`ArtefactSpec`, `validate_artefact`, `build_reprompt`,
   `PLAN_SPEC`); planner prompt suffix and `planner.template.md` now carry a write protocol; metric defaults removed;
   one targeted re-prompt; stdout fallback with `WARNING`. The validator accepts 40 of 40 non-stub repository plans.
3. **Incremental executor notes.** `EXECUTION_SPEC`, write protocol in `executor.template.md` (now rendered by the
   executor), truncation tolerance and one targeted re-prompt. Deviation: validation is report-only, and the executor
   does not synthesise a notes file from stdout, because `docs/executor/execution_architecture_specification.md`
   deliberately keeps the ledger authoritative and eight existing tests enforce that the executor writes no
   `executions/` file. The specification was updated to describe agent-written notes.
4. **Budget core.** New `budgets.py` with the bean defaults, env overrides, `StageTimer`, `format_stage_summary` and
   `BudgetBreach`.
5. **Turn supervisor.** New `turns.py` (`run_turn`, `supervise_authoring_turn`, `record_budget_failure`). `ActionRecord`
   gained optional `stage`, `budget_name`, `budget_seconds` and `elapsed_seconds` fields (omitted when unset) and the
   `budget` category, because the dict path of `append_action_record()` drops unknown keys.
6. **Stage ceilings.** `StageResult` carries `elapsed_seconds` and `budget_seconds`; `PipelineEngine` times every stage
   and logs summary lines; the review loop enforces a per-iteration ceiling; the verification `pytest` timeout is the
   execute-stage remainder (floor 30 s). `HOLON_STAGE_BUDGET_SECONDS` is supported but not injected by `cli.py`: the
   per-stage variables are already forwarded and resolve identically inside the container.
7. **Model envelope.** `apply_model_envelope()` in `local_llm.py`; pi's boolean `reasoning` flag is kept and the mode is
   written as `defaultThinkingLevel` in the agent directory's `settings.json` (the pi schema could not be checked
   offline; see follow-ups).
8. **Doctor.** `holon doctor` with envelope lines, budgets and a gateway health probe (loopback or allow-listed only, 2
   s timeout, proxies bypassed). The health schema is not in this repository, so the parser accepts several key
   spellings and falls back to `prefill_cache=unknown`.
9. **Documentation.** `docs/budgets.md` (new), `docs/sandbox/create_plan.md`, `docs/sandbox/execute_plan.md`,
   `docs/ledger_schema.md`, `docs/executor/agent_credentials_requirements.md` and the executor specification.

## Tests Run

- `uv run pytest -q -m "not integration_test and not stress"` from the workspace root: 2 failures, both the baseline
  environmental `claude` runner failures described in step 1; every other test passes. `test_agent_runner.py` passes in
  full with those variables unset.
- New test modules: `test_artefacts.py`, `test_budgets.py`, `test_turn_supervisor.py`; extended: `test_planner.py` (main
  tests rewritten over a real temporary workspace), `test_executor.py`, `test_flow.py`, `test_local_llm.py`,
  `test_cli.py`. All timing tests use fake clocks and sleepers; no process is signalled and no gateway is contacted.
- `uv run ruff check` and `uv run ruff format --check`: clean.
- `npx prettier@3.8.4 --check` on every changed markdown file: clean, with no diff on a second pass.
- `scripts/pre_commit_check.py`: passed. No ledger, `pyproject.toml` or `uv.lock` changes.

## Follow-ups

- Confirm pi's `models.json` and `settings.json` field names (`reasoning`, `defaultThinkingLevel`) against a pi build
  inside the agent image.
- Replace the tolerant health parser with the exact schema once the bean's gateway fixture is in the repository.
- Measure stall false positives on real runner turns: a planner that reads for more than 90 s before its first file
  write is stopped and resumed once. `HOLON_TURN_STALL_SECONDS=0` disables the check.
