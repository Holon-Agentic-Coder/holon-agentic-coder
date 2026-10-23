# Plan for I-1792762862-redact-github-token-in-cli-command-echoes

- **Plan ID:** P-1792765112-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-23T14:18:33.086Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** Not reframed. Three scope clarifications, all inside the intent's wording: (1)
  the shared redaction marker stays the existing ledger marker `*******`, and the one test that pins the old CLI marker
  `***REDACTED***` (`tests/test_cli.py:535`) is listed as a declared test change; (2) the mitmproxy sidecar's
  `--set web_password=<pw>` argv exposure (`cli.py:707`) is reported, and its failure stderr gets redacted, but moving
  that password off the sidecar argv is proposed as a follow-up intent because it needs a mitmproxy config-file mount
  that the intent did not ask for; (3) the hygiene check scans for credentials and never rewrites the append-only
  ledger.
- **Exploration stance:** balanced. Most of the work reuses an existing, well-tested redactor. Two facts are still
  unknown and decide whether the plan works, so each gets a cheap probe: which git version is in the sandbox image
  (needed for credential injection through `GIT_CONFIG_COUNT`), and whether tracked files already contain token-shaped
  strings (needed before a hygiene test can pass).
- **Safety priority level:** elevated
- **Priority Justification:** The plan handles a live credential and changes how the host passes secrets into the
  sandbox. `holon-config/world/constraints.md` §4 requires deep models for security-sensitive plans, and the `SER`
  (secrets) term of `docs/metrics.md` §2.2 is directly involved. The level is not critical: no sandbox-escape surface is
  added, every change stays in a branch (`docs/safety.md` "Git is the safety boundary"), and the human merge gate to
  `main` still applies.

## Exploration

- **Proportion of steps that are exploratory:** 0.25 (Step 1 and Step 6 are BALANCED probes; Step 4 contains one
  BALANCED sub-probe; the rest are EXPLOIT)
- **Justification:** The redaction patterns already exist and are mature (`entrypoint/executor.py` `redact_text`,
  `_PROVIDER_TOKEN_RE`, `redact_env_literals`), so exploiting them is the right default. The real unknowns are the
  in-container git version and what tracked history already contains. A probe of a few minutes on each is far cheaper
  than shipping a credential helper that silently breaks `git push`, or a hygiene test that fails on day one.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.72  |
| entropy_pred        | 9.0   |
| impact_pred         | 90.0  |
| cost_pred           | 36.5  |
| learning_value_pred | 5.0   |
| ev_pred             | 28.1  |

### Strategy Rationale

The work has three layers, in order of how risky it is to get them wrong:

1. **Consolidate the redactors without changing their behaviour** (Step 2). Every echo then goes through one pattern
   set, as goal 2 requires.
2. **Route every echo through that set** (Step 3). This fixes the printed leaks even if Step 4 later slips.
3. **Take the secret out of process arguments** (Step 4). This is the structural fix that the intent's clarifying answer
   requires.

The order means each finished step leaves the system strictly safer than before. If Step 4 has to be discarded, Steps
2–3 still remove every printed leak.

Aggregation:

- `entropy_pred` is the sum of the step entropies: `0.3 + 1.6 + 1.2 + 3.0 + 1.2 + 1.0 + 0.4 + 0.3 = 9.0`.
- `cost_pred` is the sum of the step costs: `2.0 + 6.0 + 5.0 + 10.0 + 7.0 + 3.0 + 1.5 + 2.0 = 36.5`.
- `impact_pred`: the step impacts sum to `108`. Tests, docs and the validation gate overlap with the implementation
  steps, so the total is normalised down to `90` on the 0–100 scale.
- `p_success_pred` is a qualitative bottleneck estimate, not a product. Step 4 (`p = 0.75`) is the bottleneck. The other
  steps are cheap to retry within one branch, so the plain product (`~0.40`) would understate the chance of success. The
  0.72 sits slightly below Step 4's 0.75 to allow for the hygiene-scan unknown in Step 6.
- `learning_value_pred` is about the maximum step value (Step 4: 6.0), discounted to 5.0 because only part of it is new
  knowledge for the KB.
- EV: `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` with `mu = 0.5` and
  `lambda = 0.3`, which gives `EV = 0.72 * 90.0 + 0.5 * 5.0 - 0.3 * 9.0 - 36.5 = 64.8 + 2.5 - 2.7 - 36.5 = 28.1`.

Strong assumptions:

- `GIT_CONFIG_COUNT` needs git 2.31 or later in the sandbox image. Step 4 probes this and has a fallback.
- Docker reads `--env-file` on the client side, so no bind mount is needed. This holds on Linux and Docker Desktop.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1: work only on prefix-isolated branches; rebase from the parent before
    executing; never commit to `main`.
  - `holon-config/world/constraints.md` §3 and `docs/safety.md` Invariant 6: `holon-knowledge/ledger/*.jsonl` is
    append-only. The hygiene step must never rewrite it.
  - `holon-config/world/constraints.md` §4: deep model routing for security-sensitive work.
  - `holon-config/world/ruleset.md` §1: Python `==3.13.*`, `uv`-managed, no new dependencies.
  - `holon-config/world/ruleset.md` §2: type annotations and docstrings on every public function.
  - `holon-config/world/ruleset.md` §3: `pytest==9.1.1`; tests sit next to their sources; test changes must be declared
    in the plan (they are declared per step below).
  - `docs/safety.md` trust model: baseline agents cannot spawn sub-intents.
  - Intent constraints: Australian English; tests only via `uv run pytest` in `apps/sandbox-executor`; ruff check and
    format must pass; prettier with the repository's `.prettierrc.json`; target-native paths; only the three sanctioned
    ledgers.
- **Potential violations or edge cases:**
  - A fake token literal in the new tests could trip the very hygiene check that Step 6 adds.
  - Moving redactors out of `entrypoint/executor.py` could break the `git.py` import and the existing tests, which
    import `redact_text` from `sandbox_executor.entrypoint.executor`.
  - Stripping credentials from `HOLON_REPO_URL` can break in-container `git push`, and the workspace-reuse equality
    check at `entrypoint/executor.py:1139`, if the credential helper does not take effect.
  - Docker `--env-file` lines cannot hold newlines or quotes the way a shell would. A value containing a newline would
    be cut short or injected as a second variable.
  - SIGKILL or a host crash during `docker run` leaves the 0600 env-file on disk.
  - A token-shaped string that already exists in the tracked, append-only ledger cannot be removed by editing.
- **Mitigations built into the plan:**
  - Fake tokens are built at runtime (for example `"ghp_" + "Z9" * 18`), so no token-shaped literal is ever committed.
  - `entrypoint/executor.py` keeps re-exporting every moved name, and an import-compatibility test pins this.
  - A credential-helper probe runs inside the built image before the URL change lands.
  - Env-file values containing `\n` or `\r` are rejected with a message that names the key but not the value.
  - The env-file lives in a `0700` directory under `~/.holon/tmp`, is created with `0600`, is removed in `finally`, and
    stale files are swept on the next run.
  - If the ledger already holds a real token, execution stops and escalates to a human for token rotation. It never
    edits the ledger and never adds a silent allowlist.
- **Residual risk accepted (and why):**
  - While the container runs, `GITHUB_TOKEN` stays readable inside it via the environment and `/proc/<pid>/environ`. The
    agent needs the credential to push, and the container is already the trust boundary.
  - The mitmproxy web password stays on the sidecar argv until the proposed follow-up intent lands. It is a local
    dashboard password on a loopback-bound port, so its blast radius is far lower than the GitHub token's.
  - An env-file left behind by SIGKILL is owner-only (`0600`) and is swept on the next run.
- **Allocated Entropy Budget:** 10.0
- **Predicted Plan Entropy:** 9.0
- **Budget Compliance:** The strategy fits within budget, with 1.0 of headroom. If Step 4's fallback path (a runtime
  `git config --global` in the dispatcher) is needed, it adds about 0.5 and the plan still fits.

## Plan Description & Strategy

### Leak inventory (goal 1)

These call sites were found by reading the current source at `apps/sandbox-executor/src/sandbox_executor/`. They are
listed exactly, so Step 1 only has to confirm them.

| ID  | Call site                                                                                                        | What leaks                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Kind                        |
| --- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------- |
| L1  | `cli.py:1077-1098` `run_docker_container`, the `Executing: docker run ...` echo                                  | An ad-hoc sanitiser masks only `GITHUB_TOKEN=`, `HOLON_AGENT_KEY=` and `HOLON_REPO_URL` userinfo. It misses `HOLON_MITM_WEB_PASSWORD` (forwarded by the `HOLON_` prefix loop at `cli.py:956-958`), any other `HOLON_*_TOKEN`, `HOLON_*_SECRET` or `HOLON_*_KEY` variable, credentials inside proxy URLs from `tr_envs` (`cli.py:1050-1051`), and bare `ghp_`/`github_pat_` values under unrecognised names. It also uses its own marker and regex, which is a divergent pattern set. | printed                     |
| L2  | `cli.py:951-954`, `cli.py:960`, `cli.py:973-974` (`-e KEY=VALUE` loop)                                           | `GITHUB_TOKEN=<token>` and `HOLON_REPO_URL=https://x-access-token:<token>@host/repo.git` (built at `cli.py:208` in `resolve_target_repo_url`) are on the `docker run` argv, so `ps` and `/proc/<pid>/cmdline` can read them.                                                                                                                                                                                                                                                         | argv                        |
| L3  | `cli.py:705-707` sidecar `--set web_password=<pw>`; `cli.py:714-720` sidecar failure `RuntimeError`              | The mitmweb password is on the sidecar argv. Sidecar stderr, which can include the mitmweb `?token=` login URL, is re-raised without redaction.                                                                                                                                                                                                                                                                                                                                      | argv and printed            |
| L4  | `entrypoint/intent_creator.py:12-20` `run_cmd`; clone at `:75`; push at `:117`                                   | `print(f"Running: {' '.join(args)}")` prints the tokenised clone URL raw. On failure, raw `Stdout:`/`Stderr:` are printed, and git errors echo the remote URL.                                                                                                                                                                                                                                                                                                                       | printed                     |
| L5  | `entrypoint/planner.py:15-23` `run_cmd`; clone at `:154`; push at `:387`                                         | Same as L4.                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | printed                     |
| L6  | `entrypoint/review_loop.py:27-42` `run_cmd`; clone at `:130`                                                     | Same as L4, written to stderr.                                                                                                                                                                                                                                                                                                                                                                                                                                                       | printed                     |
| L7  | Workspace `.git/config` (`origin` URL written by `git clone` at `entrypoint/executor.py:1123`, `:1148`, `:1450`) | The tokenised URL is saved as the remote, so it survives in the workspace volume after the run.                                                                                                                                                                                                                                                                                                                                                                                      | persisted (fixed by Step 4) |
| OK  | `entrypoint/executor.py:578-597` `run_cmd` (uses `redact_args`); `git.py:56-62` `run_git` (uses `redact_text`)   | Already redacted. They are kept as the reference behaviour.                                                                                                                                                                                                                                                                                                                                                                                                                          | safe                        |

### Approach

1. Move the existing redactors unchanged into one shared module, `sandbox_executor/redaction.py`, and add a thin
   `redact_command()` on top of them.
2. Route L1, L3 (stderr) and L4–L6 through that one module.
3. Take secrets off the docker argv (L2) with a `0600` env-file. Remove credentials from `HOLON_REPO_URL`, which also
   fixes L7, and inject the token inside the container through runtime-only `GIT_CONFIG_*` environment variables, so
   nothing is written to a tracked file.
4. Add a test layer that searches every output for any 8-character substring of a runtime-generated fake token.
5. Extend `scripts/pre_commit_check.py` with a URL-userinfo pattern and add a pytest scan of the tracked files.

Execution branches follow `constraints.md` §1:
`I-1792762862-redact-github-token-in-cli-command-echoes/P-1792765112-claude-agent-claude-opus-5-5/E-{timestamp}-{action}/_`.

---

## Step 1: Confirm the leak inventory with characterisation tests and probe the sandbox git version

- **Sub‑intent recommendation:** NO
- **Reasoning:** This step only reads code and adds probes, and its cost is tiny. It also pins today's leaking behaviour
  as failing tests, so later steps can show measured progress.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** L1–L7 are the complete set of credential echoes and argv exposures, and the sandbox image
  ships git 2.31 or later, which `GIT_CONFIG_COUNT` injection needs.
- **Learning target:** Confirmation that no other `print`, `logger.*` or `Running:` site formats a command or
  environment mapping, plus the exact git version in the image built from `apps/sandbox-executor/Dockerfile`.
- **Maximum acceptable cost for this learning:** 2.0 cost units (about 10 minutes of agent time). Stop after that and
  proceed with the inventory as already documented.

### Intent & Git Integration

- **Step Intent:** Confirm the inventory against the source, and decide between the `GIT_CONFIG_COUNT` path and the
  dispatcher-fallback path for Step 4.
- **Git branch:**
  `I-1792762862-redact-github-token-in-cli-command-echoes/P-1792765112-claude-agent-claude-opus-5-5/E-{timestamp}-leak-inventory/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Rebase the execution branch from the plan branch.
2. Search `apps/sandbox-executor/src` and `apps/sandbox-executor/entrypoint/role_dispatcher.sh` for `print(`, `logger.`,
   `echo`, `Running:`, `Executing:`, `join(args)`, `join(cmd`, `os.environ` and `env_to_forward`. Classify each hit as
   safe, printed leak or argv leak.
3. Read the base image tag from `apps/sandbox-executor/Dockerfile`. If an image is built locally, run
   `docker run --rm <image> git --version` inside the container sandbox tier. Otherwise use the distribution's package
   version for that base image.
4. Record the result in the execution report. Do not create a new ledger or file; the execution record goes through the
   normal `executions.jsonl` path.
5. If a new leak site is found, add it to the Step 3 routing list. Do not widen the plan in any other way.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §2 (container sandbox for the image probe); `safety.md` "Sandboxing is mandatory
  for execution".
- **Potential failure modes for this step:** The image is not built locally, so the version has to be inferred. A
  dynamically built print (an f-string through a helper) is missed by the search.
- **Guardrails and early‑abort checks:** Use a fake `GITHUB_TOKEN` value when running anything. Never print environment
  mappings while probing.

### Success & Discard Criteria

- **Success:** The inventory table is confirmed or extended, and the git version is known (or bounded by the base image
  release).
- **Discard:** If cost exceeds `1.5 * 2.0 = 3.0` units, stop probing, assume git 2.31 or later is unconfirmed, and use
  the Step 4 fallback.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.3   |
| impact_pred         | 8.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 7.67  |

### Step Metrics Rationale

The step is read-only, so the success probability is high and the entropy is near zero. Its learning value is moderate:
the git-version fact decides which Step 4 path is taken, and a KB pattern about "where CLIs leak" is reusable.
`EV = 0.97 * 8.0 + 0.5 * 4.0 - 0.3 * 0.3 - 2.0 = 7.76 + 2.0 - 0.09 - 2.0 = 7.67`.

---

## Step 2: Move the redactors into a shared `sandbox_executor/redaction.py` and add `redact_command()`

- **Sub‑intent recommendation:** NO
- **Reasoning:** This refactor is reusable and moderately sized. Re-exports make it mechanical, and existing tests catch
  any behaviour drift, so it does not justify a separate evaluation cycle.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Keep one canonical pattern set and add a command-echo helper on top of it. The bytes that ledger
  redaction produces must not change.
- **Git branch:**
  `I-1792762862-redact-github-token-in-cli-command-echoes/P-1792765112-claude-agent-claude-opus-5-5/E-{timestamp}-shared-redaction/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/src/sandbox_executor/redaction.py`, with a module docstring in Australian English
   ("sanitise", "recognise").
2. Move these names verbatim from `entrypoint/executor.py`:
   - `_MAX_REDACT_INPUT_LEN`
   - `_PROVIDER_TOKEN_RE`
   - `SECRET_FLAGS`
   - `redact_text`
   - `_is_secret_flag`
   - `redact_args`
   - `redact_env_literals`
   - `redact_agent_secrets`

   Keep their bodies byte-identical. In `entrypoint/executor.py`, re-import every moved name so that
   `from sandbox_executor.entrypoint.executor import redact_text` (used by `git.py:16` and the tests) keeps working. The
   budget constants that depend on `_MAX_REDACT_INPUT_LEN` stay in `entrypoint/executor.py` and use the imported name.

3. Point `git.py` at `sandbox_executor.redaction` directly. This removes the import of a whole entrypoint module that
   `git.py` only needed for redaction.
4. Give `redact_env_literals` an optional keyword `names: Sequence[str]`. Its default is the current tuple
   (`GITHUB_TOKEN`, `GH_TOKEN`, `HOLON_AGENT_KEY`), so existing callers, including the ledger path, behave exactly as
   before.
5. Add a public constant `COMMAND_ECHO_ENV_NAMES`: the default tuple plus `HOLON_MITM_WEB_PASSWORD`. This is a list of
   environment variable names whose literal values are swept. It is not a regex, so goal 2's single pattern set stays
   intact.
6. Add `redact_command(argv: Sequence[str]) -> str`:
   - Apply `redact_args` to the argv.
   - Join with spaces.
   - Apply `_PROVIDER_TOKEN_RE.sub` with the shared marker `*******`.
   - Apply `redact_env_literals(..., names=COMMAND_ECHO_ENV_NAMES)`.

   The docstring states that the repository host, path and branch survive, because the URL userinfo rule only rewrites
   `scheme://<userinfo>@`.

7. Add `is_secret_env_name(name: str) -> bool`. It returns True when `redact_text(f"{name}=x")` differs from its input,
   or when the name is in `COMMAND_ECHO_ENV_NAMES`. It reuses the canonical key regex rather than copying it, and Step 4
   uses it to decide which variables go into the env-file.
8. Add full type annotations and docstrings (inputs, outputs, side effects) as `ruleset.md` §2 requires.
9. Declared test change: add `tests/test_redaction.py`, which sits next to the new `redaction.py`. It contains:
   - an identity test that `entrypoint.executor.redact_text is redaction.redact_text` (and the same for each re-export);
   - `redact_command` cases for `-e GITHUB_TOKEN=<fake>`, `-e GH_TOKEN=<fake>`, `-e HOLON_MITM_WEB_PASSWORD=<fake>`,
     `HOLON_REPO_URL=https://x-access-token:<fake>@github.com/myorg/myrepo.git`, `http://127.0.0.1:8081/?token=<fake>`,
     and a bare `ghp_` value under an unrecognised key;
   - assertions that `github.com/myorg/myrepo.git` and `--branch I-123-x/_` remain readable.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Steps 3, 4 and 5 import from the shared module)

### Safety & Constraint Considerations

- **Relevant rules:** Intent goal 2 (no second, divergent pattern set); `ruleset.md` §2 (typing and docstrings);
  `ruleset.md` §3 (declared test changes); intent goal 4 (ledger redaction unchanged).
- **Potential failure modes for this step:** A circular import (`redaction.py` must not import `entrypoint`); a subtle
  byte change in a moved regex; a ruff isort reorder that breaks the re-export (mitigated by an `__all__` or an explicit
  `noqa: F401` with a comment).
- **Guardrails and early‑abort checks:** Run the existing `tests/test_executor.py` and `tests/test_git.py` before and
  after the move and compare pass counts. Compare `redact_text`'s source text before and after with a diff.

### Success & Discard Criteria

- **Success:** `uv run pytest tests/test_executor.py tests/test_git.py tests/test_redaction.py` passes with an unchanged
  count of pre-existing tests, and `uv run ruff check` and `uv run ruff format --check` are clean.
- **Discard:** If moving the names causes an import cycle that cannot be fixed in 2 attempts, fall back to leaving the
  definitions in `entrypoint/executor.py`. Add only `redact_command` and `is_secret_env_name` there, and import them
  from that module everywhere. This still uses one pattern set.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.6   |
| impact_pred         | 18.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 11.22 |

### Step Metrics Rationale

The step touches three source files and one new test file. The main risk is import shape, not logic, so entropy is
moderate (`SSA` around 4, `IRR` around 1, `CL` around 3 because `entrypoint/executor.py` is a hot file). The impact
covers both reuse and the enforcement of a single pattern set.
`EV = 0.90 * 18.0 + 0.5 * 3.0 - 0.3 * 1.6 - 6.0 = 16.2 + 1.5 - 0.48 - 6.0 = 11.22`.

---

## Step 3: Route every command and environment echo through `redact_command()`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Each call site needs a small, local edit, and the step builds directly on Step 2. Because it fixes
  every printed leak on its own, it should land early and on its own branch.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Fix the printed leaks L1, L3 (stderr), L4, L5 and L6.
- **Git branch:**
  `I-1792762862-redact-github-token-in-cli-command-echoes/P-1792765112-claude-agent-claude-opus-5-5/E-{timestamp}-route-echoes/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `cli.py`, replace the `sensitive_keys` and `sanitized_cmd` block (`cli.py:1077-1097`) with one
   `print(f"Executing: {redact_command(docker_cmd)}")`. Delete the private regexes there, which removes the divergent
   pattern set.
2. In `cli.py`, when the sidecar fails to start (`cli.py:714-720`) or fails readiness, pass the captured `stderr` or
   `stdout` through `redact_agent_secrets` before it is put into the `RuntimeError` message. This catches the mitmweb
   `?token=` URL and the `web_password=` echo.
3. In `entrypoint/intent_creator.py` and `entrypoint/planner.py`, change `run_cmd` to print
   `f"Running: {redact_command(args)}"`. On failure, print `redact_agent_secrets(result.stdout)` and
   `redact_agent_secrets(result.stderr)`. Add type annotations and a docstring to each `run_cmd`, which the ruleset
   requires and these functions currently lack.
4. In `entrypoint/review_loop.py`, make the same change, keeping its `file=sys.stderr` routing.
5. Leave `entrypoint/executor.py` `run_cmd` and `git.py` `run_git` as they are (already safe). Optionally switch their
   printing to `redact_command` only if the output stays identical. Do not change output that existing tests pin.
6. Keep every log string in Australian English and keep the existing marker `*******`.
7. Declared test change: update the `tests/test_cli.py:535` assertion from the old `***REDACTED***` marker to the shared
   marker. The new assertion still requires `github.com/myorg/myrepo.git` in the output, and the old `ghp_` value must
   be absent.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO (if Step 4 fails, this step still delivers most of the printed-leak value)

### Safety & Constraint Considerations

- **Relevant rules:** Intent goal 2; `ruleset.md` §3 (declared test change); Australian English constraint.
- **Potential failure modes for this step:** Over-masking of a legitimate argument such as `--branch token-rotation/_`.
  The key regex needs a `=` or `:` separator, so a bare word is safe, but tests must cover it. A redacted echo that
  makes the CLI output unreadable for operators.
- **Guardrails and early‑abort checks:** Add a test asserting that `git clone --branch I-1792762862-x/_ --single-branch`
  is printed with the branch unchanged. Snapshot one full `Executing:` line in a test, so readability regressions show
  up in review.

### Success & Discard Criteria

- **Success:** Under a runtime fake token, none of the five call sites prints any 8-character substring of the token,
  and the repository host, path and branch are still visible.
- **Discard:** If a legitimate argument gets over-masked and the only fix would be a pattern change, stop. Pattern
  changes alter ledger behaviour and are out of scope; record the case as a follow-up.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.2   |
| impact_pred         | 22.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 15.88 |

### Step Metrics Rationale

The edits are small, local and well-patterned, so success is likely and entropy is low. The impact is high because this
step removes every printed credential leak.
`EV = 0.92 * 22.0 + 0.5 * 2.0 - 0.3 * 1.2 - 5.0 = 20.24 + 1.0 - 0.36 - 5.0 = 15.88`.

---

## Step 4: Take secrets off the docker argv with a `0600` env-file, and use a credential-free `HOLON_REPO_URL` with runtime git credential injection

- **Sub‑intent recommendation:** YES
- **Reasoning:** This is the riskiest step. It changes how the host passes credentials into the container and can break
  `git clone` and `git push` for every role. It deserves its own branch and evaluation, merged into the parent intent
  branch. Under `docs/safety.md`, baseline-trust agents cannot spawn sub-intents. If this agent is not at medium trust
  or above, run the step as a dedicated execution branch with its own test gate.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Setting `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_n` and `GIT_CONFIG_VALUE_n` as plain,
  non-secret `-e` variables gives in-container git a credential helper. The helper reads `$GITHUB_TOKEN` from the
  environment, so clone, fetch and push work with a credential-free HTTPS URL and nothing is written to a file.
- **Learning target:** Whether the image's git honours these variables for every subprocess that the agent runners
  spawn, and whether any runner or the workspace-reuse path (`entrypoint/executor.py:1135-1150`) assumes the token is
  embedded in the URL.
- **Maximum acceptable cost for this learning:** 3.0 of the 10.0 cost units, spent on the probe before committing to the
  approach. If the probe fails, switch to the fallback.

### Intent & Git Integration

- **Step Intent:** Fix L2 and L7. Afterwards, the token never appears in any process's argv or in any `.git/config`.
- **Git branch:** `I-1792762862-redact-github-token-in-cli-command-echoes/I-{timestamp}-token-off-docker-argv/_` as a
  sub-intent, merged into `I-1792762862-redact-github-token-in-cli-command-echoes/_`. If trust does not allow a
  sub-intent, use
  `I-1792762862-redact-github-token-in-cli-command-echoes/P-1792765112-claude-agent-claude-opus-5-5/E-{timestamp}-env-file-credentials/_`.
- **Sub‑intent:** NEW

### Implementation Details (No code blocks, only logic/steps)

1. **Credential-free URL.** Change `resolve_target_repo_url` (`cli.py:178-210`) as follows.
   - When a token is found, return `https://{host}/{repo_path}`, normalised through `normalize_repo_remote` exactly as
     today, but with no userinfo.
   - When `HOLON_REPO_URL` is set and contains userinfo, strip it with `urllib.parse` and keep the rest unchanged. If no
     other token source exists, treat the stripped password as the token. Print a warning that names the variable but
     not the value.
   - Leave SSH remotes with no token unchanged.
   - Return the host as well, either through a small `NamedTuple` or a second helper, so step 3 below can scope the
     helper to the host.
   - Update the docstring.
2. **Env-file.** In `run_docker_container`:
   - Split `env_to_forward` into `secret_env` (keys where `is_secret_env_name(key)` is true; `GITHUB_TOKEN` is always
     included when a token exists) and `plain_env`.
   - Pass `plain_env` as `-e` arguments, as today, so the echo stays readable.
   - Write `secret_env` to an env-file:
     - Create `~/.holon/tmp` with mode `0700`, and re-apply `0700` if it already exists with looser bits.
     - Call `tempfile.mkstemp(prefix="holon-env-", dir=that_dir)`, which creates the file `0600`.
     - Call `os.fchmod(fd, 0o600)` explicitly as well.
     - Write one `KEY=VALUE` line per entry in sorted order.
     - Before writing, reject any value containing `\n` or `\r`: return exit code 1 with an error naming the key only.
   - Add `--env-file <path>` to `docker_cmd`.
   - Remove the file in the existing `finally` block, suppressing `FileNotFoundError`. Removal must happen on success,
     on an early return and on an exception.
3. **Runtime credential injection.** Add these plain, non-secret `-e` variables:
   - `GIT_CONFIG_COUNT=2`
   - `GIT_CONFIG_KEY_0=credential.https://{host}.helper` with an empty `GIT_CONFIG_VALUE_0`, which resets any inherited
     helpers;
   - `GIT_CONFIG_KEY_1=credential.https://{host}.helper` with `GIT_CONFIG_VALUE_1` set to a shell helper. The helper
     answers `get` with `username=x-access-token` and `password=` followed by the expanded `$GITHUB_TOKEN`.

   The value contains only the variable reference, never the token, so it is safe to echo. Add these only when a token
   exists and the URL is HTTPS.

4. **Probe (BALANCED).** In the container sandbox tier, using a fake token and a local `git daemon` or `file://` remote
   stand-in:
   - Confirm that the helper is invoked: `GIT_TRACE=1` shows the `credential-` helper being called. No real network is
     used.
   - Confirm that `git config --show-origin --get-all credential.https://github.com.helper` reports `command line:` /
     environment origin, not a file.
5. **Fallback** (only if the git version is below 2.31 or the probe fails). In `entrypoint/role_dispatcher.sh`, before
   dispatching the role, run `git config --global credential.https://{host}.helper` with the same helper. The host comes
   from a new plain `HOLON_GIT_CREDENTIAL_HOST` variable. This writes only to the container's ephemeral
   `$HOME/.gitconfig`, never to a tracked file. Record the extra entropy (about 0.5).
6. **Workspace reuse.** Check the equality check at `entrypoint/executor.py:1139`. A workspace cloned earlier with a
   tokenised URL now mismatches and takes the existing clean re-clone path, which is the intended outcome because it
   also purges the persisted token (L7). Document this in a comment.
7. **Stale sweep.** At the start of `run_docker_container`, delete any `~/.holon/tmp/holon-env-*` files older than 24
   hours. This is best-effort, logs at debug level, and never prints file contents.
8. **Echo.** The `Executing:` line now shows `--env-file /…/holon-env-XXXX` and the `GIT_CONFIG_*` entries. It still
   goes through `redact_command` as defence in depth.
9. **Declared test changes** in `tests/test_cli.py`:
   - Update the expectations around `cli.py:434`, `:463` and `:522-535` that assume a tokenised `HOLON_REPO_URL`. The
     new expectation is a credential-free URL, with the token present only in the env-file.

### Dependencies & Criticality

- **Depends on:** Step 2 (for `is_secret_env_name` and `redact_command`) and Step 1 (for the git-version decision)
- **Is Bottleneck:** YES. Goal 3 and the clarifying answer make taking the token off the argv mandatory.
  `p_success_pred` is 0.75, which is at least 0.6, so YES rather than STRONGLY_YES.

### Safety & Constraint Considerations

- **Relevant rules:** Intent goal 3 and the clarifying answer; intent goal 6 (target-native `.holon/` paths, no new
  dependencies, since only the stdlib `tempfile`, `os` and `urllib.parse` are used); `constraints.md` §2 (probe inside
  the container tier with no external network); `safety.md` "Sandboxing is mandatory for execution".
- **Potential failure modes for this step:**
  - The in-container `git push` fails because the helper is not inherited by an agent runner that rebuilds its
    environment from scratch.
  - A runner such as `codex-agent` or `pi-agent` reads `HOLON_REPO_URL` and expects embedded credentials.
  - Docker Desktop on macOS resolves `~/.holon/tmp` differently. The env-file is read client-side, so no mount is
    needed, but the path must exist on the host.
  - The env-file is left behind on a crash.
  - GitHub Enterprise hosts with a port need the helper URL to include the port.
- **Guardrails and early‑abort checks:**
  - Search `agent_runner.py` and the runners for `HOLON_REPO_URL` and `env=` before editing.
  - Gate the step on a test that runs a real `git ls-remote` against a local bare repository served over a fake
    credential-checking path, or on the `GIT_TRACE` probe if a local HTTP server is not available in the sandbox.
  - Abort if any test needs real network access.

### Success & Discard Criteria

- **Success:**
  - The `docker` argv captured by a monkeypatched `subprocess.run` contains no 8-character substring of the fake token.
  - `HOLON_REPO_URL` in the argv has no userinfo.
  - While the run is in progress, the env-file exists with `stat.S_IMODE == 0o600` and holds `GITHUB_TOKEN=<fake>`.
  - After the run (success, non-zero exit or raised exception), the file no longer exists.
  - The credential-helper probe shows helper invocation, with the helper config coming from the environment rather than
    a file.
- **Discard:** Take the fallback (Implementation Detail 5) if the `GIT_CONFIG_*` probe fails. Stop the step and escalate
  to the parent intent if the fallback also fails, if cost exceeds `1.5 * 10.0 = 15.0` units, or if a runner turns out
  to need embedded URL credentials in a way that cannot be fixed inside this intent. Steps 2–3 still merge on their own
  value.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 3.0   |
| impact_pred         | 30.0  |
| cost_pred           | 10.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 14.6  |

### Step Metrics Rationale

This step has the highest entropy in the plan. It changes the credential flow for every role (`SSA` around 5, `IRR`
around 3 because existing workspaces get re-cloned, `CL` around 4 on the hot `cli.py`, `SER` around 4, `NOV` around 4
for `GIT_CONFIG_*` in this repository). The success probability reflects the unknowns about the image and the runners.
The learning value is high: a verified, file-less credential-injection pattern is directly reusable by the KB for every
future sandbox role. `EV = 0.75 * 30.0 + 0.5 * 6.0 - 0.3 * 3.0 - 10.0 = 22.5 + 3.0 - 0.9 - 10.0 = 14.6`.

---

## Step 5: Add leak-proof tests across the CLI, the entrypoints and the existing redaction regressions

- **Sub‑intent recommendation:** NO
- **Reasoning:** The tests are the acceptance evidence for goal 4. They are moderate in size, low in risk, and belong on
  the same branches as the code they cover.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove goals 4a, 4b and 4c, the env-file lifecycle, and the unchanged ledger and wire-log redaction.
- **Git branch:**
  `I-1792762862-redact-github-token-in-cli-command-echoes/P-1792765112-claude-agent-claude-opus-5-5/E-{timestamp}-leak-tests/_`
  (tests for Step 4 travel with the Step 4 branch)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add a shared pytest fixture, `fake_github_token`, in `tests/conftest.py` if one exists, otherwise in the specific
   test modules.
   - Build the value at runtime from non-token-shaped parts (`"ghp_" + "Z9" * 18`), so the repository never holds a
     literal that matches `ghp_[0-9a-zA-Z]{36}`.
   - Set `GITHUB_TOKEN`, `GH_TOKEN` and `HOLON_MITM_WEB_PASSWORD` through `monkeypatch.setenv`.
   - Provide a helper `assert_no_token_fragment(text, token)` that checks every substring of length 8 or more.
2. Goal 4a, in `tests/test_cli.py`:
   - Monkeypatch `shutil.which`, `subprocess.run` (recording argv), `get_token_reduction_mounts_and_envs`,
     `get_agent_session_mounts` and `resolve_target_repo_url`.
   - Run `run_docker_container` and capture stdout with `capsys`.
   - Assert there is no token fragment in the `Executing:` line, and that `myorg/myrepo` and the role and branch
     arguments are present.
3. Goal 4c, in the same test: assert there is no token fragment in `" ".join(recorded_argv)`.
4. Env-file lifecycle, in the same test:
   - Inside the fake `subprocess.run`, find the path after `--env-file`, assert `stat.S_IMODE(os.stat(path).st_mode)`
     equals `0o600`, and assert that the file contents include the token.
   - After the return, assert `not os.path.exists(path)`.
   - Run separate cases where the fake raises `KeyboardInterrupt` and where it returns a non-zero exit code.
   - Use `tmp_path` for `HOME`, so `~/.holon/tmp` resolves inside the test sandbox.
5. Goal 4b, in `tests/test_intent_creator.py`, `tests/test_planner.py` and `tests/test_review_loop.py`:
   - Call each module's `run_cmd` with a clone argv holding `https://x-access-token:<fake>@github.com/myorg/myrepo.git`
     and `--branch I-1792762862-x/_`, with `subprocess.run` faked to fail and echo the URL in stderr.
   - Assert there is no token fragment in the captured output, and that `github.com/myorg/myrepo.git` and the branch are
     present.
6. Regression guard (declared): do not modify any existing assertion in `tests/test_executor.py` (ledger and agent
   output redaction) or in the `mitm_addon` wire-log tests. Run them unchanged and require the same pass count. Add one
   explicit test that `redact_agent_secrets` output for a fixed corpus equals a golden string captured from `main`
   before Step 2.
7. Credential-helper test: assert that the argv contains `GIT_CONFIG_KEY_1=credential.https://github.com.helper` and
   that `GIT_CONFIG_VALUE_1` contains `$GITHUB_TOKEN` as a literal reference and no token fragment.
8. Run only through `uv run pytest` from `apps/sandbox-executor`. Never use `unittest` runners or `PYTHONPATH`.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3 and 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` §3 (pytest 9.1.1, test placement, declared test changes); intent goal 4; intent goal
  6 (`uv run pytest` only).
- **Potential failure modes for this step:** A token literal accidentally committed in a test; tests that touch the real
  `~/.holon` directory; flaky permission checks on filesystems that ignore mode bits (rare in the Linux sandbox).
- **Guardrails and early‑abort checks:** Run `scripts/pre_commit_check.py` over the new test files before committing.
  Always set `HOME` to `tmp_path`.

### Success & Discard Criteria

- **Success:** All new tests pass, and all pre-existing tests pass with an unchanged count, apart from the declared
  updates in `tests/test_cli.py`.
- **Discard:** If cost exceeds `1.5 * 7.0 = 10.5` units, drop the review-loop variant of goal 4b. It is the same code
  shape as the planner variant. Record the gap.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.2   |
| impact_pred         | 15.0  |
| cost_pred           | 7.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 6.89  |

### Step Metrics Rationale

There are many test files, but each one is low risk. The success probability is lowered slightly by monkeypatch
complexity in `run_docker_container`, which has many collaborators.
`EV = 0.85 * 15.0 + 0.5 * 3.0 - 0.3 * 1.2 - 7.0 = 12.75 + 1.5 - 0.36 - 7.0 = 6.89`.

---

## Step 6: Add a hygiene check for tracked token-shaped values and `x-access-token:` userinfo

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is a small extension of an existing guard (`scripts/pre_commit_check.py`). The one unknown is what
  tracked history already contains, which is why the step is BALANCED.
- **Step Type:** CONFIG
- **Exploration level:** BALANCED

- **Hypothesis being tested:** No tracked file, including the append-only ledger and the historical `plans/` and
  `executions/` markdown, currently contains a real `ghp_`, `gho_`, `ghu_`, `ghs_` or `ghr_` token (36 characters), a
  `github_pat_` token, or an `x-access-token:<token-shaped>@` value.
- **Learning target:** Whether past runs leaked credentials into committed artefacts. Such a leak would be a real
  security incident that this intent's L4–L6 echoes could have caused.
- **Maximum acceptable cost for this learning:** 1.0 cost unit for the initial scan.

### Intent & Git Integration

- **Step Intent:** Prevent recurrence (goal 5) and detect any leak that already happened.
- **Git branch:**
  `I-1792762862-redact-github-token-in-cli-command-echoes/P-1792765112-claude-agent-claude-opus-5-5/E-{timestamp}-token-hygiene/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Scan first: run the existing `CREDENTIAL_PATTERNS` plus the new URL pattern over `git ls-files` output, without
   committing anything.
2. If a match is a real-looking token in the ledger or in history, stop.
   - Do not edit the ledger (`constraints.md` §3).
   - Escalate to a human for token rotation and a history-rewrite decision.
   - Mark the step blocked. If a mock value is merely shaped like a token, it already falls under
     `FALSE_POSITIVE_INDICATORS`.
3. Add a `CREDENTIAL_PATTERNS` entry to `scripts/pre_commit_check.py`, named "URL-embedded GitHub credential". It
   matches `x-access-token:` followed by a token-shaped value (at least 20 characters of `[A-Za-z0-9_-]`) and then `@`,
   and it excludes the masked forms `*******`, `***REDACTED***` and `${…}` references. The existing 36-character
   `ghp_`/`gho_` patterns already cover bare tokens, so they are reused, not duplicated.
4. Declared test changes in `tests/test_pre_commit_check.py`:
   - Unit cases for the new pattern: positive with a runtime-built value, and negative for `x-access-token:*******@` and
     `x-access-token:${GITHUB_TOKEN}@`.
   - A repository-wide test that runs `git ls-files` from the repository root (skipped with a clear reason when the
     working tree is not a git checkout), scans every tracked text file with the script's own `scan` function, and fails
     with the matching file and line, never the matched value.
5. If the scan is too slow (over about 5 seconds), restrict it to text extensions and skip files over 2 MB.

### Dependencies & Criticality

- **Depends on:** Step 5 (so the new tests are already token-literal-free)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §3 and `safety.md` Invariant 6 (ledger immutability); intent goal 5; intent goal
  6 (no new dependencies, no auxiliary ledgers).
- **Potential failure modes for this step:** A real historical leak is found; false positives on documentation that
  shows the URL form as an example; the failure message itself printing the matched secret.
- **Guardrails and early‑abort checks:** Failure messages print only the path, the line number and the pattern name. The
  repository-wide test must not print file contents.

### Success & Discard Criteria

- **Success:** The pattern catches the positive cases, ignores the masked forms, and the scan of tracked files passes on
  a clean tree.
- **Discard:** If a real token is found, stop and escalate to a human. That is a success for the probe and a blocker for
  the merge. If false positives exceed 3 distinct legitimate documentation lines, narrow the pattern rather than growing
  the allowlist.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.0   |
| impact_pred         | 8.0   |
| cost_pred           | 3.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 5.6   |

### Step Metrics Rationale

The code change is small. The success probability is lowered by the real chance that historical artefacts already
contain matches, given that L4–L6 printed raw clone URLs into agent output. The learning value is high because the scan
measures whether past leaks happened. `EV = 0.80 * 8.0 + 0.5 * 5.0 - 0.3 * 1.0 - 3.0 = 6.4 + 2.5 - 0.3 - 3.0 = 5.6`.

---

## Step 7: Document the credential flow and propose the follow-up intent

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is a small documentation change with no risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Explain the env-file and credential-helper flow, and record the deferred mitmweb-password argv
  exposure.
- **Git branch:**
  `I-1792762862-redact-github-token-in-cli-command-echoes/P-1792765112-claude-agent-claude-opus-5-5/E-{timestamp}-docs/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Update the credentials section of `docs/executor/agent_credentials_requirements.md` and
   `apps/sandbox-executor/README.md`. Cover how the token reaches the container (`0600` env-file plus runtime
   `GIT_CONFIG_*` helper), that `HOLON_REPO_URL` is now credential-free, and the redaction marker. Use Australian
   English.
2. Add a short "Follow-up" note proposing an intent to pass `HOLON_MITM_WEB_PASSWORD` to the sidecar through a mounted
   `0600` mitmproxy `config.yaml` instead of `--set web_password=` on the argv (L3). Do not create the intent file here.
3. Run prettier with the repository's `.prettierrc.json`, as a check and then a write, on the changed markdown files
   only. Wrap any formula or identifier containing underscores in code spans.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Intent goal 6 (markdown formatting, Australian English).
- **Potential failure modes for this step:** A prettier formatting loop caused by unescaped underscores; documentation
  examples that trip the Step 6 hygiene pattern.
- **Guardrails and early‑abort checks:** Use only masked example values (`x-access-token:*******@`) and run
  `scripts/pre_commit_check.py` over the changed docs.

### Success & Discard Criteria

- **Success:** The documentation is accurate, prettier is idempotent on the changed files, and the hygiene check passes.
- **Discard:** If prettier will not stabilise after 2 passes, simplify the markup.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.4   |
| impact_pred         | 3.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 1.73  |

### Step Metrics Rationale

Routine documentation with low impact. `EV = 0.95 * 3.0 + 0.5 * 1.0 - 0.3 * 0.4 - 1.5 = 2.85 + 0.5 - 0.12 - 1.5 = 1.73`.

---

## Step 8: Run the validation gate (tests, lint, format, hygiene) before the parent merge request

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is a mandatory final gate. It is cheap and deterministic.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Show that every acceptance criterion in `docs/metrics.md` §1.3 is met on the rebased intent branch.
- **Git branch:**
  `I-1792762862-redact-github-token-in-cli-command-echoes/P-1792765112-claude-agent-claude-opus-5-5/E-{timestamp}-validate/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Rebase onto the intent branch after Steps 2–7 have merged into it.
2. From `apps/sandbox-executor`, run `uv run pytest -m "not integration_test and not stress"`, then
   `uv run ruff check .` and `uv run ruff format --check .`.
3. From the repository root, run `uv lock --check`. It should be a no-op, which confirms no new dependency was added.
4. Run prettier in check mode on the changed markdown, and run `scripts/pre_commit_check.py` over the full diff.
5. Do a manual smoke check in the container tier with a fake token. Run `holon plan` against a local bare repository
   with `ps -o args` sampled during the run, and confirm that no token fragment appears in the process list or on
   stdout.
6. Append the execution outcome only through the sanctioned `executions.jsonl` path. Do not create any auxiliary ledger.

### Dependencies & Criticality

- **Depends on:** Steps 2–7
- **Is Bottleneck:** NO (it is a gate; any failure loops back to the step that owns it)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/metrics.md` §1.3 (acceptance criteria); `constraints.md` §1 (rebase discipline); `safety.md`
  Invariant 4 (a human approves the merge to `main`).
- **Potential failure modes for this step:** A rebase conflict on the hot file `cli.py`; ruff format drift in moved
  code.
- **Guardrails and early‑abort checks:** If the rebase conflicts more than 3 times, escalate to a human, as `safety.md`
  §"Rebase conflict" requires.

### Success & Discard Criteria

- **Success:** All commands exit 0, the smoke check shows no token fragment, and the review package is ready for human
  approval.
- **Discard:** Not applicable. Failures send work back to the owning step.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.3   |
| impact_pred         | 4.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 2.51  |

### Step Metrics Rationale

A deterministic gate with low entropy. Its impact is the confidence it gives the human reviewer.
`EV = 0.90 * 4.0 + 0.5 * 2.0 - 0.3 * 0.3 - 2.0 = 3.6 + 1.0 - 0.09 - 2.0 = 2.51`.

---
