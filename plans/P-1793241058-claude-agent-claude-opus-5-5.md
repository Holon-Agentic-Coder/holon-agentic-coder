# Plan for I-1793241043-claude-runner-permission-bypass-and-one-pass-placeholders

- **Plan ID:** P-1793241058-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-29T02:30:59.484Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. The scope is kept to the planner's substitution; the executor's similar
  chained `str.replace` (`entrypoint/executor.py`, `{plan_content}` substituted last on purpose) is recorded as a
  follow-up, not changed here, because the goal names only the planner and the executor relies on substitution order.
- **Exploration stance:** conservative. Both defects are diagnosed precisely in the intent (root cause, exact flags,
  verified behaviour in the `holon/agent-claude` image), so the value lies in exact, well-tested exploitation rather
  than exploration.
- **Safety priority level:** elevated
- **Priority Justification:** The change turns on a permission bypass (`bypassPermissions`) by default. `docs/safety.md`
  ("Sandboxing is mandatory for execution") is the condition that makes this acceptable: the claude runner only executes
  inside sandboxed role containers. The bypass must stay a CLI parameter (maintainer decision), must be opt-out via
  `HOLON_AGENT_SKIP_PERMISSIONS=false`, and must never leak into the `--settings` value, which is a credential-bearing,
  redacted channel (`preflight.py` `_SENSITIVE_ENV_VARS`).

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The root causes, the required flag pair and the desired API are fully specified by the intent and
  confirmed by reading `agent_runner.py` and `entrypoint/planner.py`. One step (Step 6) is a balanced verification gate,
  but it explores nothing new.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 4.2   |
| impact_pred         | 90.0  |
| cost_pred           | 15.0  |
| learning_value_pred | 4.5   |
| ev_pred             | 59.79 |

### Strategy Rationale

The intent bundles two small, independent defects that together block every sandboxed claude stage. The maintainer has
already answered the three design questions (CLI flags only, default on, one intent), so the plan accepts the intent as
is and executes it as one plan on one execution branch: the overall entropy (`4.2`) is well under the budget (`8.0`), so
no `## Sub-intent list` is needed and none is added.

Evidence gathered while planning:

- `agent_runner.py`: the `claude` entry in `runners` is a plain `StandardAgentRunner` with `suffix=["-p"]` and one
  `EnvMapping("HOLON_AGENT_SETTINGS", "--settings")`; `StandardAgentRunner.compose_cmd` builds
  `[binary, *prefix, model_flag, model, *suffix]`, appends env mappings, then the prompt. `AntigravityAgentRunner`
  already evaluates `HOLON_AGENT_SKIP_PERMISSIONS` (default `"true"`, truthy set `1/true/yes/on`) inside `compose_cmd`,
  i.e. at command-build time.
- `preflight.py` calls `runners[agent_id].compose_cmd(...)` for the readiness probe, so the bypass flags will also
  appear in the probe argv. That is desirable (the probe exercises the real command line) and harmless: the flags hold
  no secret and are not routed through `--settings`.
- `entrypoint/planner.py` lines 332-348: `replacements` maps `"{plan_id"` before `"{plan_id}"`, and the loop runs
  `template.replace` in insertion order, so `{plan_id}` becomes `P-...}`. Values such as `{intent_json}` and
  `{description}` can also contain brace text that later replacements re-expand (e.g. an intent description mentioning
  `{agent}`); a single regex pass with a function replacement fixes both.
- `tests/test_agent_runner.py::test_standard_runner_cmd` currently expects `["claude", "--model", ..., "-p", prompt]`.
  With default-on bypass this expectation must change; Step 3 declares that test modification explicitly, as
  `holon-config/world/ruleset.md` section 3 requires.
- Shipped templates contain free-text braces such as `{standard | elevated | critical}` and a lone `{`, so the
  template-lint test must define "unmatched placeholder" narrowly (see Step 4) to avoid false positives.

Aggregation: overall `entropy_pred` is the sum of step entropies (`1.0 + 1.0 + 0.8 + 0.8 + 0.4 + 0.2 = 4.2`). Overall
`p_success_pred` is a qualitative bottleneck estimate: the steps are strongly correlated (same author, same small code
area), so a naive product (`~0.48`) understates success; the bottleneck is Step 6 (the full `uv run task test` / `lint`
/ `lint-docs` gate at `0.85`), shaded down slightly to `0.82` for the risk that other suites (preflight, CLI, planner
integration) assert exact claude argv. Impact is not the plain sum of step impacts (`133`) because the tests and docs
mostly protect the two fixes; `90` reflects unblocking every sandboxed claude plan and execute stage. Cost is the sum of
step costs (`3 + 3 + 2.5 + 2.5 + 1.5 + 2.5 = 15.0`). Learning value is modest (`4.5`): it confirms the
claude-in-print-mode permission model inside Holon and adds a reusable template-hygiene guard.

`EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`

`EV = 0.82 * 90.0 + 0.5 * 4.5 - 0.3 * 4.2 - 15.0 = 73.80 + 2.25 - 1.26 - 15.0 = 59.79`

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 1 (Git flow): all work on the execution branch under this plan branch;
    never commit to `main`.
  - `holon-config/world/constraints.md` section 3 (Ledger immutability): no ledger edits are needed; the plan touches no
    `holon-knowledge/ledger/*.jsonl` file and creates no auxiliary ledger.
  - `holon-config/world/ruleset.md` section 1: Python `3.13`, `uv` only, no new dependencies (only stdlib `re` and `os`,
    both already imported in the touched modules).
  - `holon-config/world/ruleset.md` section 2: type annotations and docstrings on every new public function.
  - `holon-config/world/ruleset.md` section 3: `pytest` only; tests live in `apps/sandbox-executor/tests/` beside the
    matching source; test modifications are declared here (Steps 3 and 4).
  - `docs/safety.md` "Sandboxing is mandatory for execution": justifies default-on bypass, since the claude runner only
    runs in sandboxed role containers.
- **Potential violations or edge cases:**
  - Default-on bypass changes the exact argv asserted by existing tests (`test_agent_runner.py`, possibly
    `test_preflight.py`, `test_cli.py`, `test_planner*.py`).
  - Ambient `HOLON_AGENT_SKIP_PERMISSIONS=false` in a developer shell could make the default-on test flaky if not
    cleared.
  - A placeholder regex that is too greedy could swallow template text (`{plan_id}` vs `{plan_identifier}`), or one that
    is too narrow could leave `{plan_id` unrendered.
  - Using `re.sub` with a string replacement would re-interpret backslashes such as `\1` or `\n` in values.
  - The template-lint test could misfire on free-text braces in `planner.template.md`.
- **Mitigations built into the plan:**
  - Tests set or delete `HOLON_AGENT_SKIP_PERMISSIONS` explicitly with `monkeypatch` / `patch.dict`.
  - `re.sub` is called with a function replacement, so values are inserted literally; names are `re.escape`d and sorted
    longest first, with a negative look-ahead so `{plan_id` never matches the start of `{plan_identifier}`.
  - The lint test only flags `{name` forms whose `name` is a known placeholder name.
  - The bypass is appended by code to argv, never to the `HOLON_AGENT_SETTINGS` value; a test asserts the `--settings`
    value is unchanged and contains neither flag.
  - Never call real Docker, git remotes, `gh` or LLM runners in tests; only `compose_cmd` / `build_cmd` and the pure
    render function are exercised.
- **Residual risk accepted (and why):** Running claude with `bypassPermissions` lets the agent write anywhere the
  container user can. That is accepted because the container is the containment boundary, the Antigravity runner already
  does the same, the maintainer approved default-on, and an explicit opt-out exists.
- **Allocated Entropy Budget:** 8.0
- **Predicted Plan Entropy:** 4.2
- **Budget Compliance:** The strategy fits within budget (`4.2 <= 8.0`); executed as one plan, no sub-intent list.

## Plan Description & Strategy

Single execution branch, six sequential steps: two small source changes (runner bypass; one-pass placeholder renderer
wired into the planner), two declared test steps, one documentation step, and a final verification gate run from the
repository root with `uv run task test`, `uv run task lint` and `uv run task lint-docs`. Each step is small and low in
branching, so none is promoted to a sub-intent. Suggested execution branch for all steps:
`I-1793241043-claude-runner-permission-bypass-and-one-pass-placeholders/P-1793241058-claude-agent-claude-opus-5-5/E-{timestamp}-impl/_`.

---

## Step 1: Make the claude runner emit the permission bypass as CLI flags

- **Sub‑intent recommendation:** NO
- **Reasoning:** A few lines in one module, with the Antigravity runner as an in-repo precedent; low risk and not
  independently reusable enough to justify its own pipeline run.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The claude runner honours `HOLON_AGENT_SKIP_PERMISSIONS` (default true, evaluated at command-build
  time) and, when enabled, emits `--allow-dangerously-skip-permissions --permission-mode bypassPermissions` as argv
  parameters, never via `--settings`.
- **Git branch:**
  `I-1793241043-claude-runner-permission-bypass-and-one-pass-placeholders/P-1793241058-claude-agent-claude-opus-5-5/E-{timestamp}-impl/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/agent_runner.py`, add a small module-level helper (for example
   `skip_permissions_enabled() -> bool`) with a docstring, which reads `HOLON_AGENT_SKIP_PERMISSIONS` with default
   `"true"` and returns whether its lower-cased value is in the existing truthy set `("1", "true", "yes", "on")`. It is
   called inside `compose_cmd`, so evaluation happens at build time, not import time.
2. Refactor `AntigravityAgentRunner.compose_cmd` to call this helper instead of its inline check, so both runners share
   one parsing rule (behaviour unchanged; its existing tests guard it).
3. Give `StandardAgentRunner.__init__` one optional keyword parameter, for example `bypass_flags: list[str] | None`
   (default empty), stored on the instance. In `StandardAgentRunner.compose_cmd`, build the prefix as
   `[*self.prefix, *self.bypass_flags]` when `self.bypass_flags` is non-empty and `skip_permissions_enabled()` is true,
   otherwise `self.prefix`. No other runner sets `bypass_flags`, so their argv is unchanged.
4. In the `runners` registry, pass
   `bypass_flags=["--allow-dangerously-skip-permissions", "--permission-mode", "bypassPermissions"]` to the `claude`
   entry. Resulting command:
   `claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions --model <m> -p [--settings <value>] <prompt>`.
   `--permission-mode` and its value stay adjacent, and the prompt stays last.
5. Leave `EnvMapping("HOLON_AGENT_SETTINGS", "--settings")` and `_append_env_mappings` untouched; the bypass never
   touches any settings JSON or file.
6. Write comments and docstrings in Australian English (for example "honours", "behaviour"), keep the change free of new
   branches beyond the single conditional, and add no dependency.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (without it, sandboxed claude stages still cannot write files); `p_success_pred` is high, so no
  sub-intent is needed.

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` "Sandboxing is mandatory for execution"; `holon-config/world/ruleset.md` sections
  1 and 2; intent Q&A (CLI only, default on).
- **Potential failure modes for this step:**
  - Flags placed so that `bypassPermissions` is separated from `--permission-mode` or ends up after the prompt.
  - Accidentally changing argv for other runners (pi, gemini, opencode, codex).
  - Evaluating the variable at import time (for example in a module constant).
- **Guardrails and early‑abort checks:** After editing, run
  `uv run pytest apps/sandbox-executor/tests/test_agent_runner.py` from the repository root; any change in non-claude
  expected commands means the change leaked and must be reverted.

### Success & Discard Criteria

- **Success:** With the variable unset, `get_runner("claude").compose_cmd("m", "p")` returns
  `["claude", "--allow-dangerously-skip-permissions", "--permission-mode", "bypassPermissions", "--model", "m", "-p", "p"]`;
  with `HOLON_AGENT_SKIP_PERMISSIONS=false` it returns `["claude", "--model", "m", "-p", "p"]`.
- **Discard:** If the change requires touching more than `agent_runner.py`'s runner classes and registry, or cost
  exceeds `1.5 * 3.0`, stop and re-plan.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.0   |
| impact_pred         | 45.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 39.6  |

### Step Metrics Rationale

Small, well-precedented edit. Impact is the larger half of the intent, since it unblocks all file writes for claude
stages. `EV = 0.92 * 45.0 + 0.5 * 3.0 - 0.3 * 1.0 - 3.0 = 41.4 + 1.5 - 0.3 - 3.0 = 39.6`.

---

## Step 2: Replace the planner's chained str.replace with a one-pass render_placeholders

- **Sub‑intent recommendation:** NO
- **Reasoning:** One pure function plus one call-site change in `planner.py`; trivially testable and low risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Every planner template substitution goes through one small function that renders all placeholders in
  a single regex pass, accepts both `{name}` and unmatched `{name`, is independent of mapping order and inserts values
  literally.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/planner.py` (`re` is already imported), add
   `render_placeholders(template: str, values: Mapping[str, str]) -> str` with a docstring covering inputs, output and
   the literal-insertion guarantee. Keys are bare names (`"plan_id"`, not `"{plan_id}"`).
2. Logic: if `values` is empty, return the template unchanged. Otherwise build one pattern: a literal `{`, then a
   capture group that alternates the `re.escape`d names sorted by descending length, then a negative look-ahead for
   `[A-Za-z0-9_]` (so `{plan_id` never matches inside `{plan_identifier}`), then an optional `}`. Call `re.sub` with a
   function replacement that returns `values[match.group(1)]`. A function replacement inserts the value verbatim, so
   backslashes and braces in values are never re-expanded, and since the scan is single-pass, inserted text is never
   rescanned.
3. Replace the `replacements` dict and the `for k, v in replacements.items(): template = template.replace(k, v)` loop
   with a `values` dict keyed by bare names (`timestamp`, `agent`, `agent_version`, `model`, `intent_id`,
   `parent_intent_id`, `description`, `plan_id`, `date`, `intent_json`, `safe_model`, `budget_from_intent`) and a single
   `template = render_placeholders(template, values)`. Drop the `"{plan_id"` partial-key entry; the regex covers it.
4. Ensure no other template substitution in `planner.py` uses `str.replace` (grep confirmed only this loop; line 70's
   `model_name.replace("/", "_")` is not a template substitution and stays).
5. Australian English in the docstring and comments; no new dependency; keep the function to a few lines.
6. Do not change `entrypoint/executor.py` in this plan; note in the execution notes that its `_render` loop could adopt
   `render_placeholders` in a follow-up intent.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (without it, plans land under `P-...}.md`); `p_success_pred` is high.

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 2 (typing, docstrings); intent goal 2.
- **Potential failure modes for this step:**
  - A string replacement in `re.sub` re-interpreting `\1` or `\g<0>` in values.
  - Missing the look-ahead, so `{plan_id` matches the prefix of a longer placeholder.
  - Changing the rendered prompt in ways the planner tests do not expect (for example `{date` partials).
- **Guardrails and early‑abort checks:** Run `uv run pytest apps/sandbox-executor/tests/test_planner.py` and
  `test_planner_integration.py` from the repository root right after the edit.

### Success & Discard Criteria

- **Success:** `render_placeholders("plans/{plan_id}.md", {"plan_id": "P-1"})` returns `"plans/P-1.md"`, and the
  existing planner tests stay green.
- **Discard:** If existing planner tests depend on the old order-dependent behaviour in a way that needs more than
  updating expected strings, stop and re-plan.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.0   |
| impact_pred         | 35.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 30.4  |

### Step Metrics Rationale

Pure function, deterministic, with a clear specification.
`EV = 0.92 * 35.0 + 0.5 * 3.0 - 0.3 * 1.0 - 3.0 = 32.2 + 1.5 - 0.3 - 3.0 = 30.4`.

---

## Step 3: Add and update pytest tests for the claude runner bypass (declared test change)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Focused unit tests on `compose_cmd` / `build_cmd`, with no I/O; small and low risk.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Lock in default-on, opt-out, flag ordering, coexistence with `HOLON_AGENT_SETTINGS`, and the
  guarantee that the bypass never appears inside the `--settings` value.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Declared modification (per `holon-config/world/ruleset.md` section 3): in
   `apps/sandbox-executor/tests/test_agent_runner.py::test_standard_runner_cmd`, update the expected `claude` command to
   `["claude", "--allow-dangerously-skip-permissions", "--permission-mode", "bypassPermissions", "--model", "gemini-3.5-flash", "-p", "compiled prompt text"]`,
   and add `HOLON_AGENT_SKIP_PERMISSIONS` to the list of ambient variables popped for hermeticity.
2. Add new tests (same file, plain pytest functions with `monkeypatch`, matching the file's style where practical):
   - default-on: variable deleted, both flags present in the order above;
   - explicit opt-out: parametrise over `false`, `0`, `no`, `off`; neither `--allow-dangerously-skip-permissions` nor
     `--permission-mode` nor `bypassPermissions` appears;
   - ordering yields a valid command line: `cmd[0] == "claude"`, the element after `--permission-mode` is
     `bypassPermissions`, both bypass flags precede `-p`, and the prompt is the last element;
   - coexistence: with `HOLON_AGENT_SETTINGS` set to a JSON string, `--settings` is followed by exactly that string and
     the bypass flags are also present;
   - bypass never inside `--settings`: the value after `--settings` equals the input unchanged and contains neither
     `bypassPermissions` nor `skip-permissions`;
   - evaluated at build time: toggling the variable between two `compose_cmd` calls on the same runner instance changes
     the output.
3. Check `test_preflight.py`, `test_cli.py` and `test_planner*.py` for exact claude argv assertions; update any that
   break (expected to be none, since `test_preflight.py` uses `in` checks), declaring each change in the execution
   notes.
4. Use `HOLON_AGENT_KEY=dummy` so `build_cmd` validation passes; never invoke the `claude` binary, Docker, git remotes
   or `gh`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 3; intent goal 3 (no real Docker, git remotes, `gh` or LLM
  runners).
- **Potential failure modes for this step:** Ambient env leakage makes tests order-dependent; `get_version` being
  invoked accidentally (it spawns a subprocess), which these tests must avoid.
- **Guardrails and early‑abort checks:** Run the file under `pytest -n auto` (via `uv run task test`) to surface
  ordering or ambient-env leaks.

### Success & Discard Criteria

- **Success:** All new and updated runner tests pass from the repository root via `uv run pytest`.
- **Discard:** If a test needs a real binary or network to pass, the design is wrong; stop and re-plan.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.8   |
| impact_pred         | 15.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 11.76 |

### Step Metrics Rationale

Straightforward unit tests; small risk from hidden exact-argv assertions elsewhere.
`EV = 0.90 * 15.0 + 0.5 * 2.0 - 0.3 * 0.8 - 2.5 = 13.5 + 1.0 - 0.24 - 2.5 = 11.76`.

---

## Step 4: Add pytest tests for render_placeholders and a shipped-template lint (declared test change)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure-function tests plus one file-reading guard; small, though the lint needs careful scoping to avoid
  false positives.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove correct rendering, partial-brace handling, order independence and literal insertion, and stop
  any shipped template from carrying an unmatched placeholder brace.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_planner.py`, add tests importing `render_placeholders` from
   `sandbox_executor.entrypoint.planner`:
   - `plans/{plan_id}.md` with `plan_id=P-1793241058-a-b` renders to `plans/P-1793241058-a-b.md` (no stray `}`);
   - an unmatched `{plan_id` followed by a space or end of string renders to the value;
   - order independence: the same values in forward and reversed insertion order give identical output, including a
     template that mixes `{plan_id` and `{plan_id}`;
   - literal values: a value containing `{agent}`, `\1`, `\g<0>` and `\n` is inserted verbatim, and the `{agent}` inside
     the value is not expanded even when `agent` is also a key;
   - no false prefix match: `{plan_identifier}` is left untouched when only `plan_id` is a key;
   - unknown placeholders and free-text braces (`{standard | elevated}`) are left untouched.
2. Add a template-lint test (in `test_planner.py`, or a new `tests/test_prompt_templates.py` if cleaner) that locates
   the repository root from the test file path with `pathlib` (target-native paths, no `PYTHONPATH`), globs
   `holon-config/prompts/*.template.md`, asserts at least one file was found, and for each file:
   - collects the known placeholder names: every `name` appearing in a complete `{name}` (identifier pattern
     `[a-z][a-z0-9_]*`) across all shipped templates, plus the planner's own value keys;
   - fails, listing file and line, if any known name appears as `{name` not immediately followed by `}` or by another
     identifier character. This narrow definition ignores free-text braces such as `{standard | elevated | critical}`
     and a lone `{`, which are template prose rather than placeholders.
3. Run the lint against the current templates first; if it flags a real defect in a shipped template, fix the template
   brace in the same step and record the fix in the execution notes.
4. Tests are hermetic: no Docker, git remotes, `gh` or LLM calls; file reads only.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 3; intent goal 3.
- **Potential failure modes for this step:** The lint is too broad and flags prose braces (false positive), or too
  narrow and misses `{plan_id` partials (false negative); repository-root resolution breaks under `pytest -n auto`.
- **Guardrails and early‑abort checks:** Include a negative self-check in the test module (an in-memory sample with
  `{plan_id ` must be flagged by the same helper) so the lint cannot pass vacuously.

### Success & Discard Criteria

- **Success:** All placeholder tests pass; the template lint passes on the shipped templates and demonstrably fails on
  an injected `{plan_id` sample.
- **Discard:** If no narrow, false-positive-free definition of an unmatched placeholder is achievable within `1.5 * 2.5`
  cost, ship the render tests and record the lint as a follow-up.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.8   |
| impact_pred         | 15.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 11.96 |

### Step Metrics Rationale

The lint's definition is the only subtle point, hence slightly lower probability and higher learning value than Step 3.
`EV = 0.88 * 15.0 + 0.5 * 3.0 - 0.3 * 0.8 - 2.5 = 13.2 + 1.5 - 0.24 - 2.5 = 11.96`.

---

## Step 5: Update documentation describing the claude runner flags and HOLON_AGENT_SKIP_PERMISSIONS

- **Sub‑intent recommendation:** NO
- **Reasoning:** A few doc lines; trivial risk, but required by the intent's constraints.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Docs that describe the claude command line or `HOLON_AGENT_SKIP_PERMISSIONS` match the new behaviour.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `docs/executor/agent_credentials_requirements.md`, section "3. `claude`": change the command template to
   `claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions --model <model_name> -p [--settings <HOLON_AGENT_SETTINGS>] <prompt>`,
   and add a bullet explaining that `HOLON_AGENT_SKIP_PERMISSIONS` (default true; `false`, `0`, `no` or `off` opts out)
   controls the two bypass flags, that they are CLI parameters only and never part of the settings JSON, and that both
   are needed in print mode. If the env-var list near line 68 lists agent variables, add `HOLON_AGENT_SKIP_PERMISSIONS`
   there for the claude and antigravity runners.
2. Grep `docs/`, `apps/sandbox-executor/README.md` and `apps/sandbox-executor/docs/` for `SKIP_PERMISSIONS`,
   `dangerously` and `claude --model`; update any description that says only Antigravity honours the variable (for
   example `apps/sandbox-executor/docs/agy_sandbox_execution_solutions.md`).
3. Australian English; run `uv run task lint-docs` so prettier formatting passes.
4. Do not edit historical `plans/` or `executions/` artefacts.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 4 (update docs; `lint-docs` must pass).
- **Potential failure modes for this step:** Prettier reflow failures; a stale mention missed.
- **Guardrails and early‑abort checks:** Grep for `claude --model` after editing to confirm no stale template remains.

### Success & Discard Criteria

- **Success:** The docs describe the flags and the variable accurately and `uv run task lint-docs` passes.
- **Discard:** N/A (cannot fail meaningfully; at worst a stale line is fixed in review).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.4   |
| impact_pred         | 8.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 6.32  |

### Step Metrics Rationale

Low-risk documentation. `EV = 0.93 * 8.0 + 0.5 * 1.0 - 0.3 * 0.4 - 1.5 = 7.44 + 0.5 - 0.12 - 1.5 = 6.32`.

---

## Step 6: Run the full verification gate from the repository root

- **Sub‑intent recommendation:** NO
- **Reasoning:** A pure verification step; it produces no new code beyond fixes for the failures it finds.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The default-on bypass and the single-pass renderer change no behaviour other than the
  intended argv and rendered prompt, so the full suite and both linters pass with only the declared test updates.
- **Learning target:** Whether any other suite (preflight, CLI, flow, planner integration) silently depended on the
  exact claude argv or on the old order-dependent substitution.
- **Maximum acceptable cost for this learning:** about `1.5 * 2.5` cost units; beyond that, stop and re-plan.

### Intent & Git Integration

- **Step Intent:** Demonstrate acceptance: `uv run task test`, `uv run task lint` and `uv run task lint-docs` pass.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. From the repository root, run `uv run task lint` (ruff check plus ruff format check, and the uv lock check that
   confirms no dependency change). Fix only formatting or lint issues in the touched files.
2. Run `uv run task lint-docs`.
3. Run `uv run task test` (pytest with xdist). Never use `PYTHONPATH`. If an unrelated pre-existing failure appears,
   confirm it also fails on the plan branch before the change and record it rather than "fixing" unrelated code.
4. Confirm via `git status` that no file under `holon-knowledge/ledger/` changed and no new ledger-like file exists.
5. Write execution notes listing every test file modified and why (declared changes from Steps 3 and 4), plus the
   executor `_render` follow-up suggestion.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 5
- **Is Bottleneck:** YES (acceptance requires green tests and lint); `p_success_pred` 0.85 is well above 0.6.

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 4; `holon-config/world/constraints.md` section 3; `docs/metrics.md` section 1.3
  (acceptance criteria).
- **Potential failure modes for this step:** Hidden exact-argv assertions; prettier reflow in docs; xdist-specific env
  leakage.
- **Guardrails and early‑abort checks:** Iterate at most three fix-and-rerun cycles; then stop and report.

### Success & Discard Criteria

- **Success:** All three commands exit 0; the ledger files are untouched.
- **Discard:** More than three fix cycles, or failures requiring changes outside the touched area.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.2   |
| impact_pred         | 10.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 6.94  |

### Step Metrics Rationale

The gate is where hidden coupling surfaces, so it has the lowest probability and is the overall bottleneck.
`EV = 0.85 * 10.0 + 0.5 * 2.0 - 0.3 * 0.2 - 2.5 = 8.5 + 1.0 - 0.06 - 2.5 = 6.94`.

---
