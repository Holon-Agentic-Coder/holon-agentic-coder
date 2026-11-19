# Plan for I-1794196901-resolver-uv-available-and-fail-loudly

- **Plan ID:** P-1794196917-8bed7894-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-09T04:01:57.933Z

## Planner Autonomy Summary

- **Intent handling:** REFRAME (light). The four goals are accepted, and one root-cause fix is added in the flow loop.
  It comes from the planning analysis and the intent did not name it.
- **Reframed intent (if applicable):** Same goals 1 to 5. One addition: a failed resolver pass must never feed its
  fallback dispositions into the loop's rejected-findings history. Read-through of the code shows this is how the bad
  "unanimous approval, zero nits" post came about. Fixing only the heading would hide the symptom and leave the cause.
- **Exploration stance:** balanced. Most of the work is exploit: the defects are located to the line. One step is
  exploratory: capturing the container output tail. The host dispatcher (`cli/__init__.py`) streams container output and
  does not capture it, and that file is outside the intent scope, so the tail has to come from inside the container.
- **Safety priority level:** elevated
- **Priority Justification:** This is a silent data-loss path, and the GitHub post states something false. The fix
  touches the Stage 4 review loop, which decides what is pushed to PR branches and what is posted under the agent's name
  (`docs/safety.md` §5, human review is the final gate). The loop must still never merge. The append-only ledgers
  (`holon-config/world/constraints.md` §3) are written by the code under change, so recording semantics must stay
  append-only and honest. Elevated is the right level. Critical is not needed, because nothing here widens agent
  capability, network reach or credentials.

## Exploration

- **Proportion of steps that are exploratory:** 0.125 (1 of 8 steps; Step 4 is BALANCED/EXPLORATORY on output-tail
  capture)
- **Justification:** The bugs were confirmed by reading the code (see Plan Description). Only the source of the "output
  tail" is uncertain, and it has a bounded fallback. More exploration would add cost with little learning return.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.72  |
| entropy_pred        | 12.4  |
| impact_pred         | 90.0  |
| cost_pred           | 35.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 28.58 |

## Plan Description & Strategy

### Causal chain confirmed during planning (read-only analysis)

1. `entrypoint/review_loop.py` `run_cmd` calls `subprocess.run` with no `try`. If `uv` is absent it raises
   `FileNotFoundError`. `_verify_before_push` is called from `_commit_and_push_resolution`, outside the `try` in
   `_run_resolver_mode`. The exception therefore escapes, the result file is never rewritten and the container exits
   non-zero with a traceback.
2. `flow/review/resolver.py` `_dispatch_review_resolver` then reads the result file. That file still holds the host's
   input payload, because `--findings-file` and `--result-file` are the same `/tmp/review_result.json`. It has no
   `dispositions` key, so `deserialize_finding_dispositions(None, findings)` assigns every finding the fallback
   disposition `rejected` / `out of scope` (`review_report.py` `_disposition_from_mapping`). An error string is set, but
   `pushed=False` and the dispositions are all "rejected".
3. `flow/review/loop.py` halts on a resolver error only when `not head_changed and not rejected_now`. Because
   `rejected_now` is non-empty (the fallback rejections), the loop does **not** halt. It records the fake rejections
   into `rejected_history` and `rejected_ids` and starts the next iteration.
4. In the next iteration `_filter_adjudicated_findings` removes every finding whose signature is in `rejected_history`.
   It then rewrites a `CHANGES_REQUESTED` reviewer with no kept findings to `APPROVED`. The ensemble then sees unanimous
   approval with zero findings, and `_heading` in `review_report.py` prints `APPROVED ({configured}/{configured})`
   without counting real approvals.

Also found: if the resolver's commit succeeds but its push fails, `head_sha` differs from the reviewed head, so
`head_changed` is true. The loop then re-reviews a head that was never pushed. The same "halt on any resolver error"
rule closes this path.

### Strategy

Fix each link of the chain at the module that owns the data. Each link gets a hermetic test:

- **Image:** ship a pinned uv in `holon-base` with a build-time smoke check (Step 2).
- **Entrypoint:** turn a missing executable into a structured, named verification failure, and guarantee a resolver
  result file is always written, including the exit code and a bounded output tail (Step 3).
- **Flow resolver dispatch:** treat a non-zero exit, or a missing resolver result, as a resolver failure that carries
  the exit code and tail. Move result parsing to a new focused module so the line budgets of `resolver.py` (368/368) and
  `loop.py` (966/966) are not raised (Step 4).
- **Flow loop:** halt on any resolver error **before** its dispositions can enter the rejected history (Step 5).
- **Report:** compute the approval fraction from the real reviewer verdicts and always list open findings (Step 6).
- **Tests and validation:** add tests (a) to (e), then run lint, lint-docs, the budgets check and the full suite once
  (Steps 7 and 8).

Budget facts that constrain placement: every module in `flow_module_budgets.json` sits exactly at its budget. That
includes `review/loop.py` 966, `review/resolver.py` 368 and `github/review_post.py` 216. Any added line in those files
must be paid for by moving existing code into a new module. The new module gets a budget entry at its creation size,
which the budget file's own description allows. No existing budget is raised. `review_post.py` needs no change: it
already passes the deciding iteration's `reviewers` straight into `build_review_consensus_report`. The honesty fix
therefore belongs in `review_report.py`. `review_post.py` stays untouched unless Step 6 finds otherwise.

### Touch set (module granularity)

- `apps/sandbox-executor/Dockerfile` (holon-base stage, plus one tiny named `uv-bin` source stage)
- `apps/sandbox-executor/docs/docker_bake.md` (image contents and `UV_VERSION` build argument)
- `apps/sandbox-executor/docker-bake.hcl`: expected **unchanged**. It is touched only if the executor decides to surface
  `UV_VERSION` as a bake `args` entry. The Dockerfile `ARG` default is the single source of truth.
- `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/review/resolver.py` (shrinks)
- `apps/sandbox-executor/src/sandbox_executor/flow/review/resolver_result.py` (**new**, focused: parsing the container
  resolver result and the failure semantics)
- `apps/sandbox-executor/src/sandbox_executor/flow/review/loop.py` (net line count unchanged or lower)
- `apps/sandbox-executor/flow_module_budgets.json`: **add** one entry for the new module at its creation size, and lower
  the `resolver.py` and `loop.py` entries if they shrink. No entry is raised.
- `apps/sandbox-executor/src/sandbox_executor/review_report.py`
- `apps/sandbox-executor/tests/test_container_images.py`, `tests/test_review_loop.py`, `tests/test_review_report.py`,
  and optionally a new `tests/test_review_resolver_failure.py` for the loop-level halting tests

`flow_module_budgets.json` is not in the intent's `scope` list. The budget file's own contract requires a new entry for
a new flow module, so this one-entry addition is declared here explicitly. The alternative, squeezing everything into
modules already at budget, has a much lower `p_success`.

---

## Step 1: Establish the baseline and pin the facts the later steps depend on

- **Sub‑intent recommendation:** NO
- **Reasoning:** This step only reads code and runs commands, costs little and changes nothing. It fixes the line
  counts, the test baseline and the uv release used later.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm the causal chain and the budgets recorded in this plan against the executor's checkout, and
  choose the uv release to pin.
- **Git branch:**
  `I-1794196901-resolver-uv-available-and-fail-loudly/P-1794196917-8bed7894-claude-agent-claude-opus-5-5/E-{timestamp}-implement/_`
  (the single execution branch for all steps)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Rebase the execution branch on the plan branch, as `holon-config/world/constraints.md` §1 requires.
2. Run
   `uv run pytest apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_review_report.py apps/sandbox-executor/tests/test_container_images.py`
   and `uv run task lint`, and record the baseline pass counts.
3. Run `python scripts/check_flow_module_budgets.py` (through `uv run`) and record the current line counts of
   `review/loop.py`, `review/resolver.py` and `github/review_post.py` against their budgets. Planning found all three
   exactly at budget.
4. Choose the uv release to pin. Use the newest stable `ghcr.io/astral-sh/uv:<x.y.z>` tag that can parse the
   repository's `uv.lock` (check it with `uv --version` locally, against the lock's `version`/`revision`). Record the
   exact `x.y.z`. Do not use `latest` or a floating minor.
5. Read `sandbox_executor.turns.run_turn` and note whether its outcome exposes captured stdout and stderr (field names).
   Step 4 uses this to build the output tail.
6. Read `tests/test_unique_stage_ids.py` and `tests/test_build_all_images.py` to see whether a new Dockerfile stage name
   (`uv-bin`) or a new `ARG` breaks an existing assertion.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 (rebase discipline), `docs/safety.md` §1
- **Potential failure modes for this step:** the baseline is already red; the uv release cannot be checked offline.
- **Guardrails and early‑abort checks:** if the baseline suite is red on untouched code, record which tests fail and
  continue. Do not fix unrelated failures (no behaviour change outside scope).

### Success & Discard Criteria

- **Success:** Baseline counts, budget counts, the uv version and the `run_turn` output fields are written into the
  execution notes.
- **Discard:** Never discarded. If the uv tag cannot be checked, pin the version from `astral-sh/setup-uv` release notes
  that is compatible with the lock, and flag it for review.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.6   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 9.52  |

### Step Metrics Rationale

`EV = 0.97 * 10.0 + 0.5 * 4.0 - 0.3 * 0.6 - 2.0 = 9.70 + 2.00 - 0.18 - 2.00 = 9.52`. Read-only, so almost certain to
succeed. The learning value is in confirming facts that decide where the later code goes.

---

## Step 2: Ship a pinned uv in holon-base with a build-time smoke check

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, declarative and checked by a hermetic test. Nothing else in the plan can break it.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make every image built from `holon-base` (all six agents and the orchestrator) carry the same pinned
  uv on PATH for the `holon` user. The build fails if `uv --version` does not run.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. At the top of the Dockerfile, before the first `FROM`, declare one global build argument `UV_VERSION`, defaulting to
   the exact release chosen in Step 1. This is the single pin.
2. Add a minimal named source stage that pulls the official distroless uv image at that tag, for example
   `ghcr.io/astral-sh/uv:${UV_VERSION}`, named `uv-bin`. Astral documents this pattern. It is multi-arch, needs no
   installer script and no network download in `holon-base`, and is reproducible.
3. In `holon-base`, while still root (after the Node.js layer and before `USER holon`), copy `/uv` and `/uvx` from
   `uv-bin` into `/usr/local/bin/`. That directory is on PATH for every user. A cache mount does not apply to a COPY.
   The existing pattern stays where it belongs: the agent stages' `npm` caches and the antigravity `uv` cache mount at
   `/home/holon/.cache/uv`. Leave those unchanged.
4. After `USER holon` and the `ENV PATH=...` line, add `RUN uv --version`. It runs as the `holon` user, so it proves uv
   is on that user's PATH, and a non-zero exit fails the build. Every downstream stage inherits it from `holon-base`.
5. Do not set `UV_PYTHON_DOWNLOADS`, `UV_CACHE_DIR` or other uv environment variables. That would change behaviour
   beyond the intent.
6. Update the target list comment at the top of the Dockerfile only if `uv-bin` needs mentioning. It is a source stage,
   not a build target, so a short comment on the stage itself is enough.
7. `docs/docker_bake.md`: under the base-image row (or a short new "Base image contents" note), state that `holon/base`
   ships uv pinned by the `UV_VERSION` build argument and smoke-checked at build time. Also state how to override it
   (`--set *.args.UV_VERSION=...`). Use Australian English, and keep the tables prettier-clean.
8. `docker-bake.hcl`: leave unchanged. The Dockerfile `ARG` default is the single pin, so the version is not duplicated.
9. Test (e), in `tests/test_container_images.py`: add a hermetic class that reads the Dockerfile text and asserts the
   following:
   - exactly one `ARG UV_VERSION=` with a strict `\d+\.\d+\.\d+` value (not `latest`)
   - a `FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv-bin` source stage
   - a `COPY --from=uv-bin` of `/uv` into the `holon-base` stage. Slice the text between `AS holon-base` and the next
     `FROM`.
   - `RUN uv --version` placed after `USER holon` inside that slice

   Optionally add an `integration_test`-marked container check, mirroring the existing `gh` one, that runs
   `uv --version` as UID 1000. It skips when Docker is unavailable, so it stays out of the default run.

### Dependencies & Criticality

- **Depends on:** Step 1 (uv version)
- **Is Bottleneck:** NO (the code-side failure path in Steps 3 to 5 still protects against a missing uv)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §2 (minimal base image, sandboxing), `holon-config/world/ruleset.md` §1 (uv is
  the package manager)
- **Potential failure modes for this step:** the tag does not exist (the build fails, and that is exactly what the smoke
  check is for); a stage-name uniqueness test conflicts; `ARG` scoping (a global `ARG` must be redeclared inside a stage
  to use it there, but it is used here only in a `FROM` line, where global scope works).
- **Guardrails and early‑abort checks:** if Docker is available, run
  `docker buildx bake -f apps/sandbox-executor/docker-bake.hcl base` once to prove the tag resolves. If it is not
  available, rely on the hermetic test and flag the image build for the maintainer.

### Success & Discard Criteria

- **Success:** Test (e) passes, `uv run task lint-docs` passes, and an optional local bake of `base` prints the uv
  version.
- **Discard:** If the official image pattern is unavailable (registry policy), fall back to `pip install uv==<x.y.z>` as
  root with a `--mount=type=cache,target=/root/.cache/pip` cache mount. Keep the same `ARG` and smoke check.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.6   |
| impact_pred         | 25.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 19.52 |

### Step Metrics Rationale

`EV = 0.90 * 25.0 + 0.5 * 3.0 - 0.3 * 1.6 - 4.0 = 22.50 + 1.50 - 0.48 - 4.00 = 19.52`. This step has a large impact: it
removes the trigger for every future flow. The residual risk is that the tag cannot be checked inside the sandbox.

---

## Step 3: Make a missing verification tool a structured, named resolver failure in the entrypoint

- **Sub‑intent recommendation:** NO
- **Reasoning:** The change is contained in one entrypoint module, and the existing `_run_resolver_mode` tests already
  cover the surrounding behaviour. It is moderately risky only because `run_cmd` is shared by every git call in the
  module.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** With uv absent, `_verify_before_push` returns `(False, False, message naming uv)` without raising.
  `_commit_and_push_resolution` then makes no `git add`, no commit and no push. `_run_resolver_mode` always writes a
  resolver result with the error preserved, the exit code and a bounded output tail.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `run_cmd`: wrap the `subprocess.run` call to catch `FileNotFoundError` first, then any other `OSError`. On a catch,
   build a synthetic `subprocess.CompletedProcess` with return code `127`, empty stdout, and an actionable stderr naming
   the executable (`args[0]`). For example: "required tool 'uv' was not found on PATH; it ships in the holon-base image
   (UV_VERSION) — rebuild the agent image or install uv". Name the class for a generic `OSError` (permission denied, and
   so on). Then apply the existing `check` semantics unchanged: with `check=True` it prints the redacted diagnostics and
   calls `sys.exit(127)`, as for any failed command; with `check=False` it returns the synthetic result. The echo line
   and the redaction stay as they are. Keep the function under the ruff complexity limits by putting the
   synthetic-result construction in a small helper, for example `_missing_tool_result(args, exc)`.
2. `_verify_before_push`: when lint or test returns code 127 **and** the stderr carries the missing-tool marker, return
   a message of the form "Resolver pre-push verification could not run: required tool 'uv' was not found on PATH ...".
   This keeps a missing tool distinct from a lint or test failure. The return shape `(lint_ok, tests_ok, error)` is
   unchanged, so callers and existing tests stay compatible. A missing tool reports `lint_ok=False` and
   `tests_ok=False`, because neither was measured.
3. `_commit_and_push_resolution`: no logic change is needed for the uv case, because the early return on
   `not lint_ok or not tests_ok` already comes before `git add`. Add a short comment that verification failures, a
   missing tool included, never commit or push.
4. `_run_resolver_mode`: put `_commit_and_push_resolution` (and the `git rev-parse` before it) inside a
   `try`/`except Exception`. Today it is outside any guard. A stray exception becomes
   `error = "Resolver verification or commit failed: <bounded, redacted message>"`, and the result file is still
   written. Add two keys to the outcome:
   - `output_tail`: a bounded (for example 2,000 characters), secret-redacted tail of the resolver agent turn output if
     `run_turn` exposes it (Step 1.5), else of the failure's stderr or exception text
   - `exit_code`: the code the process is about to exit with

   Both are additive JSON keys. Existing readers ignore unknown keys. Use the existing `_bounded_error_context` and
   `redact_agent_secrets` helpers; add no new dependencies.

5. Keep `_run_resolver_agent`'s existing behaviour, apart from carrying a bounded output tail into the error when it
   raises on a non-zero agent exit.
6. Keep the module's size growth small (the target is under 40 lines). `review_loop.py` is not in the flow budget file,
   but it is already 853 lines. If growth goes past about 40 lines, move the missing-tool helpers into a new focused
   module `entrypoint/review_verification.py` and import them back.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (acceptance criterion 2 depends on it). `p_success_pred` is well above 0.6, so a sub-intent is
  not required.

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 and §5 (no unverified push to a PR branch), the redaction rules (the GitHub
  token can appear in git stderr), and the complexity limits (`C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`) with no new
  `noqa`
- **Potential failure modes for this step:** a missing `git` now exits 127 instead of raising a traceback (this is an
  intended improvement, and the code stays at `check=True` semantics); the output tail leaks a secret; complexity of
  `_run_resolver_mode` goes past C901.
- **Guardrails and early‑abort checks:** pass the output tail through `redact_agent_secrets` before it is written. Run
  `ruff check` on the module after the edit. If C901 trips, move the result assembly into a helper
  `_resolver_outcome(...)`.

### Success & Discard Criteria

- **Success:** Test (a) passes: with PATH pointing at an empty temporary directory, `_verify_before_push` returns a
  failure whose message contains `uv` and does not raise. The entrypoint half of test (b) passes: the result file
  records the error, `pushed` is false and HEAD is unchanged. Existing `_run_resolver_mode` tests stay green.
- **Discard:** If existing tests depend on `run_cmd` raising `FileNotFoundError`, stop and limit the catch to the two
  verification calls. Do this through a dedicated `_run_verification_cmd` wrapper instead of changing the shared
  `run_cmd`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.8   |
| impact_pred         | 25.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 18.46 |

### Step Metrics Rationale

`EV = 0.90 * 25.0 + 0.5 * 3.0 - 0.3 * 1.8 - 5.0 = 22.50 + 1.50 - 0.54 - 5.00 = 18.46`. The fix is simple. Entropy comes
from `run_cmd` being shared across the module.

---

## Step 4: Move container resolver-result parsing into a focused module that fails closed on a non-zero exit or a missing result

- **Sub‑intent recommendation:** NO
- **Reasoning:** This step is a refactor plus a behaviour fix, and it is needed to fit the budgets. It is closely tied
  to Step 5, so splitting it into its own branch would add coordination cost and little isolation benefit.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The output tail can be delivered reliably from inside the container through the resolver
  result file (Step 3). Only hard kills (OOM, SIGKILL, a Docker failure before the entrypoint starts) leave no tail, and
  those can be reported as "no resolver result produced" with the exit code. None of this needs changes in
  `cli/__init__.py`, which is out of scope.
- **Learning target:** Whether the containerised resolver's failures are explained well enough without host-side output
  capture. The answer decides whether a follow-up intent for dispatcher output capture is worth proposing.
- **Maximum acceptable cost for this learning:** At most one extra hour of execution. No change outside the declared
  touch set.

### Intent & Git Integration

- **Step Intent:** A resolver container that exits non-zero, or produces no resolver result, gives a `ResolverOutcome`
  whose `error` names the exit code and carries the output tail, or states that none was produced. It is never "a pass
  with no changes". `resolver.py` shrinks, so its budget is respected.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `flow/review/resolver_result.py` with a module docstring. It owns two things:
   - `resolver_outcome_from_container(data, exit_code, findings, lookup, target)`, which turns the parsed result mapping
     and the dispatch exit code into a `ResolverOutcome`
   - a small predicate `_has_resolver_result(data)`, true only when the payload carries the entrypoint's marker
     `"resolver": True`. The host's own input payload lacks that marker, so an untouched input file is correctly treated
     as "no result".
2. Move `ResolverOutcome` into the new module, since it owns the type, and re-export it from `resolver.py` so
   `from sandbox_executor.flow.review.resolver import ResolverOutcome` keeps working for `loop.py` and the tests. If a
   re-export would confuse import-cycle checks, keep the dataclass where it is and move only the parsing.
3. Failure semantics in `resolver_outcome_from_container`:
   - **No resolver result**: the marker is absent or the file is unreadable. Return
     `error = "Resolver container exited with code {exit_code} and produced no resolver result"` (with "exited cleanly"
     wording when the code is 0), `pushed=False`, `lint_passed=False`, `tests_passed=False`, and `head_sha` set to the
     target's head. Use the dispositions that `deserialize_finding_dispositions([], ...)` gives, but mark the outcome as
     failed so Step 5 does not adjudicate them. The simplest way is the `error` field itself, since Step 5 makes any
     error halt before the rejected-history update.
   - **Result present and exit code not 0**: keep the entrypoint's `error` if one is set. Otherwise synthesise "Resolver
     container exited with code N". Either way, append the bounded `output_tail` from the payload (redacted with
     `redact_agent_secrets`). This replaces today's `exit_code != 0 and not err and "dispositions" not in data` guard,
     which let a non-zero exit with dispositions pass silently.
   - **Result present, exit code 0 and no error**: same as today: dispositions, `head_sha`, `pushed`, and lint and test
     flags from the payload.
4. In `resolver.py`, replace the inline parsing block in `_dispatch_review_resolver` with one call to the new function.
   The `except Exception` path keeps its current message and is unaffected. Also have the Mapping branch of
   `_invoke_resolver_pass` (the injected-seam path) pass through `exit_code` and `output_tail` when present, using the
   same helper for the error text. That keeps one place for the failure-message format. The net line change in
   `resolver.py` must be negative.
5. Add the new module to `flow_module_budgets.json` at its creation line count. Lower the `review/resolver.py` budget to
   its new size, since the budget file asks for this after shrinking a module.
6. Keep the new module under about 120 lines, with every function inside the ruff complexity limits.

### Dependencies & Criticality

- **Depends on:** Step 3 (the `resolver` marker, `exit_code` and `output_tail` keys written by the entrypoint)
- **Is Bottleneck:** YES (acceptance criterion 3). `p_success_pred` is 0.82, above 0.6, so a sub-intent is not required.

### Safety & Constraint Considerations

- **Relevant rules:** "Add no new imports from entrypoints into the host flow engine". The new module imports only
  `sandbox_executor.review_report`, `redaction` and the flow's own records. Also the flow budget contract
  (`flow_module_budgets.json`), and secret redaction.
- **Potential failure modes for this step:** existing tests construct the payload without the `resolver` marker (for
  example fakes that write `{"dispositions": [...]}` with exit 0), so they would be misread as "no result".
- **Guardrails and early‑abort checks:** treat the marker as required **only when `exit_code != 0`**. With exit code 0
  and a `dispositions` key, keep today's behaviour, so the existing fakes stay valid. Run `tests/test_review_loop.py`,
  `tests/test_review_dispatch.py` and `tests/test_review_workspace_integration.py` right after the move.

### Success & Discard Criteria

- **Success:** Test (c) passes. A fake dispatcher returns 2 and leaves the input payload untouched, and the outcome
  error contains "exit code 2" (or "exited with code 2") plus "no resolver result". A fake dispatcher returns 1 with an
  entrypoint result carrying `output_tail`, and the error includes that tail. The budgets check passes.
- **Discard:** If the extraction would put the re-export in an import cycle, keep `ResolverOutcome` in place. If the
  budgets cannot be met even then, stop and re-plan rather than raise a budget.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 2.2   |
| impact_pred         | 20.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 12.24 |

### Step Metrics Rationale

`EV = 0.82 * 20.0 + 0.5 * 5.0 - 0.3 * 2.2 - 6.0 = 16.40 + 2.50 - 0.66 - 6.00 = 12.24`. The probability is lower because
of the budget squeeze and the compatibility of existing fakes. The learning value is higher because it shows whether
host-side output capture is a necessary follow-up.

---

## Step 5: Halt the review loop on any resolver failure, before its dispositions are adjudicated

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the root-cause fix and the riskiest edit (shared loop state, ledger recording and a module at
  budget). It stays in the same branch because it cannot be tested apart from Step 4. It gets the densest test coverage
  in Step 7.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** When `resolver_outcome.error` is set, the loop records the failure, does not add any of that pass's
  dispositions to `rejected_history` or `rejected_ids`, does not move `resolved_target` to an unpushed head, and stops.
  The loop result reports the error (status `failed`, with the error in `halt_reasons`). The next iteration never starts
  as if the findings had been applied or adjudicated.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `_run_review_loop_iterations`, move the resolver-error check to **directly after** `_invoke_resolver_pass`
   returns, before `rejected_now` and `applied_now` are computed. Make it unconditional: `if resolver_outcome.error:`,
   with no `not head_changed and not rejected_now` qualifiers. That covers the crash, the non-zero exit and the
   "committed but push failed" case.
2. In that branch:
   - set a loop-local `resolver_error` to the bounded error
   - log `[Review Loop] Halting review loop at iteration N: resolver failed: <error>`
   - flush `pending_dispositions` as the current code does
   - `break`

   Do **not** record the failed pass's fallback dispositions as rejections. The post-loop "remaining findings" fallback
   that already exists records the still-open findings exactly once. That keeps the ledger append-only and avoids
   duplicates. Check the duplicate question in Step 7 against `recorded_disposition_keys`.

3. Thread `resolver_error` into the existing result assembly. Append it to `halt_reasons`, so the summary reads "halted
   without consensus ...: resolver failed: ...". Also add it as a `resolver_error` key in the loop result so the Stage 4
   stage (`stages/review.py`) and the posted consensus can show it. Status stays `failed`, because consensus was not
   reached. Do not alter `_loop_status` semantics beyond what the existing `infra_error`-style flags already give.
4. Leave `_filter_adjudicated_findings` alone. Genuine resolver rejections (the resolver argued a finding is out of
   scope) keep their current behaviour, as the intent's "no behaviour change apart from the above" requires. With step
   5.1 in place, failed passes can no longer feed it.
5. Budget: `loop.py` is at 966/966. Pay for the added lines by moving the self-contained CI-finding construction block
   (`FIND-CI-{iteration}` row plus action-id bookkeeping, about 25 lines) into a helper in the new `resolver_result.py`.
   If that is a poor fit, put it in another new focused module (for example `flow/review/ci_finding.py`, with its own
   budget entry). Then lower the `loop.py` budget to its new size if it shrinks. Never raise it.
6. Keep `_run_review_loop_iterations`' existing `noqa` list exactly as it is. Add no new suppressions. The function's
   complexity must not get worse: the moved check replaces a more complex one.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** YES (acceptance criteria 2 and 3). `p_success_pred` is 0.80, above 0.6, so a sub-intent is not
  required.

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (append-only ledgers; this step only changes **what** is
  appended, never rewrites lines), `docs/safety.md` §5 (merging stays with the human; the loop's halt-at-approval safety
  is untouched), "keep exactly the three append-only ledgers" (no new ledger file)
- **Potential failure modes for this step:** existing tests expect the loop to continue after a resolver error with
  rejections (as the old `not rejected_now` path did); the ledger count changes in `suppressed_ledger_records`; the
  budget cannot be met.
- **Guardrails and early‑abort checks:** grep the tests for scenarios with a resolver error plus rejections, and
  classify each one: does it pin the buggy behaviour, or a legitimate partial resolution? A legitimate one is a resolver
  that rejected some findings **without** error, and that is unaffected. Update only tests that pin the bug, and list
  each change in the execution report, since the ruleset requires test changes to be declared.

### Success & Discard Criteria

- **Success:** Test (b, loop half) and test (c, loop half) pass. With a failing resolver, the ensemble fake is called
  exactly once. The loop result carries the resolver error. `rejected_history` stays empty, so no reviewer verdict is
  rewritten. The flow budgets check passes with no raised budget.
- **Discard:** If more than three existing tests depend on continuing after a resolver error, stop. Record the conflict
  and re-plan rather than rewrite large parts of the test suite.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 2.4   |
| impact_pred         | 25.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 16.28 |

### Step Metrics Rationale

`EV = 0.80 * 25.0 + 0.5 * 6.0 - 0.3 * 2.4 - 6.0 = 20.00 + 3.00 - 0.72 - 6.00 = 16.28`. This step has the highest entropy
in the plan, because of shared loop state, the budget squeeze and the chance that existing tests pin the bug. The
learning value is high: it shows how many tests encode the silent-continue behaviour.

---

## Step 6: Render the consensus post's approval fraction and open findings from the final iteration as recorded

- **Sub‑intent recommendation:** NO
- **Reasoning:** A small, pure rendering change in one module, with an existing test file that pins the report shape.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The heading's fraction equals the number of completed reviewers whose recorded verdict is `APPROVED`,
  out of the configured ensemble size. Open findings of the final iteration are always listed. Per-reviewer lines keep
  showing each reviewer's recorded verdict and critical, important and nit counts.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `review_report.py`, add `_approvals(reviewers)`, which counts results with `status is ReviewerStatus.COMPLETED`
   and `verdict is ReviewerVerdict.APPROVED` (import `ReviewerVerdict` from `review_ensemble` if it is not already
   imported).
2. Change `_heading(consensus_data, configured)` to `_heading(consensus_data, approvals, configured)`:
   - Approved: `APPROVED ({approvals}/{configured})`. Under the unanimity rule in `decide_consensus` this stays `3/3`
     for a genuine approval, so the existing test at `tests/test_review_report.py:204` holds.
   - Not approved: `CHANGES REQUESTED ({approvals}/{configured} approved, Iteration N)`. The existing tests assert that
     `"APPROVED ("` does not appear in a non-approved body. The lower-case `approved` keeps that true, and the executor
     must re-check it. If the wording conflicts with the prompt template's "Posted review shape" (see
     `holon-config/prompts/review_loop.template.md`), choose wording that satisfies both, and do not edit the template
     (out of scope).
3. Body: today an approved report shows only the Verified section, so any leftover finding would go unlisted. Render the
   findings table whenever `data.findings` is non-empty, whatever the verdict. When approved with findings (which should
   not happen under unanimity, but is guarded anyway), show the Verified section followed by the findings table. Check
   the existing assertion at `tests/test_review_report.py:163`, which expects no `| ID |` in an approved body. That
   holds because approved bodies in those tests have zero findings.
4. `_evaluation` already computes the severity counts from `collect_review_data(reviewers)` of the deciding iteration,
   and `render_reviewer_line` already prints the recorded verdict and counts. Change neither. Confirm in Step 7 that the
   reviewers passed by `loop.py` (line ~777) are the final iteration's, since `reviewers` is reassigned each iteration.
5. `flow/github/review_post.py`: expected unchanged. It already forwards the reviewers and decides `--approve` from the
   ensemble verdict only. Touch it only if Step 7 shows it drops data. Its budget is 216/216, so any change must be
   line-neutral.
6. Keep the per-reviewer breakdown, the CI line and the closing notice byte-for-byte the same.

### Dependencies & Criticality

- **Depends on:** NONE (independent of Steps 3 to 5; it can be done in parallel)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §5 (the posted review is what the human maintainer relies on before merge);
  Australian English in new wording
- **Potential failure modes for this step:** the prompt template or `test_artefacts.py` pins the exact heading text;
  prettier or markdown expectations in `lint-docs` (not affected, because this is a Python string).
- **Guardrails and early‑abort checks:** run `tests/test_review_report.py`, `tests/test_artefacts.py` and
  `tests/test_review_loop.py -k consensus` immediately after the edit.

### Success & Discard Criteria

- **Success:** Test (d) passes. A final iteration with two `APPROVED` reviewers and one `CHANGES_REQUESTED` reviewer
  with one nit renders a heading containing `2/3`, the evaluation paragraph says
  `0 critical, 0 important and 1 nit findings`, the findings table lists that nit's ID and summary, and the third
  reviewer's line shows `CHANGES_REQUESTED` with `nit: 1`. All existing report tests stay green.
- **Discard:** If a pinned template contract forbids a fraction in the non-approved heading, put the fraction on the
  first line under the heading instead. That still satisfies "the approval fraction in the heading" as closely as the
  template allows, and the deviation is recorded.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.2   |
| impact_pred         | 15.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 11.14 |

### Step Metrics Rationale

`EV = 0.90 * 15.0 + 0.5 * 2.0 - 0.3 * 1.2 - 3.0 = 13.50 + 1.00 - 0.36 - 3.00 = 11.14`. A pure function with good
existing tests. Most of the impact on the honesty problem comes from Step 5, which removes the verdict rewriting; this
step removes the hardcoded fraction.

---

## Step 7: Add the hermetic tests (a) to (e) and the regression for the observed incident

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests are the acceptance evidence for every step. They follow the hermetic patterns already in place
  (`review_fakes.py`, `hermetic_fixtures.py`, injected `run_resolver_fn` and `context.dispatcher` seams).
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Encode each acceptance criterion as a deterministic, network-free pytest test, run through
  `uv run pytest`.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. **(a)** In `tests/test_review_loop.py`, use `monkeypatch.setenv("PATH", <empty tmp dir>)` and call
   `_verify_before_push(tmp_repo)`. Assert that it returns `lint_ok is False`, `tests_ok is False`, and an error string
   containing `uv` and `not found`. Assert that no exception is raised. Add a second case that patches `subprocess.run`
   to raise a generic `PermissionError` (an `OSError`), and assert a structured failure naming the tool.
2. **(b, entrypoint half)** Build a real temporary git repo (`git init`, one commit, a bare repo as `origin`). Patch
   `_run_resolver_agent` to modify a file and return one `applied` disposition. Patch `run_cmd`'s environment, or use
   PATH manipulation that keeps `git` but hides `uv`: build a temporary bin directory with a symlink to the real `git`
   only. Run `_run_resolver_mode` with a `--result-file`. Assert:
   - `SystemExit` code 1
   - the result JSON has `resolver: true`, `pushed: false`, an `error` naming `uv`, `exit_code` 1 and an `output_tail`
     key
   - `git rev-parse HEAD` is unchanged
   - the bare origin's branch ref is unchanged (no push)
   - `git log` count is unchanged (no commit)
3. **(b, loop half)** In a new `tests/test_review_resolver_failure.py`, or in `tests/test_review_loop.py` if it stays
   cohesive, drive `run_review_loop` with the existing fakes:
   - an ensemble fake whose first iteration returns `CHANGES_REQUESTED` with one finding
   - a `run_resolver_fn` returning the entrypoint-shaped failure mapping from (b, entrypoint half)

   Assert:
   - the ensemble fake was called exactly once (the loop did not start iteration 2)
   - the loop result status is `failed` and its summary or `resolver_error` contains the uv message
   - no ledger row records the finding as an applied disposition with a commit SHA
   - the finding was not added to the rejected history: a second-iteration reviewer would not be rewritten. Check this
     indirectly by asserting no `APPROVED` consensus.

4. **(c)** Using a `context.dispatcher` fake (the container path through `_dispatch_review_resolver`) that returns exit
   code 2 and leaves the result file holding the host's input payload: assert that the outcome error contains the exit
   code and "no resolver result", and that the loop halts after one ensemble call. Add a second case where the
   dispatcher writes a result with `resolver: true`, some `dispositions`, an `output_tail` and exit code 1. Assert that
   the halt error includes the tail, so a non-zero exit with dispositions is no longer a silent pass.
5. **(d)** In `tests/test_review_report.py`, build three `ReviewerResult`s (two `APPROVED` with zero counts, one
   `CHANGES_REQUESTED` with one nit finding carrying an ID and summary), with `consensus_data` set to
   `ensemble_verdict: CHANGES_REQUESTED` and `ensemble_size: 3`. Assert:
   - the heading line contains `2/3`
   - the body contains `1 nit findings`, the nit's ID and summary in the findings table, and the third reviewer's
     `CHANGES_REQUESTED` line with `nit: 1`
   - `"APPROVED ("` does not appear
6. **Incident regression:** run a two-iteration loop where the first resolver pass fails, so no "applied" happens, and a
   second-iteration reviewer still flags the same nit. Assert that the run halts after iteration 1 and is never posted
   as `APPROVED (3/3)`. This directly pins the observed incident.
7. **(e)** This was covered in Step 2. Make sure it is collected in the default (non-integration) run.
8. Run only the affected files first:
   `uv run pytest apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_review_report.py apps/sandbox-executor/tests/test_container_images.py apps/sandbox-executor/tests/test_review_dispatch.py apps/sandbox-executor/tests/test_review_workspace_integration.py apps/sandbox-executor/tests/test_artefacts.py`,
   plus the new file if one was created. No repeated runs: the intent has no timing or performance target.
9. Declare in the execution report every pre-existing test assertion that was changed, with the reason (ruleset §3).

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 4, 5, 6
- **Is Bottleneck:** YES (the acceptance evidence). `p_success_pred` is 0.85.

### Safety & Constraint Considerations

- **Relevant rules:** hermetic testing (`apps/sandbox-executor/docs/hermetic_testing.md`, `tests/conftest.py` guards,
  `gh_write_guard.py`); no network; no real `gh` calls (use `skip_post` or the existing gh guard)
- **Potential failure modes for this step:** PATH manipulation hides `git` too; the sandbox hermetic guard blocks
  subprocess use in tests.
- **Guardrails and early‑abort checks:** build the bin directory with only a `git` symlink (taken from
  `shutil.which("git")`). If the hermetic guard forbids real git in this test module, fall back to patching
  `subprocess.run` with a fake that raises `FileNotFoundError` for `uv` and delegates everything else.

### Success & Discard Criteria

- **Success:** All new tests pass, and every pre-existing test in the affected files passes, or the execution report
  declares and justifies any change.
- **Discard:** If a test needs live Docker or network, mark it `integration_test` and add a hermetic counterpart
  instead.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.8   |
| impact_pred         | 15.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 8.21  |

### Step Metrics Rationale

`EV = 0.85 * 15.0 + 0.5 * 4.0 - 0.3 * 1.8 - 6.0 = 12.75 + 2.00 - 0.54 - 6.00 = 8.21`. This step has a high cost, because
it adds a lot of test code, including real git fixtures, and a moderate risk from the hermetic guard.

---

## Step 8: Run the full validation (lint, lint-docs, flow budgets, full suite once)

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the mandatory acceptance gate. It runs commands only.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove acceptance criterion 5 and that nothing outside the intent regressed.
- **Git branch:** same execution branch. The PR goes to the intent branch, and merging to `main` stays with the human
  maintainer.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run `uv run task lint`. It covers the ruff check, ruff format check, `uv lock --check` (with no new dependencies the
   lock must be untouched) and `scripts/check_flow_module_budgets.py`.
2. Run `uv run task lint-docs` (prettier on `docs/docker_bake.md` and this plan's Markdown).
3. Run `uv run task test` once (the full suite, `-n auto --dist worksteal`). Record the pass and fail counts and the
   pytest-reported time. No repeated runs.
4. Run `git diff --stat main...HEAD` and confirm that only touch-set paths changed. Confirm no `.jsonl` ledger file
   changed and no new ledger file was added. Confirm no budget in `flow_module_budgets.json` was raised: diff the
   numbers, where only additions for new modules and decreases are allowed.
5. Grep the new strings for American spellings ("behavior", "-ize") and fix them to Australian English.

### Dependencies & Criticality

- **Depends on:** Step 7
- **Is Bottleneck:** YES (the final gate)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (ledger immutability), §1 (branch scope), the intent
  constraints (no new dependencies, no budget raised)
- **Potential failure modes for this step:** formatting drift; an unrelated flaky test.
- **Guardrails and early‑abort checks:** run `ruff format` only on touched files. A failure in an unrelated test that
  was already red at the Step 1 baseline is reported, not fixed.

### Success & Discard Criteria

- **Success:** All three commands exit 0, and the diff audit is clean.
- **Discard:** If the full suite shows regressions in untouched areas caused by this change, revert to the last green
  step and re-plan that step.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.8   |
| impact_pred         | 10.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 6.26  |

### Step Metrics Rationale

`EV = 0.85 * 10.0 + 0.5 * 2.0 - 0.3 * 0.8 - 3.0 = 8.50 + 1.00 - 0.24 - 3.00 = 6.26`. The checks are mechanical. The risk
comes from the budgets check and the full suite.

---

### Strategy Rationale

**Intent assessment:** The intent is high value and feasible: it closes a silent data-loss path and a misleading GitHub
signal. It is accepted with one reframing addition. Reading the code shows that the misleading "3/3, zero nits" post did
not come from the renderer alone. It came from the loop: a failed resolver pass produced fallback `rejected`
dispositions; the `not rejected_now` guard let the loop continue; those rejections entered `rejected_history`; and
`_filter_adjudicated_findings` then rewrote the dissenting reviewer to `APPROVED` with zero findings. Fixing only the
heading (goal 3) would leave that cause in place. Step 5 halts on any resolver error before adjudication, and so fixes
the cause. Step 6 makes the post's arithmetic honest even if a future path regresses.

**Rejected alternatives:**

- Installing uv with `curl | sh` from astral.sh: it runs an unpinned installer script and needs network access in the
  base layer. Rejected in favour of `COPY --from` the pinned official image.
- Changing `cli/__init__.py` to capture container output for the "output tail": it is outside the intent scope and is a
  shared dispatcher for every role. The tail is delivered from inside the container instead (Step 3/4). A follow-up
  intent is warranted only if Step 4's learning target shows that hard kills are common.
- Splitting into sub-intents: the work is tightly coupled. Step 5 cannot be tested without Steps 3 and 4. Predicted
  entropy (12.4) is under the 15.0 budget, so one execution branch is cheaper and keeps all the evidence in one review.
- Raising the flow module budgets: forbidden by the intent. The plan pays for the new lines with extractions into one
  new focused module instead.

**Aggregation of overall metrics:**

- `entropy_pred` is the sum of the step entropies: `0.6 + 1.6 + 1.8 + 2.2 + 2.4 + 1.2 + 1.8 + 0.8 = 12.4`, which is at
  or below the 15.0 budget.
- `cost_pred` is the sum of the step costs: `2 + 4 + 5 + 6 + 6 + 3 + 6 + 3 = 35.0`.
- `p_success_pred` is a qualitative judgement anchored on the bottleneck chain. The naive product over the bottleneck
  steps is `0.90 * 0.82 * 0.80 * 0.85 * 0.85 = 0.43`, but it understates success: the failures are strongly correlated
  (the same root cause and test fixtures) and mostly recoverable inside the step (each step has a defined fallback). The
  bottleneck is Step 5 (0.80; loop state, budget squeeze and tests pinning the bug). Weighted by recoverability, the
  estimate is `0.72`.
- `impact_pred` is 90.0. The step impacts sum to 145, but they overlap: Steps 7 and 8 are evidence, not separate value.
  The figure reflects removing a silent data-loss path and a false approval signal from every future flow.
- `learning_value_pred` is 5.0, set by the maximum step (Step 5, 6.0) and the exploratory Step 4 (5.0), discounted for
  overlap.
- `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`, with `mu = 0.5` and `lambda = 0.3`
  from `docs/metrics.md`: `EV = 0.72 * 90.0 + 0.5 * 5.0 - 0.3 * 12.4 - 35.0 = 64.80 + 2.50 - 3.72 - 35.00 = 28.58`.

**Strong assumptions:**

- The executor's sandbox has `uv` available. The planner sandbox did not. If the executor's does not either, the
  executor must install the version pinned in Step 1 before running `uv run task ...`, which is exactly the intent's
  point.
- The fakes used by existing tests write `dispositions` with exit code 0. The marker requirement (Step 4) is applied
  only on a non-zero exit, to keep them valid.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1: work only on the execution branch under the plan prefix, rebase before the
    execution, never commit to `main`.
  - `holon-config/world/constraints.md` §3 and the intent's "exactly the three append-only ledgers": no ledger line is
    rewritten and no ledger file is added. Step 5 changes only which rows the loop appends on a resolver failure.
  - `docs/safety.md` §5: the loop's human-only merge halt is untouched, and the PR merge stays with the maintainer.
  - `holon-config/world/ruleset.md` §2 and §3: typed, documented functions; tests in `tests/`; every changed
    pre-existing test assertion is declared.
  - `flow_module_budgets.json` contract and the intent's "do not raise any budget".
  - Code placement rules: no entrypoint import into the flow engine; ruff complexity limits with no new `noqa`; focused
    new modules rather than growing files past about 400 lines.
- **Potential violations or edge cases:**
  - Adding a budget entry for `resolver_result.py` edits `flow_module_budgets.json`, which is not in the intent's scope
    list. The budget file's own contract requires the entry.
  - The output tail could carry secrets from the agent's output or from git stderr.
  - A missing `git` now exits with code 127 instead of a traceback. This is a small behaviour change at a shared helper.
  - Tests that pin the old "continue after a resolver error with rejections" behaviour.
- **Mitigations built into the plan:**
  - The budget entry is declared explicitly in the touch set. Only additions for new modules and decreases are allowed,
    and the diff is audited in Step 8.
  - Every tail and error passes through `redact_agent_secrets` and `_bounded_error_context`.
  - `run_cmd`'s `check` semantics are kept. Step 3's discard path narrows the catch to the verification calls if
    anything depends on the raise.
  - Step 5's discard threshold (more than three tests pin the bug, so re-plan), and every test change is declared.
- **Residual risk accepted (and why):** Hard container kills (OOM, SIGKILL) still give only "exit code N, no resolver
  result produced", with no output tail, because host-side capture lives in the out-of-scope dispatcher. The loop still
  halts loudly with the exit code, which removes the data-loss path, and the gap is recorded as Step 4's learning
  target. Also, the uv tag cannot be proven to resolve without a registry pull. The build-time smoke check turns any
  mistake into a loud build failure, not a silent runtime one.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 12.4
- **Budget Compliance:** The strategy fits within budget (`12.4 <= 15.0`), so it is executed as a single plan with no
  sub-intent list.

## Sub-intent list

- None. The plan fits the entropy budget and runs as one execution.
