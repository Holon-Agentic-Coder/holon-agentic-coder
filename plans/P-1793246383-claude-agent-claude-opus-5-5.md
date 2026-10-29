# Plan for I-1793246367-url-fuzz-leak-check-ignores-random-temp-paths

- **Plan ID:** P-1793246383-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-29T03:59:43.890Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. The intent is narrowly scoped, test-only and its cause is confirmed by
  reading the code (see Plan Description), so no reframe is needed.
- **Exploration stance:** conservative, with one bounded balanced step. The defect and fix are well understood; the only
  open question worth learning is which part of the env-file path (the macOS `/var/folders/...` segment, the pytest
  directory names or the `mkstemp` suffix) supplies the colliding characters, which shapes the regression test.
- **Safety priority level:** standard
- **Priority Justification:** Test-only change inside one module, no source, dependency, ledger-rewrite or network
  impact. `holon-config/world/ruleset.md` §3 ("Any change to test files must be explicitly declared in the planning
  step") is satisfied by this plan naming the exact file and the exact test functions touched. The test guards a
  redaction property, so the plan forbids weakening the check (see Safety section), but nothing in `docs/safety.md`
  calls for elevated handling.

## Exploration

- **Proportion of steps that are exploratory:** 0.17
- **Justification:** One of six steps (Step 1) is BALANCED: it confirms the exact shape of the env-file path in argv and
  echo before the masking helper is written, which is cheap and prevents a mask that silently misses on macOS. The rest
  is exploitation of a deterministic diagnosis.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.2   |
| impact_pred         | 70.0  |
| cost_pred           | 10.0  |
| learning_value_pred | 3.0   |
| ev_pred             | 50.34 |

### Strategy Rationale

Diagnosis (from reading the module, no code changed):

- `_letters` (`test_url_message_fuzz.py:49-50`) draws 12 characters from `_LETTERS = "FGJQVZ"`, and `_leaks`
  (`:283-286`) lower-cases both needle and haystack, so the fake parts are runs over `{f, g, j, q, v, z}`.
- The module docstring (`:12-13`) assumes those letters "occur in no message". That holds for messages built by the
  code, but not for the env-file path: `cli.write_env_file` calls `tempfile.mkstemp(prefix="holon-env-", ...)` inside
  the directory that the autouse `isolated_env_file_dir` fixture (`conftest.py:106-116`) creates with
  `tmp_path_factory.mktemp("env-files")`. The path therefore contains an 8-character random `mkstemp` suffix over
  `[a-z0-9_]`, the pytest base directory and, on macOS, the random `/var/folders/<xx>/<random>/T/` segment. Any of these
  can contain a 4-run over `{f, g, j, q, v, z}` that also occurs in a 12-letter part. That matches the CI signature
  exactly: only `main-argv` and `echo` (the two texts that carry the path) failed, `env-item-echo` stayed clean, and the
  failure was on `macos-latest`, whose `TMPDIR` adds an extra ~28 random characters.

Fix choice. The intent prefers fake parts that incidental text cannot contain. That is not achievable here without
weakening the check: the comparison is case-insensitive on purpose (a case-folding redactor must still be caught), temp
paths may contain every lowercase ASCII letter and digit, and the parts must remain ASCII URL-userinfo characters so
that the delimiter matrix keeps exercising `redaction.url_userinfo_end`. Longer whole-token matching would stop
detecting genuine partial leaks. The plan therefore takes the intent's explicitly allowed alternative: **mask only the
exact `--env-file` path value** (the argv element that follows `--env-file`, captured by `_DockerRecorder`) in each
case's haystacks, replacing that exact string with a neutral placeholder, and nothing else. The path is produced by
`mkstemp` in a fixture-owned directory and is independent of the value, so masking it removes the only incidental text
while leaving every other character of argv and echo under the 4-character fragment check. Tests then prove both
directions: a forced collision is no longer reported, and a genuine 4-character fragment in argv or echo (including one
placed immediately next to the path) still is.

Aggregation. Steps run sequentially on one branch. Overall entropy is the sum of step entropies
(`0.2 + 0.6 + 0.6 + 0.3 + 0.3 + 0.2 = 2.2`). Cost is the sum (`1.0 + 3.0 + 2.5 + 1.0 + 2.0 + 0.5 = 10.0`). Impact is a
qualitative judgement (70.0): removing a flake that turns unrelated pull requests red is valuable but bounded to CI
reliability. The bottleneck is Step 2 (forcing a real collision through `run_docker_container` deterministically, p =
0.85); later steps are low risk and recoverable inside the branch, so overall p is set at the bottleneck (0.85) rather
than the naive product, which would double-count recoverable steps. Learning value is 3.0 (confirms the path-collision
model and the macOS `TMPDIR` contribution).

`EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S - Cost`
`EV = 0.85 * 70.0 + 0.5 * 3.0 - 0.3 * 2.2 - 10.0 = 59.50 + 1.50 - 0.66 - 10.0 = 50.34`

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §3: test changes must be declared in the plan; this plan declares
    `apps/sandbox-executor/tests/test_url_message_fuzz.py` as the only modified target file.
  - `holon-config/world/ruleset.md` §1 and the intent: no new dependencies; `uv` only.
  - `holon-config/world/constraints.md` §1: work only on the plan/execution branch prefix, never on `main`.
  - `holon-config/world/constraints.md` §3: ledgers under `holon-knowledge/ledger/` are append-only.
  - `docs/safety.md` §1 and §4: branch isolation; low entropy keeps this in the process sandbox tier.
  - Intent TOUCH SET: only `test_url_message_fuzz.py` (and `leak_assertions.py` only if genuinely needed, which this
    plan does not need); no `src/` module.
- **Potential violations or edge cases:**
  - Weakening the redaction check (masking too much, switching to case-sensitive or whole-token matching).
  - The echo might render the env-file path differently from argv (quoted or shortened), so an exact-string mask would
    miss it and the flake would remain in `echo`.
  - Touching `leak_assertions.py` or `conftest.py` out of convenience (both are off-limits; `conftest.py` is not in the
    touch set at all).
  - Harness paths (`todo/`, `.beans/`, `.agents/`, review or bean ids) leaking into the test file or its docstrings.
  - American spellings in new comments or docstrings.
- **Mitigations built into the plan:**
  - Mask is the exact argv element after `--env-file`, applied by `str.replace` of that exact string; a test proves a
    fragment adjacent to the path is still reported.
  - Step 1 verifies the echo rendering before the helper is written; if it differs, the mask uses the rendered form
    found in the echo, still as an exact string, and the deviation is noted.
  - Red-then-green: the deterministic regression test is run before the fix and must fail, then pass after.
  - Lint, lint-docs, the 20-run stability check and the full suite are required gates.
  - Reviewer checklist in Step 4 covers Australian English and harness-path hygiene.
- **Residual risk accepted (and why):** A real redaction bug that wrote part of the password into the env-file path
  itself would be masked. This is accepted because the path is built by `mkstemp` from a constant prefix inside a
  fixture-owned directory, and the test still asserts separately that the env-file content equals the value.
- **Allocated Entropy Budget:** 4.0
- **Predicted Plan Entropy:** 2.2
- **Budget Compliance:** The strategy fits within budget (`2.2 <= 4.0`); no sub-intent list is needed.

## Plan Description & Strategy

A single execution branch makes a test-only change to `apps/sandbox-executor/tests/test_url_message_fuzz.py`:

1. Confirm how the env-file path appears in the main argv, the `Executing:` echo and the probe argv.
2. Extract the per-case leak evaluation of `test_every_delimiter_in_a_password_stays_off_argv_and_echo` into a short
   helper, and add a deterministic regression test that forces a part to share a 4-letter run with the env-file path;
   confirm it fails on the unfixed check.
3. Add the exact-path mask and the genuine-partial-leak tests; the regression test turns green.
4. Update the module docstring, check style, Australian English and hygiene, run lint and lint-docs.
5. Run the bounded stability check (20 module runs) and one full suite run.
6. Write the execution record and append ledger rows.

`leak_assertions.py` is not modified: the module's own `_leaks` and `FRAGMENT_LEN = 4` are local, and the shared
helper's default of 8 is irrelevant to this test.

---

## Step 1: Confirm the env-file path shape in argv, echo and probe argv

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only check of a few lines plus one throwaway interactive run; cheap and it de-risks the mask
  design.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The `--env-file` path appears verbatim (same string) in `docker.argv` and in the
  `Executing:` echo, and does not appear in the probe argv; the colliding characters come from the random parts of that
  path (the `mkstemp` suffix, and on macOS the `/var/folders` segment).
- **Learning target:** The exact rendered form of the path in the echo (verbatim, quoted or redacted), and whether any
  other incidental random text (for example the `holon-pi-agent-` temp dir from `cli.py:1420`) also reaches argv or echo
  in this test.
- **Maximum acceptable cost for this learning:** about 1.0 cost unit (a few minutes of reading and one scratch run in a
  Python REPL or a temporary print that is never committed).

### Intent & Git Integration

- **Step Intent:** Pin down exactly what must be masked so that the mask is exact and complete.
- **Git branch:**
  `I-1793246367-url-fuzz-leak-check-ignores-random-temp-paths/P-1793246383-claude-agent-claude-opus-5-5/E-{timestamp}-fix-leak-check/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Read `cli.run_docker_container` around `cli.py:1400-1520` to see where `docker_cmd[2:2] = ["--env-file", env_file]` is
  inserted and how the `Executing:` line is printed (via `redact_command` or similar), and whether the echo is printed
  before or after the env-file is inserted.
- Read `preflight.build_probe_docker_cmd` to confirm it returns the secret as a dict (`probe_secret`) rather than an
  env-file path in argv.
- Check whether any other random temp directory (for example `holon-pi-agent-*`) can appear in argv for
  `agent_id="claude-agent"`; if so, record it in the execution notes, as it is a second incidental source to consider.
- Optionally run the existing test once locally to confirm it passes in the current environment.
- Do not commit any probe or print statements.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 (branch scope); intent TOUCH SET (read-only here).
- **Potential failure modes for this step:**
  - The echo transforms the path (quoting or redaction), making an argv-derived mask miss.
  - A second random path reaches argv or echo.
- **Guardrails and early‑abort checks:**
  - If the echo transforms the path, Step 3 masks the exact transformed form as found, still an exact string.
  - If a second random path is found, add it to the mask only if it is an exact, value-independent temp path, and record
    it; otherwise stop and report.

### Success & Discard Criteria

- **Success:** The execution notes state, with line references, the exact form of the env-file path in argv and echo and
  confirm no other incidental random text reaches the checked haystacks.
- **Discard:** If understanding the echo path takes more than 1.5 times the predicted cost, proceed with the argv value
  as the mask and rely on Step 2's forced collision to reveal any echo mismatch.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.2   |
| impact_pred         | 10.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 9.44  |

### Step Metrics Rationale

Pure reading with a trivial scratch run, so success is near certain and entropy is minimal. Impact is modest but it
prevents the most likely design error.
`EV = 0.95 * 10.0 + 0.5 * 2.0 - 0.3 * 0.2 - 1.0 = 9.50 + 1.00 - 0.06 - 1.00 = 9.44`.

---

## Step 2: Extract the per-case leak check and add a deterministic collision regression test (red)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, local refactor in one test file plus one new test; reusable inside the module only, and its risk
  is contained by running the existing test unchanged afterwards.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the false leak reproducible on every run, so the fix is proved deterministically rather than by
  repetition.
- **Git branch:** same execution branch as Step 1.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Behaviour-preserving extraction: move the body of the loop in
  `test_every_delimiter_in_a_password_stays_off_argv_and_echo` into a short typed helper with a docstring, for example
  `_delimiter_case_failures(case_id, value, parts) -> list[str]`, that runs `run_docker_container` and
  `build_probe_docker_cmd` under the existing patches and returns the list of failing sites (`main-argv`, `echo`,
  `no-run`, `not-env-file`, and so on). Optionally split the construction of the `texts` dictionary into a second helper
  so that each function stays short and low in branching. The monkeypatches of `cli.shutil.which`,
  `get_agent_session_mounts`, `get_ssh_auth_mounts` and `get_token_reduction_mounts_and_envs` move into a small shared
  setup helper (or stay in each test) so the new test can reuse them. The existing test becomes a loop over
  `_delimiter_cases()` that collects the helper's output; its assertion message is unchanged.
- Run the existing test once to confirm the extraction changed no behaviour.
- Add `test_env_file_path_sharing_a_part_run_is_not_a_leak(monkeypatch, tmp_path_factory)`:
  - Override the autouse env-file directory by monkeypatching `cli.env_file_dir` (the same attribute the autouse fixture
    patches; a later `monkeypatch.setattr` in the test wins) to a directory created with `tmp_path_factory.mktemp(...)`
    whose name contains a known 4-letter run over `FGJQVZ` in lower case, for example `env-zqvj`, and set it to mode
    0700 as the fixture does.
  - Build one delimiter case deterministically: choose a fixed form from `DELIMITER_FORMS` (for example `http`) and a
    fixed delimiter (for example `[`, matching the CI failure), and parts that are constructed (not random) so that one
    part contains `ZQVJ` and both parts are otherwise built from `_LETTERS`. Construct parts at runtime from `_LETTERS`
    characters so no secret-shaped literal is committed.
  - Assert as a precondition that the lower-cased env-file directory path does contain a 4-character run of that part
    (so the test proves it really forces the collision, and fails loudly if the directory name stops colliding).
  - Call the extracted helper and assert it returns an empty list.
- Run only the new test and confirm it FAILS on the unfixed check, reporting `main-argv` and `echo`. Record the failure
  output (site names only) in the execution notes as the reproduction.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (without a deterministic reproduction the fix cannot be proved; p = 0.85, above the 0.6
  threshold, so no sub-intent)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (typed signatures, docstrings) and §3 (declared test change);
  intent constraint on short, low-branching functions; intent TOUCH SET (`conftest.py` must not be edited, only
  overridden through `monkeypatch` inside the test).
- **Potential failure modes for this step:**
  - The test's `monkeypatch.setattr(cli, "env_file_dir", ...)` does not take effect because the autouse fixture runs
    later (it does not: autouse fixtures run before the test body, so the test's patch wins).
  - `tmp_path_factory.mktemp` appends a counter that breaks the run (it appends a digit after the name, so the 4-letter
    run inside the name is preserved).
  - The extraction subtly changes behaviour (for example patch scope).
- **Guardrails and early‑abort checks:**
  - Existing test must still pass after extraction, before the new test is added.
  - The precondition assertion guarantees the collision is real.
  - If the new test does not fail on the unfixed check, stop and re-examine Step 1 findings before Step 3.

### Success & Discard Criteria

- **Success:** The existing test passes after the extraction, and the new regression test fails deterministically (three
  consecutive runs) with `main-argv` and `echo` on the unfixed check.
- **Discard:** If no deterministic collision can be produced within 1.5 times the predicted cost, fall back to driving
  the helper with a synthetic `_DockerRecorder` argv and echo text containing the forced path, and record why.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.6   |
| impact_pred         | 25.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 19.57 |

### Step Metrics Rationale

The main uncertainty is fixture ordering and the exact path rendering, both checked by Step 1 and the precondition
assertion. Entropy reflects a moderate edit to one existing test.
`EV = 0.85 * 25.0 + 0.5 * 3.0 - 0.3 * 0.6 - 3.0 = 21.25 + 1.50 - 0.18 - 3.00 = 19.57`.

---

## Step 3: Mask only the exact env-file path and prove genuine partial leaks are still caught (green)

- **Sub‑intent recommendation:** NO
- **Reasoning:** A few lines in the same file; low risk because both directions are pinned by tests.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the leak check exact without weakening it.
- **Git branch:** same execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add a short typed helper with a docstring, for example
  `_without_env_file_path(texts: list[str], argv: list[str]) -> list[str]`, that reads the argv element immediately
  after `--env-file` (if present) and returns the texts with that exact string replaced by a neutral placeholder such as
  `<env-file>`; with no `--env-file` it returns the texts unchanged. Use plain `str.replace`, no regular expressions,
  and mask nothing else. The placeholder must not contain any 4-character run over `{f, g, j, q, v, z}`.
- If Step 1 found the echo renders the path differently, mask that exact rendered form as well (derived from the same
  argv value), and note it.
- In the per-case helper from Step 2, apply the mask to every haystack of the case (it is a no-op where the path does
  not occur, which keeps the code branch-free) before calling `_leaks`. Leave `_leaks`, `FRAGMENT_LEN`, `_LETTERS`,
  `_letters`, the case-insensitive comparison and the `not-env-file` check unchanged.
- Add genuine-leak tests that drive the same evaluation path the delimiter test uses (the texts dictionary plus mask
  plus `_leaks`), for example a parametrised `test_fragment_beside_env_file_path_is_still_a_leak` with cases:
  - a 4-character fragment of a part placed elsewhere in the main argv: reported as `main-argv`;
  - a 4-character fragment placed in the echo: reported as `echo`;
  - a fragment spliced directly onto the end of the env-file path (path immediately followed by the fragment): still
    reported, proving the mask is exact and not prefix- or token-based. Build these from the regression test's
    constructed parts and a real env-file path recorded from a run, or from a synthetic argv list in the same shape
    (`docker run --env-file <path> ...`); fragments are slices of runtime-built parts, never committed literals.
- Run the regression test (now green), the new genuine-leak tests and the whole module once.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO (a failure here is a local edit fix, recoverable within the step)

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 2 (exact check, mask only the exact `--env-file` value); intent TOUCH SET;
  `holon-config/world/ruleset.md` §2.
- **Potential failure modes for this step:**
  - Masking more than the exact path (for example the whole argv element group, or every `/`-separated token).
  - The genuine-leak test passes vacuously (fragment chosen that the mask removes).
- **Guardrails and early‑abort checks:**
  - The adjacent-fragment case must be reported; if it is not, the mask is too broad and must be fixed before moving on.
  - Each genuine-leak case asserts the specific site name, not just "some failure".

### Success & Discard Criteria

- **Success:** Regression test passes; all genuine-leak cases are reported at the expected site; the full module passes.
- **Discard:** If an exact mask cannot remove the false leak (for example the path is transformed unpredictably), stop
  and report rather than broadening the mask.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.6   |
| impact_pred         | 30.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 25.07 |

### Step Metrics Rationale

This is the core value of the plan and is mechanically simple once Step 2 exists.
`EV = 0.90 * 30.0 + 0.5 * 1.5 - 0.3 * 0.6 - 2.5 = 27.00 + 0.75 - 0.18 - 2.50 = 25.07`.

---

## Step 4: Update the docstring and pass lint, lint-docs and the hygiene review

- **Sub‑intent recommendation:** NO
- **Reasoning:** Trivial documentation and formatting gate.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Keep the module's stated contract accurate and the change compliant with repository gates.
- **Git branch:** same execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Amend the module docstring (`:12-14`) to say the letters occur in no message built by the code, and that the one
  incidental random text in the checked output, the exact `--env-file` path created by `mkstemp`, is masked before the
  fragment check, so a random temp path cannot cause a false leak.
- Give each new helper and test a short docstring in Australian English (for example "recognised", "behaviour").
- Confirm no harness paths or ids (`todo/`, `.beans/`, `.agents/`, bean or review ids) appear in the file, and no
  secret-shaped literals were added.
- Run `uv run task lint` and `uv run task lint-docs` from the repository root; fix formatting with `ruff format` only on
  the touched file if needed.
- Confirm `git diff --name-only` against the plan branch lists only
  `apps/sandbox-executor/tests/test_url_message_fuzz.py` (plus the flow's own plan, execution and ledger records later).

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint 4 (Australian English, lint gates, target-native paths); TOUCH SET.
- **Potential failure modes for this step:** A pre-existing lint-docs failure in a file owned by a concurrent change.
- **Guardrails and early‑abort checks:** If lint fails only in a file outside the touch set, do not edit it; record it
  in the execution notes.

### Success & Discard Criteria

- **Success:** Both lint commands exit 0 and the diff touches only the declared file.
- **Discard:** Not applicable beyond reporting an out-of-scope lint failure.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.3   |
| impact_pred         | 5.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 3.76  |

### Step Metrics Rationale

Routine gate with a small chance of an unrelated pre-existing failure.
`EV = 0.92 * 5.0 + 0.5 * 0.5 - 0.3 * 0.3 - 1.0 = 4.60 + 0.25 - 0.09 - 1.00 = 3.76`.

---

## Step 5: Run the bounded stability check (20 module runs) and the full suite once

- **Sub‑intent recommendation:** NO
- **Reasoning:** Explicitly requested, bounded repetition; confirms rather than proves the fix.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm the module is stable under parallel execution and that nothing else regressed.
- **Git branch:** same execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- From the repository root, run exactly
  `uv run pytest -n auto --dist worksteal apps/sandbox-executor/tests/test_url_message_fuzz.py` 20 times (a simple shell
  loop is fine; no other runner, no `PYTHONPATH`).
- For each run record: run number, passed count, failed count, and pytest's reported duration from the summary line.
  Compute and record the median duration. Record them as a table in the execution notes.
- Any failing run is a stop signal: capture the failing case ids and sites (never values), and return to Step 3.
- Run `uv run task test` once and record the summary line. Any failure outside this module is reported, not fixed.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 3 (N = 20 exactly, then the full suite once); intent constraint on test runners.
- **Potential failure modes for this step:** An unrelated flaky test in the full suite; long wall-clock time.
- **Guardrails and early‑abort checks:** Do not exceed 20 runs; do not rerun the full suite repeatedly to obtain a green
  result; report unrelated failures with their test ids.

### Success & Discard Criteria

- **Success:** 20 of 20 module runs pass with every count and duration recorded plus the median; the full suite passes.
- **Discard:** Any module failure attributable to this test sends execution back to Step 3; more than one attempt at
  that loop means stop and report.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.3   |
| impact_pred         | 8.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 5.95  |

### Step Metrics Rationale

The deterministic test already proves the fix; the residual risk is unrelated flakiness elsewhere in the suite.
`EV = 0.88 * 8.0 + 0.5 * 2.0 - 0.3 * 0.3 - 2.0 = 7.04 + 1.00 - 0.09 - 2.00 = 5.95`.

---

## Step 6: Write the execution record and append ledger rows

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical bookkeeping by the flow.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Leave an auditable record of the reproduction, fix, stability numbers and any dropped steps.
- **Git branch:** same execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Write the execution record under `executions/` with: Step 1 findings, the red-then-green evidence from Steps 2 and 3,
  the 20-run table with median, the full-suite summary, lint results, and any dropped or out-of-scope items.
- Append (never rewrite) the flow's rows to the ledgers under `holon-knowledge/ledger/`; create no other ledger.
- Run `uv run task lint-docs` again so the new markdown record is prettier-clean.

### Dependencies & Criticality

- **Depends on:** Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (append-only ledgers); intent constraint 4 (no auxiliary
  ledgers).
- **Potential failure modes for this step:** Accidental rewrite of an existing ledger line.
- **Guardrails and early‑abort checks:** Verify with `git diff` that ledger changes are pure additions at the end of
  each file.

### Success & Discard Criteria

- **Success:** Execution record present, ledger diffs are append-only, lint-docs passes.
- **Discard:** Not applicable.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.2   |
| impact_pred         | 2.0   |
| cost_pred           | 0.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 1.59  |

### Step Metrics Rationale

Routine, well-trodden flow step. `EV = 0.95 * 2.0 + 0.5 * 0.5 - 0.3 * 0.2 - 0.5 = 1.90 + 0.25 - 0.06 - 0.50 = 1.59`.

---

## Sub-intent list

None
