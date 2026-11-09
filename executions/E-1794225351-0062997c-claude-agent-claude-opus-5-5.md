# Execution Record: E-1794225351-0062997c-claude-agent-claude-opus-5-5

## Summary

Execution of plan `P-1794225025-924685ae-claude-agent-claude-opus-5-5` for intent
`I-1794224634-plan-owned-decomposition-with-plan-branch-children--1-record-the-decomposition-decision-on-the-plan-ro`:
record the decomposition decision on the plan row and make Stage 3 execute only that recorded decision.

In progress.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Move the decomposition rule into `sandbox_executor/decomposition_rule.py`

- Moved `DECISION_*`, the sub-intent list regexes and constants, `DecompositionDecision`, `is_placeholder_item`,
  `parse_sub_intent_list` and `should_decompose` out of `entrypoint/executor.py` into the new neutral module, which
  imports only `sandbox_executor.budgets`. `executor.py` re-exports the public names (`X as X` imports, like the
  redaction re-exports); `OVER_BUDGET_EXIT_CODE` and `_safe_float` stay in the executor. A private `_as_float` mirrors
  `_safe_float` in the new module.
- Deviation: `tests/test_complexity_gate.py` freezes the `(path, rule codes)` multiset of complexity `# noqa`
  annotations and explicitly forbids relocating one ("Refactor the function instead"), so moving
  `parse_sub_intent_list` with its `# noqa: C901, PLR0912, PLR0915` failed the gate. The function was refactored
  instead into a `_SubIntentScan` dataclass (one method per concern: fences, headings, item collection) plus
  `_sub_intent_dicts`, with no suppression at all. The line-level logic and comments are unchanged; the full
  decomposition regression corpus in `tests/test_executor.py` passes without any edit (`293 passed`).
- Added `InvalidPlanDecisionError`, `DecompositionDecision.to_plan_fields()` and `DecompositionDecision.from_plan_row()`
  with a strict shape check of recorded `sub_intents`; `tests/test_decomposition_rule.py` covers the round trip, the
  older-row `None`, corrupt records, the entrypoint-free imports and the re-export identity.
