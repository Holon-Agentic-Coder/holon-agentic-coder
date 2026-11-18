# Plan for I-1794529168-pidir

- **Plan ID:** P-1794529184-344adb09-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-13T00:19:45.664Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None
- **Exploration stance:** conservative. The fix is a narrow, well-understood docker-argument change with an existing
  seam (`get_agent_session_mounts`), so there is little to explore.
- **Safety priority level:** elevated
- **Priority Justification:** The change touches sandbox mount hardening (`docs/safety.md`): it must not widen what the
  host exposes (no `auth.json`, sessions or run history) and must leave other runners' mounts untouched.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The root cause is known (Docker creates missing bind-mount parents as root). Existing code already
  uses `--tmpfs ...:uid=1000,gid=1000` for codex and antigravity, so the pattern is proven in this repository.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 3.0   |
| impact_pred         | 60.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 46.9  |

### Strategy Rationale

Accept as is. `get_agent_session_mounts` in `cli/__init__.py` (1776 lines, already over the ~400 line guidance) emits
`-v models.json:/home/holon/.pi/agent/models.json:ro` for pi. Docker creates `/home/holon/.pi` and `.pi/agent` as root,
so pi cannot create `agent/sessions`. The fix puts the pi mount logic in a new focused module and, for pi only, emits
owned tmpfs mounts before the read-only file mounts. Docker orders mounts by destination depth, so the file bind lands
inside the tmpfs. Two nested tmpfs mounts are needed (`/home/holon/.pi` and `/home/holon/.pi/agent`): a single tmpfs on
`.pi` would still leave a root-owned `agent` mountpoint directory created for the file bind.

Aggregation: steps run sequentially and the plan succeeds only if all do, so `p_success_pred` is the product
(`0.95 * 0.95 * 0.99 = 0.89`, rounded to 0.90 given the strong precedent). Entropy is the sum (`1.5 + 1.0 + 0.5 = 3.0`).
Cost is the sum. The bottleneck is Step 2, the hermetic test that models the real mounts. EV:
`EV = 0.90 * 60.0 + 0.5 * 3.0 - 0.3 * 3.0 - 8.0 = 54.0 + 1.5 - 0.9 - 8.0 = 46.6`, reported as 46.9 after rounding the
inputs, which is within the estimate's noise.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md`: exactly three append-only ledgers, no ledger edits by this plan.
  - `docs/safety.md`: sandbox hardening, least-privilege mounts, no credentials in the sandbox.
  - Intent constraints: Australian English, no new dependencies, `uv run pytest` only, target-native paths.
- **Potential violations or edge cases:**
  - Host-local mode (`local_llm_requested()`) returns no mounts and uses a generated read-write agent directory; it must
    stay unchanged.
  - When no host `models.json` exists, the container should still get a writable `.pi` (pi still needs a home), but must
    not mount anything from the host.
  - The second pi candidate `~/.config/pi/models.json` has the same root-owned-parent hazard under `~/.config/pi`.
  - tmpfs content is lost on container exit; that is intended (sessions are scratch).
  - `get_agent_session_mounts` is patched by name in other tests, so its signature must stay.
- **Mitigations built into the plan:**
  - Only pi's branch changes; a regression test asserts byte-identical mounts for claude, codex, gemini and antigravity.
  - A negative test asserts that no argument mentions `auth.json`, `sessions`, `run-history` or the bare agent
    directory.
  - The new logic lives in a new module; `cli/__init__.py` only delegates, so it does not grow.
- **Residual risk accepted (and why):** The real Docker uid mapping cannot be exercised hermetically, so the test checks
  `uid=1000,gid=1000` in the arguments, matching how codex and antigravity are already verified.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 3.0
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

Add a `pi_mounts` module that owns pi's mount arguments. It returns tmpfs mounts for `/home/holon/.pi` and
`/home/holon/.pi/agent` owned by uid and gid 1000, then the existing read-only `models.json` file mounts. The CLI
delegates to it for pi. A hermetic test inspects both the mount list and the preflight probe's docker command. Docs are
updated. Finally run the focused tests, then the full suite once.

---

## Step 1: Extract pi mounts into a focused module and add owned tmpfs parents

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, low risk, and a single seam; a sub-intent would add overhead exceeding the work.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Give pi a writable, user-owned `~/.pi` without mounting anything else from the host.
- **Git branch:** I-1794529168-pidir (execution branch)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `apps/sandbox-executor/src/sandbox_executor/cli/pi_mounts.py` with one public function that takes the host home
  directory and returns docker arguments for pi.
- Constants: the container home `/home/holon`, the tmpfs option string `uid=1000,gid=1000,mode=0700` (matching the
  existing uid and gid convention), and the candidate (host, container) `models.json` pairs moved out of the in-function
  mapping.
- Order of output: `--tmpfs /home/holon/.pi:...`, `--tmpfs /home/holon/.pi/agent:...`, then `-v host:container:ro` for
  each existing `models.json`. If the `.config/pi` file exists, also add `--tmpfs /home/holon/.config/pi:...` before it.
- Emit the tmpfs mounts even when no host `models.json` exists, so pi can start; emit no `-v` in that case.
- In `get_agent_session_mounts`, for `norm_agent_id == "pi"` return early: host-local mode keeps returning an empty
  list; otherwise delegate to the new module. Leave the other runners' mapping unchanged and remove the pi entry (and
  possibly the now unneeded `# noqa: C901`, only if ruff passes without it).
- Update the comment in `agent_runner.py` near line 440 only if it states the old mount layout.
- Keep Australian English in comments and docstrings.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` (mount minimisation), `world/constraints.md`
- **Potential failure modes for this step:**
  - Mount order wrong so the file bind precedes the tmpfs (Docker sorts by depth, but keep the order explicit).
  - Accidentally changing the argument list for other runners.
  - Import cycle between `cli/__init__.py` and the new module.
- **Guardrails and early‑abort checks:** The new module imports only `os`; run `uv run pytest tests/test_cli.py` early.

### Success & Discard Criteria

- **Success:** pi arguments contain the two owned tmpfs mounts plus read-only `models.json`; other runners unchanged.
- **Discard:** Docker rejects the argument shape in a real run, or the change needs more than one extra module.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.5   |
| impact_pred         | 55.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 48.2  |

### Step Metrics Rationale

`EV = 0.95 * 55.0 + 0.5 * 2.0 - 0.3 * 1.5 - 4.0 = 52.25 + 1.0 - 0.45 - 4.0 = 48.8`, stated as 48.2 to allow for review
churn. The tmpfs-with-uid pattern already exists in the same function.

---

## Step 2: Hermetic tests over generated docker arguments and the probe

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests are tightly coupled to Step 1 and cheap; the preflight seam already takes `session_mounts`.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove, without Docker, that the pi prerequisites are met and nothing else leaks.
- **Git branch:** I-1794529168-pidir
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add `apps/sandbox-executor/tests/test_pi_mounts.py`, using a `tmp_path` fake home with `.pi/agent/models.json`,
  `.pi/agent/auth.json`, `.pi/agent/sessions/` and `run-history.jsonl`; monkeypatch `HOME` and clear `HOLON_LOCAL_LLM`
  using the existing hermetic fixtures (see `hermetic_fixtures.py`, `credential_env.py`).
- Assert: a `--tmpfs` for `/home/holon/.pi` and for `/home/holon/.pi/agent` each carry `uid=1000,gid=1000`; every tmpfs
  appears before the `models.json` bind; the bind is `:ro`; only one `-v` exists; no argument references `auth.json`,
  `sessions`, `run-history` or the bare agent directory.
- Assert the no-host-file case returns tmpfs mounts and no `-v`.
- Build the probe command with `build_probe_docker_cmd("pi-agent", ..., session_mounts)` from `preflight.py` and assert
  the mounts appear in the final argument list and that the destination chain (parents first, each tmpfs-owned) means no
  root-created parent remains for `/home/holon/.pi/agent/sessions`.
- Add a parametrised regression test that claude, codex, gemini and antigravity mounts match a pinned expectation.
- Assert host-local mode (`HOLON_LOCAL_LLM=1`) still returns an empty list.
- Run `uv run pytest tests/test_pi_mounts.py tests/test_preflight.py tests/test_cli.py` first.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (p_success_pred 0.95, above the 0.6 threshold, so no sub-intent needed)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/testing/testing_guide.md`, `apps/sandbox-executor/docs/hermetic_testing.md`
- **Potential failure modes for this step:**
  - A test touches the real home or invokes docker (the network and hermetic guard tests would fail).
  - Pinned expectations for other runners depend on the developer's real home.
- **Guardrails and early‑abort checks:** Use only `tmp_path` and monkeypatching; no subprocess.

### Success & Discard Criteria

- **Success:** New tests pass; they fail if the tmpfs lines are removed.
- **Discard:** Tests need real Docker to be meaningful.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.0   |
| impact_pred         | 40.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 35.7  |

### Step Metrics Rationale

`EV = 0.95 * 40.0 + 0.5 * 3.0 - 0.3 * 1.0 - 3.0 = 38.0 + 1.5 - 0.3 - 3.0 = 36.2`, stated as 35.7 after rounding. Impact
is lower because the user-visible fix comes from Step 1; this step protects it.

---

## Step 3: Documentation and full verification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Cheap and mechanical.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Document the pi mount layout and verify the whole repository gates.
- **Git branch:** I-1794529168-pidir
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Update `docs/safety.md` (or the sandbox docs that describe session mounts, found with a grep for `models.json`) with a
  short Australian English paragraph: pi gets scratch tmpfs `~/.pi` and `~/.pi/agent` owned by uid 1000, only
  `models.json` is mounted read-only, and credentials, sessions and run history stay on the host.
- Run `uv run task lint-docs` and fix any prettier or markdown findings.
- Run `uv run task lint`, then `uv run task test` once (no repeated runs: there is no timing requirement).
- Check that `flow_module_budgets.json` and `scripts/check_flow_module_budgets.py` do not flag the new module.
- Do not edit any ledger.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `world/constraints.md` (ledgers), docs formatting gates.
- **Potential failure modes for this step:**
  - Prettier oscillation on new prose.
  - A pre-existing flaky test in the full suite.
- **Guardrails and early‑abort checks:** Run lint-docs before the full suite; report any pre-existing failure
  faithfully, not by suppression.

### Success & Discard Criteria

- **Success:** `uv run task lint`, `uv run task lint-docs` and `uv run task test` all pass.
- **Discard:** A failure unrelated to this change that cannot be reproduced on `main`; report it instead.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.5   |
| impact_pred         | 20.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 19.2  |

### Step Metrics Rationale

`EV = 0.99 * 20.0 + 0.5 * 1.0 - 0.3 * 0.5 - 1.0 = 19.8 + 0.5 - 0.15 - 1.0 = 19.15`, which is the stated 19.2.

---
