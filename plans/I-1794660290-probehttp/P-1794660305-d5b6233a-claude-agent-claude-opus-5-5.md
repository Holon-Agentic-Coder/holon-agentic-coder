# Plan for I-1794660290-probehttp

- **Plan ID:** P-1794660305-d5b6233a-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-14T12:45:06.896Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A
- **Exploration stance:** conservative. The defect, the fix and the test shape are fully specified by the intent and
  confined to one function plus one test file; there is little to learn by exploring and much to lose by widening scope.
- **Safety priority level:** standard
- **Priority Justification:** The change touches only a host-side readiness probe that talks to 127.0.0.1 (or the
  validated `HOLON_PROXY_URL` host already probed today). No ledger, governance, estimator or git-flow rule is affected.
  `holon-config/world/constraints.md` section 3 (append-only ledgers) and the hermetic-test guard
  (`tests/network_guard.py`, loopback allowed) are the only constraints in play and both are easy to respect.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Current state was inspected during planning: `_wait_for_proxy` at
  `apps/sandbox-executor/src/sandbox_executor/cli/__init__.py:632` is a 12-line TCP-connect loop using
  `PROXY_CONNECT_TIMEOUT_SECONDS = 0.5` and `PROXY_POLL_INTERVAL_SECONDS = 0.5` (lines 141 to 142), called from the
  sidecar path (line 864, default 15 s deadline) and the attach path (line 936, `PROXY_ATTACH_TIMEOUT_SECONDS = 3.0`,
  wrapped in `except (OSError, TimeoutError)`). Every existing test monkeypatches `_wait_for_proxy`, so no current test
  depends on its internals. Nothing remains unknown that an exploratory step would resolve.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 4.0   |
| impact_pred         | 70.0  |
| cost_pred           | 10.0  |
| learning_value_pred | 3.0   |
| ev_pred             | 51.9  |

`EV = 0.88 * 70.0 + 0.5 * 3.0 - 0.3 * 4.0 - 10.0 = 61.6 + 1.5 - 1.2 - 10.0 = 51.9`

## Plan Description & Strategy

Replace the bare TCP-connect readiness check with an HTTP-level probe inside the same module that already owns the proxy
lifecycle (`cli/__init__.py`), keeping the public signature `_wait_for_proxy(host, port, timeout)` and both callers
untouched. A small private helper performs one attempt (connect, send a minimal absolute-form proxy request for
`http://holon-proxy-readiness.invalid/`, read until the first bytes arrive, answer whether they begin with `HTTP/`); the
loop in `_wait_for_proxy` retries that helper until the existing deadline. Then add hermetic loopback-listener tests to
`tests/test_token_reduction.py`, run the affected tests, then the full suite once, then lint and lint-docs.

Touch set (module granularity): `apps/sandbox-executor/src/sandbox_executor/cli/__init__.py`,
`apps/sandbox-executor/tests/test_token_reduction.py`, plus this plan file. No new modules: the helper is about 15 lines
and belongs with the proxy code it serves; extracting a new module for it would widen the change beyond the intent's
"keep the change small" and its declared scope. (`cli/__init__.py` is already 1771 lines and is not covered by
`flow_module_budgets.json`, which budgets `flow/` only; the net growth is about 20 lines.)

---

## Step 1: Turn `_wait_for_proxy` into an HTTP-level readiness probe

- **Sub‑intent recommendation:** NO
- **Reasoning:** About 20 lines in one function of one module, with a clear contract and existing callers that already
  handle a `False` result; splitting would add pipeline overhead with no risk reduction.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Declare the proxy ready only when it returns bytes starting with `HTTP/` to a proxy-style request;
  treat accept-then-close, reset, timeout and non-HTTP bytes as "not ready yet" and retry until the deadline.
- **Git branch:** `I-1794660290-probehttp/P-1794660305-d5b6233a-claude-agent-claude-opus-5-5/_` (execution branch
  created off it by the executor)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add module-level constants next to the existing proxy constants (around line 138 to 142):
   `PROXY_READINESS_HOST = "holon-proxy-readiness.invalid"` and `PROXY_READINESS_REQUEST` as bytes for
   `GET http://holon-proxy-readiness.invalid/ HTTP/1.1` with `Host: holon-proxy-readiness.invalid`, `Connection: close`
   and the terminating blank line (CRLF line endings). Absolute-form GET (not CONNECT) is used because mitmproxy in
   regular mode answers it directly, including with a 502 when the `.invalid` upstream cannot be resolved; a proxy that
   demands authentication answers 407, which is also HTTP and therefore also "ready" (correct: it is serving).
2. Add a private helper, for example `_proxy_answers_http(host, port, timeout) -> bool`, that: opens
   `socket.create_connection((host, port), timeout=...)` in a `with` block; calls `sendall(PROXY_READINESS_REQUEST)`;
   calls `recv` once for a small buffer (for example 16 bytes is enough; use 64); returns `data.startswith(b"HTTP/")`.
   An empty `recv` (peer closed) returns False. `OSError` (refused, reset, broken pipe, `socket.timeout`, which is
   `TimeoutError`, a subclass of `OSError`) is caught inside the helper and returns False. Note on partial reads: if the
   first `recv` returns fewer than 5 bytes, loop `recv` until 5 bytes or EOF so a fragmented `HTT` + `P/` response is
   not misread as "not ready"; keep this a tiny bounded loop.
3. Per-attempt timeout: use `min(PROXY_PROBE_ATTEMPT_TIMEOUT_SECONDS, max(remaining, 0.05))`, where `remaining` is the
   time left before the deadline and the new constant `PROXY_PROBE_ATTEMPT_TIMEOUT_SECONDS` is 2.0 (mitmproxy may need a
   short DNS round trip for the `.invalid` lookup before it emits the 502, so the old 0.5 s connect timeout is too tight
   for the read). This keeps a hanging listener from overshooting the caller's deadline by more than a fraction of a
   second, which matters for the 3 s attach path. Keep `PROXY_CONNECT_TIMEOUT_SECONDS` unchanged because line 760 uses
   it for an unrelated port check.
4. Rewrite the loop in `_wait_for_proxy`: compute the deadline as today; on each iteration call the helper with the
   bounded per-attempt timeout; return True when it does; otherwise, if `time.monotonic() >= deadline` return False,
   else `time.sleep(min(PROXY_POLL_INTERVAL_SECONDS, remaining))` and retry. The function never raises for socket
   errors, so the attach path's `except (OSError, TimeoutError)` stays a harmless safety net and needs no change.
5. Update the docstring (Australian English) to say the probe sends a proxy request for an unresolvable host and waits
   for an HTTP status line, because Docker's userland proxy accepts TCP on the published port before mitmproxy inside
   has bound its listener. Update the sidecar failure message at line 867 from "never accepted connections" to "never
   answered an HTTP proxy request" so the log matches the new semantics (wording only; the existing test at line 321
   asserts only that a `RuntimeError` is raised, which the executor should confirm before editing).
6. Keep complexity well inside ruff limits: helper and loop each have at most three branches; no `# noqa`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (the tests in Step 2 verify this behaviour), but `p_success_pred` is well above 0.6.

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` sandboxing (probe only targets hosts already probed today: 127.0.0.1 or the
  validated `HOLON_PROXY_URL` host); `holon-config/world/constraints.md` section 1 (commit only on the plan or execution
  branch).
- **Potential failure modes for this step:**
  - The real holon-coherence proxy on the attach path does not speak HTTP proxy semantics and never returns `HTTP/`,
    turning a formerly working attach into direct-egress fallback. Mitigation: any HTTP status (502, 400, 407) counts; a
    proxy that cannot answer any HTTP request is not usable by `HTTP_PROXY` clients anyway.
  - A per-attempt read that blocks past the 3 s attach deadline. Mitigation: per-attempt timeout bounded by remaining
    time.
  - Logging the request target leaks nothing secret: the probe host is a fixed `.invalid` name and no credentials are
    sent.
- **Guardrails and early‑abort checks:** Do not change the signature or either call site's arguments; do not touch any
  other module; if the change grows beyond about 40 net lines, stop and re-check scope.

### Success & Discard Criteria

- **Success:** `_wait_for_proxy` returns True only after `HTTP/` bytes are read; accept-then-close and silent listeners
  are retried until the deadline; `uv run ruff check` and `uv run ruff format --check` are clean for the module.
- **Discard:** If mitmproxy semantics turn out to require CONNECT or another request form (evidence from the tests or
  docs), switch the request form once; if the change needs more than one module, stop and re-plan.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 2.0   |
| impact_pred         | 50.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 42.4  |

### Step Metrics Rationale

`EV = 0.92 * 50.0 + 0.5 * 2.0 - 0.3 * 2.0 - 4.0 = 46.0 + 1.0 - 0.6 - 4.0 = 42.4`. Single function, fully specified
behaviour, no new dependencies (`socket` and `time` are already imported). The small residual risk is the partial-read
and deadline-bounding detail.

---

## Step 2: Add hermetic loopback-listener tests for the probe

- **Sub‑intent recommendation:** NO
- **Reasoning:** Three to five short tests in the existing token-reduction test file; low risk, but timing-based tests
  need care to stay fast and non-flaky.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the acceptance criteria with real loopback sockets and no Docker.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add a small test helper (in `tests/test_token_reduction.py`, near the existing `_wait_for_proxy` users) that binds a
   `socket` on `("127.0.0.1", 0)`, listens, and runs an accept loop in a daemon `threading.Thread`. It takes a
   per-connection handler callable and counts accepted connections; it is closed in a fixture teardown or a
   `try/finally` so no thread or port leaks. Loopback is allowed by `tests/network_guard.py`, so the suite-wide guard
   stays in force.
2. In each test, `monkeypatch.setattr(cli, "PROXY_POLL_INTERVAL_SECONDS", 0.02)` (and the per-attempt timeout constant
   to about 0.2) so retries are fast and the suite stays quick.
3. Test "silent close is retried until the deadline": the handler reads the request then closes without sending. Call
   `_wait_for_proxy("127.0.0.1", port, timeout=0.4)`; assert it returns False, that more than one connection was
   accepted (proving retries), and that elapsed time is at least the timeout and well under a generous ceiling (for
   example 3 s).
4. Test "immediate 502 is ready": the handler reads the request and sends `HTTP/1.1 502 Bad Gateway` with
   `Content-Length: 0`. Assert True with a 2 s timeout and that the first accepted request line starts with
   `GET http://holon-proxy-readiness.invalid/` (pins the proxy-style request form).
5. Test "answers only after a delay becomes ready": the handler closes silently for connections arriving before a start
   time plus about 0.2 s (or for the first two connections, which is deterministic and preferable), then answers 502.
   Assert True within a 3 s timeout and that at least three connections were accepted.
6. Optional, cheap and valuable: a parametrised case where the listener accepts and stays silent (never sends, never
   closes) returns False by the deadline, proving the per-attempt read timeout is bounded; and a case where the listener
   sends non-HTTP bytes is not ready. Add at most these two; keep the total under about 80 lines.
7. Run the affected tests: `uv run pytest apps/sandbox-executor/tests/test_token_reduction.py` (or from the
   `apps/sandbox-executor` directory with `uv run pytest tests/test_token_reduction.py`, matching how the repository
   invokes it).

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (acceptance criterion 2), with `p_success_pred` above 0.6.

### Safety & Constraint Considerations

- **Relevant rules:** hermetic testing (`apps/sandbox-executor/docs/hermetic_testing.md`, `tests/network_guard.py`);
  test-run discipline (no repeated runs: the intent does not ask to measure timing).
- **Potential failure modes for this step:**
  - Flakiness under `-n auto` CPU load if timeouts are too tight. Mitigation: count-based delay (first N connections
    silent) rather than wall-clock, and generous upper bounds on elapsed time.
  - Leaked listener threads or sockets. Mitigation: daemon threads, explicit close in teardown.
- **Guardrails and early‑abort checks:** Each new test must finish in under about 1 s; if any test is slower or
  intermittently fails locally, rework it to be count-driven before running the full suite.

### Success & Discard Criteria

- **Success:** New tests pass and existing tests in the file are unaffected.
- **Discard:** If a deterministic, fast form of the delayed-answer test cannot be found after two attempts, keep the
  count-based version and document it; never add sleeps longer than about 0.5 s.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.2   |
| impact_pred         | 15.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 10.0  |

### Step Metrics Rationale

`EV = 0.92 * 15.0 + 0.5 * 1.0 - 0.3 * 1.2 - 3.5 = 13.8 + 0.5 - 0.36 - 3.5 = 10.44`, reported as 10.0 for rounding.
Socket-listener tests are routine; the only real risk is timing flakiness, which the count-based design removes.

---

## Step 3: Validate with the full suite, lint and lint-docs

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mandatory gate with no design decisions.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm acceptance criterion 3 and that nothing else depended on the old TCP-only behaviour.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run the full suite once with `uv run task test`.
2. Run `uv run task lint` (ruff check, ruff format check, lock check, flow module budgets) and `uv run task lint-docs`
   (prettier on Markdown, which also covers this plan file).
3. If ruff format rewrites anything, apply `uv run ruff format` to the two touched files only and re-run lint.
4. Confirm `git status` shows changes only to the two scoped files (plus the execution report the executor writes) and
   no edits to `holon-knowledge/ledger/*.jsonl` other than the flow's own appends.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (final gate)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 3 (append-only ledgers); merge to `main` stays with
  the human maintainer.
- **Potential failure modes for this step:** an unrelated pre-existing failure in the full suite; prettier complaints on
  Markdown.
- **Guardrails and early‑abort checks:** If a failure is unrelated to the touched files, record it in the execution
  report rather than fixing it outside scope.

### Success & Discard Criteria

- **Success:** `uv run task test`, `uv run task lint` and `uv run task lint-docs` all pass.
- **Discard:** A failure that would require touching files outside the scope stops execution and is reported.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.8   |
| impact_pred         | 5.0   |
| cost_pred           | 2.5   |
| learning_value_pred | 0.0   |
| ev_pred             | 2.0   |

### Step Metrics Rationale

`EV = 0.95 * 5.0 + 0 - 0.3 * 0.8 - 2.5 = 4.75 - 0.24 - 2.5 = 2.01`. Pure verification; cost is the full-suite run time.

---

### Strategy Rationale

The intent is accepted as is: it names a real race (Docker's userland proxy accepts TCP before mitmproxy binds), a
precise fix, and a tight two-file scope, and its EV is clearly positive. Reframing to, for example, polling
`docker logs` for mitmproxy's "listening" line, or probing from inside the per-run network, would be more complex and
less portable than an HTTP-level probe from the host; an HTTP answer is the very property the role container needs.

Aggregation: overall `p_success = 0.92 * 0.92 * 0.95 ≈ 0.80` if fully independent, but Steps 2 and 3 failures are mostly
recoverable within the same execution (fix and re-run), so the overall figure is set by judgement at 0.88, with Step 1
(the probe semantics and deadline bounding) as the bottleneck. Entropy is the sum of steps (`2.0 + 1.2 + 0.8 = 4.0`).
Cost is the sum (`4.0 + 3.5 + 2.5 = 10.0`). Impact is 70: the fix removes a first-stage failure from every token-reduced
flow, which is higher than the sum of per-step impacts because the value is only realised when all three steps land.
Learning value is modest (3.0): it confirms how mitmproxy answers readiness probes and adds a reusable loopback-listener
test pattern. `EV = 0.88 * 70.0 + 0.5 * 3.0 - 0.3 * 4.0 - 10.0 = 51.9`.

Strong assumption: mitmproxy (sidecar) and the external holon-coherence proxy both answer an absolute-form GET with some
HTTP status line, as the intent states. This is checked only by unit tests with fake listeners, not against a live
container, because the tests must stay hermetic.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 1: work only on the plan and execution branches; no commits to `main`.
  - `holon-config/world/constraints.md` section 3: keep exactly the three append-only ledgers; no edits to history.
  - `docs/safety.md` "Human review is the final gate": merging the root intent stays with the human maintainer.
  - Hermetic test policy (`tests/network_guard.py`): loopback only, no Docker, no external network.
- **Potential violations or edge cases:**
  - A probe request that reached a real upstream; avoided by the reserved `.invalid` TLD (RFC 6761), which never
    resolves.
  - Behaviour change on the attach path for a proxy that accepts TCP but does not speak HTTP; such a proxy was already
    unusable, so falling back to direct egress is the correct, logged outcome.
- **Mitigations built into the plan:** per-attempt timeouts bounded by the remaining deadline; count-driven tests; scope
  guard on touched files; no new dependencies.
- **Residual risk accepted (and why):** The real mitmproxy response is not exercised in CI (would need Docker); accepted
  because the intent explicitly requires hermetic tests and the documented mitmproxy behaviour (502 for an unresolvable
  upstream) is stable.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 4.0
- **Budget Compliance:** The strategy fits within budget, so it runs as one plan with no sub-intent list.
