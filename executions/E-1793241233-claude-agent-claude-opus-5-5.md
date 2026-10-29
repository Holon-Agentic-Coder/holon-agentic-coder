# Execution Record: E-1793241233-claude-agent-claude-opus-5-5

## Summary

Implementation of plan `P-1793241058-claude-agent-claude-opus-5-5` for intent
`I-1793241043-claude-runner-permission-bypass-and-one-pass-placeholders`. All six steps were carried out on this
execution branch. The claude runner now emits `--allow-dangerously-skip-permissions --permission-mode bypassPermissions`
as CLI parameters while `HOLON_AGENT_SKIP_PERMISSIONS` is truthy (default `true`, evaluated at command-build time),
never through `--settings`. The planner renders its template with a single-pass `render_placeholders`, so
`plans/{plan_id}.md` no longer gains a stray `}`. `uv run task lint`, `uv run task lint-docs` and `uv run task test`
(2,688 passed) all pass; `uv.lock` and the ledgers are unchanged.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.95  |
| entropy_actual        | 4.0   |
| impact_actual         | 90.0  |
| cost_actual           | 13.0  |
| learning_value_actual | 4.0   |

## Steps Executed

1. **Step 1 (runner bypass): done.** Added `skip_permissions_enabled()` to `agent_runner.py` (reads
   `HOLON_AGENT_SKIP_PERMISSIONS`, default `true`, truthy set `1/true/yes/on`, evaluated on each call).
   `AntigravityAgentRunner.compose_cmd` now uses it. `StandardAgentRunner` gained an optional `bypass_flags` keyword,
   prepended to the prefix only when non-empty and enabled. The `claude` registry entry sets
   `--allow-dangerously-skip-permissions --permission-mode bypassPermissions`; the `--settings` mapping is untouched.
2. **Step 2 (one-pass placeholders): done.** Added `render_placeholders(template, values)` to `entrypoint/planner.py`:
   one compiled regex (names `re.escape`d, longest first, negative look-ahead for identifier characters, optional
   closing brace) with a function replacement, so values are inserted literally. The `replacements` loop and its
   `{plan_id` partial key were replaced by a bare-name `values` dict. Follow-up (not changed here):
   `entrypoint/executor.py` `_render` could adopt `render_placeholders` in a later intent.
3. **Step 3 (runner tests): done.** Declared modification: `test_agent_runner.py::test_standard_runner_cmd` now expects
   the claude bypass flags and pops `HOLON_AGENT_SKIP_PERMISSIONS` for hermeticity. New pytest functions cover
   default-on, opt-out (`false`, `0`, `no`, `off`, `FALSE`), valid ordering, coexistence with `HOLON_AGENT_SETTINGS`,
   the `--settings` value staying unchanged and free of bypass text, build-time evaluation, and that pi, gemini,
   opencode and codex never emit the claude flags. No other suite asserted exact claude argv; no further test changes.
4. **Step 4 (placeholder tests and template lint): done.** Added five `render_placeholders` tests to `test_planner.py`
   (plan path, unmatched brace, order independence, literal values with `{agent}`, `\1`, `\g<0>`, `\n`, and untouched
   unknown, prose and longer-name braces). Added `tests/test_prompt_templates.py`, which globs the shipped
   `holon-config/prompts/*.template.md`. The first lint definition (known `{name` followed by anything but `}` or an
   identifier character) flagged the prose groups `{bullets with rule IDs or filenames}` and `{bullets / short text}` in
   `planner.template.md`, which are agent-fill prose, not defects. The lint was narrowed to flag a known `{name` only
   when no `}` closes that brace group on the same line; self-check tests prove it still flags injected `{plan_id`
   samples and ignores prose. No shipped template needed fixing.
5. **Step 5 (docs): done.** `docs/executor/agent_credentials_requirements.md`: new claude command template, a
   permission-bypass bullet, and `HOLON_AGENT_SKIP_PERMISSIONS` in the Tier 2 variable list.
   `apps/sandbox-executor/docs/agy_sandbox_execution_solutions.md` only shows `agy` examples and makes no claim about
   other runners, so it was left unchanged.
6. **Step 6 (verification gate): done.** All three gates pass on the first full run after one lint fix (an over-long
   comment in `agent_runner.py`). `git status` shows no change under `holon-knowledge/ledger/` and no new ledger-like
   file. Environment note: the sandbox had no `uv`, so it was installed with `pip install --user uv`, and the container
   exports `PYTHONPATH=/home/holon/src`, which shadowed the workspace package; every command was run with that variable
   unset (`env -u PYTHONPATH`), never set.

## Tests Run

| command                                                                   | result                               |
| ------------------------------------------------------------------------- | ------------------------------------ |
| `uv run pytest apps/sandbox-executor/tests/test_prompt_templates.py`      | 3 passed (after narrowing the lint)  |
| `uv run pytest` on `test_agent_runner.py`, `test_planner*.py`, lint tests | all passed                           |
| `uv run task lint`                                                        | passed (ruff check, format, lock)    |
| `uv run task lint-docs`                                                   | passed                               |
| `uv run task test`                                                        | 2,688 passed, 8 warnings, 0 failures |
