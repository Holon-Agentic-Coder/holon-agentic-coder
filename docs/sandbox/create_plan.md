# Running Plan Generation via Docker

This guide explains how to run the Plan Generation role using the Docker sandbox environment.

The Planner initializes a new plan branch off of the active intent branch, compiles a prompt using system templates and
intent metadata, runs the AI agent (e.g.,`pi`) to write a structured markdown plan, extracts predicted metrics, logs
them to the plans ledger `holon-knowledge/ledger/plans.jsonl`, and pushes the new plan branch to the remote repository.

---

## Prerequisites

Before running the command, ensure you have:

1. Built the Docker images using the build script:
   ```bash
   ./apps/sandbox-executor/build_all_images.sh
   ```
2. Configured git access to GitHub on your host machine. The host fetches and pushes with its own credentials; the
   container never contacts the remote.
3. Created an intent branch (e.g., `I-1771890389-refactor-metrics/_`) and registered it in the intent ledger.

---

## Recommended Execution Methods (Holon CLI)

> [!IMPORTANT] **Use the Holon CLI instead of raw `docker run` commands.** The CLI automatically manages image mapping,
> credential passing, and the worktree mount.

You can run Plan Generation via any of the following standard pathways:

### Option A: Globally Installed Tool (`uv tool`)

If installed globally via `uv tool install ./apps/sandbox-executor` (from repository root) or
`uv tool install "git+https://github.com/Holon-Agentic-Coder/holon-agentic-coder.git@main#subdirectory=apps/sandbox-executor"`
(can pin `@main`, release `@<tag>`, or immutable `@<commit-sha>`):

```bash
holon plan "I-1782654790-bootstrap-holon-cli-intent/_" --agent pi-agent --model gemini-3.8-flash
```

### Option B: Ephemeral Execution (`uvx`)

Run on-demand without prior installation:

> [!WARNING] **Always specify `--from` when invoking via `uvx`.** Do not execute bare `uvx holon`. The package name
> `holon` is registered on public PyPI by an unrelated third-party project; omitting `--from` introduces a dependency
> confusion and namespace collision risk where `uvx` will attempt to download and run the third-party PyPI package.
> Additionally, local path invocations (`uvx --from ./apps/sandbox-executor`) must be run from the repository root.

```bash
# Run from local repository clone (must be run from repository root)
uvx --from ./apps/sandbox-executor holon plan "I-1782654790-bootstrap-holon-cli-intent/_" --agent pi-agent --model gemini-3.8-flash

# Or directly from remote Git repository (can pin @main, release @<tag>, or immutable @<commit-sha>)
uvx --from "git+https://github.com/Holon-Agentic-Coder/holon-agentic-coder.git@main#subdirectory=apps/sandbox-executor" holon plan "I-1782654790-bootstrap-holon-cli-intent/_" --agent pi-agent --model gemini-3.8-flash
```

### Option C: Repository Convenience Script (`./holon`)

Run from the repository root using the [`./holon`](../../holon) host wrapper:

```bash
./holon plan "I-1782654790-bootstrap-holon-cli-intent/_" --agent pi-agent --model gemini-3.8-flash
```

---

## Optional: Token Reduction Proxy (`--token-reduce`)

Pass `--token-reduce` to route the planner sandbox's HTTP(S) egress through a locally-owned holon-coherence proxy
sidecar, so responses can be compacted before the agent spends tokens on them.

```bash
./holon plan "I-1782654790-bootstrap-holon-cli-intent/_" --agent pi-agent --model gemini-3.8-flash --token-reduce
```

> [!WARNING] **`--token-reduce` is experimental.** The sidecar runs the holon-coherence proxy image and the mitmproxy
> addon that image ships: `holon-coherence:latest` when built locally with `holon-coherence build`, otherwise
> `ghcr.io/holon-agentic-coder/holon-coherence:latest`, pulled on first use (`HOLON_COHERENCE_IMAGE` pins another
> image). If the image cannot start or its proxy never answers, the CLI logs an actionable error and the run continues
> with **direct egress**: no interception takes place.

> [!WARNING] `--token-reduce` performs **local TLS interception**. A Holon Root CA is generated at
> `~/.holon/certs/holon-root-ca.crt` (with `basicConstraints=critical,CA:TRUE` and
> `keyUsage=critical,keyCertSign,cRLSign`, and rotated automatically once it would expire within 30 days) so it can
> decrypt and re-encrypt agent traffic.
>
> **Trust mechanism (merged bundle, not `update-ca-certificates`)**: the sandbox image runs as the unprivileged `holon`
> user, so the Debian trust store can never be refreshed. Instead the entrypoint concatenates the image's system store
> (`/etc/ssl/certs/ca-certificates.crt`) with the Holon CA into `/home/holon/.holon-ca-bundle.crt`, and `SSL_CERT_FILE`
> / `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE` point at that merged file — those variables _replace_ the trust store, so
> pointing them at the single-cert Holon mount would break every legitimate HTTPS endpoint. `NODE_EXTRA_CA_CERTS` points
> at the Holon CA alone because it _augments_ Node's built-in roots.
>
> **Key exposure**: a MITM proxy inherently requires the CA **private key** to sign forged leaves, so
> `~/.holon/proxy-ca/mitmproxy-ca.pem` (key + cert, mode `0600`) and `mitmproxy-ca-cert.pem` are mounted **read-only
> into the proxy sidecar only** (`/home/mitmproxy/.mitmproxy`). The private key is never mounted into the _agent_
> container, which only ever receives the public certificate.
>
> **Retention / redaction posture**: the proxy cache (`~/.holon/proxy-cache`) is mounted read-only into the sidecar and
> sidecar logs are size-bounded, but **no credential redaction is implemented yet** (Phase 2). `--token-reduce` must
> therefore only be used against a locally-owned proxy.

- **Prerequisites**: the `docker` and `openssl` host binaries. If either is missing, the addon script is absent, the
  sidecar fails to start, or it never becomes ready, the CLI logs an actionable error and the run continues with
  **direct egress** — a dead proxy is never injected into the sandbox.
- **Isolation**: the sidecar runs on a per-run Docker network (`holon-net-<pid>-<uuid>`), is capped at
  `--memory=256m --cpus=0.5` with bounded log rotation, and both it and its network are removed when the run finishes
  (on every exit path, including early failures while assembling the `docker run` command).

### Environment contract

| Variable             | Effect                                                                                                                                                    |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `HOLON_TOKEN_REDUCE` | Opt-in without the flag (`1`, `true`, `yes`, `on`). Attaches to an already-running proxy; never starts one.                                               |
| `HOLON_PROXY_URL`    | Proxy URL used in the `HOLON_TOKEN_REDUCE` path. Defaults to the host gateway (`host.docker.internal:8080` on macOS/Windows, `172.17.0.1:8080` on Linux). |

> [!IMPORTANT] Host `HTTP_PROXY` / `HTTPS_PROXY` are **never** interpreted as opt-in. Sandbox networking is only changed
> when you pass `--token-reduce` or set `HOLON_TOKEN_REDUCE` explicitly.

---

## Low-Level Execution (Manual `docker run`)

If you need to invoke Docker manually, run the following command to start the planner container, replacing arguments as
needed:

```bash
docker run --rm \
  -e HOLON_ROLE=planner \
  -v <worktree>:/home/holon/.holon-sandbox/workspace \
  -e HOLON_TARGET_WORKTREE=/home/holon/.holon-sandbox/workspace \
  holon/agent-pi \
  "I-1782654790-bootstrap-holon-cli-intent/_" \
  "pi-agent" \
  "gemini-3.8-flash"
```

The container never runs git against a remote. The host, with its own git credentials, fetches the base before the
container starts and pushes the committed branch after it returns, so no SSH key or agent is needed for the container.

### Argument Breakdown:

- **`-e HOLON_ROLE=planner`**: Routes the entrypoint call to the Python planner module (`planner.py`) inside the
  container.
- **`-v <worktree>:/home/holon/.holon-sandbox/workspace`** and
  **`-e HOLON_TARGET_WORKTREE=/home/holon/.holon-sandbox/workspace`**: Bind-mounts the flow's host-provisioned
  `flow-<slug>` worktree at the sandbox workspace path. The planner works in place there and only commits; it never
  clones, fetches or pushes. The host fetches the base before the container starts and pushes the committed branch after
  it returns, using the host's own git credentials. Because no git command inside the container contacts a remote, no
  `ssh-agent`, SSH key or credential-helper is mounted or needed.
- **`holon/agent-pi`**: The name of the built Docker image for the chosen agent (e.g., `holon/agent-pi`,
  `holon/agent-claude`).
- **Container Arguments:**
  1. **`intent_branch`** (e.g., `"I-1771890389-refactor-metrics/_"`): The target intent branch to branch off from.
  2. **`agent_name`** (e.g., `"pi-agent"`): The name of the planning agent.
  3. **`model_name`** (e.g., `"gemini-3.8-flash"`): The model used for planning.

---

## 2. Execution Details & Fail-Fast Behavior

During execution, the host first fetches the intent branch into the mounted flow worktree, and the container then
performs the following operations in that worktree (it never reaches the remote):

1. Works in the mounted worktree checked out at the provided `intent_branch`.
2. Creates an ephemeral plan branch: `I-{timestamp}-{slug}/P-{plan_timestamp}-{agent_name}-{model_name}/_`.
3. Compiles a detailed prompt using the template in `holon-config/prompts/planner.template.md` combined with the intent
   data from the ledger.
4. Invokes the AI planning agent under the turn, stall and stage budgets (see [Turn and Stage Budgets](../budgets.md)).
   The agent writes the plan file itself, incrementally (see below).
5. Validates the plan file and parses its overall metrics (`p_success`, `entropy`, `impact`, `cost`, `learning_value`,
   `ev`). It fails, before anything is appended or committed, when the metrics are missing or non-numeric; metrics are
   never back-filled with defaults.
6. Appends a structured plan entry to `holon-knowledge/ledger/plans.jsonl`.
7. Commits the changes and prints `plan: ok (elapsed min / 35 min)`. The host then pushes the plan branch to origin.

### Incremental plan artefact

The plan used to arrive in one final stdout message, so a final message cut off by the output-token limit lost the whole
plan. The file on disk is now the source of truth:

- **Write protocol.** `holon-config/prompts/planner.template.md` and the closing prompt instruction require the agent to
  create its plan file (`plans/{intent}/{plan_id}.md`, the `{plan_file}` placeholder) with its file-write tool
  immediately, write the header and the `## Overall Plan Metrics` table first, append each `## Step` section as it is
  finished, then the strategy rationale, justification and safety sections, and keep its final chat message to one line.
- **Validation.** A plan file is _usable_ when it exists, is at least 800 bytes and the metric rows of its
  `## Overall Plan Metrics` section (up to the next `## ` heading, wherever the section sits) have numeric values for
  the five input metrics (`p_success`, `entropy`, `impact`, `cost`, `learning_value`); an `ev_pred` row is optional
  because the planner recomputes EV from them. Without that heading every metric row in the file counts and the last one
  wins. The values the validator accepts are exactly the ones written to `plans.jsonl`: the validator, the planner and
  `holon flow` share one parser (`artefacts.plan_metrics`), so a step table can never stand in for the overall values.
  It is _complete_ when it also has a `# Plan for` title, an `## Overall Plan Metrics` heading, at least one `## Step`
  section and a `### Strategy Rationale` (or justification) section. Headings match case-insensitively.
- **Truncation tolerance.** When the plan file is usable, a non-zero exit code or a `stopReason: length` final message
  is logged as informational and the plan is committed.
- **Targeted re-prompt.** When the file exists but is incomplete and the plan stage still has time left, exactly one
  follow-up turn is issued that lists only the missing sections (and any missing metric rows) and asks the agent to
  append them.
- **Stdout fallback.** Only when the agent wrote no file at all is the plan taken from captured stdout (a fenced
  `markdown` block if present). This logs
  `WARNING: agent did not write plans/I-.../P-...; falling back to the plan text captured from stdout`, and the result
  is validated like any other plan.
- **Budget failure.** If a turn breaches a budget and no resume turn (attempted only when the salvaged plan is
  incomplete, that is not usable or missing a required section, and the stage has time left) produces a usable plan, an
  `ACT-BUDGET` action is recorded and committed together with the salvaged partial plan, and the planner exits non-zero
  (see [Turn and Stage Budgets](../budgets.md#salvage-resume-and-the-act-budget-record)).

`holon flow` launches this same planner container for Stage 2 and then adopts the plan branch it pushed. If no planner
agent produced `plans/I-.../P-*.md` on a plan branch under the intent branch, the flow stage fails; it never writes a
placeholder plan of its own.

### Agent API errors

A planner run once received an Anthropic API 400 and the stdout fallback wrote that single error line as the plan; the
metrics check then hid the real error behind `missing or non-numeric overall metrics`. API failures now surface as API
failures:

- **Detection.** When the agent left no usable plan file, every turn's stdout and stderr is checked for an API error
  (`agent_api_error.py`): an `API Error:` line, an HTTP 4xx or 5xx status line, or an error JSON body (including the
  claude CLI's `is_error` result). Detection is anchored to the first line of the output (any line of a short output
  without a `# Plan for` title), so a valid plan that quotes an API error is never rejected.
- **Failure.** The planner exits non-zero with `Error: the planner agent's API call failed: ...` (credentials redacted,
  at most 2000 characters). This is checked before the budget and metrics checks, and the stdout fallback never writes
  API-error text to the plan path.
- **No stray plan file.** Any failed attempt removes its own plan file when `git status` reports it as untracked; a
  tracked or staged plan file is never touched. The host side does the same in Stage 2: it snapshots the untracked plan
  files under `plans/{intent}/` before dispatching the planner and, after a failed dispatch, removes only the new ones,
  so a killed container also leaves a clean worktree (`plan_attempt.py`).
- **Stage error.** The planner writes the failure text to the git-ignored `.holon/plan/failure.txt`; Stage 2 reads and
  removes it and fails with `Plan stage failed: <failure text> (planner agent exited with code N)`.
- **No ledger row.** An API failure writes no ledger row; the ledgers stay `intents.jsonl`, `plans.jsonl` and
  `executions.jsonl`.

The token-reduction proxy (`--token-reduce`) can cause that 400 by adding a plain (5m) `cache_control` block before the
claude CLI's own `ttl='1h'` blocks. Token reduction is owned by the holon-coherence sidecar, whose payload cleaner
injects up to 4 plain `cache_control` blocks (tools, system, last message) with no ttl-order check, so the 400 is a
known issue for `claude-agent --token-reduce` runs until holon-coherence injects only ttl-consistent blocks. Every
sidecar start logs a warning about it; re-run without `--token-reduce` if a planner call fails this way.

---

## 3. Verification

Once execution completes, you should verify:

1. A new remote plan branch exists:
   ```bash
   git fetch origin && git branch -r
   # e.g., origin/I-1771890389-refactor-metrics/P-1772691068-PIA-G3.8F/_
   ```
   > [!NOTE] The branch and plan identifiers carry the ticker of the `agent_name` argument passed to the container
   > (`PIA` for `pi-agent`, `CLA` for `claude-agent`, `ANA` for `antigravity-agent`) and the model code derived from the
   > model name; the `plans.jsonl` row keeps the full agent and model.
2. The generated plan markdown file is present under `plans/`:
   ```markdown
   # plans/{intent}/{plan_id}.md, e.g. plans/I-1771890389-refactor-metrics/P-1772691068-PIA-G3.8F.md
   ```
3. A plan entry is appended to `holon-knowledge/ledger/plans.jsonl` with parsed metrics and expected value (EV)
   calculations:
   ```json
   {
     "plan_id": "P-1772691068-PIA-G3.8F",
     "intent_branch": "I-1771890389-refactor-metrics/_",
     "agent": "pi-agent",
     "model": "gemini-3.8-flash",
     "p_success": 0.7,
     "entropy": 2.5,
     "impact": 1.0,
     "cost": 0.5,
     "learning_value": 0.0,
     "ev": 0.45,
     "created_at": "2026-06-27T13:14:15.000Z",
     "plan_file": "plans/I-1771890389-refactor-metrics/P-1772691068-PIA-G3.8F.md",
     "status": "proposed"
   }
   ```
