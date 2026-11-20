# Plan for I-1794891082-beadspil

- **Plan ID:** P-1794892061-52a8e074-claude-agent-claude-fable-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-fable-5 (version: 2.1.302)
- **Created At:** 2026-11-17T05:07:42.599Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** n/a — this is re-plan attempt 1: the prior plan failed only on touch-set
  discipline (it wrote `apps/sandbox-executor/tests/test_beads_integration.py` without declaring it). The work itself is
  sound, so the same strategy is kept with the touch set widened to name every file the execution writes, including the
  new test module.
- **Exploration stance:** balanced. The tracker decisions (prefix, sync model, write-path exception) are already made by
  the intent; the only genuinely uncertain part is how bd behaves offline inside the sandbox, which is handled with one
  bounded probe step that degrades gracefully to authoring the config by documented shape.
- **Safety priority level:** elevated
- **Priority Justification:** The intent carves a narrow, explicit exception to the sole change path (direct bd writes
  to `refs/dolt/data` and `.beads/` data). Anything that touches change-path rules interacts with
  `holon-config/world/constraints.md` §1 (commit boundaries) and `docs/safety.md` Invariant 1 (git discipline), so the
  documentation must state the exception exactly and the tests must fence it. No constraint mandates `critical`: no
  estimator, ledger, or core-invariant change is involved.

## Exploration

- **Proportion of steps that are exploratory:** 0.2
- **Justification:** One of five steps (Step 1's bd-availability probe) is exploratory: we do not know whether the bd
  binary is present or runnable offline in the execution sandbox. Learning this calibrates the whole pilot (it decides
  whether `bd init` output or a hand-authored config is committed) at near-zero cost. Everything else is exploit:
  config, docs, pins, and hermetic tests with well-understood shapes.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 12.0  |
| impact_pred         | 80.0  |
| cost_pred           | 24.0  |
| learning_value_pred | 6.5   |
| ev_pred             | 39.65 |

### Strategy Rationale

The intent is accepted as-is. The previous execution produced the right artefacts but violated touch-set discipline by
creating `apps/sandbox-executor/tests/test_beads_integration.py` undeclared; the replan feedback offers the direct
remedy ("widen the Touch Set"), which is strictly cheaper and lower-entropy than decomposition. The plan therefore
declares the new test module explicitly, keeps all new hermetic tests inside that single new module (so no existing test
file is modified, honouring the world ruleset's execution boundary on tests), and fits comfortably inside the 15.0
entropy budget, so no `## Sub-intent list` is needed.

A deliberate scoping decision against the acceptance criteria: two of them ("bd works offline inside the holon/agent-\*
images", "Issue sync survives two concurrent flow branches") require a Docker image build and bd runtime behaviour that
a network-isolated sandbox cannot exercise. The plan handles them the only hermetic way available: pin the install at a
fixed version in the `holon-base` stage (so every `agent-*` image inherits it), make the install offline-verifiable at
build time (`bd version` RUN check in the Dockerfile itself), test the textual shape hermetically, and document the
create/claim/close and two-branch sync procedure in the agent guidance so the first real flow exercises it. This is
stated openly rather than faked with network-dependent tests.

Aggregation of overall metrics: `entropy_pred` is the sum of step entropies (`2.0 + 3.0 + 1.5 + 3.5 + 2.0 = 12.0`).
`p_success_pred` is the product of step probabilities rounded to the bottleneck
(`0.92 * 0.85 * 0.95 * 0.88 * 0.85 ≈ 0.56` raw), lifted to 0.80 because step failures here are almost always recoverable
within the run (a failing hermetic test or lint is fixed and re-run, not terminal); the true terminal risks are an
unforeseen hygiene interaction and the full-suite gate, concentrated in Steps 4–5. `cost_pred` is the sum of step costs.
`impact_pred` is taken at intent level (first tracker in any target repository, unlocking in-repo work items), not a
sum. `learning_value_pred` is a qualitative merge dominated by Step 1's probe and Step 4's hygiene-interaction evidence.
Overall `EV = 0.80 * 80.0 + 0.5 * 6.5 - 0.3 * 12.0 - 24.0 = 64.0 + 3.25 - 3.6 - 24.0 = 39.65`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1 (branch isolation, commit boundaries): all work stays on the plan/execution
    branches of `I-1794891082-beadspil`.
  - `holon-config/world/constraints.md` §2 (sandbox tiers): `entropy_pred = 12.0` lands in the container-sandbox tier
    with full network isolation — the plan assumes no network at execution time.
  - `holon-config/world/ruleset.md` §3 (execution boundary on tests): new tests go only into the newly declared module
    `apps/sandbox-executor/tests/test_beads_integration.py`; no existing test file is modified.
  - `docs/safety.md` (entropy budgets, Invariant 1): plan entropy 12.0 ≤ budget 15.0; the documented bd write-path
    exception must never permit source writes.
  - Hygiene gate (`test_repository_hygiene.py`): new tracked files must not match forbidden directory or identifier
    patterns.
- **Potential violations or edge cases:**
  - Writing any file outside the declared touch set again (the exact failure that triggered this re-plan).
  - Committing `.beads/embeddeddolt/` or a machine-specific `node_id` into `.beads/config.yaml`.
  - Dockerfile edit breaking the image build for reasons invisible offline (bad download URL/checksum for bd).
  - Documentation wording that could be read as widening the sole-change-path exception beyond `refs/dolt/data` +
    `.beads/` data.
- **Mitigations built into the plan:**
  - Touch set enumerates every file, including the new test module by name.
  - Step 1 commits only `config.yaml` (and `.gitignore`); the gitignore entry for `.beads/embeddeddolt/` is added before
    any bd invocation can create that directory.
  - The Dockerfile pin uses an `ARG BD_VERSION` with a checksum-or-fail install and an in-build `bd version` smoke RUN,
    so a broken install fails the build loudly rather than silently shipping.
  - Step 3's guidance states the exception as a closed list ("exactly `refs/dolt/data` plus `.beads/` data, never
    source") and Step 4 asserts `hac-` ids cannot match any hygiene-rejected identifier pattern.
- **Residual risk accepted (and why):** The bd binary download URL and the chosen pinned version cannot be validated
  offline; a wrong pin surfaces at the next image build, outside this flow. Accepted because the hermetic tests pin the
  declared shape and the build script is the single place to fix a bad URL.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 12.0
- **Budget Compliance:** The strategy fits within budget (12.0 ≤ 15.0); no sub-intent list is required.

## Plan Description & Strategy

Pilot the beads (`bd`) issue tracker in this repository in five steps on a single execution branch:

1. Probe whether `bd` is runnable offline in the sandbox, then commit `.beads/config.yaml` (prefix `hac-`, no `node_id`,
   dolt local-only) and gitignore `.beads/embeddeddolt/`.
2. Pin `bd` in the `holon-base` stage of `apps/sandbox-executor/Dockerfile` (inherited by every `agent-*` image) via
   `ARG BD_VERSION`, surfaced in `docker-bake.hcl` like the existing `UV_VERSION`, with an in-build offline smoke check.
3. Add agent guidance: a new focused `docs/issue_tracking.md` (bd ready / --claim / bd close workflow, refs/dolt/data
   sync, the narrow write-path exception, and the two-concurrent-branches sync procedure) plus a short pointer from
   `docs/agents.md`.
4. Add hermetic tests in the new module `apps/sandbox-executor/tests/test_beads_integration.py`: committed config shape,
   gitignore entry, Dockerfile pin, `hac-` id non-collision with the hygiene patterns, and a skip-if-bd-missing offline
   create/claim/close smoke test.
5. Run the affected tests, then the full suite once, then `uv run task lint` and `uv run task lint-docs`.

## Touch Set

- `.beads/config.yaml`
- `.gitignore`
- `apps/sandbox-executor/Dockerfile`
- `apps/sandbox-executor/docker-bake.hcl`
- `apps/sandbox-executor/build_all_images.sh`
- `apps/sandbox-executor/tests/test_beads_integration.py`
- `docs/issue_tracking.md`
- `docs/agents.md`

---

## Step 1: Probe bd offline and commit the tracker config (`.beads/config.yaml` + gitignore entry)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, cheap, reversible config work; the probe's learning value does not justify a separate branch and
  evaluation cycle.
- **Step Type:** CONFIG
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The `bd` binary is either absent in the execution sandbox or runnable fully offline; in
  both cases a `.beads/config.yaml` of the documented shape can be produced deterministically.
- **Learning target:** Whether `bd init` works offline in a Holon sandbox (decides how future target repositories
  bootstrap their trackers), and the exact config keys bd emits.
- **Maximum acceptable cost for this learning:** One command invocation and at most a few minutes; if `bd` is absent or
  fails offline, immediately fall back to authoring the config by hand.

### Intent & Git Integration

- **Step Intent:** Establish the committed tracker state: `.beads/config.yaml` with issue prefix `hac-`, no `node_id`
  key, and dolt configured local-only; `.gitignore` gains a `.beads/embeddeddolt/` entry.
- **Git branch:** I-1794891082-beadspil/P-1794892061-52a8e074-claude-agent-claude-fable-5/E-{timestamp}-execute/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Check with `command -v bd` (or equivalent) whether the bd binary exists in the sandbox. If present, run `bd init`
   with the `hac-` prefix in the workspace, record which files it creates, and keep only `.beads/config.yaml`.
2. If bd is absent or needs network, author `.beads/config.yaml` directly to the documented shape: the issue prefix set
   to `hac-`, dolt configured local-only (the appropriate key under the dolt section set so sandboxed role agents never
   attempt a remote), and no `node_id` key at all (node identity is per-clone, never committed).
3. Add `.beads/embeddeddolt/` to the repository root `.gitignore`, appended after the existing entries, so the embedded
   Dolt database directory can never be tracked. Do not add any `issues.jsonl` snapshot and do not create one.
4. Confirm `git status` shows only `.beads/config.yaml` and `.gitignore` as changes from this step.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (every later step builds on the committed config; `p_success_pred` is high, so no sub-intent
  needed)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 (commit boundaries), `docs/safety.md` Invariant 1; the
  intent's decision that bd data writes are limited to `refs/dolt/data` and `.beads/` data.
- **Potential failure modes for this step:**
  - `bd init` writes machine-specific state (node_id, embeddeddolt) that leaks into the commit.
  - bd attempts network access in a network-isolated sandbox and hangs or fails noisily.
- **Guardrails and early‑abort checks:**
  - Gitignore entry added before any bd invocation output is staged; `node_id` stripped or never written.
  - A bounded timeout on the probe; on any failure, fall back to hand-authoring (deterministic path).

### Success & Discard Criteria

- **Success:** `.beads/config.yaml` exists, parses as YAML, contains the `hac-` prefix and local-only dolt setting,
  contains no `node_id`; `.gitignore` contains `.beads/embeddeddolt/`; no other tracked file changed.
- **Discard:** The probe consumes more than ~2x its share of cost (it never should — fall back immediately), or bd
  produces a config whose shape cannot be made to match the intent's decisions.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 2.0   |
| impact_pred         | 20.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 15.55 |

### Step Metrics Rationale

Two small files with a fully specified target shape: high `p_success`, low entropy (new files, no existing-code
interaction). Learning value comes from the offline-bd probe, which informs every future tracker rollout.
`EV = 0.92 * 20.0 + 0.5 * 1.5 - 0.3 * 2.0 - 3.0 = 18.4 + 0.75 - 0.6 - 3.0 = 15.55`.

---

## Step 2: Pin bd in the agent images (Dockerfile + build scripts)

- **Sub‑intent recommendation:** NO
- **Reasoning:** A contained Dockerfile edit following the existing `UV_VERSION` pattern; risky only at build time,
  which is outside this flow, so a separate branch buys nothing.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Every `holon/agent-*` image ships a pinned, offline-runnable bd binary.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/Dockerfile`, add a top-of-file `ARG BD_VERSION=<pinned release>` next to `UV_VERSION`,
   choosing a concrete released beads version (from the KB/intent context; never `latest`).
2. In the `holon-base` stage (so agent images inherit it, mirroring how gh/node/prettier are baked in), add an install
   step: download the release artefact for the build architecture (amd64/arm64, following the Node.js install's
   dpkg-arch pattern already in the file), install the binary to a system path, and fail the build on any download or
   verification error.
3. End the install step with an offline smoke check inside the build: invoke the installed binary's version command and
   fail the layer if it does not run — this is the build-time verification that bd works offline in the images.
4. Surface `BD_VERSION` as an overridable arg in `apps/sandbox-executor/docker-bake.hcl` the same way `UV_VERSION` is
   handled, and touch `apps/sandbox-executor/build_all_images.sh` only if it needs to forward the new arg (if it
   forwards nothing today, leave it unchanged).
5. Do not attempt to build the image in the sandbox (no network); correctness is enforced textually by Step 4's tests
   and at the next real image build.

### Dependencies & Criticality

- **Depends on:** NONE (parallel to Step 1)
- **Is Bottleneck:** YES for the "pinned version" acceptance criterion, but `p_success_pred` is well above 0.6.

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §2 (network isolation — no build attempts in the sandbox);
  Dockerfile conventions already in the file (pinned versions, fail-fast installs).
- **Potential failure modes for this step:**
  - Wrong release-asset URL shape for the pinned version (undetectable offline).
  - Install placed in an agent-specific stage instead of `holon-base`, missing some `agent-*` images.
- **Guardrails and early‑abort checks:**
  - Follow the exact arch-switch and pin patterns already present for Node.js and uv.
  - In-build version smoke check makes a broken install fail the build rather than ship.

### Success & Discard Criteria

- **Success:** Dockerfile contains `ARG BD_VERSION` with a concrete default, an install in `holon-base` ending in a
  version smoke check; `docker-bake.hcl` exposes the arg; shellcheck-clean build script if touched.
- **Discard:** No authoritative release/version information is available at execution time to choose a pin — then stop
  and surface the gap rather than inventing a version string.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 3.0   |
| impact_pred         | 25.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 15.35 |

### Step Metrics Rationale

Moderate entropy: edits to a load-bearing shared Dockerfile stage affect every image, and offline validation is only
textual. The existing `UV_VERSION`/Node patterns make the edit mechanical, keeping `p_success` high.
`EV = 0.85 * 25.0 + 0.5 * 2.0 - 0.3 * 3.0 - 6.0 = 21.25 + 1.0 - 0.9 - 6.0 = 15.35`.

---

## Step 3: Agent guidance for issue tracking (`docs/issue_tracking.md` + pointer in `docs/agents.md`)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure documentation, low risk, cheap; a new focused doc keeps `docs/agents.md` from growing and keeps
  the exception wording in one authoritative place.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Agents can discover, from the repository's own guidance documents, how to use the tracker and the
  exact boundary of the issue-data write exception.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `docs/issue_tracking.md` covering: one tracker per target repository with prefix `hac-`; the agent workflow —
   `bd ready` to list unblocked work, claiming with the claim flag when starting, `bd close` when done; sync via
   `refs/dolt/data` only (never commit `issues.jsonl` snapshots); the narrow exception to the sole change path stated as
   a closed list — direct bd writes may touch exactly `refs/dolt/data` plus `.beads/` data, never source files; and the
   procedure for two concurrent flow branches (both write issue data through bd's dolt sync on `refs/dolt/data`, which
   merges at the data layer instead of via source branches, satisfying the concurrent-branches acceptance criterion by
   construction and documented procedure).
2. Note explicitly that `.beads/embeddeddolt/` is gitignored machine state and `node_id` is never committed.
3. Add a short cross-reference from `docs/agents.md` (a sentence in an appropriate section plus a link in related
   documents) pointing at the new doc; keep the edit minimal.
4. Keep prose free of the hygiene-rejected identifier shapes; use `hac-` examples only with id forms verified safe in
   Step 4.

### Dependencies & Criticality

- **Depends on:** Step 1 (documents the committed config's behaviour)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** hygiene gate (self-contained repository, no external task identifiers); `docs/safety.md` Invariant
  1 — the exception wording must not read as weakening git discipline for source changes.
- **Potential failure modes for this step:**
  - `lint-docs` (prettier) formatting failures.
  - Wording that over-widens the exception.
- **Guardrails and early‑abort checks:** State the exception as the intent's closed list verbatim; run the docs linter
  in Step 5 and fix formatting there.

### Success & Discard Criteria

- **Success:** New doc exists, `docs/agents.md` links to it, `uv run task lint-docs` passes over both, hygiene scan
  clean.
- **Discard:** n/a — documentation failure modes are all fixable in place.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.5   |
| impact_pred         | 15.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 11.30 |

### Step Metrics Rationale

Lowest-risk step: new doc plus a two-line pointer edit. Entropy is small but non-zero because `docs/agents.md` is an
existing shared document. `EV = 0.95 * 15.0 + 0.5 * 1.0 - 0.3 * 1.5 - 3.0 = 14.25 + 0.5 - 0.45 - 3.0 = 11.30`.

---

## Step 4: Hermetic tests in the new module `apps/sandbox-executor/tests/test_beads_integration.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the step the previous attempt executed outside its touch set; the fix is declaration, not
  decomposition. A single new test module, no existing tests modified.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The new `.beads/` content and `hac-` identifiers are invisible to (and incapable of
  colliding with) the hygiene gate's rejection patterns.
- **Learning target:** Concrete evidence of how tracker artefacts interact with `test_repository_hygiene.py`'s scan,
  reusable for every future per-repository tracker rollout.
- **Maximum acceptable cost for this learning:** The cost of importing the hygiene module's public helpers in a test; if
  its helpers are not importable cleanly, replicate the documented patterns locally in the new module instead.

### Intent & Git Integration

- **Step Intent:** Everything testable without network is pinned by hermetic tests in one new, declared module.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/tests/test_beads_integration.py` (module docstring explaining it guards the committed
   beads pilot surface; strict typing; repo root resolved the same way `test_repository_hygiene.py` does).
2. Config-shape tests: `.beads/config.yaml` exists, parses as YAML, issue prefix equals `hac-`, dolt is configured
   local-only, and the `node_id` key is absent.
3. Gitignore test: the root `.gitignore` contains a `.beads/embeddeddolt/` entry.
4. Dockerfile pin tests: `apps/sandbox-executor/Dockerfile` declares `ARG BD_VERSION` with a concrete non-latest
   default, installs bd in the `holon-base` stage, and includes the in-build version smoke invocation; `docker-bake.hcl`
   exposes the arg.
5. Hygiene non-collision tests: feed representative `hac-` issue ids (for example `hac-1`, `hac-0042`, a `hac-` id
   embedded in prose) through the hygiene module's violation-finding helper (imported from `test_repository_hygiene.py`,
   which stays unmodified) and assert zero matches against every forbidden identifier pattern; also assert
   `.beads/config.yaml` itself scans clean.
6. Offline smoke test for create/claim/close: skipped unless a bd binary is on PATH; when present, run bd in a temporary
   directory fully offline (local-only config) to create, claim, and close one issue, asserting the lifecycle round-trip
   — this gives the "flow can create, claim and close an issue" criterion a hermetic, skip-safe witness.
7. Keep every function inside ruff complexity limits; no new dependencies (YAML parsing via the already-available
   library in the workspace's dev dependencies).

### Dependencies & Criticality

- **Depends on:** Steps 1, 2 (tests assert their artefacts)
- **Is Bottleneck:** YES — the hermetic acceptance criteria run through this module.

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (tests live in `tests/`, and any test-file change must be
  declared in planning — this plan declares the new module explicitly); hygiene gate; ruff limits.
- **Potential failure modes for this step:**
  - Importing helpers from a test module proves awkward under the configured pytest import mode.
  - The skip-guarded bd smoke test behaves differently when bd is unexpectedly present but offline-broken.
- **Guardrails and early‑abort checks:**
  - Fallback for the import: assert against locally re-declared copies of the documented patterns instead.
  - The smoke test is marked to skip on missing binary and fails only on a genuinely broken offline lifecycle, which is
    exactly the signal the pilot wants.

### Success & Discard Criteria

- **Success:**
  `uv run pytest apps/sandbox-executor/tests/test_beads_integration.py apps/sandbox-executor/tests/test_repository_hygiene.py`
  passes with all new tests green and the hygiene gate unchanged and green.
- **Discard:** Hygiene scan reveals a genuine collision between `hac-` id shapes and a rejection pattern — then stop and
  report; changing the hygiene test's patterns is out of scope for this plan.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 3.5   |
| impact_pred         | 25.0  |
| cost_pred           | 7.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 14.95 |

### Step Metrics Rationale

Largest step: one new module with five test groups touching three other artefacts. Entropy reflects the cross-module
assertions and the import-from-test uncertainty; both have cheap, planned fallbacks, keeping `p_success` at 0.88.
Reviewing the hygiene patterns during planning showed `hac-` ids match neither the external-task shape (requires the
other tracker's word stem plus four digits) nor the finding-citation shapes, so the non-collision tests should pass.
`EV = 0.88 * 25.0 + 0.5 * 2.0 - 0.3 * 3.5 - 7.0 = 22.0 + 1.0 - 1.05 - 7.0 = 14.95`.

---

## Step 5: Verification — affected tests, full suite once, lint and docs lint

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard closing gate; cheap relative to the plan and mandated by the acceptance criteria.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the acceptance criteria that are checkable in the sandbox: full test suite, lint, docs lint.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run the affected tests first:
   `uv run pytest apps/sandbox-executor/tests/test_beads_integration.py apps/sandbox-executor/tests/test_repository_hygiene.py apps/sandbox-executor/tests/test_container_images.py apps/sandbox-executor/tests/test_build_all_images.py apps/sandbox-executor/tests/test_formatting.py`
   (the last three guard the Dockerfile/build-script and formatting surfaces the plan touches).
2. Then run the full suite once with `uv run task test`; no repeated runs — nothing in this intent measures performance
   or timing, so test-run discipline requires exactly one full-suite pass. Report pass/fail counts and the
   pytest-reported time.
3. Run `uv run task lint` and `uv run task lint-docs`; fix any formatting fallout (prettier on the new/edited markdown,
   ruff on the new test module) and re-run only the failed gate.
4. Confirm `git status` matches the declared touch set exactly before finishing.

### Dependencies & Criticality

- **Depends on:** Steps 1–4
- **Is Bottleneck:** YES — acceptance criteria gate.

### Safety & Constraint Considerations

- **Relevant rules:** Test-run discipline (affected tests first, full suite once); acceptance criteria list; touch-set
  enforcement (the cause of this re-plan).
- **Potential failure modes for this step:**
  - An unrelated flaky test fails the single full-suite run.
  - Prettier reflows the new markdown (the plan's formula code spans prevent the known oscillation mode).
- **Guardrails and early‑abort checks:**
  - A failure clearly outside the touched surface is re-run once and otherwise reported honestly, not patched around.
  - Final `git status` check against the touch set before completion.

### Success & Discard Criteria

- **Success:** Affected tests and the single full-suite run pass; both lint tasks pass; working tree contains only
  touch-set files.
- **Discard:** A failure traces back to a genuine design conflict (for example hygiene vs tracker artefacts) rather than
  a fixable implementation slip — stop and report with evidence.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.0   |
| impact_pred         | 15.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 7.65  |

### Step Metrics Rationale

Verification cost is dominated by the one full-suite run over a large test directory. `p_success` is 0.85 against
first-pass green; lint fixes are expected and cheap.
`EV = 0.85 * 15.0 + 0.5 * 1.0 - 0.3 * 2.0 - 5.0 = 12.75 + 0.5 - 0.6 - 5.0 = 7.65`.

---
