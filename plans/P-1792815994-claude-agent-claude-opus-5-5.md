# Plan for I-1792815987-redact-github-token-in-cli-echoes-v2

- **Plan ID:** P-1792815994-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-24T04:26:35.042Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. The intent is already decomposed into 22 precise goals that encode the
  reviewed end state of the closed PR #92. Reframing would throw away seven review-loop iterations of agreed semantics.
  The plan only reorders the goals into dependency-safe, independently testable steps.
- **Exploration stance:** balanced. The work is mostly exploitation of a known design (the PR #92 end state is fully
  specified). Two steps carry deliberate exploration: recovering the PR #92 diff as a reference, and validating the
  in-container credential helper against real `git` semantics. Both have a cheap fallback.
- **Safety priority level:** critical
- **Priority Justification:** The change governs how a live GitHub credential crosses the host to container boundary,
  which is the `SER` (sandbox escape / secrets) term in `docs/metrics.md` §2.2. It touches the secrets-handling surface
  that `holon-config/world/constraints.md` §2 and `docs/safety.md` "Sandboxing model" treat as escape-relevant. During
  planning the planner observed a live, concrete instance of the defect. This planning workspace's own
  `remote.origin.url` in `.git/config` carries an embedded `x-access-token:gho_...` credential, which is exactly the
  goal 10 persistence leak. The value is deliberately not reproduced anywhere in this plan. `constraints.md` §4 also
  mandates a deep model for security-sensitive plans.

## Exploration

- **Proportion of steps that are exploratory:** 0.22 (2 of 9 steps are BALANCED; none are fully EXPLORATORY)
- **Justification:** The semantics are locked by the intent's goals and Q&A, so broad exploration has low value. The two
  BALANCED steps target the only real unknowns:
  - whether the closed PR #92 head can be recovered as a reference (this saves cost and reduces divergence from the
    reviewed state);
  - whether the `GIT_CONFIG_COUNT` inline credential helper behaves as designed under real `git`. This includes helper
    reset ordering, host[:port] scoping and inheritance into agent subprocesses, and it is the bottleneck's main risk.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.68  |
| entropy_pred        | 10.9  |
| impact_pred         | 100.0 |
| cost_pred           | 51.0  |
| learning_value_pred | 4.0   |
| ev_pred             | 15.73 |

### Strategy Rationale

The intent is accepted as is. It is a positive-EV, high-impact security fix with an already-reviewed target state, and
it violates no world rule. The strategy is **foundation first, then boundary, then sweep**:

1. Make tests hermetic and add shared leak assertions, so every later step can prove "no substring of a fake value".
2. Create the single canonical `sandbox_executor/redaction.py`. Every other step imports it, so getting it right once
   avoids divergent pattern sets (goal 6).
3. Add pure URL-credential functions (`strip_url_credentials`, `credential_host`, `resolve_target_repo`). They are easy
   to test in isolation and feed the docker builder.
4. Rework `run_docker_container` (env-file, precedence, credential helper, redacted echo). This is the riskiest step and
   the **bottleneck**.
5. Route the in-sandbox echo sites through the redactors (redact before truncate).
6. Extend the hygiene check, update the docs, then sweep and run the gates.

**Aggregation:**

- `entropy_pred` is the sum of step entropies: `0.3 + 0.9 + 2.0 + 1.4 + 2.6 + 1.8 + 0.6 + 0.5 + 0.8 = 10.9`, which fits
  the 12.0 budget.
- `impact_pred` is the sum of step impacts: `4 + 8 + 18 + 14 + 24 + 14 + 6 + 5 + 7 = 100`.
- `cost_pred` is the sum of step costs: `2 + 4 + 8 + 6 + 12 + 8 + 3 + 3 + 5 = 51`.
- `learning_value_pred` is a qualitative cost-weighted mean of the step values (about 3.6), rounded up to 4.0 for the
  reusable credential-helper and CodeQL-safe-fixture patterns that the KB currently lacks.
- `p_success_pred` is **not** the naive product of step probabilities (about 0.27). That would assume independent,
  unrecoverable failures, but these steps are retried inside one execution with a review loop. Overall success is
  instead anchored on the bottleneck, Step 5 (0.75), discounted for the end-to-end gate risk in Step 9 (CodeQL zero
  alerts, a clean merge with a moved `main`) and the volume of existing tests that pin the old argv shape. The result is
  `0.68`.
- `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`, computed as
  `EV = 0.68 * 100.0 + 0.5 * 4.0 - 0.3 * 10.9 - 51.0 = 68.00 + 2.00 - 3.27 - 51.00 = 15.73`.

**Strong assumptions:**

- The PR #92 branch may be unrecoverable. The local clone is shallow with a single commit, so the plan budgets a full
  re-implementation from the goals and treats the PR #92 diff only as an optional reference.
- Baseline-trust planners cannot spawn sub-intents (`docs/safety.md` trust table). Sub-intent recommendations below
  therefore fall back to step-scoped commits on a single execution branch.

### Leak inventory (goal 1): call sites on current `main`, verified by reading the code

Paths are relative to `apps/sandbox-executor/src/sandbox_executor/`.

| #   | Call site                                                           | What leaks today                                                                                                                                                                                                                                                                                           |
| --- | ------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| L1  | `cli.py:184-186` `resolve_target_repo_url` override branch          | A `HOLON_REPO_URL` override is returned raw, userinfo included. It reaches the docker argv and is persisted by `git clone` into the workspace `.git/config`.                                                                                                                                               |
| L2  | `cli.py:205-208` `resolve_target_repo_url` origin branch            | Builds `https://x-access-token:<token>@host/path.git`, putting the live token into the URL, the argv and the container's `.git/config`. An origin URL that already carries userinfo is passed through unstripped when no host token exists.                                                                |
| L3  | `cli.py:950-969` `run_docker_container` env forwarding              | `-e GITHUB_TOKEN=<token>`, `-e HOLON_REPO_URL=<url with token>`, `-e HOLON_MITM_WEB_PASSWORD=...`, `-e HOLON_AGENT_KEY=...` and `-e HOLON_AGENT_SETTINGS=...` (via the `HOLON_*` loop) all sit on the process argv and are visible in `ps`. The host `GITHUB_TOKEN` loop also overwrites the chosen token. |
| L4  | `cli.py:1079-1098` `Executing: docker run ...` echo                 | The denylist masks only `GITHUB_TOKEN` and `HOLON_AGENT_KEY` plus `HOLON_REPO_URL` userinfo. `HOLON_MITM_WEB_PASSWORD`, `HOLON_AGENT_SETTINGS` (`apiKey` / `sk-ant-` / OAuth JSON), other `HOLON_*_TOKEN` values, local-LLM `container_env` and token-reduction env values are printed in clear.           |
| L5  | `cli.py:705-707` mitmweb sidecar argv                               | `--set web_password=<value>` on the sidecar `docker run` argv. **Known gap: document only (goal 18).**                                                                                                                                                                                                     |
| L6  | `cli.py:714-720` sidecar spawn-failure `RuntimeError`               | Raw docker stderr/stdout, which can echo the argv including `web_password`, is embedded in the error message.                                                                                                                                                                                              |
| L7  | `entrypoint/planner.py:15-21` `run_cmd`                             | `Running: git clone ... <repo_url>` (call at `planner.py:169`) is printed raw, and `Stdout:` / `Stderr:` are printed raw.                                                                                                                                                                                  |
| L8  | `entrypoint/planner.py:273-280` agent-command echo                  | Truncates each argument to 100 characters **without redaction**, so `--settings` JSON carrying `CLAUDE_CODE_OAUTH_TOKEN` or a provider key prefix survives the clamp.                                                                                                                                      |
| L9  | `entrypoint/planner.py:303-309` agent failure dump and exception    | Raw `Stdout:` / `Stderr:` and `Exception running agent: {e}`.                                                                                                                                                                                                                                              |
| L10 | `entrypoint/intent_creator.py:12-18` `run_cmd`                      | `Running: git clone ... <repo_url>` (call at `intent_creator.py:75`), with stdout/stderr raw.                                                                                                                                                                                                              |
| L11 | `entrypoint/review_loop.py:33-41` `run_cmd`                         | `Running: git clone ... <repo_url>` (call at `review_loop.py:130`), with stdout/stderr raw.                                                                                                                                                                                                                |
| L12 | `entrypoint/review_loop.py:129` clone banner                        | `Cloning <branch> into <dir> from <repo_url>...` printed raw.                                                                                                                                                                                                                                              |
| L13 | `entrypoint/executor.py:589-629` `run_cmd`                          | The argv is already `redact_args`-masked and clamped after redaction, but failure stdout/stderr pass only through `redact_text`, so bare provider tokens without a key anchor survive. Needs `redact_agent_secrets`.                                                                                       |
| L14 | `entrypoint/executor.py:1468, 1489` recovery `git fetch` diagnostic | `fetch_err` (raw git stderr, which can contain the remote URL with userinfo) is printed unredacted.                                                                                                                                                                                                        |
| L15 | `git.py:55-62` `run_git`                                            | Uses `redact_text` on the joined line: userinfo is caught, bare provider tokens are not. Switch to `redact_command` and `redact_agent_secrets`.                                                                                                                                                            |
| L16 | `entrypoint/executor.py:1121-1162` clone/fetch                      | Echoes are masked, but the token-bearing `repo_url` becomes the workspace `origin` and persists in `.git/config` (the same defect seen live in this planning workspace). Fixed at the source by L1/L2.                                                                                                     |

**Sweep candidates to confirm in Step 9 (not proven leaks yet):**

- `flow.py:3244` and `review_loop.py:235` `To approve manually, run:` (prints a fixed `holon review approve` string,
  likely safe);
- `formatting.py:86-146` prettier subprocess output;
- `agent_runner.py` warnings that print paths;
- `flow.py:254` and `flow.py:3442`, which already use `redact_text` and will be repointed to the shared module.

**Scope boundary on existing redactors:**

- `knowledge/records.py` `CredentialRedactor` (ledger action records, `[REDACTED_SECRET]` marker) and
  `token_reduction/mitm_addon.py` (wire-log, runs inside the mitmproxy container with only the addon file mounted, so it
  cannot import `sandbox_executor`) stay **byte-for-byte unchanged** (goal 17).
- They are pre-existing, separately tested ledger and wire-log redactors. This plan adds no new divergent set.
- The fixed marker of the shared module stays the existing `*******`, so moving `redact_text` cannot change any ledger
  output. The cli's ad-hoc `***REDACTED***` marker is retired in favour of it.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1: Python `==3.13.*`, `uv` only, no new dependencies.
  - `holon-config/world/ruleset.md` §2: PEP 8, strict typing and docstrings on every new public function in
    `redaction.py` and `cli.py`.
  - `holon-config/world/ruleset.md` §3: tests live in matching `tests/` files. **Test changes are explicitly declared
    here.** Declared new files are `tests/conftest.py`, `tests/leak_assertions.py` and `tests/test_redaction.py`.
    Declared modifications cover `tests/test_cli.py`, `tests/test_git.py`, `tests/test_executor.py`,
    `tests/test_planner.py`, `tests/test_review_loop.py`, `tests/test_intent_creator.py`,
    `tests/test_token_reduction.py` and `tests/test_pre_commit_check.py`. This includes updating the existing assertions
    that pin `GITHUB_TOKEN=<value>` in argv (`test_cli.py:177`, `test_cli.py:205`), the
    `https://x-access-token:<token>@` form (`test_cli.py:430-463`) and the `***REDACTED***` marker (`test_cli.py:535`).
  - `holon-config/world/constraints.md` §1: execution happens on
    `I-1792815987-redact-github-token-in-cli-echoes-v2/P-1792815994-claude-agent-claude-opus-5-5/E-{timestamp}-{action}/_`,
    rebased from its parent before execution and before merge. No commits go to `main`, and only a human merges the root
    intent.
  - `holon-config/world/constraints.md` §3 and `docs/safety.md` Invariant 6: ledgers are append-only. No auxiliary
    ledgers (only `intents.jsonl`, `plans.jsonl` and `executions.jsonl`).
  - `holon-config/world/constraints.md` §4: a deep model is mandated (security-sensitive plan).
  - `docs/safety.md` Entropy budgets: the predicted 10.9 is within the 12.0 budget, and an actual entropy above 18.0
    (1.5x the budget) triggers human review.
- **Potential violations or edge cases:**
  - Real tokens appearing in test fixtures, logs or this plan. The planner observed a live token in the workspace
    `.git/config`; it must never be copied into code, tests, plans or PR text.
  - Tests reading ambient credentials (`GITHUB_TOKEN`, `gh auth token`) or binding host port 8081.
  - CodeQL `py/clear-text-logging-sensitive-data` alerts traced from test values into production sinks.
  - A plan-ID collision like Bean 0149. Verified at planning time: `plans/P-1792815994-*` does not exist and
    `plans.jsonl` has no `1792815994` entry.
  - The in-container helper being dropped by subprocesses that build an explicit env dict not derived from `os.environ`
    (for example `flow.py:2448` and `flow.py:2636` `proxy_env = {}`), which breaks push authentication silently.
  - Behaviour change for preserved workspaces (`HOLON_KEEP_WORKSPACE=1`): an old token-bearing origin now mismatches the
    clean URL and triggers a re-clone (`executor.py:1138-1149`). This is desirable because it purges the persisted
    token, but it must be documented.
- **Mitigations built into the plan:**
  - Fake values built at runtime under neutral names: `'gh' + 'p_' + uuid4().hex`.
  - A shared autouse conftest fixture that clears credential env vars, stubs `find_github_token` and points the env-file
    directory at `tmp_path`.
  - A stubbed `cli.socket.socket.connect_ex`.
  - An equivalence test pinning `redact_text` output before and after the move.
  - The hygiene check asserting that no tracked file matches.
  - A collision re-check before any artefact is written.
  - Env-file removal in `finally`, plus a stale sweep.
  - Step 5 includes a real-`git` helper probe against a local bare repo served over `file://` (shape check only, no
    network).
- **Residual risk accepted (and why):**
  - The mitmweb `web_password` stays on the sidecar argv. The intent marks it out of scope (goal 18), and it is
    documented as a known gap.
  - The ledger `error_context` truncate-before-redact path is out of scope (Bean 0148).
  - Docker itself can still show env-file contents to anyone with docker-daemon access (`docker inspect`). That is
    inherent to the environment variable mechanism and equivalent to the pre-existing trust boundary.
- **Allocated Entropy Budget:** 12.0
- **Predicted Plan Entropy:** 10.9
- **Budget Compliance:** The strategy fits within budget (10.9 of 12.0, about 91%). The highest-entropy steps (3 and 5)
  are behind hermetic tests and have explicit discard thresholds.

## Plan Description & Strategy

Execute on one execution branch with **one commit per step**, so each commit is scoped to its step (`constraints.md`
§1). Use the branch
`I-1792815987-redact-github-token-in-cli-echoes-v2/P-1792815994-claude-agent-claude-opus-5-5/E-{timestamp}-redact-credentials/_`.
A single branch is preferred over nine sub-branches because the steps are tightly coupled through `redaction.py` and the
`test_cli.py` argv assertions. Splitting them would multiply rebase conflicts (`CL`) on the same hot files (`cli.py`,
`test_cli.py`) for no isolation benefit.

Order and data flow:

1. Provenance and baseline.
2. Hermetic test harness.
3. Canonical `redaction.py`.
4. URL-credential functions.
5. Docker boundary: env-file, precedence, helper, echo.
6. In-sandbox echo sites.
7. Hygiene check.
8. Docs.
9. Sweep, gates, CodeQL, PR.

Steps 3 and 4 are independent of each other once Step 2 lands, and can be developed in either order. Step 5 needs both.
Steps 6 to 8 need Step 3, and Step 8 also needs Steps 4 and 5. Step 9 is last. Every test uses `uv run pytest` inside
`apps/sandbox-executor`, never `unittest` and never `PYTHONPATH`.

---

## Step 1: Provenance check, rebase and PR #92 reference recovery

- **Sub‑intent recommendation:** NO
- **Reasoning:** This step only reads and checks, so it is cheap and low risk. Its value is preventing a repeat of the
  Bean 0149 ID collision and anchoring the work on current `main`, which now includes PR #91.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The closed PR #92 head (`refs/pull/92/head`) is still fetchable from the GitHub remote.
  If so, its diff can serve as a reference for the reviewed end state and reduce the re-implementation risk.
- **Learning target:** Whether PR #92 refs survive closure in this repository, how far current `main` (after PR #91) has
  diverged from the PR #92 base in `cli.py`, `executor.py` and `test_cli.py`, and a baseline test and lint status.
- **Maximum acceptable cost for this learning:** 2 cost units (one fetch and a diff-stat). If unavailable, implement
  directly from the goals, which are a complete specification.

### Intent & Git Integration

- **Step Intent:** Establish a clean, collision-free, current-`main` baseline before any code change.
- **Git branch:**
  `I-1792815987-redact-github-token-in-cli-echoes-v2/P-1792815994-claude-agent-claude-opus-5-5/E-{timestamp}-redact-credentials/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Re-verify that no file in `plans/` and no `plan_id` in `holon-knowledge/ledger/plans.jsonl` matches this plan ID.
   Check the execution ID the same way against `executions/` and `executions.jsonl`. On any collision, stop and report;
   never overwrite or hand-merge (goal 21).
2. Rebase the intent and plan branches onto current `origin/main`, deepening the shallow clone as needed, and record the
   `main` SHA in the execution notes.
3. Try to fetch `refs/pull/92/head` read-only into a scratch ref (not a branch that could be pushed). If it succeeds,
   produce a diff-stat against current `main` for the files in the leak inventory. Use it as a reference only; never
   cherry-pick blindly, because `main` has moved.
4. Run `uv run task lint` and `uv run task test` at the worktree root to capture the baseline. Pre-existing failures
   must be recorded so they are not attributed to this change.
5. Never print `git remote -v` or `git config --get remote.origin.url` in execution logs, because this workspace's
   origin carries an embedded credential. If you need to show the remote, use `redact_text` or show the host and path
   only.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §1 (rebase discipline), §3 (append-only ledger); intent goal 21 (ID collision).
- **Potential failure modes for this step:**
  - The PR ref is gone.
  - A rebase conflict on the intent branch.
  - Baseline red tests.
- **Guardrails and early‑abort checks:**
  - Abort the whole plan on an ID collision.
  - If the baseline has failures unrelated to this intent, record them and continue, but do not "fix" them.

### Success & Discard Criteria

- **Success:** No collision exists, the branch is rebased on current `main`, and the baseline gate results are recorded.
  The PR #92 reference has been either obtained or ruled out.
- **Discard:** Discard if an ID collision is detected, which halts the plan and reports. Drop the PR #92 sub-goal if the
  fetch fails twice.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.3   |
| impact_pred         | 4.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 3.21  |

### Step Metrics Rationale

This is a read-only step with near-certain success. Its learning value comes from finding out whether closed-PR refs are
recoverable, which helps future "supersedes PR #N" intents.
`EV = 0.95 * 4.0 + 0.5 * 3.0 - 0.3 * 0.3 - 2.0 = 3.80 + 1.50 - 0.09 - 2.00 = 3.21`.

---

## Step 2: Hermetic test harness and shared leak assertions

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is small, low-risk, reusable infrastructure that every later step depends on. It should land first
  so that later tests cannot accidentally read ambient credentials or bind real ports.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Ensure no test reads real credentials or depends on host ports, and provide one leak-assertion helper
  (goal 15).
- **Git branch:** same execution branch, commit `test: hermetic credential fixtures and leak assertions`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add `apps/sandbox-executor/tests/conftest.py` with an autouse fixture that:
   - removes `GITHUB_TOKEN`, `GH_TOKEN` and every `HOLON_*` variable through `monkeypatch.delenv`;
   - stubs `sandbox_executor.cli.find_github_token` to return `None` by default;
   - points the env-file directory (a module-level constant or resolver introduced in Step 5, for example
     `cli._env_file_dir`) at a `tmp_path` subdirectory. Until Step 5 exists, guard this with `raising=False`.

   Reconcile it with `tests/hermetic_fixtures.py` rather than duplicating it, and keep the existing per-test
   `patch.dict(os.environ, ..., clear=True)` usages working.

2. Add a `make_fake_value(prefix_parts)` helper that returns a runtime-built value such as `'gh' + 'p_' + uuid4().hex`.
   Return it under neutral names (for example `sample_value` or `probe`), never `token`, `secret` or `password`, so
   CodeQL does not taint-track it (goal 16). Provide variants for `gho_`, `github_pat_`, `sk-ant-` and a plain
   non-token-shaped password.
3. Add `apps/sandbox-executor/tests/leak_assertions.py` with `assert_no_leak(value, *haystacks, min_fragment=8)`.
   - It checks every substring of length `min_fragment` or more of `value` against each haystack.
   - A haystack can be a str, a list of argv strings, captured stdout/stderr, or a file path, which is read.
   - On failure it reports the haystack name and offset, never the value itself.
   - Also add `assert_names_repo(text, repo_path, branch)` so tests can prove the output stays readable.
4. Make the mitmproxy spawn-failure test in `tests/test_token_reduction.py` independent of port 8081. Stub
   `cli.socket.socket.connect_ex` and `_published_loopback_port` the way the sibling tests at `test_token_reduction.py`
   lines 2445 and 2521 do.
5. Run the full unit suite to confirm the autouse fixture does not break existing tests. Where a test genuinely needs a
   token, it sets one explicitly.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` §3 (declared test changes), intent goals 15 and 16, `docs/safety.md` "Sandbox escape
  detection" (no network or ports).
- **Potential failure modes for this step:**
  - The autouse fixture breaks tests that implicitly relied on ambient `HOLON_*` variables.
  - The fixture shadows `hermetic_fixtures.py` behaviour.
- **Guardrails and early‑abort checks:**
  - Diff the test outcome counts against the Step 1 baseline. Any newly failing test must be fixed by setting its env
    explicitly, never by weakening an assertion.

### Success & Discard Criteria

- **Success:** All baseline-green tests stay green, `leak_assertions.py` is importable, and the test environment carries
  no ambient credential.
- **Discard:** If more than 15 existing tests break because of the autouse fixture, switch to an opt-in fixture used by
  the new tests only, and record why.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.9   |
| impact_pred         | 8.0   |
| cost_pred           | 4.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 3.93  |

### Step Metrics Rationale

This is well-understood pytest work. Entropy comes mainly from the autouse fixture's reach across 26 test modules.
`EV = 0.90 * 8.0 + 0.5 * 2.0 - 0.3 * 0.9 - 4.0 = 7.20 + 1.00 - 0.27 - 4.00 = 3.93`.

---

## Step 3: Canonical `sandbox_executor/redaction.py` module

- **Sub‑intent recommendation:** YES
- **Reasoning:** This is a core refactor that every other step imports, and the ledger byte-equivalence requirement
  (goal 17) makes it risky. Under baseline trust it is executed as an isolated commit with its own equivalence tests
  rather than a spawned sub-intent.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Move the existing redaction pattern set into one shared module with one marker, and add the command,
  secret and env-name helpers (goals 2, 6 and 14).
- **Git branch:** same execution branch, commit `refactor: move redaction into shared sandbox_executor.redaction`
- **Sub‑intent:** NONE (the recommendation is YES, but it executes in-branch because a baseline-trust planner cannot
  spawn sub-intents)

### Implementation Details (No code blocks, only logic/steps)

1. Create `src/sandbox_executor/redaction.py` with a module docstring and a single `MARKER = "*******"`. This is the
   existing marker, kept so ledger and wire-log output stay byte-identical.
2. Move these unchanged from `entrypoint/executor.py`:
   - `redact_text` with `_MAX_REDACT_INPUT_LEN`;
   - `SECRET_FLAGS`, `_is_secret_flag` and `redact_args`;
   - `_PROVIDER_TOKEN_RE`.

   In `executor.py`, re-import them under the same names so existing imports (`git.py`, `flow.py`, tests and
   `executor.sanitize_agent_output`, which uses `_PROVIDER_TOKEN_RE` at line 559) keep working.

3. Make two pattern changes, the only ones allowed:
   - add `sk-ant-[A-Za-z0-9_-]{8,}` to the closed provider list (goal 14);
   - add `apikey` to the key-name alternation, matched case-insensitively so it covers camelCase `apiKey` (the regex
     already uses `re.IGNORECASE`).

   Before committing, prove that neither change alters the output of any existing ledger or wire-log test input (see
   point 9). If one does, scope the change to `redact_agent_secrets` only and keep `redact_text` byte-stable.

4. `redact_command_args(argv) -> list[str]` returns `redact_args(argv)` with `_PROVIDER_TOKEN_RE` applied to each
   element. It is the per-argument form, for callers that clamp each argument or build `CalledProcessError`.
5. `redact_command(argv) -> str` joins `redact_command_args(argv)` with spaces, then applies `redact_text` and the
   provider sweep to the joined line again. The second pass catches a value split across an argument boundary (for
   example `-e` followed by `GITHUB_TOKEN=...`, or `--set` followed by `web_password=...`).
6. `redact_agent_secrets(text, env=None) -> str` applies `redact_text`, then the provider sweep, then replaces the
   literal values of secret-classified environment variables (from `env`, defaulting to `os.environ`). Only values of at
   least 8 characters are replaced, longest first, so short common strings are not shredded.
7. `is_secret_env_name(name) -> bool` returns true for:
   - `GITHUB_TOKEN`, `GH_TOKEN` and `HOLON_MITM_WEB_PASSWORD`;
   - any name ending in `_TOKEN`, `_SECRET`, `_PASSWORD`, `_PASSWD` or `_KEY` (for example `HOLON_AGENT_KEY`,
     `ANTHROPIC_API_KEY` and `CLAUDE_CODE_OAUTH_TOKEN`).

   It is deliberately **false** for `HOLON_AGENT_SETTINGS`, which is classified by pattern in Step 5 (see the intent
   Q&A).

8. Repoint the importers to `sandbox_executor.redaction`: `git.py`, `flow.py`, `entrypoint/executor.py` and, in Step 6,
   `cli.py`, `planner.py`, `intent_creator.py` and `review_loop.py`. Do not touch `knowledge/records.py` or
   `token_reduction/mitm_addon.py`.
9. Add `tests/test_redaction.py`:
   - parametrised equivalence over every existing `redact_text` / `redact_args` input in `tests/test_executor.py` and
     `tests/test_flow.py` (identical outputs);
   - `redact_command` boundary-spanning cases;
   - an `sk-ant-` value, an `apiKey` JSON key and a `CLAUDE_CODE_OAUTH_TOKEN` settings JSON;
   - a negative test showing that a high-entropy base64 blob is **not** masked (closed list, no entropy rule);
   - `is_secret_env_name` truth table;
   - literal env-value replacement.

   All values come from Step 2's runtime builders.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO. Every step depends on it, but a failure is recoverable by keeping the functions in
  `executor.py` with re-exports.

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` §2 (typing, docstrings, no wildcard imports); intent goals 6 and 17; Bean 0148 scope
  exclusion.
- **Potential failure modes for this step:**
  - An import cycle (`executor.py` imports `ledger`, `git.py` imports `executor`). This is avoided because
    `redaction.py` imports only `re` and `os`.
  - The `apikey` addition changing an existing ledger expectation.
  - Accidentally touching the `error_context` 500-character truncation path (Bean 0148).
- **Guardrails and early‑abort checks:**
  - `redaction.py` imports nothing from the `sandbox_executor` package.
  - Run the equivalence tests before repointing any importer.
  - `git diff` must show no change to `error_context` logic in `executor.py`.

### Success & Discard Criteria

- **Success:** One module exists with one marker and all four new functions. Equivalence tests pass, all existing
  `test_executor.py` and `test_flow.py` redaction tests pass, and nothing else defines provider patterns (excluding the
  two documented, untouched ledger and wire-log redactors).
- **Discard:** If byte-equivalence cannot be kept with the `apikey` change, confine it to `redact_agent_secrets`. If
  cost exceeds 12 units, stop and re-plan.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.0   |
| impact_pred         | 18.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 8.20  |

### Step Metrics Rationale

This step moves code in a core module touched by many tests, so `SSA` and `CL` are moderate. The `redact_text` regex is
intricate, which is why the equivalence test is the main guard.
`EV = 0.85 * 18.0 + 0.5 * 3.0 - 0.3 * 2.0 - 8.0 = 15.30 + 1.50 - 0.60 - 8.00 = 8.20`.

---

## Step 4: URL-credential functions (`strip_url_credentials`, `credential_host`, `resolve_target_repo`)

- **Sub‑intent recommendation:** NO
- **Reasoning:** These are pure functions with an exhaustive, spec-driven test table. The risk is semantic and fully
  covered by the intent Q&A.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Strip credentials from both the override and origin URLs, using exactly the userinfo classification
  rules in goals 7 to 10.
- **Git branch:** same execution branch, commit `feat(cli): strip URL credentials from override and origin`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add `strip_url_credentials(url) -> tuple[str, str | None]` in `cli.py`, or in `redaction.py` if a shared home suits
   reuse better; keep it importable from `cli`.
   1. If the scheme (lower-cased) is not `http` or `https`, return the input byte-for-byte with `None`. This covers
      `ssh://`, `git+ssh://`, SCP-style `git@host:org/repo.git` and `file://`.
   2. Otherwise parse with `urllib.parse.urlsplit`, take the raw userinfo (everything before the last `@` in the
      netloc), split it on the first `:`, and percent-decode user and password with `urllib.parse.unquote`.
   3. Rebuild the netloc as host[:port] without userinfo, preserving case, port, path, query and fragment exactly.
   4. Classify the credential:
      - **(a)** the password equals `x-oauth-basic` case-insensitively and the user is non-empty: the credential is the
        user;
      - **(b)** otherwise, a non-empty password is the credential;
      - **(c)** otherwise, a bare user that fully matches `(gh[opsu]_|github_pat_)[A-Za-z0-9_-]{8,}` is the credential;
      - otherwise the credential is `None` (a login name, stripped silently).
2. Add `credential_host(url) -> str | None`, which returns the lower-cased `host[:port]` for `https://` URLs only, else
   `None`.
3. Add `resolve_target_repo(repo_dir=None) -> tuple[str, str | None]`.
   - Read `HOLON_REPO_URL`. If it is unset, run `git config --get remote.origin.url` once.
   - Strip both through `strip_url_credentials`.
   - When a credential was stripped, print a stderr warning naming the source (`HOLON_REPO_URL` or `remote.origin.url`),
     never the value, saying the token will be supplied through a credential helper.
   - If the clean URL is plain `http://` and a credential was stripped, the warning instead says it was stripped and
     **discarded** because plain HTTP is never sent a credential, and recommends an HTTPS or SSH remote. Return `None`
     as the credential.
   - Keep origin normalisation (`normalize_repo_remote` to `https://host/path.git`) when a host token exists, but
     **without** userinfo.
4. Keep `resolve_target_repo_url(repo_dir=None) -> str` as a thin wrapper that returns only the URL, for backward
   compatibility.
5. Write the docstrings of both functions branch-for-branch in the same order as the Step 8 docs text, to satisfy
   goal 19.
6. Add tests in `tests/test_cli.py`. Each one checks the result and runs `assert_no_leak` over stdout, stderr and the
   return value.
   - **Byte-identical:** `ssh://`, `git+ssh://` and SCP inputs are returned unchanged.
   - **Login names:** a username-only URL gives no warning and a `None` credential; token-as-username gives a
     credential.
   - **Password forms:** `user:password`, `x-access-token:<value>` and `<value>:x-oauth-basic` (also as
     `X-OAuth-Basic`).
   - **Edge cases:** the empty-user `:x-oauth-basic` edge (empty user, so rule (b) applies with the literal password,
     which then must not be forwarded; assert the result matches the doc), and a percent-encoded credential.
   - **Plain HTTP:** override and origin wording, a `None` credential, and fallback to the stubbed host token.
   - **Origin stripping:** a token-bearing origin in a temp `git init` repo is stripped.
   - **Updated tests:** change `test_resolve_target_repo_url_ssh_with_token` and
     `test_resolve_target_repo_url_https_with_token` to expect `https://github.com/myorg/myrepo.git` with no userinfo (a
     declared test change).

### Dependencies & Criticality

- **Depends on:** Step 2 (fixtures); independent of Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goals 7 to 10; Q&A on SSH, plain HTTP and bare user names; `ruleset.md` §2 docstrings.
- **Potential failure modes for this step:**
  - `urlsplit` lower-casing or reordering the netloc.
  - IPv6 hosts in brackets.
  - A `@` inside the path being mistaken for userinfo. Use the netloc only, and split on the last `@`.
- **Guardrails and early‑abort checks:**
  - Round-trip tests assert that a credential-free http(s) URL is returned unchanged byte-for-byte.
  - Include a bracketed IPv6 case.

### Success & Discard Criteria

- **Success:** Every goal 8 form has a passing test, SSH and SCP inputs are byte-identical, and no fragment of any fake
  value appears in outputs.
- **Discard:** If the classification cannot match the docs for the empty-user edge, stop and flag it for human
  clarification rather than guessing.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.4   |
| impact_pred         | 14.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 7.48  |

### Step Metrics Rationale

These are pure functions with precise specifications, but many edge forms, so success is high. Learning value comes from
codifying git's userinfo semantics as a reusable pattern.
`EV = 0.85 * 14.0 + 0.5 * 4.0 - 0.3 * 1.4 - 6.0 = 11.90 + 2.00 - 0.42 - 6.00 = 7.48`.

---

## Step 5: Docker boundary: env-file, credential precedence, host-scoped helper, redacted echo

- **Sub‑intent recommendation:** STRONGLY_YES
- **Reasoning:** This is the bottleneck and the highest-entropy step. It changes the host to container credential path
  that every role depends on, and it rewrites many `test_cli.py` argv assertions. Ideally it would be its own sub-intent
  with its own evaluation. Under baseline trust it is executed as an isolated commit gated by a real-`git` helper probe.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** An inline `GIT_CONFIG_COUNT=2` helper scoped to `credential.https://<host[:port]>`
  authenticates in-container `clone`, `fetch` and `push` and agent-spawned `git`. The helper must reset inherited
  helpers first, then answer `get` with `username=x-access-token` and `password=$GITHUB_TOKEN`. It must work without the
  token ever entering argv or a tracked file.
- **Learning target:** Whether `GIT_CONFIG_*` env config survives every in-container subprocess path, in particular
  explicit env dicts such as `flow.py` `proxy_env` and agent runners. Also whether git applies `host:port`-scoped
  credential keys as documented, and whether docker `--env-file` handles the helper's quoting literally.
- **Maximum acceptable cost for this learning:** 4 cost units within this step's 12. If the helper approach fails the
  probe, fall back to the intent's permitted alternative: a runtime `url.<base>.insteadOf` rewrite set through
  `GIT_CONFIG_*` env, still never written to a tracked file.

### Intent & Git Integration

- **Step Intent:** Keep every secret out of process argv, give the container the token only through a 0600 env-file plus
  a runtime credential helper, and make the `Executing:` echo leak-free (goals 3, 11, 12, 13h and 14).
- **Git branch:** same execution branch, commit
  `feat(cli): pass secrets via 0600 env-file and host-scoped credential helper`
- **Sub‑intent:** NONE (the recommendation is STRONGLY_YES, but it executes in-branch because a baseline-trust planner
  cannot spawn sub-intents)

### Implementation Details (No code blocks, only logic/steps)

1. In `run_docker_container`, replace `resolve_target_repo_url` with `resolve_target_repo`, which yields `clean_url` and
   `embedded_credential`. Set `gh_token = embedded_credential or find_github_token()`.
2. Run the host forwarding loop for `HOLON_*` and `GITHUB_TOKEN` first. **Afterwards**, set
   `env_to_forward["GITHUB_TOKEN"] = gh_token` (when non-empty) and `HOLON_REPO_URL = clean_url`, so a host
   `GITHUB_TOKEN` cannot overwrite the chosen token (goal 11).
3. Configure the helper only when all of these hold:
   - `gh_token` is set;
   - `credential_host(clean_url)` is non-`None` (https only);
   - the URL came from origin, or the override itself carried the credential.

   Then add `GIT_CONFIG_COUNT=2` with two entries:
   - `GIT_CONFIG_KEY_0` = `credential.https://<host[:port]>.helper`, with an empty `GIT_CONFIG_VALUE_0` to reset
     inherited helpers;
   - `GIT_CONFIG_KEY_1` = the same key, with `GIT_CONFIG_VALUE_1` set to an inline shell helper. It answers only the
     `get` action, emitting `username=x-access-token` and `password=` followed by the container's `$GITHUB_TOKEN`
     expanded at call time.

   No helper is configured for SSH, plain http or a credential-free override.

4. Classify each `KEY=VALUE` destined for the container, covering `env_to_forward`, `ssh_envs`, `tr_envs`, the local-LLM
   `container_env` and the late `HOLON_AGENT_PROVIDER`. An entry goes to the env-file when `is_secret_env_name(KEY)` is
   true **or** `redact_command([f"{KEY}={VALUE}"])` differs from the input. Otherwise it stays as `-e`.
   - This classification by pattern covers `HOLON_AGENT_SETTINGS` carrying `apiKey`, `sk-ant-` or OAuth keys (goal 14).
   - It also routes the helper entry, harmlessly, because its literal `password=$GITHUB_TOKEN` text matches the key
     regex. Assert that this is the case and that the value contains no secret.
5. Env-file creation:
   - `os.makedirs(~/.holon/tmp/env, mode=0o700)` followed by `os.chmod` to 0700, to defeat umask. The directory comes
     from a module-level resolver so tests can redirect it.
   - Create the file with `tempfile.mkstemp(prefix="holon-env-", dir=...)` and `os.fchmod(fd, 0o600)` before writing.
   - Write one `KEY=VALUE` per line.
   - Reject any value containing `\n` or `\r` with `Error: environment value for <KEY> contains a line break` (the key
     is named, the value never), and return 1.
   - Insert `--env-file <path>` immediately after `docker`, `run`.
6. Move env-file creation inside the existing `try`, and remove the file in the existing `finally` with
   `contextlib.suppress(FileNotFoundError)` on every exit path. That includes the early `return 1` paths for intent-file
   errors and `LocalLLMConfigError`.
7. Before creating a new env-file, do a best-effort sweep that deletes files in the env directory matching the fixed
   `holon-env-` prefix whose mtime is older than 24 hours. Suppress `OSError`.
8. Replace the ad-hoc `sensitive_keys` / `***REDACTED***` block with
   `print(f"Executing: {redact_command(docker_cmd)}")`. The argv now holds no secrets, so this is defence in depth. The
   env-file path is shown, its contents never.
9. Tests in `tests/test_cli.py`, parametrised where noted:
   - **Argv and echo:** a fake GitHub value in `GITHUB_TOKEN` appears in neither the docker argv (captured via a patched
     `subprocess.run`) nor the stdout echo. The echo still names the repository path and branch argument.
   - **Env-file:** while `subprocess.run` is patched, it has mode `0o600`, its directory has mode `0o700`, and it holds
     the value. Afterwards it is removed, including on the intent-file error path and the line-break rejection path.
   - **Precedence:** parametrised over override and origin, an embedded credential wins over a different host
     `GITHUB_TOKEN`.
   - **Helper scoping:** the helper key includes the port for `https://ghe.example:8443/...`. There is no helper and no
     `GIT_CONFIG_*` for SSH, plain http or a credential-free override.
   - **Settings and mitmweb password:** `HOLON_AGENT_SETTINGS` with an `apiKey` / `sk-ant-` value goes to the env-file,
     while a multi-line non-secret settings JSON stays on `-e` and is not rejected. `HOLON_MITM_WEB_PASSWORD` is neither
     in argv nor in the echo.
   - **Stale sweep:** an old env-file is deleted and a fresh one is kept.
   - **Updated assertions:** rewrite the existing assertions at `test_cli.py:177`, `:205` and `:535` to the new contract
     (declared test change).
10. Exploratory probe (a unit test, no network):
    - Create a local bare repository and a non-bare clone in `tmp_path`.
    - With the `GIT_CONFIG_*` env from point 3 and a fake `GITHUB_TOKEN`, run `git credential fill` against
      `https://ghe.example:8443/org/repo.git` and assert it returns the fake value.
    - Run it against a different host and assert it does not.
    - Then grep the source for `subprocess` calls that pass explicit env dicts not derived from `os.environ` (for
      example `flow.py:2448` and `flow.py:2636`). Verify that they inherit or merge `GIT_CONFIG_*` and `GITHUB_TOKEN`;
      if not, fix them so they merge `os.environ`.

### Dependencies & Criticality

- **Depends on:** Steps 3 and 4
- **Is Bottleneck:** YES (`p_success_pred` 0.75, which is at least 0.6, so the in-branch execution with probe gating is
  acceptable)

### Safety & Constraint Considerations

- **Relevant rules:**
  - intent goals 3, 11, 12 and 14, and Q&A 1, 6 and 7;
  - `docs/safety.md` "Container sandbox" (secrets must not leak across the boundary);
  - `ruleset.md` §1 (no new dependencies: `tempfile`, `os` and `contextlib` only).
- **Potential failure modes for this step:**
  - The env-file is left behind after a SIGKILL. This is covered by the 24-hour sweep, and the 0700 directory limits
    exposure in the meantime.
  - Docker's env-file parser treating quotes literally, which breaks the helper. The probe catches this, with the
    `insteadOf` fallback.
  - Inherited global helpers (for example a host-mounted gitconfig) answering first. Entry 0 resets them.
  - Existing tests asserting positional argv order.
  - Preserved workspaces re-cloning once because the origin URL changed. This is expected and is documented in Step 8.
- **Guardrails and early‑abort checks:**
  - `assert_no_leak` runs on argv, stdout, stderr and the env-file path listing in every new test.
  - Abort the helper approach if the probe fails twice, and switch to `insteadOf`.
  - Never log env-file contents.

### Success & Discard Criteria

- **Success:**
  - No fake value appears in docker argv or the echo, and the env-file is 0600 and always removed.
  - Precedence and helper scoping tests pass, and the real-`git` credential probe returns the fake value only for the
    scoped host.
  - All `test_cli.py` tests are green.
- **Discard:** Escalate to human review if the cost exceeds `1.5 * 12 = 18` units, or if both the helper and the
  `insteadOf` mechanisms fail the probe. Do not fall back to argv tokens, which the Q&A explicitly forbids.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 2.6   |
| impact_pred         | 24.0  |
| cost_pred           | 12.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 8.22  |

### Step Metrics Rationale

This step has the highest `SSA` (`cli.py` plus a large `test_cli.py` rewrite), the highest `SER` relevance (credential
transport) and moderate `NOV` (runtime-env git config is new to the KB). The learning value is high because the
validated helper pattern is reusable for every future containerised git role.
`EV = 0.75 * 24.0 + 0.5 * 6.0 - 0.3 * 2.6 - 12.0 = 18.00 + 3.00 - 0.78 - 12.00 = 8.22`.

---

## Step 6: Redact before truncation at every in-sandbox and sidecar echo site

- **Sub‑intent recommendation:** YES
- **Reasoning:** This step touches seven modules mechanically, with many small no-leak tests. It is reusable and
  medium-risk because of the many test files involved, so it is executed as one commit after Step 3.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Route leak inventory items L6 to L15 through the shared redactors, always redacting before any clamp
  (goal 13 a to g).
- **Git branch:** same execution branch, commit
  `fix: redact command, URL and subprocess-output echoes before truncation`
- **Sub‑intent:** NONE (the recommendation is YES, but it executes in-branch because a baseline-trust planner cannot
  spawn sub-intents)

### Implementation Details (No code blocks, only logic/steps)

1. Planner (L7 to L9):
   1. Extract `format_agent_cmd_for_display(cmd) -> str` into `entrypoint/planner.py`. It applies `redact_command_args`
      per argument **first**, then the existing 100-character clamp with `...`, then a final `redact_command`-style
      sweep of the joined line.
   2. Make `run_cmd` echo through `redact_command` and print failure `Stdout:` / `Stderr:` through
      `redact_agent_secrets`.
   3. Pass the agent-failure dump at lines 303-305 and the `Exception running agent` message through
      `redact_agent_secrets`.
   4. Tests:
      - a `--settings` JSON whose `env` carries `CLAUDE_CODE_OAUTH_TOKEN`;
      - a provider value placed so that it straddles the 100-character clamp, with no 8-character fragment surviving;
      - a clone echo that names the branch but not the token.
2. `intent_creator.run_cmd` (L10): route the echo through `redact_command` and stdout/stderr through
   `redact_agent_secrets`. Test the clone echo.
3. `review_loop` (L11, L12): route `run_cmd` the same way, and pass the `Cloning ... from <url>` banner through
   `redact_command`. Test both.
4. `executor.run_cmd` (L13):
   - Use `redact_command_args` for `Running:` and `Command args:`, keeping the existing 250-character per-argument clamp
     **after** redaction and the `_MAX_PRINT_LEN` clamp.
   - Build `CalledProcessError.cmd` from the redacted argv.
   - Pass failure stdout/stderr, and the `output` / `stderr` attributes, through `redact_agent_secrets`.
   - Leave the ledger `error_context` path untouched (Bean 0148).
   - Test a provider token appearing at character 240 to 260 of an argument.
5. Executor recovery fetch diagnostic (L14): pass `fetch_err` through `redact_agent_secrets` before printing, and test
   it with fake git stderr carrying a userinfo URL.
6. `git.run_git` (L15): use `redact_command(["git", *args])` for the timeout and failure messages, and
   `redact_agent_secrets` for stderr/stdout. Add or extend tests in `tests/test_git.py`.
7. Sidecar spawn failure (L6): pass the stderr in the `RuntimeError` in `setup_token_reduction_proxy` through
   `redact_agent_secrets`, with `HOLON_MITM_WEB_PASSWORD` in the literal-value env. Also mask mitmweb `?token=` values,
   which the existing `redact_text` query rule already covers; assert this.
   - Do **not** move `web_password` off argv (goal 18).
   - Test with a stubbed failing `subprocess.run` whose stderr echoes `web_password=<fake>` and
     `http://127.0.0.1:8081/?token=<fake>`. Stub the port probe, with no real port.
8. `intent_creator` and `cli` echoes (13h): the cli echo is done in Step 5; confirm the `intent_creator` echo is done in
   point 2.

### Dependencies & Criticality

- **Depends on:** Step 3 (and Step 5 for the cli sidecar touch-points, to avoid `cli.py` edit conflicts)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goals 13, 17 and 18; Bean 0148 exclusion; `ruleset.md` §3 (matching test files).
- **Potential failure modes for this step:**
  - Over-redaction that hides branch or repository names. Use `assert_names_repo` in every test.
  - Changing `CalledProcessError` content that a caller parses. Search for callers that read `.cmd` or `.stderr`.
  - Accidentally editing the `error_context` truncation.
- **Guardrails and early‑abort checks:**
  - Every new test asserts both no-leak and readability.
  - Check `git diff` around `error_context` for zero change.

### Success & Discard Criteria

- **Success:** All 13 a to g sites are covered with passing no-leak tests, and no existing test regresses.
- **Discard:** If a call site's existing test contract conflicts with redaction (for example it asserts raw stderr),
  update it as a declared test change. If that takes more than five such conflicts, pause and list them for review.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 1.8   |
| impact_pred         | 14.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 3.94  |

### Step Metrics Rationale

The changes are mechanical but broad (seven modules, six test files), so `SSA` is moderate and `NOV` is low.
`EV = 0.82 * 14.0 + 0.5 * 2.0 - 0.3 * 1.8 - 8.0 = 11.48 + 1.00 - 0.54 - 8.00 = 3.94`.

---

## Step 7: Repository hygiene check for embedded tokens

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is a small, self-contained extension to an existing script with an existing test file.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Fail the commit check if any tracked file contains an `x-access-token:` followed by a token-shaped
  value and `@`, or a GitHub token pattern (goals 5 and 20).
- **Git branch:** same execution branch, commit `chore(hygiene): detect embedded GitHub tokens and x-access-token URLs`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `scripts/pre_commit_check.py`, add to `CREDENTIAL_PATTERNS`:
   - an `x-access-token URL credential` pattern matching `x-access-token:` followed by at least 8 characters of
     `[A-Za-z0-9_-]` and then `@`;
   - a widened GitHub pattern `(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}`. The existing rule requires exactly 36
     characters; keep it or merge it.
   - `github_pat_` is already covered.
2. Ensure the masked forms do not match: `x-access-token:*******@`, `x-access-token:${GITHUB_TOKEN}@` and
   `x-access-token:$GITHUB_TOKEN@`. They cannot match the character class; add explicit negative tests anyway.
3. In `apps/sandbox-executor/tests/test_pre_commit_check.py`:
   - positive cases built at runtime (never literal token-shaped strings in the file);
   - negative cases for the marker and `${GITHUB_TOKEN}` references;
   - a repository-wide test that runs the credential scan over `git ls-files` output and asserts zero matches.
4. Run the scan before committing. Existing test strings such as `gh_test_token_123` and `ghp_mock_token_123` in
   `test_cli.py`: the second one contains `ghp_` followed by alphanumerics plus underscores. The character class
   excludes `_`, so it does not match the widened rule, but verify this and replace any matching literals with runtime
   builders.

### Dependencies & Criticality

- **Depends on:** Step 2 (runtime builders); runs after Steps 4 to 6 so their new test files are scanned
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goals 5, 16 and 20; CI workflow `.github/workflows/test-hygiene.yml`.
- **Potential failure modes for this step:**
  - False positives on documentation examples.
  - The widened rule matching legitimate identifiers.
- **Guardrails and early‑abort checks:**
  - The repository-wide zero-match test is the gate.
  - Prefer making documentation examples use the marker, rather than adding `FALSE_POSITIVE_INDICATORS` entries.

### Success & Discard Criteria

- **Success:** The new patterns are tested positively and negatively, the repository-wide scan finds zero matches, and
  the `test-hygiene` workflow passes.
- **Discard:** If the widened rule produces more than three false positives in tracked files, fall back to the exact
  36-character rule plus the `x-access-token` rule.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.6   |
| impact_pred         | 6.0   |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 3.22  |

### Step Metrics Rationale

This is a small regex extension with a clear test oracle.
`EV = 0.90 * 6.0 + 0.5 * 2.0 - 0.3 * 0.6 - 3.0 = 5.40 + 1.00 - 0.18 - 3.00 = 3.22`.

---

## Step 8: Documentation that matches behaviour exactly

- **Sub‑intent recommendation:** NO
- **Reasoning:** This step changes docs only. The risk is drift between the docs and the docstrings, which a
  side-by-side review resolves.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make `docs/executor/agent_credentials_requirements.md` and `apps/sandbox-executor/README.md` state
  the implemented behaviour branch-for-branch (goal 19).
- **Git branch:** same execution branch, commit `docs: document credential stripping, env-file and redaction`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `docs/executor/agent_credentials_requirements.md`, add a "Repository URL credentials" section listing:
   - the three userinfo rules in the same order as the `strip_url_credentials` docstring;
   - that bare login names are stripped silently;
   - that SSH, `git+ssh` and SCP remotes are untouched;
   - plain-HTTP strip-and-discard with a warning;
   - that both `HOLON_REPO_URL` and `remote.origin.url` are stripped;
   - embedded-credential precedence over the ambient token;
   - the 0700 directory and 0600 `--env-file` lifecycle, including the 24-hour stale sweep and the line-break rejection;
   - the host-scoped `GIT_CONFIG_COUNT` helper;
   - `HOLON_AGENT_SETTINGS` classified by pattern;
   - the redact-before-truncate rule;
   - the one-time re-clone of preserved workspaces whose origin carried a token.
2. Add a "Known gap" subsection: the mitmweb `web_password` is still on the sidecar `docker run` argv and visible in the
   host process list during start-up. Echoes and diagnostics are redacted, and moving it off argv is a tracked
   follow-up.
3. Reference the hygiene test at the correct path, `apps/sandbox-executor/tests/test_pre_commit_check.py`.
4. Give `apps/sandbox-executor/README.md` a concise version of the same points, linking to the credentials doc.
5. Compare the doc bullets with the `strip_url_credentials` and `resolve_target_repo` docstrings line by line.
6. Format with the repository prettier configuration (`npx prettier --write` on the two files, then
   `uv run task lint-docs`). Use Australian English throughout. Doc examples use the `*******` marker or
   `${GITHUB_TOKEN}`, never token-shaped literals.

### Dependencies & Criticality

- **Depends on:** Steps 4, 5 and 6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goals 18, 19 and 22 (Australian English, markdown formatting).
- **Potential failure modes for this step:**
  - The docs and docstrings disagree on the empty-user `:x-oauth-basic` edge.
  - Prettier oscillation on tables.
- **Guardrails and early‑abort checks:**
  - Do the side-by-side comparison.
  - Run prettier twice and require a stable result.

### Success & Discard Criteria

- **Success:** Both docs cover every goal 19 item, `task lint-docs` passes, and the hygiene scan stays clean.
- **Discard:** N/A. This step is always completed; on disagreement, the code docstring is the source of truth and the
  doc is fixed.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.5   |
| impact_pred         | 5.0   |
| cost_pred           | 3.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 1.75  |

### Step Metrics Rationale

This is low-risk documentation work, but required for acceptance.
`EV = 0.88 * 5.0 + 0.5 * 1.0 - 0.3 * 0.5 - 3.0 = 4.40 + 0.50 - 0.15 - 3.00 = 1.75`.

---

## Step 9: Final leak sweep, gates, CodeQL and PR

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the integration and verification step. It must run on the full branch and cannot be isolated.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove completeness (goal 13 sweep), pass every gate (goal 21) with zero CodeQL alerts (goal 16), and
  open the PR with the known gap documented (goal 18).
- **Git branch:** same execution branch, then a PR targeting the intent branch
  `I-1792815987-redact-github-token-in-cli-echoes-v2/_`. Only a human merges it on to `main`.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Sweep all non-test tracked `.py`, `.sh` and `Makefile` files for `print(`, `logger.*(`, `echo` and `RuntimeError(` /
   `Exception(` messages that interpolate:
   - a command or argv;
   - a URL or `repo_url`;
   - an environment mapping;
   - subprocess `stdout` or `stderr`.

   This includes `entrypoint/role_dispatcher.sh`, `build_all_images.sh`, `Makefile`, `formatting.py`, `flow.py` and
   `agent_runner.py`. Record each hit as redacted, safe (with the reason) or fixed, and append the results to the
   execution report and the PR body.

2. Confirm that the Step 5 probe finding on explicit env dicts (for example `flow.py` `proxy_env`) is resolved.
3. Rebase on current `origin/main` again and confirm a clean merge.
4. Run `uv run task lint`, `uv run task lint-docs` and `uv run task test` at the worktree root, and `uv run ruff check`
   and `uv run ruff format --check` in `apps/sandbox-executor`. All existing and new tests must be green.
5. Push the branch and open the PR. The PR body:
   - states that it supersedes the closed PR #92 and cites Bean 0149 provenance;
   - includes the leak inventory and the sweep results;
   - records the mitmweb argv known gap and the out-of-scope Bean 0148 note;
   - ends with the required attribution line.

   Wait for all `pull_request` workflows: `test-unit`, `test-hygiene`, `make`, `build-images` and CodeQL.

6. If CodeQL reports `py/clear-text-logging-sensitive-data`, fix the source: rename the variable to a neutral name,
   build the value at runtime, or ensure the sink receives already-redacted text. Never dismiss an alert or edit the
   CodeQL configuration.
7. Append to the execution ledger only through the standard tooling. Create no auxiliary ledgers.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 8
- **Is Bottleneck:** NO (failures here are usually fixable iterations rather than plan-ending)

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §1 (rebase, human-only merge to `main`) and §3 (ledger); `docs/safety.md`
  Invariants 1, 4 and 6; intent goals 16, 21 and 22.
- **Potential failure modes for this step:**
  - CodeQL flags production sinks that log env-derived data.
  - A merge conflict with new `main` commits in `cli.py` or `test_cli.py`.
  - CI flakiness.
- **Guardrails and early‑abort checks:**
  - Never force-push to the intent branch or `main`.
  - Make the PR body free of any token: run the hygiene scan over the body text before posting.

### Success & Discard Criteria

- **Success:** The sweep is documented, all local gates and CI checks are green, there are zero open CodeQL alerts, and
  the PR is open against the intent branch.
- **Discard:** If CI or CodeQL still fails after three fix iterations, stop and request human review with the
  diagnostics. If more than three conflicts appear on rebase, escalate per `docs/safety.md` "Rebase conflict".

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 0.8   |
| impact_pred         | 7.0   |
| cost_pred           | 5.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 2.36  |

### Step Metrics Rationale

Success depends on external CI and CodeQL behaviour and on how far `main` has moved. Learning value comes from recording
which CodeQL taint paths the runtime-built fixture pattern avoids.
`EV = 0.80 * 7.0 + 0.5 * 4.0 - 0.3 * 0.8 - 5.0 = 5.60 + 2.00 - 0.24 - 5.00 = 2.36`.

---
