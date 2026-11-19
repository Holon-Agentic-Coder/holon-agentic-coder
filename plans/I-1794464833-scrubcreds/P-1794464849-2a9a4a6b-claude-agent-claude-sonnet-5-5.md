# Plan for I-1794464833-scrubcreds

- **Plan ID:** P-1794464849-2a9a4a6b-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-12T06:27:30.872Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None.
- **Exploration stance:** conservative. The fix is a small, well-understood test-isolation change; the only exploratory
  element is the child-pytest guard, which is cheap.
- **Safety priority level:** elevated
- **Priority Justification:** The change touches credential handling in tests. The guard must use only dummy values, and
  nothing real may be logged or written. No production code may change (`docs/safety.md`, `holon-config/world/`).

## Exploration

- **Proportion of steps that are exploratory:** 0.2
- **Justification:** One step (the child-pytest guard) has a modest design choice, namely how to run a child suite
  without recursion or xdist interference. The rest is mechanical.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 3.5   |
| impact_pred         | 60.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 47.9  |

### Strategy Rationale

The failing tests share one cause: `no_ambient_credentials` in `apps/sandbox-executor/tests/conftest.py` clears
`GITHUB_TOKEN`, `GH_TOKEN` and `HOLON_*`, but not the Claude credential variables. The code under test reads
`CLAUDE_CREDENTIAL_ENV_NAMES` (`CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`) in `cli/__init__.py`, `preflight.py` and
`agent_runner.py`. Runner detection in `agent_defaults.py` also reads `AGY_USER_TOKEN`, `GOOGLE_API_KEY`,
`GEMINI_API_KEY`, `PI_API_KEY`, `OPENAI_API_KEY`, `CODEX_API_KEY` and `OPENCODE_API_KEY`. The plan defines one test-side
tuple of credential names, built from the production constant plus the runner keys, so a new production name is picked
up automatically. The fixture clears that tuple, and the guard proves it from a real environment.

Overall metrics: the steps are sequential and the bottleneck is Step 2 (the guard), which carries the most design risk.
`p_success` is about the product of the step probabilities, `0.99 * 0.92 * 0.97 * 0.98 = 0.87`, raised to 0.90 because
the steps are strongly correlated and mostly mechanical. Entropy is the sum of the step entropies
(`0.6 + 0.6 + 1.4 + 0.6 + 0.3 = 3.5`). Cost and impact are qualitative judgements.
`EV = 0.90 * 60.0 + 0.5 * 6.0 - 0.3 * 3.5 - 8.0 = 54.0 + 3.0 - 1.05 - 8.0 = 47.95`. The headline table rounds this to
47.9.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:** `docs/safety.md` (credential handling, no secrets in logs);
  `holon-config/world/ruleset.md` (append-only ledgers, three ledgers only, scope limited to the intent's paths);
  `docs/testing/testing_guide.md` (tests only via `uv run pytest`).
- **Potential violations or edge cases:** A child pytest may recurse into the hermetic guard or the stress canary. An
  xdist child may inherit `-n auto`. A guard that clears the variables in its own body proves nothing, because the
  fixture has already run. The ledger and knowledge-index guards could trip if the child writes to the checkout.
- **Mitigations built into the plan:** The child runs a narrow selection with `-p no:xdist`, `-p no:cacheprovider` and
  `HOLON_CHILD_SUITE_ACTIVE=1`, with `--basetemp` under `tmp_path`. Only dummy values are used, such as
  `sk-ant-oat-dummy-not-real`. Production files under `src/` are untouched, which a `git diff --stat` check confirms.
- **Residual risk accepted (and why):** The child pytest adds a few seconds to the suite. That is acceptable for a
  regression guard on a halting failure.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 3.5
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

Work in five small steps on one branch with no sub-intents: reproduce, scrub, guard, verify, document. Only
`apps/sandbox-executor/tests/` and `docs/` (plus `apps/sandbox-executor/docs/hermetic_testing.md`, the hermeticity doc
named in the repository layout) change. Use Australian English in comments and docs.

---

## Step 1: Reproduce the four failures and enumerate the credential variables

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only reproduction of a known failure, cheap and low risk.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm the exact failing tests and list every environment variable the code under test reads as a
  credential.
- **Git branch:** I-1794464833-scrubcreds
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Run the four named tests in `tests/test_cli.py` with `CLAUDE_CODE_OAUTH_TOKEN=dummy` via `uv run pytest <paths>` and
  confirm four failures. Run again without it and confirm four passes.
- Read `claude_credential.py` (`CLAUDE_CREDENTIAL_ENV_NAMES`, `read_secrets_file`), `agent_defaults.py`
  (`_detect_api_key_present` runner keys), `preflight.py` and `cli/__init__.py` around the credential loops. Record the
  full variable list.
- Check whether `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL` or `HOLON_*` secrets are consulted anywhere. `HOLON_*` is
  already cleared.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` (credential handling)
- **Potential failure modes for this step:** Dummy values are echoed in output; the list of names misses one.
- **Guardrails and early‑abort checks:** Use dummy values only. Cross-check the names by grepping `src/` for `environ`
  and `getenv`.

### Success & Discard Criteria

- **Success:** Four failures reproduced and a complete variable list recorded.
- **Discard:** The failures do not reproduce. Then re-diagnose before any edit.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.6   |
| impact_pred         | 10.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 10.4  |

### Step Metrics Rationale

`EV = 0.99 * 10.0 + 0.5 * 3.0 - 0.3 * 0.6 - 1.0 = 9.9 + 1.5 - 0.18 - 1.0 = 10.22`, about 10.4 after rounding the inputs.
It is a cheap gate that makes the later list trustworthy.

---

## Step 2: Scrub credential variables in the isolation fixture

- **Sub‑intent recommendation:** NO
- **Reasoning:** A small edit in one test file with a clear acceptance test.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make `no_ambient_credentials` clear every credential variable the code reads.
- **Git branch:** I-1794464833-scrubcreds
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add a focused helper module, `tests/credential_env.py`, exposing `CREDENTIAL_ENV_NAMES`. It is the Claude names
  imported from `sandbox_executor.claude_credential.CLAUDE_CREDENTIAL_ENV_NAMES` plus the runner keys from Step 1. Keep
  `conftest.py` under about 400 lines.
- In `conftest.py`, extend `_AMBIENT_CREDENTIAL_NAMES` to include the tuple, and update the fixture docstring and the
  module docstring.
- Do not clear the names in `_HARNESS_NAMES`, and do not touch the `isolated_claude_secrets_file` fixture.
- Tests that need a token keep planting it with `monkeypatch.setenv` or `patch.dict`.
- Run `tests/test_cli.py`, `test_claude_credential.py`, `test_preflight.py` and `test_agent_defaults.py` first, with and
  without the dummy variables set. Fix any test that silently relied on an ambient value by planting its own.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md`; scope limit to `apps/sandbox-executor/tests/`
- **Potential failure modes for this step:** A test that reads the ambient variable on purpose now fails; the xdist
  worker environment differs from the parent.
- **Guardrails and early‑abort checks:** `git diff --stat` shows no `src/` changes. Run the affected files with the
  variables set before the full suite.

### Success & Discard Criteria

- **Success:** The four named tests pass with both variables set to dummies and with them unset.
- **Discard:** More than three unrelated tests fail. Then re-scope the clearing instead of widening it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.6   |
| impact_pred         | 40.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 37.1  |

### Step Metrics Rationale

`EV = 0.97 * 40.0 + 0.5 * 2.0 - 0.3 * 0.6 - 1.5 = 38.8 + 1.0 - 0.18 - 1.5 = 38.12`. This is the step that removes the
Stage 4 failure, so it carries most of the impact.

---

## Step 3: Add the guard test that runs the credential tests with the variables really set

- **Sub‑intent recommendation:** NO
- **Reasoning:** One new test module; the risk is limited to child-process hygiene.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** A child pytest launched with dummy credential variables in its real environment passes
  the credential tests only while the fixture clears them.
- **Learning target:** Whether a child pytest is a stable, fast regression guard under xdist and the existing guards.
- **Maximum acceptable cost for this learning:** Under 15 seconds of added suite time and no flakiness over the planned
  verification runs.

### Intent & Git Integration

- **Step Intent:** Fail the suite if the fixture ever stops clearing a credential variable the code reads.
- **Git branch:** I-1794464833-scrubcreds
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add `tests/test_credential_hermeticity.py`. The first test is parametrised over `CREDENTIAL_ENV_NAMES`. It launches
  `sys.executable -m pytest` through `subprocess.run` with the real environment copied, that one variable set to a
  dummy, and a selection of the four named `test_cli.py` tests plus an in-module probe test.
- The probe is a test that only runs in the child, gated on a sentinel variable. It asserts the variable is absent from
  `os.environ`. That fails if the fixture stops clearing it, even if no production test notices.
- Child flags: `-p no:xdist`, `-p no:cacheprovider`, `-q`, `--basetemp` under `tmp_path`, and
  `HOLON_CHILD_SUITE_ACTIVE=1` so the guard does not recurse. Check that `HOLON_CHILD_SUITE_ACTIVE` is honoured the way
  `test_sandbox_hermetic_guard.py` honours it, and reuse its pattern.
- Add a second, in-process test asserting that `CREDENTIAL_ENV_NAMES` is a superset of `CLAUDE_CREDENTIAL_ENV_NAMES` and
  that `conftest._AMBIENT_CREDENTIAL_NAMES` contains every name.
- Prove the guard bites: temporarily remove one name from the fixture, confirm the guard fails, then restore it. Do not
  commit the broken state.
- Make the failure message name the variable and the fix.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (p_success is above 0.6, so a sub-intent is not needed)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md`; the test-run discipline (no open-ended repetition).
- **Potential failure modes for this step:** Child recursion; leaking the dummy into the parent environment (always pass
  a copied `env=` and never mutate `os.environ`); ledger or knowledge-index guards tripping; a slow child.
- **Guardrails and early‑abort checks:** Select only the four tests and the probe by node id, with a timeout on the
  `subprocess.run` call. Check the ledgers are unchanged with `git status` after the run.

### Success & Discard Criteria

- **Success:** The guard passes, and fails when a name is temporarily dropped from the fixture.
- **Discard:** The child cannot be made hermetic after two attempts. Then fall back to an in-process
  `patch.dict(os.environ)` test that runs the fixture function directly, and record the reason.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.4   |
| impact_pred         | 30.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 27.2  |

### Step Metrics Rationale

`EV = 0.92 * 30.0 + 0.5 * 6.0 - 0.3 * 1.4 - 3.0 = 27.6 + 3.0 - 0.42 - 3.0 = 27.18`. It has the highest entropy because
of subprocess behaviour, but there is a clear fallback.

---

## Step 4: Verify the whole suite with dummy credentials set on the host

- **Sub‑intent recommendation:** NO
- **Reasoning:** Verification only; no edits expected.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the suite passes with `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_API_KEY` set, and still passes
  without them.
- **Git branch:** I-1794464833-scrubcreds
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Run `uv run pytest apps/sandbox-executor/tests/test_cli.py tests/test_credential_hermeticity.py` first.
- Then run the full suite once with the dummy values set, using `uv run task test`, which applies the repository's xdist
  settings. Run it once more with the variables unset. No repetition beyond these two runs, since the intent has no
  timing requirement.
- Record the pass and fail counts and pytest-reported times in the execution notes. Run `uv run task lint` and
  `uv run task lint-docs`.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/testing/testing_guide.md`
- **Potential failure modes for this step:** Unrelated flaky tests fail; lint complaints about complexity or line
  length.
- **Guardrails and early‑abort checks:** Re-run a failing test alone to attribute it. Do not use `--no-verify` or skip
  tests.

### Success & Discard Criteria

- **Success:** All tests pass in both runs, and both lint tasks pass.
- **Discard:** A failure that traces to credential variables not in the list. Return to Step 1 and extend the list.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.6   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 14.0  |

### Step Metrics Rationale

`EV = 0.97 * 15.0 + 0.5 * 3.0 - 0.3 * 0.6 - 2.0 = 14.55 + 1.5 - 0.18 - 2.0 = 13.87`. Most of the cost is the wall time
of two full-suite runs.

---

## Step 5: Document the hermeticity rule

- **Sub‑intent recommendation:** NO
- **Reasoning:** A short documentation edit with low risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Record in the hermeticity docs which variables the suite clears and how the guard works.
- **Git branch:** I-1794464833-scrubcreds
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Update `apps/sandbox-executor/docs/hermetic_testing.md` with a short section: the credential variables cleared, where
  the list lives (`tests/credential_env.py`), the guard, and the rule that a test needing a token plants its own.
- Add one line to `docs/testing/testing_guide.md` linking to it, if that file lists the suite-wide isolation fixtures.
- Use Australian English. Run `uv run task lint-docs` and keep the formatting stable across one prettier pass.
- Do not touch the ledgers.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md`; the exactly-three-ledgers rule
- **Potential failure modes for this step:** Docs lint failures; a stale variable list.
- **Guardrails and early‑abort checks:** Name the list's source file instead of copying the values.

### Success & Discard Criteria

- **Success:** `uv run task lint-docs` passes and the docs name the guard.
- **Discard:** Not applicable; the step is cheap and reversible.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.3   |
| impact_pred         | 5.0   |
| cost_pred           | 0.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 4.8   |

### Step Metrics Rationale

`EV = 0.98 * 5.0 + 0.5 * 1.0 - 0.3 * 0.3 - 0.5 = 4.9 + 0.5 - 0.09 - 0.5 = 4.81`. A low-value but necessary step.

---
