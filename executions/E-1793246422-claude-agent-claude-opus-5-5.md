# Execution Record: E-1793246422-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1793246292-claude-agent-claude-opus-5-5`: a test-run discipline rule in the planner and executor
templates, a commit-per-step directive in the executor template, the same rule in the testing guide, and tests that pin
the rendered wording.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.9   |
| entropy_actual        | 1.5   |
| impact_actual         | 65.0  |
| cost_actual           | 7.0   |
| learning_value_actual | 2.0   |

## Steps Executed

- Step 1: added a `**Test-Run Discipline:**` block to `holon-config/prompts/planner.template.md` after the entropy
  budget contract; no braces; prettier-formatted. Template test module passes.
- Step 2: replaced the bare-runner directive in `holon-config/prompts/executor.template.md` with affected tests
  (`uv run pytest <paths>`) then one `uv run task test`; added the repetition rule (directive 4) and the recording rule
  (directive 5); added the commit-per-step, never-push item to the Write Protocol and renumbered. `.venv/bin/pytest` no
  longer appears; all original placeholders are intact.
- Step 3: added a `Repeated runs` bullet after the xdist bullet in `docs/testing/testing_guide.md`, with the motivating
  figures (about 30 s per serial run under load against 16.97 s under `-n auto`). The odd fence above it was left alone
  because `lint-docs` passes.
- Step 4: added `rendered_planner`, `rendered_executor` (the real `build_execution_prompt`, no fallback needed) and
  `normalised` helpers plus three tests (one parametrised over both prompts) to
  `apps/sandbox-executor/tests/test_prompt_templates.py`; the four existing tests are unchanged.
- Step 5: one full-suite run plus both lint tasks; diff confined to the four touch-set files and this record.

### Deviations

- Commit-as-you-go was not possible: `git commit` failed with `Author identity unknown` because no git identity is
  configured in the container. Per the plan, the entrypoint (owned by a concurrent change) was not edited and no git
  config was written; the changes are left in the working tree for the entrypoint to commit.
- `uv` was not on `PATH` in the container; it was installed for the user with `pip install --user uv` (no repository
  dependency changed).

## Tests Run

| command                                                              | result                | time    |
| -------------------------------------------------------------------- | --------------------- | ------- |
| `uv run pytest apps/sandbox-executor/tests/test_prompt_templates.py` | 8 passed, 0 failed    | 0.01 s  |
| `uv run task test` (single run, so also the median)                  | 2693 passed, 0 failed | 29.03 s |
| `uv run task lint`                                                   | passed                | n/a     |
| `uv run task lint-docs`                                              | passed                | n/a     |

No repeated runs: this intent does not measure performance.
