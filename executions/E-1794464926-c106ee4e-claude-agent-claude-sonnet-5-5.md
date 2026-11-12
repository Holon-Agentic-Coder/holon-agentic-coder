# Execution Record: E-1794464926-c106ee4e-claude-agent-claude-sonnet-5-5

## Summary

Make the suite-wide `no_ambient_credentials` fixture clear every credential variable the code reads, add a child-pytest
guard, and document the rule. Test code and docs only.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.9   |
| entropy_actual        | 3.5   |
| impact_actual         | 60.0  |
| cost_actual           | 8.0   |
| learning_value_actual | 6.0   |

## Steps Executed

1. Reproduced four failures with `CLAUDE_CODE_OAUTH_TOKEN=dummy`; credential names taken from `claude_credential.py` and
   `agent_defaults.py`.
2. Added `tests/credential_env.py` (`CREDENTIAL_ENV_NAMES`) and extended `_AMBIENT_CREDENTIAL_NAMES` in
   `tests/conftest.py`.
3. Added `tests/test_credential_hermeticity.py` (child pytest per name, probe, coverage check). Dropping the names from
   the fixture made 10 guard tests fail; restored.
4. Ran the full suite with and without dummy variables.
5. Documented the rule in `apps/sandbox-executor/docs/hermetic_testing.md`. `testing_guide.md` does not list the
   isolation fixtures, so it is unchanged.

## Tests Run

- `CLAUDE_CODE_OAUTH_TOKEN=dummy uv run pytest` on `test_cli.py`, `test_claude_credential.py`, `test_preflight.py` and
  `test_agent_defaults.py`: 673 passed in 3.02s.
- `uv run pytest tests/test_credential_hermeticity.py`: 10 passed, 1 skipped in 3.51s.
- `uv run task test` with dummy variables set: 1 failed, 3637 passed, 1 skipped in 34.36s.
- `uv run task test` without them: 1 failed, 3637 passed, 1 skipped in 33.83s.
- The one failure, `test_children.py::test_child_payload_fallbacks_and_copies`, also fails on base commit `aa60be9`; it
  is unrelated to credentials and left untouched.
- `uv run task lint` and `uv run task lint-docs`: passed after one prettier pass.
