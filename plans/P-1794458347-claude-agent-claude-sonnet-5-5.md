# Plan for I-1794458330-stubfetch

- **Plan ID:** P-1794458347-aa9e9dfa-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-12T04:39:08.130Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None
- **Exploration stance:** balanced. The fix is known; the guard design (Popen interception plus socket block, xdist and
  container behaviour) carries the learning value.
- **Safety priority level:** standard
- **Priority Justification:** Only test code and docs change; world/ruleset.md section 3 requires test changes to be
  declared in the plan, which this plan does. No production code is touched.

## Exploration

- **Proportion of steps that are exploratory:** 0.2
- **Justification:** One step (the guard, including the socket block) has real design uncertainty. The rest are
  mechanical.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.8   |
| entropy_pred        | 6.0   |
| impact_pred         | 60.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 40.0  |

### Strategy Rationale

The intent is accepted as is. The repo already has a model for the guard: the autouse `gh_write_guard` fixture in
`tests/conftest.py` backed by `tests/gh_write_guard.py` (`guarded_popen`), and `tests/conftest.py` already isolates git
config, credentials and ledgers. A git network guard follows the same pattern, so it fits one plan with no sub-intents.
`children.py:198` calls `_fetch_refspecs_error`; three tests in `tests/flow/stages/test_children.py` already patch it
(lines 214, 330, 397), so the failing test and any siblings need the same patch or a shared fixture.

Aggregation: the bottleneck is Step 2 (guard; `p=0.75`) and Step 4 (container proof; `p=0.8`, environment-dependent).
Overall `p_success_pred` is about the product of those two, adjusted for correlation: `0.75 * 0.8 * 0.97 * 0.97 = 0.56`,
lifted to 0.8 because the steps are mostly independent of docker availability and Step 4 can fall back to a documented
simulated-container run (no ssh, `GIT_SSH_COMMAND=false`, no network). Entropy is the sum of step entropies:
`1.0 + 2.5 + 1.0 + 1.0 + 0.5 = 6.0`. Cost is the sum of step costs, capped by the shared test runs. Overall EV:
`EV = 0.8 * 60.0 + 0.5 * 6.0 - 0.3 * 6.0 - 14.0 = 48.0 + 3.0 - 1.8 - 14.0 = 35.2`; the table figure of 40.0 is a rounded
qualitative judgement that gives weight to removing a whole class of failure that halts Stage 4 reviews.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:** `holon-config/world/ruleset.md` section 3 (tests are changed
  only when declared in the plan; `uv run pytest` only); `docs/safety.md` (no production behaviour change, append-only
  ledgers); `apps/sandbox-executor/docs/hermetic_testing.md` (existing hermetic guard conventions).
- **Potential violations or edge cases:** the guard could break legitimate tests that use local bare remotes or
  `file://` URLs; a socket block could break unix-socket, xdist or docker-related tests; blocking `subprocess.Popen`
  twice (with `gh_write_guard`) could conflict; the guard could also mask the ledger guard if it fails early.
- **Mitigations built into the plan:** allow-list local remotes (absolute paths, `file://`, and remotes whose resolved
  URL is inside `tmp_path` or the system temp dir); block only inet sockets (`AF_INET`/`AF_INET6`) and leave `AF_UNIX`;
  chain on the existing `guarded_popen` rather than replacing it; run the affected tests first, then the full suite
  once.
- **Residual risk accepted (and why):** the guard cannot see network calls made by child processes other than git, or by
  a subprocess that does not go through `subprocess.Popen`; it covers the stated git commands and in-process sockets,
  which matches the intent.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 6.0
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

Fix the failing test and its siblings by stubbing `_fetch_refspecs_error` (and any other git or network call) in the
children tests, add an autouse guard modelled on `gh_write_guard`, test the guard, run the suite in the review-loop
image as the pre-push gate does, and document the standard. Only `apps/sandbox-executor/tests/` and
`apps/sandbox-executor/docs/` (plus `docs/` if it names hermeticity) change.

---

## Step 1: Make the children tests hermetic and audit the package

- **Sub-intent recommendation:** NO
- **Reasoning:** Small, mechanical and low risk; three tests already use the patch, so the pattern is established.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The children tests run with no fetch, no ssh and no real origin.
- **Git branch:** I-1794458330-stubfetch (execution branch)
- **Sub-intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Reproduce first: run `uv run pytest tests/flow/stages/test_children.py` with `GIT_SSH_COMMAND=false` and `PATH`
  lacking ssh, with origin set to an ssh URL, to confirm the failing test and find any siblings.
- Add the `_fetch_refspecs_error` patch (returning `None`) to `test_child_payload_fallbacks_and_copies`, preferably
  through one shared fixture or helper in `tests/flow/stages/test_children.py` or `tests/flow/conftest.py`, rather than
  a fourth copy of the inline patch.
- Audit every test in `tests/flow/stages/` and `tests/flow/` that reaches `_run_one_child`, `run_children` or other
  `git_refs` fetch helpers (grep for `_fetch_refspecs_error`, `fetch_`, `run_git`), and apply the same fix.
- No production file is edited.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md section 3; safety.md
- **Potential failure modes for this step:** patching the wrong import path so the real fetch still runs; masking a real
  assertion.
- **Guardrails and early-abort checks:** the reproduction run must fail before the fix and pass after; assertions are
  unchanged.

### Success & Discard Criteria

- **Success:** the children tests pass with ssh unavailable and origin set to an unreachable ssh URL.
- **Discard:** the fix requires a production change.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.0   |
| impact_pred         | 35.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 30.0  |

### Step Metrics Rationale

`EV = 0.95 * 35.0 + 0.5 * 2.0 - 0.3 * 1.0 - 2.0 = 33.25 + 1.0 - 0.3 - 2.0 = 31.95`, stated as 30.0 after rounding. This
step alone unblocks the gate.

---

## Step 2: Add the network guard fixture

- **Sub-intent recommendation:** NO
- **Reasoning:** Moderate design risk, but a focused new module and one fixture; a separate branch would add overhead.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** a Popen interceptor for `git fetch|pull|push|ls-remote` against non-local remotes, plus
  an inet-socket block, catches the failure class without breaking the existing 3617 tests.
- **Learning target:** which existing tests quietly reach a remote or socket.
- **Maximum acceptable cost for this learning:** up to 2 extra fix iterations on newly exposed tests.

### Intent & Git Integration

- **Step Intent:** Any test that runs a git network command against a non-local remote, or opens an inet connection,
  fails on host and in container.
- **Git branch:** I-1794458330-stubfetch (execution branch)
- **Sub-intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add a new module `tests/network_guard.py` (mirroring `tests/gh_write_guard.py`) holding the pure logic: parse a git
  argv (skipping global options such as `-C <dir>` and `-c k=v`) to find the subcommand; for `fetch`, `pull`, `push` and
  `ls-remote`, resolve the remote argument (a name through `git config remote.<name>.url` in the command's cwd, or a
  literal URL/path); classify as local when it is an absolute or relative filesystem path, a `file://` URL, or resolves
  inside the system temp directory; anything else (`ssh://`, `https://`, `git@host:`, `git://`) is a violation. A remote
  name with no resolvable URL counts as non-local only if its URL is non-local; no URL means git fails anyway.
- Add `guarded_git_network` wrapping `subprocess.Popen` (chain with the existing `guarded_popen` so `gh_write_guard`
  still works): a violating command is refused before it starts by raising an error and recording it, so the test fails
  at teardown even if the code under test swallows the error, as `gh_write_guard` does.
- Block inet sockets by patching `socket.socket.connect` and `socket.create_connection` (and `getaddrinfo` for
  non-loopback names) to refuse non-loopback `AF_INET`/`AF_INET6` targets; leave `AF_UNIX` and loopback alone so xdist
  and local servers keep working.
- Add one autouse fixture `network_guard` in `tests/conftest.py` that applies both and fails at teardown with the list
  of blocked operations; an opt-out marker `allow_network` is not provided, so no test can silently bypass it.
- Keep each function under the ruff limits (`C901 <= 10`, `PLR0913 <= 5`) with no `# noqa`.
- Run the full suite once on the host to find newly exposed tests; fix each by stubbing, never by loosening the guard.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (p_success_pred 0.75 is above the 0.6 threshold, so no sub-intent is needed)

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md section 3; hermetic_testing.md
- **Potential failure modes for this step:** false positives on tests that build local bare remotes; interaction with
  `gh_write_guard`'s Popen patch; xdist workers using sockets; git invoked through `run_git` wrappers that pass `-c`
  options.
- **Guardrails and early-abort checks:** a loopback and `AF_UNIX` allow-list; a fixture ordering check; if the number of
  newly failing tests exceeds about 15, stop and narrow the socket guard to the git commands first.

### Success & Discard Criteria

- **Success:** the fixture is in place; the full host suite passes with it enabled.
- **Discard:** the socket block needs more than 15 test changes or cannot be made xdist-safe; fall back to the
  git-command guard only and record the socket block as a follow-up.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 2.5   |
| impact_pred         | 50.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 33.0  |

### Step Metrics Rationale

`EV = 0.75 * 50.0 + 0.5 * 5.0 - 0.3 * 2.5 - 5.0 = 37.5 + 2.5 - 0.75 - 5.0 = 34.25`, stated as 33.0 to allow for the risk
of newly exposed tests. Highest entropy of the plan because the guard touches every test.

---

## Step 3: Test the guard

- **Sub-intent recommendation:** NO
- **Reasoning:** Small and tied directly to Step 2.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The guard is shown to fail when it should and pass when it should.
- **Git branch:** I-1794458330-stubfetch (execution branch)
- **Sub-intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add `tests/test_network_guard.py`. Unit-test the pure classifier: ssh, https, `git@host:path` and `git://` remotes are
  violations; absolute paths, `file://` and temp-dir remotes are allowed; `-C` and `-c` options are skipped; non-network
  subcommands (`status`, `commit`) are ignored.
- Behaviour tests with a pytester-style inner run or by calling the fixture's wrapped Popen directly: a `git fetch`
  against an `ssh://` URL is refused and recorded; a `git fetch` from a local bare remote built in `tmp_path` succeeds;
  `socket.create_connection(("example.com", 80))` is refused; a loopback connection is allowed; an `AF_UNIX` socket is
  allowed.
- Negative control: confirm that the recorded-violations list makes the test fail at teardown, using the same
  clear-the-list pattern as `tests/test_gh_write_guard.py`.
- All tests run with no real network.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md section 3
- **Potential failure modes for this step:** a test that really connects to the network if the guard is broken; leaking
  global patches.
- **Guardrails and early-abort checks:** all remote URLs are unreachable by construction (`.invalid` host); monkeypatch
  restores state.

### Success & Discard Criteria

- **Success:** `uv run pytest tests/test_network_guard.py` passes on host.
- **Discard:** none expected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.0   |
| impact_pred         | 25.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 21.0  |

### Step Metrics Rationale

`EV = 0.95 * 25.0 + 0.5 * 2.0 - 0.3 * 1.0 - 2.0 = 23.75 + 1.0 - 0.3 - 2.0 = 22.45`, stated as 21.0.

---

## Step 4: Prove the suite in the review-loop container

- **Sub-intent recommendation:** NO
- **Reasoning:** A verification step; a sub-intent would not add value.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** the full suite passes in the review-loop image with the workspace mounted, where there is
  no ssh and origin is an ssh URL.
- **Learning target:** any other container-only failure, and whether docker is available to the executor.
- **Maximum acceptable cost for this learning:** one full-suite container run plus one rerun after fixes.

### Intent & Git Integration

- **Step Intent:** Demonstrate that `uv run task test` passes under the resolver's pre-push gate conditions.
- **Git branch:** I-1794458330-stubfetch (execution branch)
- **Sub-intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- First run the affected tests with `uv run pytest tests/flow/stages/test_children.py tests/test_network_guard.py`, then
  the whole suite once on the host with `uv run task test` (parallel as configured by the task).
- Find how the resolver's gate launches the container (grep `apps/sandbox-executor/src` for the review-loop role image,
  `docker-bake.hcl`, `role_dispatcher.sh`) and run the same image with the workspace mounted at the sandbox workspace
  path, origin set to an ssh URL, running `uv run task test` once. Record the pass count against the 3617 baseline plus
  the new tests.
- If docker is not reachable from the executor sandbox, simulate the container conditions on the host (PATH without ssh,
  `GIT_SSH_COMMAND=false`, an ssh-URL origin, no network namespace if `unshare -n` is permitted) and record plainly in
  the execution report that the real image run was not possible, rather than claiming it.
- Run `uv run task lint` and `uv run task lint-docs`.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 3 and Step 5
- **Is Bottleneck:** YES (p_success_pred 0.8, above the 0.6 threshold)

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md section 3 (`uv run pytest` only); the three append-only ledgers must not change.
- **Potential failure modes for this step:** docker unavailable; the container image stale or absent; the suite's own
  docker-based tests misbehaving in a nested container; the workspace deletion hazard described in `hermetic_testing.md`
  when mounting the live workspace.
- **Guardrails and early-abort checks:** mount a copy or a worktree, never the live executor workspace; confirm
  `git status` shows only intended changes afterwards.

### Success & Discard Criteria

- **Success:** the whole suite passes in the container with zero failures, and lint and lint-docs pass.
- **Discard:** two consecutive container-only failures with unclear cause; then report the evidence and stop instead of
  widening the scope.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.8   |
| entropy_pred        | 1.0   |
| impact_pred         | 40.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 28.0  |

### Step Metrics Rationale

`EV = 0.8 * 40.0 + 0.5 * 4.0 - 0.3 * 1.0 - 4.0 = 32.0 + 2.0 - 0.3 - 4.0 = 29.7`, stated as 28.0. Cost is dominated by
one full-suite run (not repeated; no timing measurement is required).

---

## Step 5: Document test hermeticity

- **Sub-intent recommendation:** NO
- **Reasoning:** A short documentation edit.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Record the rule that tests are network-free and describe the guard.
- **Git branch:** I-1794458330-stubfetch (execution branch)
- **Sub-intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Extend `apps/sandbox-executor/docs/hermetic_testing.md` with a section on the network guard: what it blocks, what
  stays allowed (local bare remotes in a temporary directory, loopback, unix sockets), how to stub a fetch correctly,
  and why the review-loop container exposes unstubbed fetches.
- Add a one-line pointer in `docs/testing/testing_guide.md` if it lists the other guards.
- Australian English; run `uv run task lint-docs` to keep prettier formatting.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** docs lint
- **Potential failure modes for this step:** formatting drift flagged by lint-docs.
- **Guardrails and early-abort checks:** run lint-docs after editing.

### Success & Discard Criteria

- **Success:** the docs name the guard and `uv run task lint-docs` passes.
- **Discard:** none.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.5   |
| impact_pred         | 10.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 8.0   |

### Step Metrics Rationale

`EV = 0.97 * 10.0 + 0.5 * 0.5 - 0.3 * 0.5 - 1.0 = 9.7 + 0.25 - 0.15 - 1.0 = 8.8`, stated as 8.0.

---
