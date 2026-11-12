# Execution Record: E-1794461599-c7922f92-claude-agent-claude-sonnet-5-5

## Summary

Name failed tests, keep the head and tail, and write the full redacted output to `.holon/review/verification/` when
the Stage 4 pre-push test step fails.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 3.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

- Step 1: confirmed `_bounded_error_context` caps at `2000` characters, `run_cmd` accepts `env`, `.holon/` is ignored.
