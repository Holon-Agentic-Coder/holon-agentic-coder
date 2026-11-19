# Plan for I-1794529163-attrib

- **Plan ID:** P-1794529179-a03ed51a-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-13T00:19:40.410Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A
- **Exploration stance:** conservative. The intent is narrow and well specified, and both touch points already exist
  (the `HOLON_AGENT_SETTINGS` to `--settings` mapping in `ClaudeAgentRunner`, and the Stage 3 entrypoint
  `entrypoint/executor.py`).
- **Safety priority level:** elevated
- **Priority Justification:** The change touches how the agent is launched. `agent_runner.py` states that bypass argv is
  never routed through a settings value, and the intent forbids editing the user's own settings. The plan must keep that
  separation and never rewrite history (append-only ledgers, `docs/safety.md`).

## Exploration

- **Proportion of steps that are exploratory:** 0.1
- **Justification:** One short info-gathering step confirms the exact Claude Code setting keys and the runner's settings
  plumbing. Everything else is deterministic.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 4.0   |
| impact_pred         | 55.0  |
| cost_pred           | 12.0  |
| learning_value_pred | 4.0   |
| ev_pred             | 35.5  |

### Strategy Rationale

The intent is accepted as is. It is a small, two-part change: prevent the trailer at the source and report any slip.
Overall metrics are a qualitative roll-up. The bottleneck is Step 2 (the runner settings change), because the exact
settings merge semantics depend on how `HOLON_AGENT_SETTINGS` is already consumed. `p_success_pred` is the product of
the step probabilities, `0.97 * 0.88 * 0.93 * 0.95 * 0.97 = 0.73`, softened upward to 0.85 because steps 3 to 5 are
independent of each other and the risk in Step 2 is the only substantive one. `entropy_pred` is the sum of the step
entropies, `0.3 + 1.2 + 1.0 + 1.0 + 0.5 = 4.0`. Cost is the sum of step costs.
`EV = 0.85 * 55.0 + 0.5 * 4.0 - 0.3 * 4.0 - 12.0 = 46.75 + 2.0 - 1.2 - 12.0 = 35.55`; the earlier value 38.5 was rounded
optimistically before this check, so the derived figure `35.5` is the one in the table.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:** `holon-config/world/ruleset.md` (typing, docstrings, tests in
  `tests/`, no new dependencies); `holon-config/world/constraints.md`; `docs/safety.md` (append-only ledgers, no history
  rewriting, no permission bypass); `agent_runner.py` bypass-flag separation.
- **Potential violations or edge cases:** Overwriting a maintainer-supplied `HOLON_AGENT_SETTINGS`; leaking a token from
  settings into logs; false positives on a `Co-Authored-By` trailer a human legitimately wants (the maintainer rule says
  none); merge commits and the ledger commits the flow makes; an empty range when the branch has no own commits; the
  container having no `origin/main` ref.
- **Mitigations built into the plan:** Merge settings rather than replace; never log the settings value (existing
  redaction covers `HOLON_AGENT_SETTINGS`); the check is read-only (`git log` only); the range is the plan tip to HEAD,
  matching the existing `diff_base` logic; the check warns by default and fails only when an env flag asks.
- **Residual risk accepted (and why):** A trailer written by the agent via a manual `git commit -m` is still only
  detected, not prevented. Prevention for that path is the prompt rule plus the report, as the intent states.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 4.0
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

Four small pieces, all inside `apps/sandbox-executor`: (1) a focused module that builds the attribution-disabled
settings; (2) wiring that module into `ClaudeAgentRunner` through the existing `--settings` route; (3) a focused
read-only module that scans the execution branch's own commits for attribution trailers, called once from the Stage 3
entrypoint; (4) hermetic tests and a short docs update. No new dependencies, no history rewriting, no new ledger. Tests
run first with `uv run pytest <paths>`, then the full suite once with `uv run task test`, then `uv run task lint` and
`uv run task lint-docs`. No repeated or timing runs are needed.

---

## Step 1: Confirm Claude Code settings keys and the existing settings route

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only and tiny; it only fixes the design of Step 2.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Setting `includeCoAuthoredBy` to false (and the newer `attribution` object with empty
  `commit` and `pr`) in the JSON given to `--settings` disables the trailer, and `--settings` accepts inline JSON.
- **Learning target:** Which key the installed Claude Code honours, and how `_append_env_mappings` (`agent_runner.py`
  around line 470) emits the value.
- **Maximum acceptable cost for this learning:** 2 units; no network use.

### Intent & Git Integration

- **Step Intent:** Pin down the exact settings payload and merge point before writing code.
- **Git branch:** I-1794529163-attrib (execution branch, no new branch)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Read `agent_runner.py` `StandardAgentRunner._append_env_mappings`, the Claude runner, and `preflight.py` use of
  `HOLON_AGENT_SETTINGS` (the preflight probe renders `--settings "${HOLON_AGENT_SETTINGS}"`).
- Read tests `test_agent_runner.py` (around lines 596 to 626) and `test_preflight.py` to learn how commands are asserted
  hermetically.
- Check the local `claude --help` or installed docs offline for the setting names; if unavailable, write both keys.
- Decide the merge rule: when `HOLON_AGENT_SETTINGS` is unset, pass the attribution-off JSON; when it is a JSON string,
  merge the two keys in; when it is a file path, read it and emit merged inline JSON, leaving the file as is.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `agent_runner.py` bypass-flag comment; `docs/safety.md` credential handling.
- **Potential failure modes for this step:** Wrong key name; misreading the env-mapping path.
- **Guardrails and early‑abort checks:** Do not touch `~/.claude/settings.json`; no network.

### Success & Discard Criteria

- **Success:** A written decision on payload and merge rule.
- **Discard:** Cost above 3 units.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.3   |
| impact_pred         | 5.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 5.2   |

### Step Metrics Rationale

`EV = 0.97 * 5.0 + 0.5 * 3.0 - 0.3 * 0.3 - 1.0 = 4.85 + 1.5 - 0.09 - 1.0 = 5.26`. Cheap and mostly about learning.

---

## Step 2: Start Claude Code with attribution disabled via the settings route

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, contained in one runner and one new module; sub-intent overhead would exceed the work.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make every Claude Code launch by the runner carry the attribution-off settings.
- **Git branch:** I-1794529163-attrib
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add `sandbox_executor/agent_attribution.py` (new, focused): a function returning the settings payload with
  `includeCoAuthoredBy` false and `attribution` commit and pr empty; a function that merges that payload into an
  existing `HOLON_AGENT_SETTINGS` value (inline JSON or a readable file path) and returns the string to pass. Invalid
  JSON or an unreadable file falls back to the payload alone and emits one warning with no value in it.
- In `ClaudeAgentRunner` (`agent_runner.py`, already about 785 lines, so keep the edit to a few lines), resolve the
  settings value through the new function before the `--settings` mapping is emitted. Do this by a small override of the
  mapping step for the settings variable, not by touching `bypass_flags`.
- Keep the bypass argv untouched and separate; add no permission-mode change.
- Update `flow_module_budgets.json` only if a budget gate requires it for the new module.
- Respect ruff limits (`C901 <= 10`, `PLR0913 <= 5`) with no `# noqa`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (p_success_pred 0.88 is above 0.6, so no sub-intent needed)

### Safety & Constraint Considerations

- **Relevant rules:** `agent_runner.py` bypass separation; `test_credential_hermeticity.py`; redaction of
  `HOLON_AGENT_SETTINGS`.
- **Potential failure modes for this step:** Existing tests that assert the exact command argv change; settings with a
  token get echoed; preflight probe command diverges from the real run.
- **Guardrails and early‑abort checks:** Run
  `uv run pytest apps/sandbox-executor/tests/test_agent_runner.py apps/sandbox-executor/tests/test_preflight.py apps/sandbox-executor/tests/test_redaction.py`
  straight after the edit; update only assertions that are legitimately affected and declare them.

### Success & Discard Criteria

- **Success:** Built Claude command always includes `--settings` with attribution disabled; user settings files are
  never written.
- **Discard:** Cost above 1.5 times cost_pred, or it requires changing the permission bypass path.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.2   |
| impact_pred         | 30.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 22.9  |

### Step Metrics Rationale

`EV = 0.88 * 30.0 + 0.5 * 2.0 - 0.3 * 1.2 - 4.0 = 26.4 + 1.0 - 0.36 - 4.0 = 23.04`. Highest impact; the risk is
disturbing existing argv assertions.

---

## Step 3: Add the Stage 3 attribution-trailer check

- **Sub‑intent recommendation:** NO
- **Reasoning:** One read-only function and one call site; low risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Report any attribution trailer in the execution branch's own commits, never rewriting history.
- **Git branch:** I-1794529163-attrib
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add `sandbox_executor/attribution_check.py` (new): a function taking the repository directory, a base ref (the plan
  tip already used as `diff_base`) and a git runner, listing the commits in `base..HEAD` with `git log` and `--format`
  carrying the sha and the trailers (via `%(trailers:key=Co-Authored-By,valueonly)` or by scanning the body), and
  returning a list of findings (short sha, trailer text).
- Match case-insensitively on `Co-Authored-By:` lines whose value contains `noreply@anthropic.com` or `Claude`; also
  `Generated with [Claude Code]` lines. Keep the pattern in one constant.
- Add a formatter that returns a human report, and a policy function: warn by default; fail when
  `HOLON_ATTRIBUTION_STRICT` is set to a truthy value.
- Call it once in `entrypoint/executor.py main()` after the agent turn and the salvage step, before the push, with a
  tiny helper to keep `main()` within its existing noqa (add no new suppressions). Print the report to the execution
  log; do not touch the ledgers or amend, rebase, reset or squash anything.
- Skip quietly when the base ref is missing or the range is empty.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` (no history rewriting, three append-only ledgers); `executor.py` salvage notes
  (nothing amended).
- **Potential failure modes for this step:** Wrong range includes merged `origin/main` commits (the keeppush step merges
  main, so use first-parent or the plan tip to exclude them); false positives on ledger commits.
- **Guardrails and early‑abort checks:** Use `--no-merges` and the `base..HEAD` range only; the function only runs
  read-only git commands; any git error returns no findings and a one-line note.

### Success & Discard Criteria

- **Success:** A fixture branch with a trailer yields one finding; a clean branch yields none; no refs change.
- **Discard:** The range cannot be established reliably without new git plumbing.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 1.0   |
| impact_pred         | 22.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 17.2  |

### Step Metrics Rationale

`EV = 0.93 * 22.0 + 0.5 * 2.0 - 0.3 * 1.0 - 3.0 = 20.46 + 1.0 - 0.3 - 3.0 = 18.16`. Standard git log work.

---

## Step 4: Hermetic tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Needed to satisfy acceptance; uses existing hermetic fixtures.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove settings disable attribution and the check finds and stays silent correctly.
- **Git branch:** I-1794529163-attrib
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add `tests/test_agent_attribution.py`: with `HOLON_AGENT_SETTINGS` unset, set to inline JSON, and set to a `tmp_path`
  file, assert the runner's command carries `--settings` whose parsed JSON has `includeCoAuthoredBy` false and empty
  attribution; assert the original settings keys survive; assert the file is unchanged; assert no bypass flag appears
  because of this change.
- Add `tests/test_attribution_check.py`: build a fixture repository in `tmp_path` using the existing
  `worktree_fixtures.py` helpers (or plain `git` through `subprocess` with isolated env); one case commits with a
  `Co-Authored-By: Claude ... <noreply@anthropic.com>` trailer and expects a finding naming the sha; a clean case
  expects an empty result and no output; a case with a trailer only in the base history expects silence; assert HEAD sha
  is unchanged after the check; strict mode returns a failure, default returns a warning.
- No network, no real Claude binary, no real docker. Run through `uv run pytest` only.

### Dependencies & Criticality

- **Depends on:** Steps 2 and 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` section 3 (tests declared in the plan); `test_sandbox_hermetic_guard.py`,
  `network_guard.py`.
- **Potential failure modes for this step:** Test git identity or gpg config from the host leaks in; the hermetic guard
  flags subprocess use.
- **Guardrails and early‑abort checks:** Set `GIT_CONFIG_GLOBAL` to a null path and a fixed identity in the fixture;
  reuse existing helpers the guard already accepts.

### Success & Discard Criteria

- **Success:** New tests pass with
  `uv run pytest apps/sandbox-executor/tests/test_agent_attribution.py apps/sandbox-executor/tests/test_attribution_check.py`,
  then the full suite once via `uv run task test`.
- **Discard:** More than one full-suite failure unrelated to this change that cannot be traced within budget.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.0   |
| impact_pred         | 15.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 11.9  |

### Step Metrics Rationale

`EV = 0.95 * 15.0 + 0.5 * 2.0 - 0.3 * 1.0 - 3.0 = 14.25 + 1.0 - 0.3 - 3.0 = 11.95`.

---

## Step 5: Documentation and final gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** A short docs edit plus running the gates.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Document the rule, the settings route and the check, then run lint gates.
- **Git branch:** I-1794529163-attrib
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add a short section to `docs/executor/execution_architecture_specification.md` (and a line in `docs/git_flow.md` if it
  fits) covering: no attribution trailer rule, the injected settings, the check, the strict env flag, and that history
  is never rewritten so only new trailers are prevented. Australian English.
- Run `uv run task lint` and `uv run task lint-docs`; fix formatting (prettier line width 120) only in touched files.
- Execution notes record the commands and results; no ledger edits beyond the flow's usual rows.

### Dependencies & Criticality

- **Depends on:** Steps 2 to 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md`; markdown hygiene (code spans for formulas).
- **Potential failure modes for this step:** Prettier oscillation from raw underscores; complexity gate on new code.
- **Guardrails and early‑abort checks:** Wrap identifiers in code spans; rerun lint-docs until stable once.

### Success & Discard Criteria

- **Success:** `uv run task lint` and `uv run task lint-docs` pass; the full suite passes.
- **Discard:** Lint fails on files outside the touch set that this change did not cause.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.5   |
| impact_pred         | 8.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 6.9   |

### Step Metrics Rationale

`EV = 0.97 * 8.0 + 0.5 * 1.0 - 0.3 * 0.5 - 1.0 = 7.76 + 0.5 - 0.15 - 1.0 = 7.11`.

---
