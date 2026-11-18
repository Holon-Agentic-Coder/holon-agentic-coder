# Plan for I-1794136091-persist-review-loop-sandbox-across-iterations

- **Plan ID:** P-1794136106-claude-agent-claude-fable-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-fable-5 (version: 2.1.302)
- **Created At:** 2026-11-08T11:08:27.414Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** Not reframed. One architectural decision is adopted within the intent's own
  disjunction ("persist the sandbox across iterations, **or** allow a single container run to execute the iterative
  cycle"): this plan persists the **workspace**, not the container. Each reviewer and resolver pass keeps launching a
  fresh `--rm` container (preserving the "fresh context" loop invariant in
  `holon-config/prompts/review_loop.template.md` and the host's ownership of mergeability, CI gating and posting), but
  all passes of one Stage 4 session mount the same named Docker volume at the container workspace path. The first pass
  clones; every later pass takes the already-implemented-but-currently-unreachable fetch-and-checkout path in
  `review_loop._clone_or_fetch` (`entrypoint/review_loop.py:153-154`). The single-long-lived-container alternative
  (detached container plus `docker exec` per pass) was rejected: it rewrites the dispatch lifecycle in
  `cli/__init__.py`, weakens per-pass isolation, adds a leaked-container failure mode, and breaks the host-side loop's
  per-iteration ledger and CI sequencing for strictly less reuse of existing code.
- **Exploration stance:** balanced — the mechanism (volume mount + incremental fetch) reuses two proven precedents
  (`HOLON_KEEP_WORKSPACE` in `entrypoint/executor.py` and the seeded-remote volume fixtures in
  `tests/hermetic_fixtures.py`), but volume ownership/uid behaviour and stale-state hygiene carry genuine unknowns worth
  one cheap probing step before implementation.
- **Safety priority level:** elevated
- **Priority Justification:** The plan modifies the sandbox dispatch path itself (`run_docker_container` in
  `cli/__init__.py` and the Stage 4 loop in `flow.py`), which is the mechanism constraints.md §2 relies on for
  containment. Mounting a persistent volume into role containers deliberately extends what survives a container exit, so
  the plan must prove the extension is confined to one dedicated review-session volume and that no isolation flag
  (`--rm`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, metadata sinkholes) is weakened. Ledger hermeticity
  (constraints.md §3) must also be shown to hold across reused checkouts. No governance, estimator or trust-model
  surface is touched, so `critical` is not warranted.

## Exploration

- **Proportion of steps that are exploratory:** 0.14
- **Justification:** One of seven steps (Step 1) is an explicit cheap probe of Docker named-volume ownership semantics
  against the real agent image before any code changes. The remainder is exploit-mode engineering on a well-understood
  dispatch path: the Explore pass already located every function, line and test fixture involved, so further exploration
  would add entropy without learning.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 11.0  |
| impact_pred         | 78.0  |
| cost_pred           | 15.5  |
| learning_value_pred | 5.0   |
| ev_pred             | 47.7  |

### Strategy Rationale

The intent is accepted as-is and executed as a single plan (no sub-intent list: predicted entropy `11.0` sits under the
allocated budget of `15.0`). The strategy is **persist the workspace, not the container**:

1. A Stage 4 session (one `run_review_loop` invocation on the host) lazily creates one labelled Docker volume and mounts
   it at the container workspace path for every reviewer-ensemble dispatch and every resolver dispatch of that session,
   removing it in a `finally` teardown.
2. Inside the container, `_clone_or_fetch` already branches on `_has_checkout`; the plan hardens the existing fetch
   branch (origin-URL verification, `git reset --hard`, `git clean -fdx`) so a reused checkout is hermetically
   equivalent to a fresh clone, then relies on the existing `git fetch origin +refs/heads/<b>:refs/remotes/origin/<b>`
   - `git checkout -B <b> origin/<b>` + `_verify_head` sequence for incremental sync — exactly the intent's goal 2.
3. Ledger hermeticity is free by construction — the canonical ACT-REV/ACT-FIND/ACT-RES/ACT-POST and summary rows are
   written by the **host** loop (`flow.record_review_action`, `flow.py:3404`), never by the container — and is then made
   enforced-by-test: the in-container clone's own uncommitted ledger deltas are discarded by the hardened sync, and
   tests assert no cross-iteration leakage.

Aggregation of step metrics into the overall table:

- `entropy_pred` is the **sum** of step entropies: `0.5 + 3.0 + 2.0 + 2.0 + 1.5 + 1.5 + 0.5 = 11.0`. Cross-checked
  against the bootstrap formula (metrics.md §2.2): `Delta_S = 0.30*SSA + 0.25*IRR + 0.20*CL + 0.15*SER + 0.10*NOV` with
  `SSA ~ 6` (three core modules, two test modules, docs), `IRR ~ 2` (all revertible; the only lingering artefact is a
  leaked volume, mitigated by labels and teardown tests), `CL ~ 5` (`flow.py` and `cli/__init__.py` are hot files),
  `SER ~ 3` (the change is to the sandbox dispatch itself), `NOV ~ 4` (first persistence mechanism in the review path),
  giving `0.30*6 + 0.25*2 + 0.20*5 + 0.15*3 + 0.10*4 = 1.80 + 0.50 + 1.00 + 0.45 + 0.40 = 4.15` as a floor; the per-step
  sum of `11.0` is deliberately the more conservative estimate and fits the `15.0` budget.
- `cost_pred` is the **sum** of step costs: `1.5 + 3.0 + 2.0 + 2.0 + 3.0 + 2.5 + 1.5 = 15.5`.
- `p_success_pred` (`0.82`) is **qualitative, bottleneck-weighted**: the naive product of step probabilities (~0.52)
  overstates risk because every step is retryable in-branch. The binding risks are Step 2 (threading a session resource
  through `run_review_loop` → `_dispatch_role` → `dispatch_role_agent` without disturbing the many existing call sites
  and tests) and Step 6 (the Docker integration test is environment-sensitive, mitigated by graceful skip). Residual
  failure mass ~0.18.
- `impact_pred` (`78`) is the sum of step impacts (`90`) **discounted for overlap** since all steps serve one acceptance
  outcome; the headline benefit is removing a full network clone and container cold-start per pass — two containers per
  iteration today — from every multi-iteration convergence cycle.
- `learning_value_pred` (`5.0`) is **max-anchored**: the durable artefacts are (a) a reusable persistent-workspace
  pattern for role containers (directly transferable to the planner/executor dispatches, which still `--depth 1` clone
  per run) and (b) measured knowledge about volume ownership and stale-state hygiene, both KB-pattern material.
- `ev_pred` uses ev_config.json (`lambda = 0.3`, `mu = 0.5`):
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` =
  `0.82 * 78.0 + 0.5 * 5.0 - 0.3 * 11.0 - 15.5 = 63.96 + 2.50 - 3.30 - 15.50 = 47.66`, reported as `47.7`.

The bottleneck step is Step 2 (host-side session lifecycle and threading, `p = 0.85`): if the session resource cannot be
threaded cleanly through the dispatch chain, the whole design fails. It stays below the STRONGLY_YES sub-intent
threshold because its `p_success_pred` is well above `0.6` and it is fully revertible in-branch.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - constraints.md §1 (prefix-based branch isolation; commits scoped to the execution branch).
  - constraints.md §2 (sandbox containment tiers; escape actions): the work edits the container dispatch path, so
    isolation flags must be preserved byte-for-byte except for the one added volume mount.
  - constraints.md §3 / safety.md Invariant 6 (append-only ledgers): `intents.jsonl`, `plans.jsonl`, `executions.jsonl`
    are append-only; the existing `atomic_ledger_append` + `assert_ledger_prefix` machinery must not be bypassed or
    altered.
  - ruleset.md §3 (tests in `apps/sandbox-executor/tests/`, executions may not weaken test assertions; test changes are
    hereby **explicitly declared**: this plan adds new tests and extends `test_review_loop.py`,
    `test_review_dispatch.py`, `test_flow.py` and `test_cli.py`-adjacent dispatch assertions without deleting or
    loosening existing assertions).
  - safety.md §5 / git_flow.md Safety Halt: merging stays with the human maintainer; nothing in this plan touches the
    consensus halt or PR merge behaviour.
  - Intent constraints: Australian English, no new dependencies, `uv run pytest` only, `uv run task lint` passes,
    target-native paths.
- **Potential violations or edge cases:**
  - A stale or cross-session volume could carry another repository's checkout into a review pass.
  - Uncommitted state (reviewer scratch under `.holon/review/`, resolver dispositions, unpushed in-container ledger
    rows) could leak from iteration N into iteration N+1.
  - A crashed loop could leak Docker volumes on the host.
  - Mount plumbing could accidentally drop an isolation flag or mount a broader host path.
  - A resumed flow (`--from-stage`/checkpoint) could meet a half-synced volume.
- **Mitigations built into the plan:**
  - Origin-URL verification in `_clone_or_fetch`: a checkout whose `origin` does not match `HOLON_REPO_URL` is wiped and
    re-cloned (same rule as the executor's `HOLON_KEEP_WORKSPACE` precedent).
  - `git reset --hard` + `git clean -fdx` before every reuse, making each pass's starting state a pure function of the
    fetched commit — this is the hermetic boundary, and it is asserted by tests (Steps 5 and 6).
  - Volumes are named deterministically per session, labelled (`holon.review-session=1`), removed in a `finally`
    teardown, and safe to reuse after a crash precisely because of the two mitigations above.
  - The docker argument test asserts the full isolation flag set is unchanged apart from the one added
    `-v <volume>:<workspace>` mount.
  - A kill switch (`HOLON_REVIEW_PERSIST=0`) restores the current clone-per-pass behaviour without a code revert.
  - Canonical ledger rows remain host-written through `record_review_action`; no new ledger writer is introduced.
- **Residual risk accepted (and why):** Docker volume ownership semantics vary with image layout (Step 1 probes this
  first); the integration test cannot run where Docker is unavailable and skips gracefully, so full end-to-end proof
  lands only in environments with a daemon. Accepted because the unit layer covers the same logic with fakes and the
  pattern mirrors already-trusted fixtures.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 11.0
- **Budget Compliance:** The strategy fits within budget (`11.0 <= 15.0`); no sub-intent decomposition is required.

## Plan Description & Strategy

Today each Stage 4 iteration launches two throwaway containers (reviewer ensemble, then resolver), and each container
performs a **full** network clone (`git clone --branch <b> <url> .`, `entrypoint/review_loop.py:151` — notably without
even `--depth 1`) into an empty workspace, because `run_docker_container` (`cli/__init__.py:1379`) mounts no workspace
and runs with `--rm`. The fetch-and-checkout branch of `_clone_or_fetch` is dead code in practice.

The plan makes that branch live:

1. **Probe** named-volume ownership against the real agent image (Step 1).
2. **Host session lifecycle** (Step 2): `run_review_loop` on the host creates one labelled Docker volume per Stage 4
   session and threads it to `_dispatch_review_ensemble` and `_dispatch_review_resolver`; teardown in `finally`.
3. **Mount plumbing** (Step 3): `dispatch_role_agent`/`run_docker_container` accept an optional review-workspace volume
   and add exactly one `-v` argument; all isolation flags unchanged.
4. **In-container sync hardening** (Step 4): `_clone_or_fetch` verifies origin, resets and cleans before the existing
   incremental `git fetch origin <head_branch>` + `git checkout -B` + `_verify_head` sequence.
5. **Unit tests** (Step 5) and a **hermetic Docker integration test** against a seeded-remote volume (Step 6).
6. **Docs and the verification gate** (Step 7): targeted `uv run pytest` first, then `uv run task test` once, plus
   `uv run task lint`.

No repeated test runs are planned: the intent adds behavioural guarantees, not a timing target, so test-run discipline
is "affected tests first, full suite once". The wall-clock win itself is reported qualitatively (clone eliminated per
pass), not benchmarked.

---

## Step 1: Probe Docker named-volume ownership and sync semantics against the agent image

- **Sub‑intent recommendation:** NO
- **Reasoning:** Cheap, read-mostly probe that de-risks the two implementation steps; produces no reusable branch
  artefact of its own.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLORATORY

- **Hypothesis being tested:** A named Docker volume mounted at the container workspace path inherits usable ownership
  for the container's `holon` user (Docker copies ownership from the image path into an empty named volume), so a second
  container can fetch into the first container's clone without `chown`/`safe.directory` failures.
- **Learning target:** (a) whether volume ownership works unaided or needs the image's workspace directory
  pre-created/owned; (b) whether `git` in the second container needs a `safe.directory` entry; (c) confirmation that
  `_clone_or_fetch`'s fetch branch fires when `.git` exists.
- **Maximum acceptable cost for this learning:** one short session of local Docker experiments (`cost <= 1.5`); if
  Docker is unavailable in the execution sandbox, fall back to reading the Dockerfile/`hermetic_fixtures.py` volume
  usage and encode the uncertainty as a defensive `safe.directory` + ownership check in Step 4.

### Intent & Git Integration

- **Step Intent:** Establish the exact volume behaviour the implementation must handle, before touching code.
- **Git branch:**
  I-1794136091-persist-review-loop-sandbox-across-iterations/P-1794136106-claude-agent-claude-fable-5/E-{ts}-persist-review-sandbox/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Inspect `apps/sandbox-executor/Dockerfile` for the workspace path, its ownership, and the `holon` user's uid.
2. If a Docker daemon is available: create a labelled volume, run the agent (or any available) image twice with the
   volume mounted at the workspace path — first run initialises a git repo in it, second run fetches and writes — and
   record ownership, `safe.directory` behaviour and whether untracked files survive (they should, until cleaned).
3. Re-read `tests/hermetic_fixtures.py` volume helpers (`create_seeded_remote`, `remote_volume_args`, `remote_path`,
   `remove_remote_volume`) to decide how Step 6's integration test will seed the remote and assert reuse.
4. Record findings as notes in the execution report; choose the final mount path (the in-container `get_workspace_dir()`
   default, `~/.holon-sandbox/workspace`, versus an explicit `--target-worktree` path) and the volume-naming scheme
   (deterministic per session, e.g. derived from intent id + flow start timestamp).

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO (a null result only shifts handling into Step 4's defensive checks)

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md §2 (probe runs stay inside the execution sandbox's own Docker access); safety.md
  "Sandboxing is mandatory".
- **Potential failure modes for this step:** no Docker daemon in the execution environment; image not built locally.
- **Guardrails and early‑abort checks:** time-box the probe; on any unavailability, take the documented fallback
  (defensive ownership/`safe.directory` handling in Step 4) rather than blocking.

### Success & Discard Criteria

- **Success:** A written answer to the three learning targets, plus a decided mount path and volume-naming scheme.
- **Discard:** Probe exceeds `1.5x` its cost prediction — adopt the defensive fallback and move on.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.5   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 8.6   |

### Step Metrics Rationale

Read-mostly with at most throwaway Docker side effects, hence near-zero entropy and high `p_success`.
`EV = 0.95 * 10.0 + 0.5 * 1.5 - 0.3 * 0.5 - 1.5 = 9.50 + 0.75 - 0.15 - 1.50 = 8.60`. The learning value is the main
point: it converts the two riskiest unknowns of Steps 2-4 into decided facts.

---

## Step 2: Add a per-session review workspace lifecycle to the host Stage 4 loop

- **Sub‑intent recommendation:** NO
- **Reasoning:** The riskiest step, but still a bounded, fully revertible change in one module with `p = 0.85`; a
  sub-intent would add pipeline overhead larger than the risk it removes.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** One Stage 4 session owns one persistent workspace volume shared by all its reviewer and resolver
  dispatches, created lazily and always torn down.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `flow.py`, introduce a small review-session workspace helper (context-manager style): derives a deterministic,
   labelled volume name from the flow/intent identity; creates the volume on first use via the docker CLI wrapper
   already used by dispatch; exposes the name; removes the volume in teardown. Honour the kill switch
   `HOLON_REVIEW_PERSIST` (truthy by default; `0/false/no/off` disables persistence and the mount entirely, restoring
   today's behaviour).
2. Wire it into `run_review_loop` (`flow.py:4122`): when running unsandboxed (host engine mode, i.e. the
   `_dispatch_review_ensemble`/`_dispatch_review_resolver` path), create the session before the iteration `while` loop
   and tear it down in a `finally` that also covers exceptions and early stop paths (refusals, no-convergence, budget
   exhaustion). In-container mode (`reviewers_sandboxed()` true) never creates a session.
3. Thread the volume name through `_dispatch_review_ensemble` (`flow.py:3670`) and `_dispatch_review_resolver`
   (`flow.py:3757`) into `_dispatch_role` (`flow.py:1859`) as a new optional parameter, defaulting to none so every
   other role dispatch (planner, executor, intent creator) is untouched.
4. Resume semantics: a resumed flow derives the same volume name; because Step 4 makes reuse hermetic (origin check +
   reset/clean + fetch), a surviving stale volume is safe and a missing one simply re-clones. Nothing new is stored in
   the checkpoint.
5. Teardown failure handling: a failed `docker volume rm` logs a warning naming the labelled volume (prunable via its
   label) but never fails the stage — consensus correctness must not depend on cleanup.
6. Australian English in all new docstrings and messages; strict type annotations per ruleset.md §2.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (`p_success_pred = 0.85 >= 0.6`, so no forced sub-intent)

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md §2 (containment), safety.md Invariant 2; git_flow.md Stage 4 (host ownership of
  verification and posting must be preserved).
- **Potential failure modes for this step:** volume leak on crash; threading breaks existing dispatch call sites or the
  injected-dispatcher test seam (`_get_dispatcher`); session created in in-container mode where no Docker exists.
- **Guardrails and early‑abort checks:** default-none parameter keeps all existing call sites compiling and
  behaviour-identical; the kill switch provides a no-redeploy rollback; run
  `uv run pytest apps/sandbox-executor/tests/test_review_dispatch.py apps/sandbox-executor/tests/test_flow.py` after the
  change and abort/repair before proceeding if the seam broke.

### Success & Discard Criteria

- **Success:** Both dispatchers receive the same volume name across all iterations of one loop run; the volume is
  removed on success, failure and exception paths; `HOLON_REVIEW_PERSIST=0` yields byte-identical dispatch args to
  today's; existing dispatch/flow tests still pass.
- **Discard:** The threading forces signature changes in more than the planned chain (loop → dispatchers →
  `_dispatch_role` → `dispatch_role_agent`) or cost exceeds `1.5x cost_pred` — stop and re-plan toward an
  environment-variable-based transport instead of parameters.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 3.0   |
| impact_pred         | 25.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 18.1  |

### Step Metrics Rationale

Highest entropy of the plan: `flow.py` is the hottest file in the repo (high CL) and this step owns the new lifecycle
(the only semi-irreversible artefact — a leakable volume — hence non-zero IRR).
`EV = 0.85 * 25.0 + 0.5 * 1.5 - 0.3 * 3.0 - 3.0 = 21.25 + 0.75 - 0.90 - 3.00 = 18.10`.

---

## Step 3: Plumb the workspace volume mount through the docker dispatch builder

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, surgical change to `run_docker_container`/`dispatch_role_agent`, but security-sensitive;
  cheapest handled inline with a dedicated argument-equality test rather than as a child pipeline.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** `run_docker_container` mounts the session volume at the container workspace path when (and only when)
  one is supplied, changing nothing else about the command line.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add an optional review-workspace volume parameter to `dispatch_role_agent` (`cli/__init__.py:1773`) and
   `run_docker_container` (`cli/__init__.py:1379`), defaulting to none.
2. When set, append exactly one `-v <volume_name>:<container_workspace_path>` mount (path decided in Step 1 — the
   sandbox `get_workspace_dir()` default, kept in one shared constant so entrypoint and dispatcher cannot drift), and
   pass the matching `--target-worktree`/`HOLON_TARGET_WORKTREE` only if Step 1 showed the default path needs
   overriding.
3. Preserve every existing element: `--rm`, env forwarding, `--env-file` secrets, `build_network_isolation_args` output
   (`--cap-drop=ALL`, `--security-opt=no-new-privileges`, metadata sinkholes), result-file mount, tmpfs and session
   mounts, and the `finally` cleanup — the diff to the constructed argv must be exactly one `-v` pair.
4. Only the two Stage 4 dispatch call sites pass the new parameter; `holon intent`/`plan`/`execute` dispatches are
   unchanged.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (without the mount nothing persists; `p = 0.90`, no forced sub-intent)

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md §2 escape actions (no broadening of host filesystem exposure — a named volume,
  never a host bind of the operator's checkout); safety.md sandbox escape detection.
- **Potential failure modes for this step:** mount path mismatch with `get_workspace_dir()` so the clone lands outside
  the volume; accidental reordering/dropping of isolation args.
- **Guardrails and early‑abort checks:** a unit test (Step 5) asserts argv equality modulo the single `-v` pair; the
  shared path constant removes the drift failure mode by construction.

### Success & Discard Criteria

- **Success:** With a volume supplied, argv contains the one new mount and is otherwise identical; without one, argv is
  byte-identical to pre-change;
  `uv run pytest apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_cli_options.py` passes.
- **Discard:** The workspace path cannot be made consistent between dispatcher and entrypoint without invasive changes —
  stop and reconsider mounting at an explicit `--target-worktree` path instead.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 2.0   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 11.2  |

### Step Metrics Rationale

Few lines, but in the security-relevant command builder (SER-weighted entropy).
`EV = 0.90 * 15.0 + 0.5 * 0.5 - 0.3 * 2.0 - 2.0 = 13.50 + 0.25 - 0.60 - 2.00 = 11.15`.

---

## Step 4: Harden the in-container incremental sync path for hermetic reuse

- **Sub‑intent recommendation:** NO
- **Reasoning:** Localised change in `entrypoint/review_loop.py` following the executor's `HOLON_KEEP_WORKSPACE`
  precedent; moderate risk fully covered by unit tests with the existing `FakeGitCommands` fixture.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** A reused checkout is made hermetically equivalent to a fresh clone before each pass, then synced
  incrementally.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Extend `_clone_or_fetch` (`entrypoint/review_loop.py:142`): when `_has_checkout` is true, first verify the checkout's
   `origin` URL equals `HOLON_REPO_URL` (mirroring `entrypoint/executor.py`'s keep-workspace check); on mismatch, wipe
   the directory contents and fall through to the full-clone branch (fail safe, never fetch from the wrong remote).
2. On match, enforce the hermetic boundary before syncing: abort any in-progress rebase/merge state defensively, then
   `git reset --hard` and `git clean -fdx`. This discards reviewer scratch (`.holon/review/`), resolver artefacts,
   result files and — critically for goal 3 — any uncommitted in-container ledger rows appended to the clone's
   `holon-knowledge/ledger/executions.jsonl` during the previous pass (host rows are the canonical provenance; the
   clone's copy must match the fetched commit exactly).
3. Then run the existing incremental sequence unchanged: `git fetch origin +refs/heads/<b>:refs/remotes/origin/<b>`,
   `git checkout -B <b> origin/<b>`, and `_verify_head` fetching the exact SHA when needed — this is precisely the
   intent's "fetch only new incremental commits" goal, and it naturally picks up resolver commits pushed by the previous
   pass.
4. Apply Step 1's findings: add an ownership/`safe.directory` guard only if the probe showed it is needed.
5. All git calls stay routed through `_checked` so failures raise `ReviewPreparationError` and take the existing
   `_fail_preparation` path (one failure row, result file, exit 1) — no new failure channel.
6. Keep the full-clone branch for the first pass exactly as is (empty volume → clone), so behaviour without a mounted
   volume is unchanged.

### Dependencies & Criticality

- **Depends on:** Step 1 (ownership findings); independent of Steps 2-3 in code, joint at runtime
- **Is Bottleneck:** YES (hermetic goal 3 lives here; `p = 0.90`, no forced sub-intent)

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md §3 (ledger immutability — host rows canonical, clone deltas discarded, no rewrite
  of committed history); intent goal 3; review_loop.template.md invariant 9 (ledger API only).
- **Potential failure modes for this step:** `git clean -fdx` removing something a later stage expected (nothing
  legitimate is uncommitted at pass start by design); origin check too strict against credential-helper URL variants.
- **Guardrails and early‑abort checks:** compare normalised URLs; unit tests cover the mismatch-wipe, the clean/reset
  ordering, and the leftover-file discard before any integration run.

### Success & Discard Criteria

- **Success:** Second-pass preparation performs fetch + checkout only (no clone) on a matching checkout; plants of
  untracked files and fake uncommitted ledger lines from "iteration 1" are gone at "iteration 2" start; origin mismatch
  triggers wipe-and-clone; all existing `_clone_or_fetch`/`_verify_head` tests still pass.
- **Discard:** Cost exceeds `1.5x cost_pred` or the hermetic reset cannot be made reliable — fall back to
  wipe-and-full-clone inside the persistent volume (still saves container cold-start and keeps git objects for a cheaper
  clone via local fs, though losing part of goal 2) and record the limitation.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 2.0   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 11.4  |

### Step Metrics Rationale

Follows a proven in-repo precedent, confined to one function plus its failure path.
`EV = 0.90 * 15.0 + 0.5 * 1.0 - 0.3 * 2.0 - 2.0 = 13.50 + 0.50 - 0.60 - 2.00 = 11.40`.

---

## Step 5: Unit tests for session lifecycle, mount plumbing and hermetic sync

- **Sub‑intent recommendation:** NO
- **Reasoning:** Required by the intent (goal 4); builds entirely on existing fixtures (`FakeGitCommands`, `_LoopStubs`,
  `review_fakes`, injected dispatchers), so cheap and low-entropy.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Hermetic unit coverage proving multi-iteration reviews share one workspace, sync incrementally, and
  leak no state — without any real Docker or network.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `test_review_loop.py` additions (entrypoint layer, via `FakeGitCommands`/`_run_entrypoint`): origin-URL mismatch
   wipes and re-clones; matching checkout runs reset → clean → fetch → checkout in order with no clone; planted
   untracked file and uncommitted ledger-line simulation are discarded; failure in any sync command still produces
   exactly one preparation-failure row and exit 1.
2. `test_review_dispatch.py`/`test_flow.py` additions (host layer, fake dispatcher seam): a 2-iteration loop (reviewer
   objects → resolver pushes → reviewer approves) passes the **same** volume name to all four dispatches; volume created
   once, removed once, including when the loop raises mid-iteration; `HOLON_REVIEW_PERSIST=0` passes no volume;
   in-container mode (`reviewers_sandboxed()` true) creates no session; per-iteration ACT-REV/ACT-FIND rows and the
   summary row still land append-only on the host ledger (reusing the canonical-ledger assertions at
   `test_review_dispatch.py:227`).
3. Docker argv test (CLI layer): with a volume, `run_docker_container`'s constructed command equals the baseline argv
   plus exactly one `-v` pair; without, byte-identical — guarding every isolation flag.
4. All tests follow hermetic rules (`docs/hermetic_testing.md`): pinned `HOLON_TARGET_WORKTREE` fixtures, patched
   cleanup, no network, no real `docker` invocation at unit level; respect the conftest autouse guards.
5. Run
   `uv run pytest apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_review_dispatch.py apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_cli.py -n auto --dist worksteal`
   and fix failures.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 4
- **Is Bottleneck:** YES (acceptance requires the tests; `p = 0.90`)

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md §3 (test location and declared test changes); hermetic_testing.md Rules 1-4; intent
  constraint "tests strictly via `uv run pytest`".
- **Potential failure modes for this step:** flakiness from the suite-wide canary guard (new tests must not touch the
  simulated workspace); fixture interference with autouse guards.
- **Guardrails and early‑abort checks:** every new test uses `tempfile` fixtures and the existing seams; no test weakens
  or deletes an existing assertion.

### Success & Discard Criteria

- **Success:** All new tests pass under the unit marker selection; no existing test modified except by addition; the
  four targeted modules pass in one run.
- **Discard:** A design gap surfaces that unit seams cannot express (e.g. lifecycle not observable) — return to Step 2
  to improve the seam rather than writing weaker tests.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.5   |
| impact_pred         | 12.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 7.6   |

### Step Metrics Rationale

Additive test code is low-entropy; cost reflects the breadth (four modules).
`EV = 0.90 * 12.0 + 0.5 * 0.5 - 0.3 * 1.5 - 3.0 = 10.80 + 0.25 - 0.45 - 3.00 = 7.60`.

---

## Step 6: Hermetic Docker integration test for multi-pass workspace reuse

- **Sub‑intent recommendation:** NO
- **Reasoning:** Environment-sensitive (needs a Docker daemon) but modelled exactly on the existing seeded-remote volume
  fixtures and `integration_test` marker conventions; graceful skip bounds the risk.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Two successive real review-loop container runs sharing one workspace volume behave as
  designed: the first clones, the second fetches incrementally and sees the inter-run commit, and no untracked state
  crosses the boundary.
- **Learning target:** End-to-end confirmation of the volume ownership findings from Step 1 under the real image and
  entrypoint, closing the sim-to-real gap the unit fakes cannot.
- **Maximum acceptable cost for this learning:** `cost <= 2.5` plus one image build if absent; if the real agent image
  cannot run reviewers hermetically, degrade the assertion to the preparation phase only (clone vs fetch) which needs no
  model.

### Intent & Git Integration

- **Step Intent:** Prove incremental sync and state hygiene across a real container boundary.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. New `integration_test`-marked test in `apps/sandbox-executor/tests/` using `hermetic_fixtures.create_seeded_remote`
   (named-volume bare remote), `--network none`, no host `~/.ssh`, per Rule 2 of hermetic_testing.md; skip gracefully
   without a daemon.
2. Run 1: launch the review-loop role container with the session workspace volume mounted and `HOLON_REPO_URL` pointing
   at the seeded remote; drive only the preparation phase (or a stubbed/failing review that still prepares) so no model
   is needed; afterwards plant a marker file in the volume's `.git` directory (via a helper container) and plant an
   untracked junk file in the worktree.
3. Between runs: push a new commit to the seeded remote via a helper container (simulating a resolver push).
4. Run 2: same volume; assert afterwards (via helper container reads): the `.git` marker survived (no re-clone), HEAD is
   the new commit (incremental fetch picked it up), and the junk file is gone (hermetic boundary held).
5. Clean up volumes with `self.addCleanup`/`remove_remote_volume`-style reclamation, including the session volume.
6. Verify with `uv run pytest -m integration_test <new test path>` where Docker exists; the unit suite remains
   unaffected by the marker.

### Dependencies & Criticality

- **Depends on:** Steps 3, 4 (and Step 2 for naming conventions)
- **Is Bottleneck:** NO (goal 4's integration requirement, but unit coverage in Step 5 carries the logic if this
  environment lacks Docker; the test itself still lands and runs in CI-capable environments)

### Safety & Constraint Considerations

- **Relevant rules:** hermetic_testing.md Rule 2 (no external network, volume-based remote, graceful skip);
  constraints.md §2.
- **Potential failure modes for this step:** image unavailable; daemon-less environment; ownership issue only
  reproducible here.
- **Guardrails and early‑abort checks:** skip-if-no-docker guard first; reuse only fixture-managed volumes; never touch
  the live workspace.

### Success & Discard Criteria

- **Success:** The test passes where Docker is available and skips cleanly elsewhere; its three assertions (no re-clone,
  new commit synced, junk gone) all hold.
- **Discard:** More than `1.5x cost_pred` spent fighting the environment — keep the test marked and documented as
  CI-verified, note the local skip in the execution report.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.5   |
| impact_pred         | 8.0   |
| cost_pred           | 2.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 4.2   |

### Step Metrics Rationale

Lowest `p_success` of the plan because it depends on the runtime environment, which is why it is BALANCED with an
explicit degradation path. `EV = 0.80 * 8.0 + 0.5 * 1.5 - 0.3 * 1.5 - 2.5 = 6.40 + 0.75 - 0.45 - 2.50 = 4.20`.

---

## Step 7: Documentation and the full verification gate

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mandatory closing gate; trivial risk, no reuse value as a separate pipeline.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Document the persistent review workspace and prove the whole branch green.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Update `docs/git_flow.md` (Stage 4 reviewer-ensemble passage) and `apps/sandbox-executor/README.md` (reviewer
   ensemble section): one Stage 4 session reuses a labelled workspace volume across reviewer and resolver dispatches;
   first pass clones, later passes fetch incrementally after a hermetic reset; `HOLON_REVIEW_PERSIST=0` opt-out; host
   ledger rows remain canonical. Australian English throughout (`initialise`, `synchronise`, `optimisation`); arithmetic
   in code spans per the repo's markdown hygiene rules.
2. Mention the volume label and that a leaked volume is prunable by label, in the troubleshooting-appropriate spot.
3. Verification gate, in order:
   `uv run pytest apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_review_dispatch.py apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_cli.py -n auto --dist worksteal`
   (affected tests), then `uv run task test` once (full unit suite, `-n auto --dist worksteal` built in), then
   `uv run task lint`, and `npx prettier --check` via `uv run task lint-docs` for the touched markdown. Report pass/fail
   counts and pytest-reported times once each; no repeated timing runs are needed because the intent sets no
   performance-measurement target.
4. Fix anything the gate surfaces; the step ends only with every command green.

### Dependencies & Criticality

- **Depends on:** Steps 2-6
- **Is Bottleneck:** YES (acceptance requires lint and tests green; `p = 0.95`)

### Safety & Constraint Considerations

- **Relevant rules:** intent constraints (Australian English, `uv run task lint` passes, tests via `uv run pytest`);
  repo markdown hygiene (code spans for formulas, prettier-stable).
- **Potential failure modes for this step:** prettier oscillation from raw underscores in new prose; lint failures from
  the new code.
- **Guardrails and early‑abort checks:** run `uv run task format` before the check pass if needed; keep formulas in code
  spans by construction.

### Success & Discard Criteria

- **Success:** Targeted pytest, `uv run task test`, `uv run task lint` and `uv run task lint-docs` all pass; docs
  updated in Australian English.
- **Discard:** Persistent unexplained full-suite failures unrelated to the branch — bisect against the parent branch
  before burning more than `1.5x cost_pred`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.5   |
| impact_pred         | 5.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 0.0   |
| ev_pred             | 3.1   |

### Step Metrics Rationale

Mechanical closing step. `EV = 0.95 * 5.0 + 0.5 * 0.0 - 0.3 * 0.5 - 1.5 = 4.75 + 0.00 - 0.15 - 1.50 = 3.10`.

---
