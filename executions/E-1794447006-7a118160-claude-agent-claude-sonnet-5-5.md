# Execution Record: E-1794447006-7a118160-claude-agent-claude-sonnet-5-5

## Summary

Implemented plan `P-1794446908-d2eae989-claude-agent-claude-sonnet-5-5` for intent `I-1794446637-creds`. The claude
runner now discovers its credential from `~/.holon/secrets/claude-oauth-token` (or an explicit `CLAUDE_CODE_OAUTH_TOKEN`
/ `ANTHROPIC_API_KEY`), exports it by detected type, and never consults `HOLON_AGENT_KEY`. The pre-flight probe and hint
were reworked, the whole `--settings` value is redacted in echoes, and tests and docs were updated.

Scope note: the intent's scope list omits `claude_credential.py` (new), `cli/__init__.py`,
`entrypoint/role_dispatcher.sh` and `agent_defaults.py`. They were touched as the minimum needed for the acceptance
criteria, as the plan anticipated.

Finding: `HOLON_ECHO_PAD` exists nowhere in `src/`, `tests/`, `entrypoint/` or `docs/` (only in the intent text and the
plan), so there was nothing to remove. `_apply_generic_token` is likewise only a stale comment in a test.

One suite failure, `tests/flow/stages/test_children.py::...::test_child_payload_fallbacks_and_copies`, fails identically
without these changes (checked with `git stash`) and is unrelated.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.95  |
| entropy_actual        | 7.0   |
| impact_actual         | 70.0  |
| cost_actual           | 12.0  |
| learning_value_actual | 5.0   |

## Steps Executed

1. **Step 1, survey.** The claude mapping lives in the `claude)` branch of `entrypoint/role_dispatcher.sh`; host env
   assembly is in `cli/__init__.py` (`run_docker_container`); the probe credential check is in `preflight.py`;
   `agent_defaults._detect_api_key_present` and the bundle ingestion in `agent_runner.py` also fed `HOLON_AGENT_KEY` to
   claude. No `HOLON_ECHO_PAD` implementation exists.
2. **Step 2, discovery and wiring.** New `claude_credential.py` (path, trimmed read, mode warning, classifier,
   resolver). `cli/__init__.py` drops `HOLON_AGENT_KEY` for claude and forwards the resolved pair through the env-file.
   Dispatcher no longer maps or ingests a bundle key for claude. `ClaudeAgentRunner.required_keys` is now the two claude
   variables with a claude-specific error. Both names added to `DEFAULT_ENV_LITERAL_NAMES`.
3. **Step 3, pre-flight.** Claude-aware credential check, probe env, and a claude `not_logged_in` hint listing the
   secrets file, then `CLAUDE_CODE_OAUTH_TOKEN`, then `ANTHROPIC_API_KEY`, with `claude setup-token` in the output. Both
   variables added to `_SENSITIVE_ENV_VARS`.
4. **Step 4, redaction.** `redaction.py` replaces the whole `--settings` value with `<redacted>` (separate, `=`-joined
   and `sh -c` forms). No echo pad to remove.
5. **Step 5, tests and docs.** New `tests/test_claude_credential.py`; additions to `test_redaction.py` and
   `test_cli.py`; existing claude fixtures in `test_preflight.py`, `test_cli.py`, `test_agent_runner.py` and
   `test_planner.py` moved from `HOLON_AGENT_KEY` to the claude variables. Executor and planner credential docs updated.

## Tests Run

- `uv run pytest apps/sandbox-executor/tests/test_claude_credential.py apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_redaction.py apps/sandbox-executor/tests/test_preflight.py apps/sandbox-executor/tests/test_agent_runner.py apps/sandbox-executor/tests/test_planner.py`:
  all passed.
- `uv run task test` (once): `1 failed, 3582 passed` in `32.97s`; the one failure is the pre-existing
  `test_child_payload_fallbacks_and_copies`.
- `uv run task lint`: passed. `uv run task lint-docs`: passed.
