# Holon Sandbox Executor (`sandbox-executor`)

The `sandbox-executor` package is the core execution and orchestration engine of Holon Agentic Coder. It manages
containerised and process-isolated sandboxes for autonomous agents, powers the 5-stage Holon Flow pipeline, enforces
safety boundaries, and maintains append-only ledger audit logs.

---

## 1. Package Architecture & Submodules

```
apps/sandbox-executor/
├── src/
│   └── sandbox_executor/
│       ├── flow.py              # 5-stage lifecycle pipeline engine & checkpointing
│       ├── cli.py               # Unified CLI interface (holon subcommands)
│       ├── git.py               # Centralised Git repository abstraction & operations
│       ├── calibration.py       # Post-execution calibration engine
│       ├── agent_runner.py      # Docker container execution & runner lifecycle
│       ├── ledger.py            # Canonical 3-ledger operations & LedgerLock concurrency
│       ├── formatting.py        # Prettier formatting convergence utilities
│       ├── context_cleaner.py   # Context and git workspace sanitisation
│       ├── entrypoint/          # Sandbox role entrypoints & Stage 4 review loop
│       │   ├── planner.py       # Sandboxed plan generation entrypoint
│       │   ├── executor/        # Sandboxed execution entrypoint package & credential scrubber
│       │   └── review_loop.py   # Autonomous native PR review loop runner
│       ├── knowledge/           # Knowledge & Wisdom consult engine
│       │   ├── consult.py       # Multi-label consult engine & payload token budgeter
│       │   ├── index.py         # SQLite FTS5 action index database & sync
│       │   ├── records.py       # Knowledge record ingestion, validation & redaction
│       │   ├── promotion.py     # Qualification heuristic & KB/WB promotion
│       │   ├── stats.py         # Self-telemetry & operational consultation metrics
│       │   └── wisdom.py        # Wisdom base axiom ingestion & synchronisation
│       └── launcher/            # Role containers, env-files & the holon-coherence proxy sidecar
└── tests/                       # Hermetic pytest suite
```

### Key Submodules

- **`flow.py`**: Houses `PipelineEngine`, `FlowContext`, `FlowCheckpoint`, and stage runners (`run_intent_stage`,
  `run_plan_stage`, `run_execute_stage`, `run_review_stage`, `run_calibrate_stage`). Stages 1-3 dispatch the same role
  containers as the per-stage commands (through `cli.dispatch_role_agent`) and adopt the branches they push; the flow
  never authors plans or code itself. Also manages atomic checkpointing, the empty-diff guard, consensus approval
  outcomes (`APPROVED`) and human-only merge boundaries.
- **`cli.py`**: Entrypoint for all `holon` commands (`flow`, `plan`, `execute`, `review-loop`, `calibrate`, `consult`,
  `record`, `reconcile`, `promote`, `veto`, `stats`, `ledger`). Handles argument parsing, proxy setup, and process
  dispatch.
- **`git.py`**: Centralised repository abstraction providing typed Git interactions (`run_git`, `get_diff_numstat`,
  `resolve_ref`, `get_head_commit`, `branch_exists`) with automatic credential scrubbing and timeout enforcement.
- **`calibration.py`**: Executes post-execution calibration analysis comparing predicted EV and step metrics against
  actual test results and git logs, committing markdown reports to `/calibrated` branches.
- **`agent_runner.py`**: Manages isolated Docker container invocations with CPU/memory limits, volume mounts, and
  hardened security policies for planner and executor agents.
- **`ledger.py`**: Provides structured querying, deduplication, conflict resolution, and filtering across
  `intents.jsonl`, `plans.jsonl`, and `executions.jsonl`. Implements `LedgerLock` (POSIX advisory file locking with
  exponential backoff) for concurrent append safety.
- **`review_ensemble.py`**: The independent reviewer ensemble that decides every Stage 4 iteration (reviewer turns, the
  review trailer parser, the request builder and the unanimity consensus); see [Reviewer ensemble](#reviewer-ensemble).
- **`review_report.py`**: Renders the posted Stage 4 consensus report from the real reviewer results only.
- **`entrypoint/review_loop.py`**: The `review-loop` role container entrypoint: prepares the workspace and runs the
  Stage 4 loop, with its reviewer agent turns, inside the sandbox; exits 0 only for an `APPROVED` ensemble verdict.
- **`knowledge/`**: Implements Knowledge and Wisdom consultation (`ConsultEngine`, `ActionIndexDatabase`), indexing
  discrete actions into SQLite FTS5 with sub-50ms query SLAs, semantic deduplication, and candidate promotion.
- **`launcher/`**: Launches role containers and, for `--token-reduce`, the per-run proxy sidecar. Token reduction itself
  is owned by holon-coherence: the sidecar runs the holon-coherence proxy image and the mitmproxy addon it ships, and
  this package vendors no addon code. `proxy_image.py` picks the image (`HOLON_COHERENCE_IMAGE` when set, else a local
  `holon-coherence:latest`, else `ghcr.io/holon-agentic-coder/holon-coherence:latest`), `root_ca.py` and
  `root_ca_openssl.py` keep the host Root CA at `~/.holon/certs` valid (same layout as holon-coherence, so either tool
  reuses the other's CA), `proxy_paths.py` and `sidecar_plan.py` default the sidecar's host wire-log and cache
  directories to `~/.holon/mitm_wire_logs` and `~/.holon/cache` under the per-user state directory outside any
  repository worktree (overridable via `WIRE_LOG_DIR` and `CACHE_DIR`), and `proxy_readiness.py` waits until the sidecar
  or an attached proxy answers HTTP.

---

## 2. Container Security Hardening

Agent execution within sandboxes is strictly isolated using non-root container containment and minimal privilege
enforcement:

- **Capability Dropping (`--cap-drop=ALL`)**: All Linux kernel capabilities are stripped by default from agent
  containers, preventing administrative escalation, network raw socket manipulation, and device tampering.
- **Privilege Boundary (`--security-opt=no-new-privileges`)**: Disables `setuid` and `setgid` binaries from acquiring
  elevated privileges inside the container environment.
- **Non-Root Execution (`holon` user)**: Agent processes execute as an unprivileged system user (`holon`, UID `10001`,
  GID `10001`) with isolated user directories and sandboxed workspace access.
- **Network Containment**: Agent outbound traffic is routed through the local MITM proxy sidecar, stripping
  authentication credentials and enforcing strict egress filtering.
- **Credentials Off the Command Line**: The GitHub token and other credential variables reach the container through a
  temporary `0600` docker `--env-file` (in the `0700` directory `~/.holon/tmp/env-files`) that is removed after the run;
  the pre-flight readiness probe (`holon doctor`, and before `holon plan` / `execute` / `flow`) uses the same env-file
  and classifier for `HOLON_AGENT_KEY`, a credential-bearing `HOLON_AGENT_SETTINGS` and `HOLON_AGENT_OAUTH_TOKEN` (the
  claude OAuth token now has its own route; see [Configuration Files](#3-configuration-files-holonenv-and-repoenv)). No
  role container runs git against a remote, so no git credential-helper (`GIT_CONFIG_*`) is injected and no `ssh-agent`
  socket is mounted; `GITHUB_TOKEN` is forwarded for `gh` API calls only. The old repository URL variable
  `HOLON_REPO_URL` was removed: a stale value in the environment is ignored, neither forwarded nor an error. Every
  echoed command is masked through the shared `sandbox_executor.redaction` patterns, always before any length clamp.
  Known gap: the mitmweb dashboard password is still on the sidecar's command line. See
  [Agent Credentials](../../docs/executor/agent_credentials_requirements.md#host-side-fetch-and-push).

---

## 3. Configuration Files (`~/.holon/.env` and `<repo>/.env`)

Persistent `HOLON_*` settings live in two dotenv layers, resolved once per `holon` invocation by
`sandbox_executor.config_env`:

- the user-wide file `~/.holon/.env`, mode `0600`;
- the project file `<repo-root>/.env` (the first directory upwards with a `.git` file or directory), which `holon init`
  git-ignores. `holon init` also scaffolds a commented `.env.example` and never creates `.env` itself.

**Precedence.** Each variable resolves on its own. From broadest to most specific: built-in defaults, the user-wide
file, the project file, the process environment, explicit CLI flags (`--agent`, `--model` and the other flags). The most
specific layer that defines a key wins; a broader layer only supplies keys the more specific layers leave unset. The
file values are applied to the host process environment as fill-only values, so an exported variable or a flag always
wins.

**Format.** A strict `KEY=VALUE` subset: blank lines, lines whose first non-space character is `#`, an optional pair of
single or double quotes whose content is taken literally, and an inline ` #` comment after an unquoted value. A shell
`export ` prefix is rejected. Nothing is expanded (`"$HOME"` stays `$HOME`) and nothing is executed. A malformed line is
skipped with a warning that names the file and the line number, never the line.

**Empty values.** `KEY=` means "not set in this layer": the next broader layer with a non-empty value, or the built-in
default, supplies the key, so an empty line copied from `.env.example` never blanks a user-wide setting. The empty layer
is still reported among the shadowed layers as `<layer> (empty)`. A key that every defining layer leaves empty is unset.

**Accepted keys.** Only `HOLON_*` keys (`^HOLON_[A-Z0-9_]+$`) and `GITHUB_TOKEN` are accepted. Every host-set key
(listed after the key table, among them `HOLON_ROLE`, `HOLON_CONFIG_RESOLVED`, `HOLON_SKIP_PREFLIGHT`,
`HOLON_IN_SANDBOX` and `HOLON_TARGET_WORKTREE`) and the retired `HOLON_REPO_URL` are refused. The project `.env` is
repository-controlled, so it may not set a token-file reference or a host-reach key either:
`HOLON_ANTIGRAVITY_SESSION_DIR` (a host directory mounted into the container), `HOLON_AGENT_SETTINGS` and
`HOLON_AGENT_CONFIG` (runner settings that can redirect the API endpoint), `HOLON_TOKEN_REDUCE`,
`HOLON_PASSIVE_MONITORING`, `HOLON_PROXY_URL`, `HOLON_PROXY_PORT` (the credential-bearing proxy and its routing),
`HOLON_MITM_USER`, `HOLON_MITM_WEB_PORT` and `HOLON_MITM_WEB_PASSWORD` (the proxy sidecar user and dashboard),
`HOLON_COHERENCE_IMAGE` (the proxy sidecar image, which sees agent credentials), `HOLON_LOCAL_BASE_URL`,
`HOLON_HOST_LOCAL_HOSTS`, `HOLON_ROOT_CA_PATH` and `HOLON_CA_BUNDLE_PATH` (the trusted CA), `HOLON_SECRET_BUNDLE_PATH`
(a file the role dispatcher reads or writes), and the credentials `GITHUB_TOKEN`, `HOLON_AGENT_OAUTH_TOKEN` and
`HOLON_AGENT_KEY` are set only in `~/.holon/.env` or the process environment. Any other refused key draws
`Warning: <file>: ignoring <KEY>: <reason>.`, which names the key and never the value.

**Warnings.** A layer or token file with group or other permission bits draws
`Warning: <path> has mode 0NNN; expected 0600. The file is left unchanged.` A project `.env` that holds a secret key and
is tracked by git draws `Warning: <path> is tracked by git and holds secret keys (<KEYS>); untrack it.` Warnings go to
stderr on every invocation and appear under `holon doctor`.

**Secrets by file.** The claude credential is best kept in the OS keychain (see below). When a layer does supply it, the
credential is referenced by the path of its token file, never pasted in:

| file key                       | value key                 | in-container variable     |
| ------------------------------ | ------------------------- | ------------------------- |
| `HOLON_AGENT_OAUTH_TOKEN_FILE` | `HOLON_AGENT_OAUTH_TOKEN` | `CLAUDE_CODE_OAUTH_TOKEN` |

Only `~/.holon/.env` and the process environment may name a token file: the project `.env` is repository-controlled, so
a file key there is ignored with `Warning: <file>: ignoring <KEY>: ...` and nothing is read. The token file (mode
`0600`) is read on the host. Its value travels only in the `0600` docker `--env-file`, and the image's
`role_dispatcher.sh` exports it as `CLAUDE_CODE_OAUTH_TOKEN` for the claude runner. An explicit
`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` wins over it, and it wins over the OS keychain entry. It is no longer
carried inside `HOLON_AGENT_SETTINGS`. Without any of these, the claude credential is read on the host from the OS
keychain (service `holon`, account `claude-oauth-token`; macOS keychain or Linux Secret Service), and it travels in the
same `0600` env-file. An older plaintext file `~/.holon/secrets/claude-oauth-token` is imported into the keychain once
by pre-flight and deleted after a verified read-back; a file key that names it is ignored with
`Warning: <origin>: ignoring <KEY>: ...`, so that file is never read as the credential. The store commands and the
migration are described in `docs/executor/agent_credentials_requirements.md`. The mapping lives in the image, so rebuild
the images (`build_all_images.sh`) after upgrading; an old image fails visibly in `holon doctor` as "not logged in".

**Inheritance.** Every role container (intent creator, planner, executor, review loop, and the runs calibration drives)
and the pre-flight probe receive the effective values through the `0600` env-file, never as `-e KEY=value` on the argv,
together with `HOLON_CONFIG_RESOLVED=1`. A nested `holon` that sees the marker reads no layer file and reports every key
as `process (inherited)`.

**Reporting.** `holon doctor` lists every effective setting as `KEY = value   [source; shadows ...]`, and under
`configuration.settings` with `--json` (`key`, `value`, `display`, `source`, `shadowed`, `secret`). A secret is shown
only as `set (<layer>)`, a key that every layer leaves empty as `unset (<layer>)`. Like every other doctor message, a
value that could carry a credential or is invalid is withheld as `set (<layer>; value withheld)`: a numeric key
(`_SECONDS`, `_MINUTES`, `_TIMEOUT`, `_TOKENS`, `_WINDOW`, `_BYTES`, `_PORT`) shows only a finite decimal or
`unlimited`, and any other key only a short plain value with no URL authority, `@`, token-like run or non-finite number.

Supported keys (secret keys are also every key ending in `_TOKEN`, `_KEY`, `_PASSWORD` or `_SECRET`, except `*_FILE`):

| key                                           | default                                                        | secret | purpose                                                                                                |
| --------------------------------------------- | -------------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------ |
| `HOLON_AGENT`                                 | the harness agent detected from the runtime                    | no     | Agent runner (`claude`, `pi`, `codex`, `opencode`, `antigravity`).                                     |
| `HOLON_MODEL`                                 | unset                                                          | no     | Model the runner uses.                                                                                 |
| `HOLON_AGENT_EFFORT`                          | `medium`                                                       | no     | Reasoning effort passed to the claude runner.                                                          |
| `HOLON_CLAUDE_CODE_MODE`                      | `1`                                                            | no     | Enable Claude Code experimental code mode in the claude runner's `--settings` `env` block.             |
| `HOLON_AGENT_OAUTH_TOKEN_FILE`                | unset                                                          | no     | Path of a file holding the claude subscription token; read on the host into `HOLON_AGENT_OAUTH_TOKEN`. |
| `HOLON_AGENT_OAUTH_TOKEN`                     | unset                                                          | yes    | Claude subscription token; prefer the OS keychain, or `HOLON_AGENT_OAUTH_TOKEN_FILE`.                  |
| `HOLON_AGENT_KEY`                             | unset                                                          | yes    | API key of a runner other than claude.                                                                 |
| `HOLON_AGENT_SETTINGS`                        | unset                                                          | yes    | Extra claude `--settings` JSON (attribution); no longer needed for the token.                          |
| `HOLON_AGENT_PROVIDER`                        | unset; in host-local mode `HOLON_LOCAL_PROVIDER`, else `local` | no     | Provider of the pi runner.                                                                             |
| `HOLON_AGENT_MODE`                            | unset                                                          | no     | `--agent` value of the opencode runner.                                                                |
| `HOLON_AGENT_OSS_MODE`                        | unset                                                          | no     | Pass `--oss` to the codex runner when `1` or `true`.                                                   |
| `HOLON_AGENT_LOCAL_PROVIDER`                  | unset                                                          | no     | `--local-provider` value of the codex runner.                                                          |
| `HOLON_AGENT_CONFIG`                          | unset                                                          | no     | `-c` value of the codex runner.                                                                        |
| `HOLON_AGENT_SKIP_PERMISSIONS`                | `true`                                                         | no     | Run the agent with its permission prompts bypassed.                                                    |
| `HOLON_AGENT_LOG_BYTES`                       | `65536`                                                        | no     | Bytes of agent output kept in execution logs.                                                          |
| `HOLON_SECRET_BUNDLE_PATH`                    | `/run/secrets/holon_auth.json`                                 | no     | Secret bundle read inside the container.                                                               |
| `HOLON_ROOT_CA_PATH`                          | `/usr/local/share/ca-certificates/holon-root-ca.crt`           | no     | Holon Root CA trusted inside the container when present.                                               |
| `HOLON_CA_BUNDLE_PATH`                        | `/home/holon/.holon-ca-bundle.crt`                             | no     | Merged CA bundle the role dispatcher writes inside the container.                                      |
| `GITHUB_TOKEN`                                | `gh auth token`                                                | yes    | GitHub token for pushes and pull requests.                                                             |
| `HOLON_TURN_TIMEOUT_SECONDS`                  | `3600`                                                         | no     | Wall-clock ceiling of one agent turn.                                                                  |
| `HOLON_TURN_STALL_SECONDS`                    | `90`                                                           | no     | Silence after which a turn counts as stalled.                                                          |
| `HOLON_STAGE_BUDGET_INTENT_MINUTES`           | `10`                                                           | no     | Budget of the intent stage.                                                                            |
| `HOLON_STAGE_BUDGET_PLAN_MINUTES`             | `35`                                                           | no     | Budget of the plan stage.                                                                              |
| `HOLON_STAGE_BUDGET_EXECUTE_MINUTES`          | `45`                                                           | no     | Budget of the execute stage.                                                                           |
| `HOLON_STAGE_BUDGET_REVIEW_ITERATION_MINUTES` | `120`                                                          | no     | Budget of one review iteration.                                                                        |
| `HOLON_STAGE_BUDGET_CALIBRATION_MINUTES`      | `10`                                                           | no     | Budget of the calibration stage.                                                                       |
| `HOLON_REVIEW_CI_WAIT_SECONDS`                | `1800`                                                         | no     | Wait for pending CI checks after the reviewers ran.                                                    |
| `HOLON_REVIEW_QUOTA_WAIT_SECONDS`             | `900`                                                          | no     | Longest wait for a reviewer quota reset.                                                               |
| `HOLON_SCOPED_REVIEW_MAX_LINES`               | `400`                                                          | no     | Largest delta reviewed scoped instead of in full; `0` disables it.                                     |
| `HOLON_SCOPED_REVIEW_CONTEXT_BYTES`           | `40000`                                                        | no     | Bytes of each modified file shown to a scoped review.                                                  |
| `HOLON_CI_REGISTRATION_TIMEOUT`               | `180`                                                          | no     | Wait for CI checks to register on a pushed head.                                                       |
| `HOLON_TEST_MEASUREMENT_TIMEOUT_SECONDS`      | `900`                                                          | no     | Ceiling of one measured test run.                                                                      |
| `HOLON_GIT_LOCK_AGE_SECONDS`                  | `60`                                                           | no     | Age after which a stale git lock is removed.                                                           |
| `HOLON_PREFLIGHT_TIMEOUT`                     | `60`                                                           | no     | Timeout of the pre-flight readiness probe.                                                             |
| `HOLON_TOKEN_REDUCE`                          | unset                                                          | no     | Enable the token-reduction proxy when `1` or `true`.                                                   |
| `HOLON_PROXY_URL`                             | the host gateway on port `8080`                                | no     | External token-reduction proxy to attach to.                                                           |
| `HOLON_COHERENCE_IMAGE`                       | a local image, else the GHCR image                             | no     | Image of the token-reduction proxy sidecar.                                                            |
| `HOLON_PROXY_PORT`                            | `8080`                                                         | no     | Port of the token-reduction proxy.                                                                     |
| `HOLON_PASSIVE_MONITORING`                    | unset                                                          | no     | Run the proxy in passive monitoring mode.                                                              |
| `HOLON_MITM_USER`                             | unset                                                          | no     | User of the mitmweb dashboard.                                                                         |
| `HOLON_MITM_WEB_PORT`                         | `8081`                                                         | no     | Port of the mitmweb dashboard.                                                                         |
| `HOLON_MITM_WEB_PASSWORD`                     | unset                                                          | yes    | Password of the mitmweb dashboard.                                                                     |
| `HOLON_LOCAL_LLM`                             | unset                                                          | no     | Enable host-local models for the pi runner.                                                            |
| `HOLON_LOCAL_PROVIDER`                        | unset                                                          | no     | Host-local provider to select.                                                                         |
| `HOLON_LOCAL_MODELS`                          | unset                                                          | no     | Host-local models to expose.                                                                           |
| `HOLON_LOCAL_BASE_URL`                        | unset                                                          | no     | Base URL of the host-local server.                                                                     |
| `HOLON_LOCAL_CONTEXT_WINDOW`                  | unset                                                          | no     | Context window of host-local models.                                                                   |
| `HOLON_HOST_LOCAL_HOSTS`                      | unset                                                          | no     | Extra hosts allowed as host-local endpoints.                                                           |
| `HOLON_CONTEXT_RESERVE_TOKENS`                | `4096`                                                         | no     | Tokens reserved out of a host-local context window.                                                    |
| `HOLON_REASONING_MODE`                        | unset                                                          | no     | Reasoning mode of host-local models.                                                                   |
| `HOLON_ACTION_LOGGING`                        | unset                                                          | no     | Record agent actions when `1` or `true` (alias `HOLON_RECORD_ACTIONS`).                                |
| `HOLON_RECORD_ACTIONS`                        | unset                                                          | no     | Alias of `HOLON_ACTION_LOGGING`.                                                                       |
| `HOLON_ATTRIBUTION_STRICT`                    | unset                                                          | no     | Fail, rather than warn, on a commit attribution problem.                                               |
| `HOLON_ANTIGRAVITY_SESSION_DIR`               | `~/.holon/sessions/antigravity`                                | no     | Session directory mounted for the antigravity runner.                                                  |

Set by the host for role containers (or by a maintainer for one run), not in layer files: `HOLON_ROLE`,
`HOLON_CONFIG_RESOLVED`, `HOLON_SKIP_PREFLIGHT`, `HOLON_IN_SANDBOX`, `HOLON_AGENT_ID`, `HOLON_TARGET_WORKTREE`,
`HOLON_TARGET_BRANCH`, `HOLON_STAGE_BUDGET_SECONDS`, `HOLON_REVIEW_ITERATION`, `HOLON_REVIEW_FINDING_IDS`,
`HOLON_REVIEW_SEVERITY`, `HOLON_ACTION_ORIGIN`, `HOLON_PR_HEAD_SHA`, `HOLON_REPLAN_FEEDBACK`,
`HOLON_ALLOW_MODULE_BUDGET_CHANGE`, `HOLON_REPO_URL`, `HOLON_COHERENCE_WEB_PASSWORD` (the launcher derives it from
`HOLON_MITM_WEB_PASSWORD` for the proxy sidecar).

---

## 4. CLI Subcommands Reference

The package provides the unified `holon` CLI command.

### `holon flow`

Runs the automated 5-stage Holon Flow lifecycle pipeline (Intent -> Plan -> Execute -> Review Loop -> Calibrate). A flow
run is equivalent to invoking `holon intent`, `holon plan`, `holon execute`, `holon review-loop` and `holon calibrate`
by hand: Stages 1-3 launch the same role containers with the resolved `--agent` and `--model`; the host fetches the base
before each container starts and pushes the branch it committed after it returns. The flow fails Stage 2 rather than
fabricate a plan when no planner produced `plans/P-*.md`, and fails Stage 3 (recording `status: "failed"`, opening no
PR) when the execution changes nothing outside `holon-knowledge/ledger/`, `plans/` and `executions/`. `--skip-push` is
only accepted with `--dry-run`, which validates without dispatching. See
[the flow equivalence guarantee](../../docs/git_flow.md#flow-equivalence-guarantee).

```bash
# Run complete lifecycle
./holon flow intents/my-task.json

# Run with token reduction and dashboard
./holon flow intents/my-task.json --token-reduce --mitm-web

# Resume pipeline from a specific stage
./holon flow --from-stage execute --checkpoint .holon/flow/checkpoint-my-task.json

# Skip the calibration stage
./holon flow intents/quick-task.json --skip-calibrate
```

### `holon plan`

Runs the Plan Generator inside a sandboxed container to produce a structured plan branch and markdown file:

```bash
./holon plan I-1784983150-build-execution/_ --agent antigravity-agent --model gemini-3.8-flash-medium
```

### `holon execute`

Runs the Sandbox Executor to implement plan changes and run tests within container containment:

```bash
./holon execute I-1784983150-build-execution/P-1784983200-antigravity-agent/_ --agent antigravity-agent
```

### `holon review-loop` (aliases: `review`, `pr-review-loop`)

Runs the autonomous native review loop on an execution branch or pull request:

```bash
# Review an execution branch with the default three independent reviewers
./holon review-loop I-1784983150-build-execution/P-1784983200-antigravity-agent/E-1784983300-impl/_

# Review a pull request by number or URL with max 10 iterations
./holon review-loop 87 --max-iterations 10 --ensemble-size 3

# Run in dry-run mode without applying changes or pushing commits
./holon review-loop 87 --dry-run
```

Each iteration probes mergeability and CI, then runs `--ensemble-size` (default 3) independent reviewer agent turns on
the real diff between the base and the resolved head SHA. Approval needs every reviewer to approve with zero findings,
plus a mergeable PR and green CI. There is no automatic resolver: the first iteration that is not approved stops the
loop, its findings are recorded in `executions.jsonl` and the command exits 1. Reviewer turns run only inside the
sandbox: `holon review-loop` without `--local`, `--json` or `--dry-run` runs the loop in the `review-loop` role
container, and a host run (`--local`, `--json`, or `holon flow` Stage 4 on the host) refuses with "Refusing to run
reviewer agent turns on the host" before any PR, iteration, reviewer row or post.

The review loop fails closed:

- **Target resolution.** A PR number or GitHub PR URL is resolved through
  `gh pr view <target> --json number,headRefName,headRefOid,baseRefName,title,body` to its head branch, head SHA and
  base branch (`sandbox_executor/review_target.py`); a branch resolves to the mounted worktree's `HEAD`. The container
  entrypoint does this before any git operation, with `gh` learning the repository from the mounted worktree's own
  remote configuration without network access, then validates the mounted worktree in place without cloning or fetching:
  `HEAD` must be a readable commit on the head branch (a branch target refuses a detached `HEAD`) and equal the PR head
  SHA. On any resolution or validation failure it appends one `ACT-REV` failure row to
  `holon-knowledge/ledger/executions.jsonl`, prints a FAILED summary and exits 1 without computing or posting a
  consensus. Limitation: when the workspace has no `holon-knowledge/ledger/` directory, the failure is reported only on
  stderr and through the exit code.
- **Empty-diff guard.** The guard runs for every target, whatever `--auto-pr` is and whether or not `gh` is installed:
  an empty diff is `rejected` and an unverifiable one `failed`, before any PR, iteration or post. Execution branches are
  compared with their plan branch, PRs by head SHA with their own base branch (the intent's target branch when `gh`
  reports none), and other branches with the intent's target branch (`main` by default). With an `origin` remote the
  base and a plain-branch head are fetched first, so a stale or unpushed local copy never decides the verdict; a failed
  fetch or a git command that times out is unverifiable. Without `origin` the local branches are compared.
- **Reviewers.** Zero reviewers, any `FAILED` reviewer (including an unparseable report), any `CHANGES_REQUESTED`
  verdict or any finding (a single nit) blocks approval, as do a dirty PR, an unresolved ref and failing or unknown CI.
- **Posting.** The report is posted only when at least one reviewer completed and no infrastructure step (sandbox check,
  target resolution, diff) failed; otherwise nothing is posted and no `ACT-POST` row is written. It names each
  reviewer's real agent, model, status, verdict and counts (a failed reviewer with its redacted reason), lists every
  finding, and is titled with the intent title, else the PR title, else the PR number or branch. A consensus whose post
  fails or raises is `post_failed`, never approved; the `ACT-POST` failure row is still written.
- **Dry runs.** A dry run measures nothing and dispatches no reviewer, so it reports `not_measured` and is never
  approved.
- **Exit code.** The command exits 0 only when the ensemble verdict is `APPROVED` and the consensus is approved and
  posted; otherwise it exits 1 and prints the reason. The summary prints one line per reviewer. It never merges and
  never enables auto-merge.
- **Bounded waits.** Pending CI checks are re-probed every `CI_REGISTRATION_POLL_SECONDS` (15 s) until they settle or
  the iteration's `review_iteration` budget runs out; checks still pending then count as a CI finding.

#### Host worktree workspace

Every reviewer-ensemble and resolver pass launches a fresh `--rm` role container that works in place on the host
worktree under review; nothing is cloned, fetched, reset or cleaned inside the container. `run_docker_container()`
requires an explicit `target_worktree` for the `review-loop` and `executor` roles and validates it with
`git_flow.sandbox_worktree_mounts()`: it must be an attached, non-bare working tree that does not have the default
branch checked out. Its top-level directory is bind-mounted at the sandbox workspace path
(`agent_runner.SANDBOX_WORKSPACE_MOUNT`, with `HOLON_TARGET_WORKTREE` pinned to the same path). A linked worktree's git
directory and the repository's common git directory are mounted at their host paths, which the worktree's `.git` pointer
names, so git works in the container without network access. The repository `config` (and any `config.worktree`),
`hooks/`, `modules/`, the whole `worktrees/` administrative directory, the whole `objects/info/` directory (`objects/`
stays writable for in-container commits, but everything under `objects/info/` is host-read: `alternates` redirects where
host git reads object data from, and a planted commit-graph would misrepresent ancestry; nothing in-container writes
`objects/info/`), the whole `info/` directory (`exclude` and `attributes` are host-read, and so is any file planted
beside them, such as `info/grafts`, which still subverts host-side ancestry; nothing in-container writes `info/`) and
the `.git`, `gitdir` and `commondir` pointers are overlaid read-only, so the container cannot plant a hook,
configuration, attribute, exclude pattern, graft or commit-graph that host git later reads or runs, cannot touch any
sibling worktree's administrative files (`commondir`, `gitdir`, `HEAD`, a plantable `config.worktree`) and cannot create
new `worktrees/` entries. The common-root ancestry and ref-substitution surfaces are overlaid read-only too:
`refs/replace/` and `packed-refs` (`refs/` itself stays writable -- in-container commits write loose refs, which never
rewrite `packed-refs`; only ref deletion and `pack-refs`/`gc` do, and nothing in-container runs those), `shallow`, whose
entries would cut perceived ancestry exactly like grafts, and the shared rerere cache `rr-cache/`, whose planted
resolutions host git applies silently to the maintainer's next conflicting merge (its mere presence activates rerere
even with no rerere configuration; an in-container conflicting merge against the read-only overlay fails only to record
its preimage, non-fatally, and completes as an ordinary conflicted merge). Absent `hooks/`, `modules/`, `worktrees/`,
`config`, `config.worktree`, `objects/info/`, `info/` and `refs/replace/` are created before overlaying so the container
cannot create them either; an absent `packed-refs` is created with only the standard `# pack-refs with: ...` header
line, which git treats exactly like no packed refs while keeping host `git fsck` free of the `emptyPackedRefsFile`
warning a zero-byte file draws; and an absent `shallow` is created as a read-only _directory_ instead, because an empty
`shallow` file would itself mark the repository shallow, while git treats a `shallow` directory as no shallow file at
all (the only host operation the directory blocks -- turning the full clone into a shallow one later -- fails loudly,
never silently). An absent `rr-cache/` is the inverse: an empty `rr-cache` _directory_ would itself turn host rerere on,
so a zero-length `rr-cache` _file_ is created and overlaid read-only instead -- inert for git, while the one host
operation it blocks, enabling rerere in this clone later, fails loudly, never silently. Where host rerere is already
enabled (`git config rerere.enabled` true) the stand-in file would break the host's merges and `git gc`, so an empty
read-only `rr-cache` _directory_ is created instead (a stand-in file left by an earlier mount is replaced by it);
in-container rerere then simply cannot record (the host's own directory stays writable to the host). The created empty
directory stays behind on the host: if `rerere.enabled` is later unset, its mere presence turns host rerere on (an
explicit `false` still disables it). A non-empty regular file at `rr-cache` with rerere enabled is refused with a
`RuntimeError` asking for the stray file's removal. When the host clone is itself shallow, its existing `shallow` file
is overlaid read-only, so anything that must rewrite it fails loudly: a shallow host clone is effectively unsupported
in-container, and flow worktrees come from full clones. When the mounted worktree is a linked worktree, the common
`HEAD` -- the primary checkout's, which in-container git never needs -- is overlaid read-only as well, along with the
rest of the primary's per-worktree state at the common root, each when it exists: `index`, `FETCH_HEAD`, the transient
`MERGE_HEAD`/`MERGE_MSG`/`MERGE_MODE`/`MERGE_AUTOSTASH`/`ORIG_HEAD`/`CHERRY_PICK_HEAD`/`REVERT_HEAD`/`SQUASH_MSG`/`AUTO_MERGE`
files, the mid-merge rerere map `MERGE_RR` (per-worktree state: the primary's lives at the common root, while a
sibling's copy is already covered by the `worktrees/` overlay), the mid-rebase `REBASE_HEAD` file and `rebase-merge/`
and `rebase-apply/` directories (whose `git-rebase-todo` `exec` lines host `git rebase --continue` executes), the
`sequencer/` directory (whose `todo` redirects a host `cherry-pick/revert --continue`) and the `BISECT_*` bisect-state
files. A created-empty stand-in would not be inert -- git rejects a zero-length index, treats an empty `MERGE_HEAD` as
an in-progress merge, reports a rebase in progress (and refuses to start one) over an empty `rebase-merge/` or
`rebase-apply/` directory, and fails a multi-commit cherry-pick or revert over a pre-existing empty `sequencer/` -- so
state absent at mount time stays plantable, the documented accepted residual, which covers these directories as well as
the transient files; a mounted primary checkout's own `HEAD`, `index` and `FETCH_HEAD` stay writable for its commits and
fetches. Only the mounted worktree's own administrative directory is re-mounted read-write on top (docker layers the
deeper mount destination over the shallower one), restoring exactly the writes its own git needs (`HEAD`, `index`,
`FETCH_HEAD`) while its pointer files stay read-only through still-deeper overlays; the container sets its git identity
in its own global config. A repository with `extensions.worktreeConfig` enabled is refused, because git would then read
every worktree's `config.worktree` from the container's own writable git directory. The image runs as uid 1000 and a
Linux bind mount keeps host ownership, so on Linux the worktree, its git directory and the common git directory must be
writable by uid 1000 (as they are when the host user is uid 1000); otherwise `sandbox_worktree_mounts()` refuses the
worktree before any container starts. On a host whose user has another uid (for example 1001 on CI runners), grant uid
1000 write access, e.g. `setfacl -R -m u:1000:rwX,d:u:1000:rwX <path>`; the preflight honours a named-user ACL entry for
uid 1000 (limited by the ACL mask). Docker Desktop on macOS and Windows maps ownership, so the check is Linux-only. The
resolver stages its fixes without `holon-knowledge/ledger/executions.jsonl`, and a pass the host dispatches with
`--result-file` appends its ledger rows to container scratch: the host loop owns the worktree's ledger and records every
pass from the result file. The entrypoint only validates the mount: HEAD must be a readable commit on the review branch
(a branch target refuses a detached HEAD) and equal the resolved PR head SHA, otherwise preparation fails closed. Edits,
logs and reviewer scratch under `tmp/review/` land on the host at once and survive a container crash. No Docker volume
is created and no root init container runs.

Every stage works the same way on the run's `flow-<slug>` worktree, with the same protections for all four roles (the
Stage 1 intent creator, Stage 2 planner, Stage 3 executor and Stage 4 review-loop and resolver). No role container ever
runs `git clone`, `fetch`, `pull`, `push`, `ls-remote` or `reset` against a remote; roles only commit in the mounted
worktree. The host, with its own credentials, fetches the base before each container starts (the target branch for Stage
1, the intent branch for Stage 2, the plan branch plus the target branch for Stage 3, the plan and execution branches
for Stage 4's diff check) and pushes the committed branch after the container returns: Stage 1 pushes the intent branch,
Stage 2 the new plan branch, Stage 3 every new execution branch (including a failed one so a resume can find it), and
Stage 4's resolver commits in the container while the host pushes that head, fast-forward only and never forced. This
lives in `flow/host_remote.py` (`fetch_base`, `push_branch`, `push_commit`, `publish_new_child_branches`,
`HostRemoteError`) and, for the standalone commands, `cli/commands/_host_publish.py`: `holon intent`, `holon plan` and
`holon execute` fetch on the host first and push the checked-out branch after a successful container exit. No stage
depends on an `ssh-agent`, SSH key, `known_hosts` or the origin URL scheme inside a container, so a worktree whose
`origin` is an SSH URL passes all four stages. When the host cannot fetch or push, the stage error names the operation,
the branch and the remote with credentials stripped, for example
`host push of '<branch>' through remote 'origin' failed: <detail>`; a failed host push leaves the commits on the host's
local branch.

For the Stage 3 executor, the host fetches the plan branch (and any partial execution branch a resume continues) into
the worktree before dispatch: `holon flow` through its existing fetch of the plan's branches, and `holon execute`
through `git_flow.fetch_origin_branch()`, after provisioning or reusing the plan's `flow-<slug>` worktree when it is
given a default-branch checkout. The entrypoint (the `entrypoint/executor/` package) recognises the mount by
`HOLON_TARGET_WORKTREE` naming the sandbox workspace path and then never clones, fetches, resets or cleans: it requires
a readable `HEAD`, a worktree without uncommitted changes (edits left by a crashed run stay on the host and are never
swept into a new execution) and a plan branch that already resolves, creates the execution branch from the plan tip (or
the resumed partial head) in the host worktree, writes its git identity to the container's global config; it does not
push. A repository that is unusable after the agent ran fails the run instead of being re-initialised. Commits and edits
are on the host at once, so a crash, OOM kill or timeout loses nothing. The intent creator and planner mount the same
worktree. A pass that runs `uv run` in the mounted worktree uses the container-private environment
`UV_PROJECT_ENVIRONMENT=/home/holon/.cache/holon-venv`, so it never rebuilds the host's `.venv`.

`holon review-loop <target>` resolves the worktree on the host: the `--target-worktree` when it has the review branch
checked out, otherwise the existing worktree of that branch (normally the run's `flow-<slug>`), otherwise, for a PR, a
new `pr<N>-<slug>` worktree of its head branch (`git_flow.resolve_review_worktree()`); a branch target with no worktree
is refused. `git_flow.ephemeral_reviewer_worktree()` provides `flow-<slug>-rev<i>` sibling worktrees, removed and pruned
on exit, for reviewer turns that need an isolated checkout. The review loop does not call it yet: reviewer turns write
only their report and run no test suite of their own, so every reviewer reads the shared mounted worktree.

#### Reviewer ensemble

`sandbox_executor/review_ensemble.py` holds the reviewer ensemble that `run_review_loop()` calls once per iteration (the
`run_ensemble_fn` keyword is the test seam).
`build_review_request(target, base_ref, iteration, pr_title, pr_body, repo_dir)` builds the request from
`git diff <base>...<head_sha>` without the ledgers, plans and execution notes; a failing diff fails the iteration.

- **Public API.** `run_ensemble(request, *, agent, model, size=DEFAULT_ENSEMBLE_SIZE, **seams)` runs `size` reviewers
  (default `DEFAULT_ENSEMBLE_SIZE`, 3) one after another through `run_reviewer(...)` and returns one `ReviewerResult`
  per reviewer. `decide_consensus(results, configured_size, mergeable, ci_green)` returns `APPROVED`,
  `CHANGES_REQUESTED` or `FAILED`. `parse_review_trailer(text)` and `render_review_trailer(parsed)` read and write the
  trailer; `serialize_review_outcome(reviewers, consensus_verdict)` and `deserialize_review_outcome(data)` transport
  verified outcomes across the container boundary; `build_reviewer_prompt(template_text, request, report_rel)` fills
  `holon-config/prompts/review_loop.template.md`.
- **Reviewer turns.** Each reviewer runs in a fresh scratch directory `tmp/review/iter-<n>/reviewer-<i>/` (git-ignored)
  under the same turn supervision as the planner (`supervise_authoring_turn`), within one shared `review_iteration`
  budget per ensemble, and ephemeral credentials are cleaned up in a `finally`. Its prompt holds only the diff, the PR
  description, its report path and the trailer contract. Before the next reviewer starts, the previous report has been
  read into memory and its directory removed, so no reviewer sees another's output. A runner exception (including an
  unknown agent), a budget breach on any turn, a missing report or an invalid trailer makes the reviewer `FAILED` with a
  redacted reason; a `FAILED` reviewer carries no verdict and is never counted as a vote.
- **Trailer contract.** A report ends with a `<!-- holon-review-trailer -->` line, one JSON object with exactly the keys
  `verdict` (`APPROVED` or `CHANGES_REQUESTED`), `critical`, `important`, `nit` and `findings` (objects with `severity`,
  `file`, `line` and `summary`), and a final `<!-- /holon-review-trailer -->` line. Counts must equal the findings per
  severity, `APPROVED` with any non-zero count is rejected, and a key repeated in any object is rejected rather than
  resolved to its last value, so an inconsistent report fails closed.
- **Unanimity rule.** `FAILED` when no reviewer was dispatched, any reviewer failed, or the number of results differs
  from the configured size. `CHANGES_REQUESTED` when any reviewer requested changes or reported any finding (a single
  nit blocks), or mergeability or CI is not known to be clean. `APPROVED` only otherwise: there is no majority vote and
  zero completed reviewers can never approve. Approval never merges; merging stays with the human maintainer.

### `holon calibrate`

Runs post-execution calibration on an execution branch without rerunning tests:

```bash
./holon calibrate I-1784983150-build-execution/P-1784983200-antigravity-agent/E-1784983300-impl/_
```

### `holon consult`

Consults historical knowledge records and wisdom using multi-label queries and full-text search:

```bash
# Conjunctive query: match records with all specified labels
./holon consult --all python git

# Disjunctive query with full-text search
./holon consult --any cli docker -q "permission denied" --top-k 5

# JSON formatted query output
./holon consult --all test pytest --json
```

### `holon record`

Records a discrete action event to the executions ledger with automatic credential scrubbing:

```bash
./holon record --action "Applied fix for timeout jitter" --category test --verdict success --labels "test,ci"
```

### `holon reconcile`

Reconciles pending action outcomes and expires stale unconfirmed records:

```bash
# Reconcile specific action outcome
./holon reconcile --action-id ACT-1792384779-621819d7 --verdict success --reason "Verified in test run"

# Expire stale records older than TTL days
./holon reconcile --expire-ttl 7
```

### `holon promote`

Evaluates qualified knowledge candidates and promotes them to the Knowledge Base (KB) or Wisdom Base (WB):

```bash
# Dry-run promotion evaluation
./holon promote --dry-run --min-trials 3

# Promote all qualified pending candidates
./holon promote --pending
```

### `holon veto`

Inspects and manages deterministic failure vetoes to prevent repeated failures:

```bash
# List active and shadowed failure vetoes
./holon veto list

# Lift a failure veto by context fingerprint
./holon veto lift --context 7d7d77ee670a4c23 --reason "Upstream bug fixed in v1.2"
```

### `holon stats`

Displays operational self-metrics and consult telemetry:

```bash
# Display telemetry table
./holon stats

# Output telemetry in structured JSON
./holon stats --json
```

### `holon ledger query`

Queries and filters immutable audit ledger entries:

```bash
# Query failures in executions ledger
./holon ledger query --outcome failure --category bugfix

# Query with free-text keyword
./holon ledger query --query "merge conflict" --limit 10
```

---

## 5. Development Workflows & Testing

All commands must be executed strictly from the repository root using `uv`:

### Running Tests

```bash
# Run unit tests (excluding integration and stress)
uv run task test

# Run full pytest suite
uv run pytest apps/sandbox-executor/tests

# Run a specific test file
uv run pytest apps/sandbox-executor/tests/test_flow.py
```

### Code Formatting & Linting

```bash
# Run complete validation checks (ruff + prettier)
uv run task check

# Automatically format Python and markdown files
uv run task format
```
