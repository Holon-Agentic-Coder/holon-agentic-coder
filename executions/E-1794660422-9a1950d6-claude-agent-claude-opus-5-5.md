# Execution Record: E-1794660422-9a1950d6-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1794660305-d5b6233a-claude-agent-claude-opus-5-5` for intent `I-1794660290-probehttp`:
`_wait_for_proxy` becomes an HTTP-level readiness probe that sends an absolute-form proxy request for
`http://holon-proxy-readiness.invalid/` and declares the proxy ready only once `HTTP/` response bytes come back.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.9   |
| entropy_actual        | 3.5   |
| impact_actual         | 70.0  |
| cost_actual           | 8.0   |
| learning_value_actual | 2.0   |

## Steps Executed

### Step 1: Turn `_wait_for_proxy` into an HTTP-level readiness probe

- Added `PROXY_PROBE_ATTEMPT_TIMEOUT_SECONDS = 2.0`, `PROXY_READINESS_HOST` (`holon-proxy-readiness.invalid`) and
  `PROXY_READINESS_REQUEST` (absolute-form `GET`, `Connection: close`, CRLF line endings) next to the proxy constants.
- New helper `_proxy_answers_http` connects, sends the request, reads until at least 5 bytes or EOF (so a fragmented
  `HTT` + `P/` is not misread), and returns whether the reply starts with `HTTP/`; any `OSError` (refused, reset,
  timeout) is "not ready".
- `_wait_for_proxy` keeps its signature; each attempt's timeout is `min(2.0, max(remaining, 0.05))` and the sleep is
  bounded by the remaining time, so the 3 s attach path cannot overshoot by more than a fraction of a second.
- Sidecar failure message now reads "never answered an HTTP proxy request"; the existing test at line 330 asserted the
  old wording, so its assertion was updated in the same commit (in scope).
- `PROXY_CONNECT_TIMEOUT_SECONDS` unchanged (still used by the unrelated port check).

### Step 2: Add hermetic loopback-listener tests for the probe

- Added `_LoopbackListener` (daemon accept thread on `127.0.0.1:0`, records each request, delegates the answer to a
  handler given the 1-based connection index) and a `loopback_listener` fixture that shortens
  `PROXY_POLL_INTERVAL_SECONDS` to `0.02` and `PROXY_PROBE_ATTEMPT_TIMEOUT_SECONDS` to `0.2` and closes every listener.
- Tests: silent close is retried until the deadline (`False`, more than one connection, elapsed `>= 0.4` s); an
  immediate 502 is ready and pins the `GET http://holon-proxy-readiness.invalid/ HTTP/1.1` request form; a listener that
  answers only from its third connection (count-driven, not wall-clock) becomes ready; parametrised silent-hold and
  non-HTTP listeners are not ready by the deadline.
- Slowest new test `0.40` s; all five finish well under 1 s.

### Step 3: Validate with the full suite, lint and lint-docs

- Full suite, `uv run task lint` and the scoped files all pass. `uv run task lint-docs` fails on 39 Markdown files that
  already fail on the base commit (`.agents/holon_flow.md`, `docs/optimisation/token_reduction_measurement_plan.md`,
  `holon-config/metrics/README.md`, two older plans and 34 `*_calibration.md` reports); they are outside the intent's
  scope and were left untouched. This execution record is prettier-clean, and no other plan or execution record from
  this run is flagged.
- `git diff --stat main...HEAD` touches only `cli/__init__.py`, `tests/test_token_reduction.py` and this record (plus
  the plan file committed by the planner); ledger changes are only the flow's own intent and plan appends; `uv.lock`
  unchanged.

## Tests Run

| command                                                                                          | result                                  |
| ------------------------------------------------------------------------------------------------ | --------------------------------------- |
| `uv run pytest apps/sandbox-executor/tests/test_token_reduction.py -q -n auto` (after Step 1)    | 222 passed in 2.15s                     |
| `uv run pytest apps/sandbox-executor/tests/test_token_reduction.py -q -n auto -k wait_for_proxy` | 5 passed in 2.39s                       |
| `uv run pytest apps/sandbox-executor/tests/test_token_reduction.py -q -n auto --dist worksteal`  | 227 passed in 2.74s                     |
| `uv run task test`                                                                               | 4019 passed, 1 skipped in 23.95s        |
| `uv run task lint`                                                                               | pass (ruff, format, lock, budgets)      |
| `uv run task lint-docs`                                                                          | fail: 39 pre-existing files (see above) |

Each command ran once; no timing measurement was required, so no repeated runs and no median apply.
