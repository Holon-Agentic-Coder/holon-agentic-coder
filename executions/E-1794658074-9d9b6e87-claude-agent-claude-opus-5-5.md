# Execution Record: E-1794658074-9d9b6e87-claude-agent-claude-opus-5-5

## Summary

In progress: fixing the Stage 5 `git diff` exit 128 "ambiguous argument" on nested sub-intent branch names.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

1. Step 1 (reproduction): added `apps/sandbox-executor/tests/flow/test_calibrate_nested_refs.py`, a hermetic nested
   layout (root intent, `--1-child` child intent, child plan and child execution branches) against a local bare origin.
   Of the three cases, only the plan branch absent everywhere reproduces the reported failure through
   `generate_calibration`: `RuntimeError` "Git diff failed ... exit code 128: fatal: ambiguous argument". The
   remote-only case resolves through `resolve_git_ref`'s fetch fallback, and the local case is the control. So the
   nested `/_` name alone does not fail; an unresolvable plan ref, passed raw to `git diff`, does. The absent case is
   marked `xfail(strict=True)` until Step 3.
