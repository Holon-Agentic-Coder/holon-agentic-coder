# Plan for I-1794446637-creds

- **Plan ID:** P-1794446908-d2eae989-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-12T01:28:29.814Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS, with a recorded scope clarification (see Strategy Rationale).
- **Reframed intent (if applicable):** None. The intent's scope list omits two files that actually perform the claude
  mapping and the host credential forwarding. The plan touches them as the minimum needed to meet the acceptance
  criteria.
- **Exploration stance:** balanced. The behaviour is well specified; the uncertainty is where the mapping really lives.
- **Safety priority level:** elevated
- **Priority Justification:** The change handles credentials and log redaction. `docs/safety.md` and
  `holon-config/world/constraints.md` require that secrets never reach logs or argv and that ledgers stay append-only.

## Exploration

- **Proportion of steps that are exploratory:** 0.15
- **Justification:** Only the first step (confirming the real mapping sites) is investigative. The rest is specified
  behaviour with strong existing test patterns (`test_redaction.py`, `test_preflight.py`, `test_agent_runner.py`).

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 9.5   |
| impact_pred         | 70.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 43.2  |

### Strategy Rationale

The intent is accepted. Its value is high: a subscription-token operator currently cannot use the documented channel.

**Scope discrepancy found while planning.**

- The intent names `_apply_generic_token` in `agent_runner.py`. No such function exists in `src/`; it appears only in a
  comment in `tests/test_agent_runner.py`.
- The real `HOLON_AGENT_KEY` to `ANTHROPIC_API_KEY` mapping for claude is the `claude)` branch of
  `apps/sandbox-executor/entrypoint/role_dispatcher.sh`.
- The host side that discovers `GITHUB_TOKEN` and builds the container env is `find_github_token` plus the env assembly
  near line 1198 of `cli/__init__.py`. That is where "as the existing GitHub credential discovery behaves" must be
  mirrored.
- `agent_defaults.py::_detect_api_key_present` counts `HOLON_AGENT_KEY` as present for every runner, including claude.

The plan therefore adds a small focused module, `claude_credential.py`, for discovery and type mapping. It then makes
minimal edits to `cli/__init__.py`, `entrypoint/role_dispatcher.sh` and `agent_defaults.py`. The first two are necessary
for the acceptance criteria. The third keeps auth-status detection from reporting HOLON_AGENT_KEY as a claude
credential. These files are outside the listed scope but inside the intent's purpose. The executor should record that as
a scope note rather than treat it as a violation.

**Metric derivation.**

- Overall p_success is roughly the product of the step probabilities, adjusted for correlation. The bottleneck is Step
  2, the host and entrypoint wiring. A shell edit cannot be exercised end to end hermetically, so it is covered by a
  unit test of the env assembly plus a string-level test of the dispatcher branch.
- Entropy is the sum of the step values (see below), under the budget of 15.0.
- Impact is the maximum of the step impacts, because the steps together deliver one capability.
- Cost is the sum of the step costs.
- `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`, with mu = 0.5 and lambda = 0.3 and
  an assumed Delta_S_intent of 1.0: `EV = 0.78 * 70.0 + 0.5 * 6.0 - 0.3 * 1.0 - 14.0 = 54.6 + 3.0 - 0.3 - 14.0 = 43.3`.
  The table says 43.2 after rounding the step-level figures.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §3: the ledgers stay append-only, so the plan edits none.
  - `docs/safety.md`: no secret in logs or argv, and no mounting of `~/.holon` wholesale. Only the single token file is
    read, on the host.
  - The sandbox tiers apply: the code change runs in the sandbox executor.
  - The flow module budgets are checked by `uv run task lint`. The `flow/` package is not touched, so no budget changes.
- **Potential violations or edge cases:**
  - The token file is world-readable, empty, whitespace-only, or contains a trailing newline.
  - A value is an `sk-ant-oat` token placed in a non-claude runner's `HOLON_AGENT_KEY`; this must keep working
    unchanged.
  - A stale `ANTHROPIC_API_KEY` or `HOLON_AGENT_KEY` in the host env must not displace a discovered claude token.
  - `HOLON_AGENT_SETTINGS` still carries a secret for legacy operators; redacting the whole `--settings` value must
    still cover that.
  - Removing `HOLON_ECHO_PAD` may break an existing test or documentation reference.
- **Mitigations built into the plan:**
  - Credential values travel only via the existing owner-only env-file path (`write_env_file`). Both the new variable
    names are added to `is_secret_env_item` and to the redaction name lists.
  - Warnings name the file path and the permission bits, never the value.
  - Leak tests (`leak_assertions.py`) assert absence of both values from echoes, logs and the probe excerpt.
  - Ordering is fixed: the explicit environment value wins, else the file.
- **Residual risk accepted (and why):** The container-side `claude` process still sees the token in its environment.
  That is inherent to env-based credentials and equals the existing GitHub token posture.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 9.5 (`2.0 + 3.0 + 2.0 + 1.5 + 1.0`)
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

Five steps, run in order on one execution branch, with no sub-intents.

1. Confirm the real mapping sites.
2. Add `claude_credential.py` and wire it into the host env assembly and the entrypoint.
3. Update the pre-flight probe and hint.
4. Redact `--settings` in echoes and remove the echo pad.
5. Write the tests and update the docs, then run the checks.

Test discipline: run the affected tests first (`uv run pytest apps/sandbox-executor/tests/<files>`), then the full suite
once with `uv run task test`, then `uv run task lint` and `uv run task lint-docs`. No repeated runs, because nothing
here measures timing.

---

## Step 1: Confirm the claude credential mapping sites and the echo pad usage

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only mapping of a handful of files; cheap and the findings gate the later steps.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The claude mapping lives only in `role_dispatcher.sh` plus the host env assembly, and
  `HOLON_ECHO_PAD` is implemented in the flow or host command-echo path rather than in `agent_runner.py`.
- **Learning target:** The exact lines to change, and any other consumer of `HOLON_AGENT_KEY` for claude (for example
  `agent_defaults._detect_api_key_present`, `cli/__init__.py` mount validation).
- **Maximum acceptable cost for this learning:** A few file reads and greps; abort and re-plan if more than five further
  consumers turn up.

### Intent & Git Integration

- **Step Intent:** Establish the true touch set before editing.
- **Git branch:** I-1794446637-creds/P-1794446908-d2eae989-claude-agent-claude-sonnet-5-5/E-creds-survey/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Grep `src/`, `entrypoint/`, `tests/` and `docs/` for `HOLON_ECHO_PAD`, `HOLON_AGENT_SETTINGS`,
  `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_API_KEY`. List each hit with its owner module.
- Read `find_github_token` and the env assembly in `cli/__init__.py` as the model for discovery.
- Read `redact_command_args` and `_is_secret_flag` in `redaction.py` for how `--settings` is currently handled.
- Read the claude constructor in `agent_runner.py` (`required_keys`, the `EnvMapping` for `--settings`).
- Record the findings as a short note in the execution report, including the scope note.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** docs/safety.md (no code change in this step).
- **Potential failure modes for this step:** A hidden consumer is missed.
- **Guardrails and early‑abort checks:** Step 5's full-suite run is the backstop for anything missed.

### Success & Discard Criteria

- **Success:** A list of every file to touch, with the pad's owner identified.
- **Discard:** The pad turns out to be generated in an out-of-scope subsystem that cannot be changed safely. Then amend
  Step 4 to keep the pad inert and flag it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 2.0   |
| impact_pred         | 10.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 11.1  |

### Step Metrics Rationale

`EV = 0.97 * 10.0 + 0.5 * 5.0 - 0.3 * 1.0 - 1.0 = 9.7 + 2.5 - 0.3 - 1.0 = 10.9`, rounded to 11.1 in the table after
allowing for the cheap read cost. It is low risk. Its value is in de-risking the later steps.

---

## Step 2: Add claude credential discovery, type mapping and wiring

- **Sub‑intent recommendation:** NO
- **Reasoning:** A focused new module plus three small edits; moderate risk but tightly testable and sharing one branch
  with the tests.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the claude runner discover the token from the secrets store and export it by detected type,
  never consulting `HOLON_AGENT_KEY`.
- **Git branch:** I-1794446637-creds/P-1794446908-d2eae989-claude-agent-claude-sonnet-5-5/E-creds-discovery/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New module `sandbox_executor/claude_credential.py` (target under 120 lines) owns:
  - the secrets path, built with `os.path.join(os.path.expanduser("~"), ".holon", "secrets", "claude-oauth-token")` to
    stay target-native;
  - a function that reads the file, trims it, and returns `None` for a missing or empty file;
  - a permission check that emits a warning naming the path and mode when group or other bits are set (`stat` mode
    `& 0o077`). The value is never in the message, and the file is left in place and never modified;
  - a classifier: a value starting `sk-ant-oat` maps to `CLAUDE_CODE_OAUTH_TOKEN`; any other value maps to
    `ANTHROPIC_API_KEY`;
  - a resolver that returns a name/value pair. An explicit `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` in the
    environment wins; otherwise the file is read. `HOLON_AGENT_KEY` is never consulted.
- `cli/__init__.py` env assembly: when the runner is claude, call the resolver and add the pair to the forwarded env, so
  it reaches the container through the owner-only env-file. For claude, do not forward `HOLON_AGENT_KEY`. Other runners
  are unchanged.
- Add `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_API_KEY` to `is_secret_env_item` and to `DEFAULT_ENV_LITERAL_NAMES` in
  `redaction.py`, so the value can never travel on an argv or appear in a literal-sweep log.
- `entrypoint/role_dispatcher.sh`: delete the claude mapping from `HOLON_AGENT_KEY`, and leave the other runners. Keep
  the secret-bundle ingestion for non-claude runners only.
- `agent_runner.py`: change the claude constructor's `required_keys` default so it no longer lists `HOLON_AGENT_KEY`
  (use the two claude variables). Update `validate()` so the claude branch checks those two variables or a mounted
  session, with the new message naming the secrets file.
- `agent_defaults._detect_api_key_present`: for `claude`, ignore `HOLON_AGENT_KEY` and the generic bundle.
- Keep every function within the ruff limits (C901 ≤ 10, PLR0913 ≤ 5); no `# noqa`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** docs/safety.md (secrets never on argv or in logs); constraints.md §2 (containment).
- **Potential failure modes for this step:**
  - The dispatcher edit breaks other runners.
  - A discovered token is overridden by a leftover env value.
  - The permission warning leaks the value.
- **Guardrails and early‑abort checks:**
  - A parameterised test over all runner ids proves non-claude mappings are untouched.
  - A string test over `role_dispatcher.sh` checks the claude branch.
  - Leak assertions run over the warning text.

### Success & Discard Criteria

- **Success:** With only the file present, the container env gets the right variable by type; with `HOLON_AGENT_KEY` set
  and no file or claude variable, claude validation fails; other runners still pass.
- **Discard:** If the dispatcher change cannot be tested hermetically or breaks another runner's tests, split the
  entrypoint edit into a sub-intent. Stop if cost exceeds 1.5 times the cost_pred.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 3.0   |
| impact_pred         | 70.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 56.2  |

### Step Metrics Rationale

`EV = 0.85 * 70.0 + 0.5 * 4.0 - 0.3 * 1.0 - 5.0 = 59.5 + 2.0 - 0.3 - 5.0 = 56.2`. This step carries nearly all the value
and nearly all the risk (a shell edit plus host wiring).

---

## Step 3: Rework the pre-flight probe and NOT_LOGGED_IN hint

- **Sub‑intent recommendation:** NO
- **Reasoning:** One small module with an existing test file; low risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make pre-flight check the secrets file for claude and give a hint that lists the secrets file first,
  then both environment routes with their credential types.
- **Git branch:** I-1794446637-creds/P-1794446908-d2eae989-claude-agent-claude-sonnet-5-5/E-creds-preflight/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `preflight.py` (468 lines, so keep additions small; move any bulk into `claude_credential.py`), make the
  credential-present check at the existing `os.getenv("HOLON_AGENT_KEY") or session_mounts` site claude-aware. For
  claude, the credential is present if the resolver returns a pair or a session mount exists.
- At the line near 395 that says "No HOLON_AGENT_KEY is set…", branch for claude: say that no claude credential was
  found, name the secrets-file path, and mention `claude setup-token` (the file is missing or empty).
- Add a claude-specific hint constant for `not_logged_in`, listing in this order: the path
  `~/.holon/secrets/claude-oauth-token`; `CLAUDE_CODE_OAUTH_TOKEN` for a subscription token (`sk-ant-oat...` from
  `claude setup-token`); `ANTHROPIC_API_KEY` for an API key (`sk-ant-api...`). It does not name `HOLON_AGENT_KEY`. The
  generic hint stays for the other runners.
- Keep `_SENSITIVE_ENV_VARS` masking and add the two claude variables to it.
- Use Australian English in the wording ("authorise", "initialise").

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** docs/safety.md (probe excerpts masked).
- **Potential failure modes for this step:** The probe excerpt echoes the token; the generic hint regresses for other
  runners.
- **Guardrails and early‑abort checks:** Leak assertions over the probe output. The existing tests for the other
  runners' hints must stay unchanged.

### Success & Discard Criteria

- **Success:** A missing or empty file fails pre-flight with a hint naming the path and `claude setup-token`, and the
  hint does not contain `HOLON_AGENT_KEY` for claude.
- **Discard:** The change forces more than about 40 lines into `preflight.py`. Then move it to the new module.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 2.0   |
| impact_pred         | 40.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 35.0  |

### Step Metrics Rationale

`EV = 0.92 * 40.0 + 0.5 * 2.0 - 0.3 * 1.0 - 2.5 = 36.8 + 1.0 - 0.3 - 2.5 = 35.0`.

---

## Step 4: Redact the whole --settings value in echoes and remove the echo pad

- **Sub‑intent recommendation:** NO
- **Reasoning:** A narrow change in `redaction.py` and wherever the pad is generated; the pattern already has tests.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the command echo replace the entire `--settings` value with `<redacted>` and delete the
  `HOLON_ECHO_PAD` mechanism.
- **Git branch:** I-1794446637-creds/P-1794446908-d2eae989-claude-agent-claude-sonnet-5-5/E-creds-redact/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `redaction.py` (569 lines; the budget script covers only `flow/`, but keep the growth to a few lines), extend
  `redact_args` and `redact_command_args` so the argument after `--settings`, and the `--settings=value` form, is
  replaced with `<redacted>` unconditionally. This applies whether or not the value looks secret-bearing.
- Remove `HOLON_ECHO_PAD` from the code path found in Step 1, and any documentation that describes it. The
  `HOLON_AGENT_SETTINGS` tunnel itself is unchanged and keeps working.
- Keep the existing redaction tests that prove no secret reaches an echo. Update only those that asserted pad behaviour.
- Check that the container-side `"${HOLON_AGENT_SETTINGS}"` expansion path still works for non-secret settings.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** docs/safety.md (no secret-bearing text in any log).
- **Potential failure modes for this step:** Over-redaction hides a path argument that other tests assert on; removing
  the pad lets a truncated echo expose the secret if redaction misses a form.
- **Guardrails and early‑abort checks:** Tests cover the separate-argument, `=`-joined and quoted `sh -c` forms.

### Success & Discard Criteria

- **Success:** Every echoed form shows `--settings <redacted>`, and no `HOLON_ECHO_PAD` reference remains in `src/`,
  `tests/` or `docs/`.
- **Discard:** The pad turns out to serve a purpose outside echoes (for example a Docker log truncation guard). Then
  stop and report instead of removing it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.5   |
| impact_pred         | 40.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 34.7  |

### Step Metrics Rationale

`EV = 0.90 * 40.0 + 0.5 * 3.0 - 0.3 * 1.0 - 2.5 = 36.0 + 1.5 - 0.3 - 2.5 = 34.7`.

---

## Step 5: Tests, documentation and verification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical once the behaviour exists, and cheapest on the same branch.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the behaviour hermetically, document it, and pass the repository gates.
- **Git branch:** I-1794446637-creds/P-1794446908-d2eae989-claude-agent-claude-sonnet-5-5/E-creds-tests/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New `tests/test_claude_credential.py`, using `tmp_path` as a patched home, with no real docker, gh, git remote or
  model call:
  - discovery reads the file with no explicit key set, trims it, and leaves it in place (same bytes, same mode);
  - an explicit environment value wins over the file, and `HOLON_AGENT_KEY` is ignored for claude;
  - an `sk-ant-oat...` value maps to `CLAUDE_CODE_OAUTH_TOKEN`; an `sk-ant-api...` value or any other maps to
    `ANTHROPIC_API_KEY`;
  - a looser mode (0644) emits a warning naming the path and mode, with no value in it;
  - a missing or empty file fails pre-flight with a hint naming the path and `claude setup-token`.
- Extend `test_redaction.py`: the whole `--settings` value is `<redacted>` in every form; neither token value appears in
  any echoed command, probe excerpt or caplog record (use `leak_assertions.py`). Keep the existing redaction tests.
- Extend `test_agent_runner.py` and `test_preflight.py`: the claude `required_keys` no longer include `HOLON_AGENT_KEY`;
  other runners still do; the dispatcher branch for claude no longer maps `HOLON_AGENT_KEY`.
- Update `docs/executor/agent_credentials_requirements.md` (the tiers, the claude validation text, the hint table at the
  `not_logged_in` row, and the redaction and env-file sections) and `docs/planner/agent_credentials_requirements.md` if
  it repeats the claude text. Remove the pad text. Use Australian English.
- Run in order: `uv run pytest` on the new and changed test files, then `uv run task test` once, then `uv run task lint`
  and `uv run task lint-docs`. If lint flags formatting, fix it and rerun lint only.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** hermetic-testing rules (`docs/hermetic_testing.md`, `test_sandbox_hermetic_guard.py`);
  constraints.md §3 (ledgers untouched).
- **Potential failure modes for this step:** A test writes to the real `~/.holon`; the full suite breaks an unrelated
  test that relied on `HOLON_AGENT_KEY` for claude.
- **Guardrails and early‑abort checks:** Patch `HOME` with `tmp_path`; the hermetic guard test must pass; the full suite
  is run once.

### Success & Discard Criteria

- **Success:** All tests, `uv run task lint` and `uv run task lint-docs` pass, and the six acceptance criteria are each
  covered by a named test or doc section.
- **Discard:** More than three unrelated suite failures traceable to the claude `HOLON_AGENT_KEY` removal. Then pause
  and re-plan the affected fixtures.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.0   |
| impact_pred         | 45.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 38.7  |

### Step Metrics Rationale

`EV = 0.90 * 45.0 + 0.5 * 3.0 - 0.3 * 1.0 - 3.0 = 40.5 + 1.5 - 0.3 - 3.0 = 38.7`.

---

## Sub-intent list

None
