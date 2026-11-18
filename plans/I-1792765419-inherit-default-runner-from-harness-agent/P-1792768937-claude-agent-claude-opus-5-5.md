# Plan for I-1792765419-inherit-default-runner-from-harness-agent

- **Plan ID:** P-1792768937-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-23T15:22:17.934Z

## Planner Autonomy Summary

- **Intent handling:** REFRAME
- **Reframed intent (if applicable):**
  `{"keep": "all seven goals", "amend": ["the antigravity default model literal is confirmed against the live agy model list before it is written into the default table, because the flow path already ships gemini-3.8-flash-medium and that is the label the last ~40 ledger rows carry", "harness detection is disabled when HOLON_ROLE or HOLON_IN_SANDBOX is set, and it ignores credential variables (AGY_USER_TOKEN, GOOGLE_API_KEY) so it only reacts to markers that identify a running agent process", "review-loop keeps routing on the raw --agent value, so the existing local-versus-container decision does not change", "historical provenance fallbacks (calibration.py) keep a fixed legacy constant and never use runtime harness detection"]}`
- **Exploration stance:** balanced. Most of the work is a mechanical consolidation of literals into one resolver
  (exploit). One cheap, read-only info-gathering step checks the agy CLI's model and `--effort` contract first, because
  a wrong guess there would break every default antigravity run.
- **Safety priority level:** elevated
- **Priority Justification:** This intent changes which agent and credentials Holon picks by default, and adds a
  pre-flight that must run before any branch or ledger write. That touches `constraints.md` §1 (branch isolation),
  `constraints.md` §3 (append-only ledger) and `safety.md` Invariant 6 (immutability). If the default runner is
  mis-detected, commits get attributed to the wrong runner and calibration drifts (`safety.md` "Calibration
  degradation"). The level is not critical: nothing here touches estimators, routing policy or core invariants.

## Exploration

- **Proportion of steps that are exploratory:** 0.14 (1 of 7 steps)
- **Justification:** Only one fact can't be read from the repo: which model strings the current agy CLI accepts with
  `--effort`. The repo disagrees with itself. `cli.py` plan/execute default to `gemini-3.5-flash`, while flow,
  review-loop and recent ledger rows use `gemini-3.8-flash-medium`, and the intent asks for `gemini-3.8-flash`. One
  read-only probe settles it for very little cost. Everything else uses existing patterns (the `runners` registry,
  `AGENT_IMAGE_MAPPING`, `PipelineEngine`), so more exploration would add entropy with little learning gain.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.70  |
| entropy_pred        | 10.2  |
| impact_pred         | 90.0  |
| cost_pred           | 30.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 32.44 |

### Strategy Rationale

The intent is high-value. Today's default (`antigravity-agent` with `gemini-3.5-flash`) is rejected by the current agy
CLI, so a plain `holon plan` or `holon execute` with no flags fails, and only after setup work has started. The repo
also has two disagreeing default sets: `cli.py` lines 1159-1190 and `entrypoint/executor.py` line 1083 use
`gemini-3.5-flash`, while `cli.py` lines 1396 and 1778, `flow.py` lines 164-306 and `entrypoint/review_loop.py` line 122
use `gemini-3.8-flash-medium`. Merging them into one resolver removes a real source of drift.

I reframed rather than accepted as-is, for four reasons:

1. **Model label vs provenance.** Goal 4 says ledger rows record the resolved runner and model exactly as today. The
   ledger and plan IDs (`P-...-antigravity-agent-gemini-3.8-flash-medium`) currently carry the raw `--model` string. If
   the default silently becomes `gemini-3.8-flash`, the calibration series splits under two labels for the same model
   and effort. Step 1 checks the live agy contract, and the default table records whichever literal agy accepts with
   `--effort`. If both are accepted, it uses the intent's `gemini-3.8-flash` and documents the label change.
2. **Detection inside the sandbox.** The planning environment itself has `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT` and
   `HOLON_ROLE` set at once. Inside a container, harness detection would pick up the container's own agent rather than
   the operator's harness, so it must be off when `HOLON_ROLE` or `HOLON_IN_SANDBOX` is set. Also, `role_dispatcher.sh`
   exports `AGY_USER_TOKEN` and `GOOGLE_API_KEY`. These are credentials, not harness markers, and must not trigger agy
   detection.
3. **Review-loop routing.** In `cli.py` around line 1855, review-loop runs locally when `args.agent is None`. Resolving
   the agent before that check would silently move every bare `holon review-loop` into a container. The resolver output
   is used only for attribution and image selection, never for the local/container decision.
4. **Historical rows.** `calibration.py` line 1184 fills in missing agent/model on old rows. Using runtime detection
   there would rewrite history depending on who runs calibration. It keeps a fixed `LEGACY_DEFAULT_AGENT` constant.

How the overall metrics were derived:

- `p_success_pred = 0.70`: bottleneck judgement, not a product. The naive product of step probabilities is
  `0.95 * 0.92 * 0.82 * 0.72 * 0.85 * 0.95 * 0.88 = 0.37`, but steps 5 and 7 are cheap and retryable inside the same
  execution, and their failures are highly correlated with step 4. So the overall value is set just under the weakest
  critical step (step 4, `0.72`).
- `entropy_pred = 10.2`: sum of step entropy, `0.3 + 1.6 + 2.8 + 3.2 + 1.4 + 0.6 + 0.3 = 10.2`.
- `impact_pred = 90.0`: qualitative. Step impacts sum to `108`, discounted because steps 5-7 mainly protect the impact
  of steps 2-4 rather than adding to it.
- `cost_pred = 30.0`: sum of step cost, `2 + 4 + 7 + 8 + 5 + 2 + 2 = 30`.
- `learning_value_pred = 5.0`: weighted between the step 1 and step 4 maxima (`6.0`) and the routine steps.
- `ev_pred`: `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` gives
  `EV = 0.70 * 90.0 + 0.5 * 5.0 - 0.3 * 10.2 - 30.0 = 63.00 + 2.50 - 3.06 - 30.00 = 32.44`, with `lambda = 0.3` and
  `mu = 0.5` from `holon-config/metrics/ev_config.json`.

Strong assumptions: the existing image builds already contain the agent binaries, so the probe needs no new image layer.
The probe calls the runner binary directly through `docker run --entrypoint`, with a command line built on the host. It
does not add a new `role_dispatcher.sh` role, which would force every image to be rebuilt before `holon doctor` worked.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1: prefix-based branch isolation. The pre-flight must fail before
    `run_intent_stage` creates or pushes `I-.../_`.
  - `holon-config/world/constraints.md` §3 and `docs/safety.md` Invariant 6: append-only ledger. The pre-flight writes
    nothing to `holon-knowledge/ledger/`, and the intent forbids auxiliary ledgers. Doctor output goes to stdout/stderr
    only (plus optional `--json`).
  - `holon-config/world/constraints.md` §2 and `docs/safety.md` "Container sandbox": the probe runs inside the runner's
    own container with the same network isolation args (`build_network_isolation_args`) and credential mounts
    (`get_agent_session_mounts`) as a real run. The real model call goes through the existing egress path. It never
    calls the provider directly from the host.
  - `holon-config/world/ruleset.md` §2: typed public functions with docstrings for inputs, outputs, exceptions and side
    effects.
  - `holon-config/world/ruleset.md` §3: tests live in `apps/sandbox-executor/tests/`, matching the source module. Test
    edits are declared here explicitly (steps 5 and 3).
  - Intent goal 7: Australian English (initialise, behaviour, authorise, normalise), no new dependencies,
    `uv run pytest` only, ruff check and format, prettier (`printWidth` 120, `proseWrap` always).
- **Potential violations or edge cases:**
  - The pre-flight runs after `save_checkpoint`, leaving a stale `.holon/` flow checkpoint. That is not a ledger, but it
    would make `--from-stage` resume misleading.
  - Harness detection inside a container attributes work to the container's agent rather than the operator's choice.
  - Several markers are present at once (for example Claude Code launched from pi), giving an ambiguous detection.
  - Claude is detected, but the `claude` runner needs `HOLON_AGENT_KEY` and Claude Code's OAuth session isn't mounted.
    The probe must report "not logged in / missing credential" with a clear fix, not a stack trace.
  - Probe output includes provider error bodies that may echo key fragments.
  - Existing tests assert the old literal defaults (92 occurrences across 10 test files). Updating them changes test
    assertions and must be declared.
  - Behaviour change: operators who relied on the antigravity default while working inside Claude Code would now be
    routed to `claude-agent`.
- **Mitigations built into the plan:**
  - The pre-flight runs in `cli.py` before `run_flow_pipeline` is called and before any `FlowContext` checkpoint is
    saved. A test checks that no branch, ledger row or checkpoint was created.
  - Detection is skipped when `HOLON_ROLE` or `HOLON_IN_SANDBOX` is set. A fixed priority order is used, and every
    detected marker is logged together with the winner.
  - Every resolver decision logs its source, for example
    `Agent runner resolved to claude-agent (source: harness detection, marker CLAUDECODE)`.
    `HOLON_AGENT=antigravity-agent` restores the old behaviour in one line, and the docs say so.
  - Probe output is passed through the existing `redact_text` / `GITHUB_TOKEN`/`HOLON_AGENT_KEY` redaction before
    printing.
  - The probe uses a hard timeout (default 60 s, overridable through `HOLON_PREFLIGHT_TIMEOUT`) and a fixed one-token
    prompt.
- **Residual risk accepted (and why):** The probe spends one real minimal model call per `plan`, `execute` or `flow`
  invocation. The intent's clarifying answer requires this, and it is far cheaper than a flow failing at Stage 2 after
  branch creation. `--skip-preflight` is the escape hatch. Free-text classification of provider errors is heuristic, so
  unrecognised failures land in `unknown` and still abort.
- **Allocated Entropy Budget:** 12.0
- **Predicted Plan Entropy:** 10.2
- **Budget Compliance:** The strategy fits within budget (10.2 of 12.0, leaving 1.8 headroom for the step 4
  classification heuristics, which are the most likely to overrun).

## Plan Description & Strategy

The work is ordered by dependency and risk.

1. Gather evidence (agy model contract, full literal inventory, test assertions to update).
2. Build a pure, side-effect-free resolver module, `sandbox_executor/agent_defaults.py`, holding the precedence chain,
   the harness-marker table, the per-runner default model table, and the legacy constant.
3. Wire the resolver into every call site, replacing literals and keeping provenance unchanged.
4. Add the pre-flight probe (`sandbox_executor/preflight.py`), the `holon doctor` subcommand, and the `--skip-preflight`
   gating on plan, execute and flow.
5. Add and update tests.
6. Update docs.
7. Run the full verification gate.

Steps 3 and 4 are recommended as sub-intents because they touch the two hot files (`cli.py` at 2125 lines and `flow.py`
at 3417 lines) and carry most of the risk. Steps 1, 2, 5, 6 and 7 run as execution branches under this plan. Step 4
depends on step 2, not step 3, so the two sub-intents can run in parallel once step 2 has merged. Both edit `cli.py`, so
the step 4 sub-intent rebases onto its parent after step 3 merges (`constraints.md` §1 rebase discipline).

---

## Step 1: Confirm the agy model/effort contract and inventory every default literal

- **Sub‑intent recommendation:** NO
- **Reasoning:** This step is read-only, cheap and low risk. Its output (a decision note) feeds the default table in
  step 2, and the recurring agy model churn makes it worth recording in the knowledge base.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The current agy CLI accepts `gemini-3.8-flash` with `--effort medium`. The literal
  `gemini-3.8-flash-medium` used by the flow path is either also accepted as an alias, or it works because agy tolerates
  effort-suffixed names.
- **Learning target:** The exact model strings agy lists and accepts with `--effort`. Whether changing the antigravity
  default label from `gemini-3.8-flash-medium` to `gemini-3.8-flash` would split calibration series. The complete list
  of files and lines holding default literals, including tests.
- **Maximum acceptable cost for this learning:** `cost_pred` 2, with no more than one real model call. If agy isn't
  reachable in the execution sandbox, fall back to reading `agy --help` or the model list output only, and record the
  decision as "intent literal adopted, unverified".

### Intent & Git Integration

- **Step Intent:** Turn the antigravity default-model choice from a guess into an evidence-backed decision, and produce
  the full change inventory for steps 3 and 5.
- **Git branch:**
  `I-1792765419-inherit-default-runner-from-harness-agent/P-1792768937-claude-agent-claude-opus-5-5/E-{timestamp}-inventory-defaults/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Inside the `holon/agent-antigravity` image, run the agy model listing command and `agy --help`, and capture which
   models are listed and whether `--effort` is documented per model. Do not commit any credentials or raw output that
   contains account identifiers.
2. If credentials are available, try one minimal prompt each with `--model gemini-3.8-flash --effort medium` and
   `--model gemini-3.8-flash-medium --effort medium`, and record accept or reject.
3. Grep `apps/sandbox-executor/src` and `apps/sandbox-executor/tests` for `antigravity-agent`, `"antigravity"`,
   `gemini-3.5-flash` and `gemini-3.8-flash-medium`. Classify each hit as one of: a default to replace, provenance to
   keep (calibration legacy fallback, `mitm_addon.py` `HOLON_AGENT_ID` label), or a test assertion to update.
4. Record the decision and inventory in the execution report (`executions/E-...md`). Do not create any new ledger file.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO (if it fails, step 2 adopts the intent's literal `gemini-3.8-flash` and the risk is documented)

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §2 (container sandbox), `safety.md` "Sandbox escape detection", intent goal 7 (no
  auxiliary ledgers)
- **Potential failure modes for this step:** agy unreachable or quota-exhausted. Model list output leaking account data.
- **Guardrails and early‑abort checks:** At most one real call per candidate literal. Redact output before recording it.
  Abort the live calls on the first 429 and keep the help-text evidence only.

### Success & Discard Criteria

- **Success:** A recorded decision naming the antigravity default literal and its evidence, plus a complete inventory
  table of literal sites.
- **Discard:** Cost exceeds `1.5 * 2 = 3` units, or credentials are unavailable. Then adopt `gemini-3.8-flash` as stated
  in the intent and mark it unverified.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.3   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 10.41 |

### Step Metrics Rationale

This step is read-only, so entropy is minimal. Learning value is high because the repo holds three conflicting model
literals and the agy contract has already changed under the project once.
`EV = 0.95 * 10.0 + 0.5 * 6.0 - 0.3 * 0.3 - 2.0 = 9.50 + 3.00 - 0.09 - 2.00 = 10.41`.

---

## Step 2: Add the shared resolver module (agent runner, model default, harness detection)

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is a new, pure module with no side effects beyond logging, and it is easy to unit test. It is
  reusable, but small enough to run as an execution branch.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Provide one source of truth, `resolve_agent_runner()` and `resolve_model()`, with logged decisions,
  and fail early with an actionable message when a runner has no mapped image.
- **Git branch:**
  `I-1792765419-inherit-default-runner-from-harness-agent/P-1792768937-claude-agent-claude-opus-5-5/E-{timestamp}-resolver-module/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/src/sandbox_executor/agent_defaults.py` with a module docstring and these parts:
   - `FALLBACK_AGENT = "antigravity-agent"` and `LEGACY_DEFAULT_AGENT` / `LEGACY_DEFAULT_MODEL` constants for historical
     provenance back-fill only.
   - `DEFAULT_MODELS`, mapping each normalised runner id (`antigravity`, `claude`, `pi`, `codex`, `gemini`, `opencode`)
     to its default model. Antigravity uses the step 1 literal. Other runners use the model their CLI picks when
     `--model` is omitted, written explicitly, or an entry marking "omit `--model`" if the CLI has no stable default.
     The entries are decided in step 1 and documented.
   - `HARNESS_MARKERS`, an ordered tuple of (runner id, env var name or prefix, match kind): `PI_CODING_AGENT` for pi;
     `CLAUDECODE` and `CLAUDE_CODE_ENTRYPOINT` for claude; `CODEX_` prefix markers for codex, excluding credential
     variables such as `CODEX_API_KEY` (to be confirmed in step 1); `OPENCODE` markers for opencode, excluding
     `OPENCODE_API_KEY`; agy/antigravity process markers for antigravity, explicitly excluding `AGY_USER_TOKEN` and
     `GOOGLE_API_KEY`. The tuple order is the documented tie-break.
   - A small frozen dataclass `Resolution` holding `value`, `source` (flag, env, harness, fallback, table) and `reason`,
     so call sites can log the outcome and tests can assert on it.
   - `normalise_agent_id(name)`, factoring out the duplicated `.replace("-agent", "").replace("agent-", "")` logic.
   - `detect_harness_agent(environ)`: returns None when `HOLON_ROLE` or `HOLON_IN_SANDBOX` is truthy. Otherwise returns
     the first matching runner and logs every other marker that also matched.
   - `resolve_agent_runner(explicit, environ=None)`: explicit, then `HOLON_AGENT`, then harness, then fallback. It
     normalises the result to the canonical `{id}-agent` form used in ledger rows and plan IDs today, logs one INFO line
     with the source and reason, and checks the id against `AGENT_IMAGE_MAPPING`. If unmapped, it raises a dedicated
     `UnmappedAgentRunnerError(ValueError)` whose message lists the supported runners and names the variable that
     supplied the bad value.
   - `resolve_model(agent_id, explicit, environ=None)`: explicit, then `HOLON_MODEL`, then `DEFAULT_MODELS[agent_id]`,
     logged the same way.
2. Move `AGENT_IMAGE_MAPPING` into `agent_defaults.py` and re-export it from `cli.py`, so existing imports keep working.
   This avoids a `cli` / `flow` import cycle.
3. Treat `environ` as an injected mapping (defaulting to `os.environ`), so tests never need to monkeypatch globals.
4. Write all docstrings and log strings in Australian English. Add no new dependencies (stdlib `logging`, `os`,
   `dataclasses` only).

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (every later step consumes this API)

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` §2 (typing, docstrings, clean architecture), intent goals 1 and 2
- **Potential failure modes for this step:** Import cycle once `AGENT_IMAGE_MAPPING` moves. Harness false positives from
  credential variables. Canonical-form mismatch (for example `claude` vs `claude-agent`) changing plan IDs.
- **Guardrails and early‑abort checks:** The canonical-form rule is that output always matches `{id}-agent`, which is
  what `antigravity-agent` rows already use. A unit test pins this. If an import cycle shows up, stop and keep the
  mapping in `cli.py`, using a lazy import inside the resolver.

### Success & Discard Criteria

- **Success:** The module imports cleanly, ruff is clean, and unit tests for every precedence tier pass.
- **Discard:** The design needs changes to `agent_runner.runners` semantics, or cost exceeds `6` units.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.6   |
| impact_pred         | 25.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 20.02 |

### Step Metrics Rationale

This is a new isolated file with a well-specified contract, so success is high. Entropy comes from adding a new module
path (NOV) and moving one constant.
`EV = 0.92 * 25.0 + 0.5 * 3.0 - 0.3 * 1.6 - 4.0 = 23.00 + 1.50 - 0.48 - 4.00 = 20.02`.

---

## Step 3: Wire the resolver into every call site and remove the duplicated literals

- **Sub‑intent recommendation:** YES
- **Reasoning:** This step touches the two hot files (`cli.py` and `flow.py`) plus `entrypoint/executor.py`,
  `entrypoint/review_loop.py`, `token_reduction/ringer_orchestrator.py` and `calibration.py`, and it carries the
  review-loop routing and provenance subtleties. A separate branch and evaluation contains conflicts with the step 4
  `cli.py` edits.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make plan, execute, review-loop and flow all use the resolver, with no change to the runner and model
  strings recorded in ledger rows for a given resolved value.
- **Git branch:** `I-1792765419-inherit-default-runner-from-harness-agent/I-{timestamp}-wire-agent-resolver/_`
- **Sub‑intent:** NEW

### Implementation Details (No code blocks, only logic/steps)

1. In `cli.py`, change `--agent` and `--model` for plan, execute, flow and review-loop to `default=None`, and update the
   help text to describe the precedence rather than a literal. After parsing, for plan, execute and flow, call
   `resolve_agent_runner(args.agent)` and then `resolve_model(agent_id, args.model)`. Assign the results back to
   `args.agent` and `args.model` so `container_args` and the downstream ledger rows carry the resolved strings,
   preserving provenance.
2. Replace the inline `agent_id = args.agent.replace(...)` near line 2075 and the review-loop equivalent near line 1879
   with `normalise_agent_id`. Replace `AGENT_IMAGE_MAPPING.get(agent_id, f"holon/agent-{agent_id}")` with a strict
   lookup that raises the step 2 error, so unmapped runners fail before Docker is invoked.
3. Review-loop: keep the `is_local` decision based on the raw `args.agent is None`, computed before resolution. Only
   then resolve for image and attribution. Add a comment explaining why.
4. In `flow.py`, make the `FlowContext.agent` and `.model` dataclass defaults empty strings. Resolve them in the
   `cli.py` flow branch (which already constructs `FlowContext`). In `migrate_checkpoint_data` (around line 164) and
   `FlowContext.from_dict` (around line 305), keep a checkpoint's recorded agent and model when present. When absent,
   back-fill with `LEGACY_DEFAULT_AGENT` / `LEGACY_DEFAULT_MODEL`, because an existing checkpoint was produced under the
   legacy default and must resume with the same attribution. In consensus attribution (around line 1360) and the review
   record (around line 2735), replace the `or "antigravity-agent"` fallbacks with `context.agent`, which is now always
   resolved, and fall back to the legacy constant only for consensus payloads read from historical data.
5. In `entrypoint/executor.py` (around line 1083), replace the argv fallbacks with `FALLBACK_AGENT` and
   `DEFAULT_MODELS`. Do not use harness detection inside the container (the step 2 guard enforces this anyway). Do the
   same in `entrypoint/review_loop.py` (around line 121).
6. In `token_reduction/ringer_orchestrator.py`, make `executor_model` default to the antigravity entry of
   `DEFAULT_MODELS`.
7. In `calibration.py` (around line 1184), replace the literals with `LEGACY_DEFAULT_AGENT` / `LEGACY_DEFAULT_MODEL`
   (same values as today), so historical calibration is unchanged.
8. Leave `holon intent` (orchestrator image, fixed `agent_id="antigravity"` for credential plumbing) and the
   `mitm_addon.py` telemetry label unchanged. Note them as out of scope in the execution report.
9. Update the existing test assertions that pin the old literal defaults, as inventoried in step 1. This is a declared
   test change under `ruleset.md` §3. Each updated assertion either pins the resolver output under a controlled
   `environ`, or keeps its current literal by passing `--agent` / `--model` explicitly.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (goals 1, 2 and 4 depend on it)

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §1 (commit boundaries, rebase discipline), `constraints.md` §3 (provenance and
  append-only), `ruleset.md` §3 (declared test edits), intent goal 4
- **Potential failure modes for this step:** Bare `holon review-loop` silently switching to container mode. Resumed
  checkpoints changing attribution. Many test assertions breaking at once. Rebase conflicts in `cli.py` against step 4.
- **Guardrails and early‑abort checks:** Run
  `uv run pytest tests/test_cli.py tests/test_flow.py tests/test_review_loop.py tests/test_executor.py tests/test_calibration.py`
  after each file is converted. Diff a sample ledger row produced by a dry-run flow before and after, with explicit
  `--agent antigravity-agent`, and require byte-identical agent and model fields.

### Success & Discard Criteria

- **Success:** `grep` finds no `gemini-3.5-flash` in `src/`. The only `antigravity-agent` literals left in `src/` are in
  `agent_defaults.py`. All targeted test files pass. The dry-run provenance diff is empty.
- **Discard:** More than 3 rebase-conflict cycles with step 4, or provenance diff not empty after one fix attempt.
  Escalate per `safety.md` "Rebase conflict".

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 2.8   |
| impact_pred         | 25.0  |
| cost_pred           | 7.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 14.16 |

### Step Metrics Rationale

There are six source files and roughly ten test files, and two of the source files are hot, so SSA and CL dominate
entropy. Success is reduced by the review-loop routing and checkpoint resumption subtleties.
`EV = 0.82 * 25.0 + 0.5 * 3.0 - 0.3 * 2.8 - 7.0 = 20.50 + 1.50 - 0.84 - 7.00 = 14.16`.

---

## Step 4: Add the pre-flight readiness probe, `holon doctor`, and gating before Stage 1

- **Sub‑intent recommendation:** YES
- **Reasoning:** This is the riskiest and most novel step: real container calls, heuristic error classification, and a
  strict "before any branch or ledger write" ordering guarantee. It is reusable by future runners and deserves its own
  evaluation.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** One minimal prompt per runner, run inside its normal container with the same mounts and
  network args, reliably separates ok, quota-exhausted, missing-credential, expired-key and unknown outcomes using exit
  code plus stderr/stdout pattern matching.
- **Learning target:** The real error signatures each runner CLI emits for 429 / `RESOURCE_EXHAUSTED`, missing session,
  and expired or invalid key, recorded as a reusable pattern table for the knowledge base.
- **Maximum acceptable cost for this learning:** `cost_pred` 8. Classification patterns are capped at what can be
  verified for antigravity and claude. Other runners can map to `unknown` with a documented gap rather than guessed
  patterns.

### Intent & Git Integration

- **Step Intent:** Fail fast with an actionable diagnosis before Holon creates branches, pushes, or appends ledger rows.
- **Git branch:** `I-1792765419-inherit-default-runner-from-harness-agent/I-{timestamp}-preflight-probe/_`
- **Sub‑intent:** NEW

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/src/sandbox_executor/preflight.py` containing:
   - A `PreflightStatus` StrEnum with `ok`, `quota_exhausted`, `not_logged_in`, `expired_key` and `unknown`.
   - A frozen dataclass `PreflightResult` holding status, agent, model, image, exit code, a redacted output excerpt, and
     a remediation hint string.
   - `classify_probe_output(exit_code, output)`: a pure function with an ordered pattern table. Quota patterns are
     `429`, `RESOURCE_EXHAUSTED` and quota/rate-limit phrases. Not-logged-in patterns cover missing session, "not logged
     in", "please login", absent credential directories, and the existing `validate()` error texts from
     `agent_runner.py` lines 270-370. Expired-key patterns cover expired, invalid API key, `401` and `UNAUTHENTICATED`.
     Exit code 0 with non-empty output means `ok`. Anything else is `unknown`.
   - `run_preflight_probe(agent, model, repo_dir, timeout, runner_fn=None)`: builds the probe command on the host via
     the runner's `build_cmd` logic without calling the host-side `validate()` (the binary only exists in the image). It
     reuses the docker argument assembly from `run_docker_container`. That assembly is factored into a helper that
     returns the argv without executing it, so mounts, network isolation and env forwarding stay identical. The probe
     overrides the entrypoint to the runner binary, uses a fixed one-token prompt ("Reply with the single word OK."),
     and applies the timeout. The `runner_fn` injection point (defaulting to `subprocess.run`) exists for tests. Output
     is redacted with the existing `redact_text` helper before it is stored.
   - Remediation hints per status, for example the session-initialise command already printed by
     `get_agent_session_mounts`, setting `HOLON_AGENT_KEY`, waiting for quota reset or choosing another runner via
     `--agent` / `HOLON_AGENT`.
2. In `cli.py`, add a `doctor` subparser with `--agent`, `--model`, `--repo-dir`, `--json` and `--timeout`. It resolves
   the agent and model through the step 2 resolver and prints the resolution source plus the probe result. It exits 0
   only on `ok`. It writes nothing to `holon-knowledge/`, `plans/` or the ledgers.
3. Add `--skip-preflight` to plan, execute and flow. In each command branch, run the probe immediately after resolution,
   before `run_docker_container` (plan/execute) and before constructing or saving `FlowContext` and calling
   `run_flow_pipeline` (flow). On a non-ok status, print the diagnosis and exit non-zero. When resuming flow with
   `--from-stage` past `intent`, run the probe as well. Skip the probe for `--dry-run` flows, which never call the
   agent; log that it was skipped.
4. Also honour `HOLON_SKIP_PREFLIGHT=1`, so the inner container invocations and CI do not re-probe recursively.
   `run_docker_container` sets this variable for the containers it launches.
5. Keep the probe strictly read-only with respect to the repo: no git commands, no writes under `.holon/`, and no
   checkpoint save.

### Dependencies & Criticality

- **Depends on:** Step 2. Rebase onto the parent after step 3 merges, because both edit `cli.py`.
- **Is Bottleneck:** YES (goal 3, and the main test in goal 5)

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §1 and §3 (no branch or ledger write before readiness), `constraints.md` §2 and
  `safety.md` "Container sandbox" (the probe runs in the same isolated container), `safety.md` "Sandbox escape
  detection" (no direct host-to-provider egress), intent goal 7 (no auxiliary ledgers)
- **Potential failure modes for this step:** Classification false negatives (a quota error reported as `unknown` still
  aborts, which is safe). False positives that block a healthy runner, mitigated by `--skip-preflight`. Refactoring
  `run_docker_container` changes its behaviour. Probe hangs on an interactive login prompt. Secrets in the probe output
  excerpt.
- **Guardrails and early‑abort checks:** The probe uses `-i` without a TTY plus a hard timeout, and a timeout maps to
  `unknown` with a "possible interactive login" hint. A regression test asserts that the docker argv from the factored
  helper equals what `run_docker_container` executed before the refactor. If the refactor proves invasive (more than
  about 80 changed lines in `run_docker_container`), stop and duplicate only the mount/network assembly calls instead.

### Success & Discard Criteria

- **Success:** `holon doctor` reports `ok` against a working antigravity setup and a correct non-ok status for a forced
  bad key. A mocked quota-exhausted probe aborts flow with no branch, ledger row or checkpoint created.
- **Discard:** `p_success` drops below `0.4` (for example the image entrypoint override is blocked by the images), or
  cost exceeds `1.5 * 8 = 12`. Then fall back to a credential-presence-only pre-flight, record that as a partial
  outcome, and re-propose the real-call probe as a new intent.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.72  |
| entropy_pred        | 3.2   |
| impact_pred         | 25.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 12.04 |

### Step Metrics Rationale

This is the highest entropy in the plan: a new module, a refactor of the container launcher, and real external calls
(SER and NOV). Success is limited by heuristic classification and by reproducing real error signatures. Learning value
is high, because the runner failure-signature table is reusable and currently undocumented.
`EV = 0.72 * 25.0 + 0.5 * 6.0 - 0.3 * 3.2 - 8.0 = 18.00 + 3.00 - 0.96 - 8.00 = 12.04`.

---

## Step 5: Tests for precedence, model defaults, unmapped runners and probe-abort ordering

- **Sub‑intent recommendation:** NO
- **Reasoning:** These are additive test files with clear specifications and low blast radius. They can run as an
  execution branch, and each sub-intent also adds its own tests.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Lock in goal 5's behaviour so future default changes cannot silently regress precedence or the "no
  write before readiness" guarantee.
- **Git branch:**
  `I-1792765419-inherit-default-runner-from-harness-agent/P-1792768937-claude-agent-claude-opus-5-5/E-{timestamp}-resolver-preflight-tests/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add `apps/sandbox-executor/tests/test_agent_defaults.py`, matching the new module, with these tests:
   - Parametrised precedence cases: flag beats env, env beats harness, harness beats fallback. Fallback is logged at
     INFO with source `fallback` (asserted via `caplog`).
   - Each harness marker maps to the right runner. Multiple markers resolve by the documented order and log the ignored
     markers.
   - `HOLON_ROLE` or `HOLON_IN_SANDBOX` disables detection.
   - `AGY_USER_TOKEN` and `GOOGLE_API_KEY` alone do not trigger agy detection.
   - Canonical `{id}-agent` normalisation.
   - Unmapped runner (for example `HOLON_AGENT=foo-agent`) raises `UnmappedAgentRunnerError`, and the message lists the
     supported runners and names `HOLON_AGENT` as the source.
   - Model resolution: flag beats `HOLON_MODEL`, which beats the table. The antigravity table entry equals the step 1
     literal and is never `gemini-3.5-flash`.
2. Add `apps/sandbox-executor/tests/test_preflight.py` covering:
   - The classifier truth table over representative outputs (429, `RESOURCE_EXHAUSTED`, not logged in, missing session
     dir, expired key, 401, timeout, and empty output with exit 0).
   - Redaction of `HOLON_AGENT_KEY` values in the excerpt.
   - `holon doctor` exit codes and `--json` shape via `main()`, with the probe mocked.
3. In `tests/test_flow.py` or `tests/test_cli.py`, add the mandated abort test. Run `main()` for `flow` against a
   temporary git repo built with the existing `hermetic_fixtures.py`, with the probe mocked to return `quota_exhausted`.
   Assert:
   - non-zero exit;
   - `run_intent_stage` was never called;
   - `git branch --list "I-*"` is empty and no ref was pushed;
   - `holon-knowledge/ledger/intents.jsonl`, `plans.jsonl` and `executions.jsonl` are byte-identical to before;
   - no checkpoint file exists.
4. Add tests showing `--skip-preflight` and `HOLON_SKIP_PREFLIGHT` bypass the probe, and that a bare `holon review-loop`
   still runs locally.
5. Inject `environ` mappings or use `monkeypatch.delenv` for every harness variable, so the suite passes both inside and
   outside Claude Code, pi, and the other harnesses. This environment itself has `CLAUDECODE` set, so leaking harness
   variables would make tests flaky.
6. Run only with `uv run pytest` from `apps/sandbox-executor`: never unittest, never `PYTHONPATH`.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 4
- **Is Bottleneck:** NO (failing tests are fixed in place; they don't invalidate the design)

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` §3 (pytest, test location, declared test edits), intent goals 5 and 7,
  `apps/sandbox-executor/docs/hermetic_testing.md`
- **Potential failure modes for this step:** Ambient harness variables leaking into tests. Tests accidentally making a
  real docker or model call. Hermetic guard (`test_sandbox_hermetic_guard.py`) violations.
- **Guardrails and early‑abort checks:** An autouse fixture in the new test modules clears all harness markers and
  `HOLON_AGENT` / `HOLON_MODEL`. Probe calls are always mocked, and an assertion checks `subprocess.run` is never called
  with `docker`.

### Success & Discard Criteria

- **Success:** The new tests pass, and the full `uv run pytest` passes in `apps/sandbox-executor`.
- **Discard:** The abort-ordering test can't be made hermetic within `1.5 * 5 = 7.5` cost. Then fall back to asserting
  call order on mocked `run_intent_stage` / git helpers, and document the gap.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.4   |
| impact_pred         | 12.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 6.28  |

### Step Metrics Rationale

The tests are additive and use existing hermetic fixtures. Risk comes mostly from ambient environment leakage and the
end-to-end abort test. `EV = 0.85 * 12.0 + 0.5 * 3.0 - 0.3 * 1.4 - 5.0 = 10.20 + 1.50 - 0.42 - 5.00 = 6.28`.

---

## Step 6: Document precedence, `HOLON_AGENT`, `HOLON_MODEL` and `holon doctor`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Documentation only, low risk. It can run as an execution branch.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the new defaulting behaviour discoverable, and give operators a one-line way to restore the old
  default.
- **Git branch:**
  `I-1792765419-inherit-default-runner-from-harness-agent/P-1792768937-claude-agent-claude-opus-5-5/E-{timestamp}-docs-runner-precedence/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `docs/agents.md`, add a "Runner and model resolution" section covering:
   - the precedence table;
   - the harness marker table with tie-break order;
   - the in-sandbox detection guard;
   - the per-runner default model table;
   - fallback logging;
   - `HOLON_AGENT=antigravity-agent` to restore the legacy default.
2. In `docs/executor/agent_credentials_requirements.md` and `docs/planner/agent_credentials_requirements.md`, add a
   `holon doctor` section listing the statuses, example output, remediation per status, `--skip-preflight` and
   `HOLON_SKIP_PREFLIGHT`, and a note that one minimal real model call is made.
3. Replace stale `gemini-3.5-flash` / hard-coded default mentions in `README.md`, `apps/sandbox-executor/README.md`,
   `docs/sandbox/create_plan.md`, `docs/sandbox/execute_plan.md` and `docs/ledger_schema.md`. Leave literals only where
   they describe historical rows. Do not edit historical `plans/` or `executions/` files.
4. Run prettier with the repository configuration (`.prettierrc.json`) on the changed files only, and write in
   Australian English.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 4 (documented names must match the implementation)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goals 6 and 7 (Australian English, prettier); `constraints.md` §3 by analogy (historical
  plan and execution records stay untouched)
- **Potential failure modes for this step:** Prettier reflowing unrelated files. Docs drifting from flag names.
- **Guardrails and early‑abort checks:** Limit prettier to the changed paths, then run `make lint-md`.

### Success & Discard Criteria

- **Success:** Prettier check passes. Every flag and env var named in the docs exists in the `holon --help` output.
- **Discard:** Cost exceeds `3` units.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.6   |
| impact_pred         | 5.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 3.07  |

### Step Metrics Rationale

This step is routine prose. Its small entropy reflects the five to seven files touched.
`EV = 0.95 * 5.0 + 0.5 * 1.0 - 0.3 * 0.6 - 2.0 = 4.75 + 0.50 - 0.18 - 2.00 = 3.07`.

---

## Step 7: Full verification gate (pytest, ruff, format, markdown, provenance spot-check)

- **Sub‑intent recommendation:** NO
- **Reasoning:** A mandatory, cheap closing check that produces the evidence for human review of the root intent's merge
  to `main`.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the integrated root-intent branch satisfies every constraint in goal 7 before it is presented
  for human review.
- **Git branch:**
  `I-1792765419-inherit-default-runner-from-harness-agent/P-1792768937-claude-agent-claude-opus-5-5/E-{timestamp}-verification-gate/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Rebase onto the intent branch after both sub-intents have merged (`constraints.md` §1).
2. In `apps/sandbox-executor`, run `uv run pytest`, `uv run ruff check` and `uv run ruff format --check`. At the repo
   root, run `make lint-md` (or prettier `--check` on the changed markdown) and `make lint-json`.
3. Check that `git diff` against the intent branch base adds no new `.jsonl` file anywhere, and no dependency changes in
   either `pyproject.toml` or in `uv.lock`.
4. Provenance spot-check: with `--agent antigravity-agent --model <step 1 literal>` explicit, a dry-run flow produces
   context agent and model fields identical to the inputs. With no flags under a `CLAUDECODE`-only environment, the log
   shows `source: harness detection`.
5. Spot-check Australian English in new comments, docstrings and log strings (for example grep for `initialize`,
   `behavior` and `authorize` in the diff).
6. Record results in the execution report.

### Dependencies & Criticality

- **Depends on:** Steps 3, 4, 5, 6
- **Is Bottleneck:** NO (a failure loops back to the failing step rather than sinking the plan)

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 7, `metrics.md` §1.3 (acceptance criteria), `safety.md` "Human review boundaries"
  (root-intent merge)
- **Potential failure modes for this step:** Pre-existing unrelated test failures on `main`. Prettier version drift.
- **Guardrails and early‑abort checks:** Compare against a baseline `uv run pytest` run on the intent branch's base
  commit, and report pre-existing failures separately rather than "fixing" unrelated code.

### Success & Discard Criteria

- **Success:** All checks green, or only documented pre-existing failures. No auxiliary ledger, no new dependency.
- **Discard:** New failures persist after two fix iterations. Then hand back to the owning step and do not request the
  merge.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.3   |
| impact_pred         | 6.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 4.19  |

### Step Metrics Rationale

This step is verification only and changes almost no state. The success probability reflects the chance of pre-existing
or formatting failures. `EV = 0.88 * 6.0 + 0.5 * 2.0 - 0.3 * 0.3 - 2.0 = 5.28 + 1.00 - 0.09 - 2.00 = 4.19`.

---
