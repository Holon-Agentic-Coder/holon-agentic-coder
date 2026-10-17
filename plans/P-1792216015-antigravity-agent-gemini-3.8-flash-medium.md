# Plan for I-1792215929-update-docker-agent-images-and-build-bake

- **Plan ID:** P-1792216015-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-17T05:46:55.331Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with empirical discovery of agent runner execution parameters, non-interactive flags,
  browser dependencies, and tmpfs overlay isolation semantics under read-only container policies.
- **Safety priority level:** elevated
- **Priority Justification:** Triggered by `docs/safety.md` §1 (Git as safety boundary), §2 (Mandatory execution
  sandboxing), `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: container isolation, network
  containment, read-only root filesystems, strict credential boundary enforcement), `holon-config/world/constraints.md`
  §3 (Ledger Immutability: zero modification policy for append-only archives), and `holon-config/world/ruleset.md` §1,
  §2, §3 (Python `==3.13.*`, workspace layout under `apps/sandbox-executor`, PEP 8, static typing, docstring integrity,
  hermetic test boundary). Elevated safety ensures that writable tmpfs overlays for
  `/home/holon/.gemini/antigravity-cli/log` and `/cache` strictly preserve credential isolation without leaking host
  secrets or exposing write access to host authentication tokens.

## Exploration

- **Proportion of steps that are exploratory:** 0.17
- **Justification:** Step 1 performs a focused exploratory audit of package changelogs, non-interactive execution
  switches, model resolution overrides, browser driver dependencies, and npm cache mounts across all five upgraded agent
  CLIs prior to committing Dockerfile modifications, preventing runtime regressions during image builds.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.40  |
| impact_pred         | 94.0  |
| cost_pred           | 12.5  |
| learning_value_pred | 5.6   |
| ev_pred             | 77.60 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.93. Governed by the critical bottleneck step (Step 4: 0.93), which builds and loads all eight
  Docker container targets using Docker Buildx Bake concurrently. Network package fetching, npm compilation steps, and
  local Docker daemon ingestion represent the primary failure surface.
- **entropy_pred**: 0.40. Evaluated as the maximum single-step risk profile (Step 4: 0.40), where multi-stage Buildx DAG
  evaluation, npm cache mount binding, and image export introduce concurrency and cache interaction entropy. The
  cumulative sum of predicted step entropies across all six steps is 1.65
  (`0.30 + 0.35 + 0.25 + 0.40 + 0.20 + 0.15 = 1.65`), remaining well within the allocated entropy budget of 15.0.
- **impact_pred**: 94.0. High architectural impact concurrently fulfilling Bean 0112 (upgrading all agent runner
  packages to latest stable releases and introducing writable tmpfs overlays for logs and caches under read-only
  container policies) and Bean 0114 (full Buildx Bake image compilation, daemon loading, container smoke verification,
  and comprehensive build performance documentation).
- **cost_pred**: 12.5. Calculated as the direct sum of predicted step costs
  (`1.6 + 2.2 + 2.0 + 3.5 + 1.8 + 1.4 = 12.5`).
- **learning_value_pred**: 5.6. Substantial epistemic gain delivered to the system through empirical discovery of agent
  CLI flags, BuildKit caching characteristics across multi-stage targets, and secure tmpfs permission scoping
  (`uid=1000,gid=1000`) for host session mounting.
- **ev_pred**: 77.60. Derived using the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system coefficients
  `lambda = 0.3` and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.93 * 94.0 + 0.5 * 5.6 - 0.3 * 0.40 - 12.5 = 87.42 + 2.80 - 0.12 - 12.5 = 77.60`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: strict prefix isolation, commit scoping to
    active step branch, mandatory rebase discipline).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: container sandbox with read-only root, no network
    at runtime, resource constraints, zero credential leakage).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modifications to
    `holon-knowledge/ledger/*.jsonl`).
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python `==3.13.*`, uv workspace
    management).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8, static typing annotations, docstring
    preservation).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: pytest 9.1.1, test hermeticity, preserve test boundaries in
    `apps/sandbox-executor/tests/`).
  - `docs/safety.md` §1 & §2 (Git as safety boundary, blast radius containment, mandatory sandboxing).
  - Intent Q1 Answer: `@anthropic-ai/claude-code@2.1.302`, `@openai/codex@0.163.0`, `@google/gemini-cli@0.64.0`,
    `opencode-ai@1.19.3`, and `@earendil-works/pi-coding-agent@1.3.0`.
  - Intent Q2 Answer: Docker `--tmpfs` mounts with `uid=1000,gid=1000` targeting
    `/home/holon/.gemini/antigravity-cli/log` and `cache`.
  - Intent Q3 Answer: Build using `apps/sandbox-executor/build_all_images.sh` or
    `docker buildx bake -f apps/sandbox-executor/docker-bake.hcl --load`.
- **Potential violations or edge cases:**
  - Accidental exposure of host credentials or API tokens when mounting host directories into the container sandbox.
  - Making host session directories writable directly rather than using tmpfs overlays, violating read-only host mount
    policies.
  - Incompatible CLI invocation syntax or interactive prompt stalls introduced by newer agent CLI releases.
  - Build failure or cache invalidation cascade when building multiple agent targets under Docker Buildx Bake.
  - Regressing test suites in `apps/sandbox-executor/tests/test_cli.py` that enforce read-only directory mount checks.
- **Mitigations built into the plan:**
  - Restrict writable mounts strictly to in-memory `--tmpfs` overlays (`/log` and `/cache`) with ownership
    `uid=1000,gid=1000`, preserving host credential isolation and read-only `:ro` host directory mounts.
  - Validate non-interactive flags, telemetry disable switches, and model override flags during discovery before
    finalising image configurations.
  - Test Docker Buildx Bake both with and without `--no-cache` to ensure reproducible compilation.
  - Update unit test assertions in `test_cli.py` to assert both the `-v ...:ro` mount and the `--tmpfs` overlay
    arguments for both macOS and Linux code paths.
  - Execute full quality verification (`make lint`, `uv run pytest`, `ruff check`, `ruff format --check`,
    `prettier --check`) to prevent regressions.
- **Residual risk accepted (and why):**
  - Upstream npm package download latencies during local image building; accepted because Buildx Bake leverages caching
    mounts (`--mount=type=cache,id=npm-...`) to minimise repeated fetch overhead.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 1.65
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

This plan implements Bean 0112 and Bean 0114 across `apps/sandbox-executor`:

1. **Bean 0112 (Agent Runner Package Upgrades & Writable Tmpfs Mounts):**
   - Audit and upgrade agent CLI packages in `apps/sandbox-executor/Dockerfile` to targeted releases:
     `@anthropic-ai/claude-code@2.1.302`, `@openai/codex@0.163.0`, `@google/gemini-cli@0.64.0`, `opencode-ai@1.19.3`,
     and `@earendil-works/pi-coding-agent@1.3.0`.
   - Verify non-interactive execution arguments, model resolution overrides, browser driver configurations, and npm
     allow-scripts configurations in the Dockerfile.
   - Update `get_agent_session_mounts` in `apps/sandbox-executor/src/sandbox_executor/cli.py` to supply writable
     `--tmpfs` mounts for `/home/holon/.gemini/antigravity-cli/log:uid=1000,gid=1000` and
     `/home/holon/.gemini/antigravity-cli/cache:uid=1000,gid=1000` alongside the existing read-only host session mount
     (`:ro`).
   - Update corresponding unit tests in `apps/sandbox-executor/tests/test_cli.py` to assert the presence and exact
     parameters of these tmpfs overlays while ensuring read-only assertions remain valid.

2. **Bean 0114 (Build All Images with Buildx Bake, Smoke Checks & Docs):**
   - Execute Docker Buildx Bake via `apps/sandbox-executor/build_all_images.sh` or
     `docker buildx bake -f apps/sandbox-executor/docker-bake.hcl --load` to build and load all eight images:
     `holon/base`, `holon/agent-antigravity`, `holon/agent-claude`, `holon/agent-codex`, `holon/agent-gemini`,
     `holon/agent-opencode`, `holon/agent-pi`, and `holon/orchestrator`.
   - Run container smoke checks: inspect image creation timestamps with `docker images` and `docker inspect`, verify
     entrypoint executor help menus, and verify Python importability of `sandbox_executor` modules within built images.
   - Update `apps/sandbox-executor/docs/docker_bake.md` to document build benchmarks, caching considerations, and tmpfs
     overlay architectures.

3. **Goal 5 (Verification & Quality Gates):**
   - Verify that `make lint`, `uv run pytest apps/sandbox-executor/tests -m 'not integration_test and not stress'`,
     `uv run ruff check .`, `uv run ruff format --check .`, and `npx prettier --check '**/*.md'` all pass cleanly.

---

## Step 1: Audit agent runner package releases, CLI flags, and dependencies (Bean 0112 Discovery)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Focused informational exploration step to verify release notes, non-interactive flags, and dependency
  requirements without modifying code.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The targeted agent runner releases (`@anthropic-ai/claude-code@2.1.302`,
  `@openai/codex@0.163.0`, `@google/gemini-cli@0.64.0`, `opencode-ai@1.19.3`, `@earendil-works/pi-coding-agent@1.3.0`)
  maintain backward compatibility with non-interactive CLI flags, model override flags, and npm allow-scripts
  directives.
- **Learning target:** Specific execution flags, environment variables, npm script execution permissions, and browser
  driver requirements across all five upgraded CLI runners.
- **Maximum acceptable cost for this learning:** 2.0 cost units.

### Intent & Git Integration

- **Step Intent:** Audit targeted agent package versions against current Dockerfile layers, verify required npm
  permissions, and inspect CLI switches for non-interactive execution.
- **Git branch:**
  `I-1792215929-update-docker-agent-images-and-build-bake/P-1792216015-antigravity-agent-gemini-3.8-flash-medium/E-1792216030-audit-runner-packages/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Review each target package specification and version requirement:
   - Claude Code: `@anthropic-ai/claude-code@2.1.302` (upgrading from `2.1.247`).
   - Gemini CLI: `@google/gemini-cli@0.64.0` (upgrading from `0.57.0`).
   - OpenCode AI: `opencode-ai@1.19.3` (upgrading from `1.18.23`).
   - Pi Agent: `@earendil-works/pi-coding-agent@1.3.0` (upgrading from `0.84.3`).
   - Codex Agent: `@openai/codex@0.163.0` (upgrading from `0.150.1`).
2. Audit `.npmrc` allow-scripts configurations in `apps/sandbox-executor/Dockerfile`:
   - Verify if `@anthropic-ai/claude-code` requires script lifecycle hooks.
   - Verify if `@google/gemini-cli` requires `@github/keytar` or `node-pty`.
   - Verify if `opencode-ai` requires `allow-scripts=opencode-ai`.
   - Verify if `@earendil-works/pi-coding-agent` requires `@google/genai` and `protobufjs`.
   - Verify if `@openai/codex` requires script lifecycle permissions.
3. Review agent runner invocation contracts in `apps/sandbox-executor/src/sandbox_executor/agent_runner.py` and
   `role_dispatcher.sh` to confirm flag compatibility for headless execution, model overrides, and non-interactive
   operation.
4. Document any breaking changes, deprecated flags, or required environment variables in the audit record.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §2 (Sandbox containment tiers), `docs/safety.md` §2 (Mandatory
  sandboxing), `docs/safety.md` §4 (Exploration vs exploitation safety).
- **Potential failure modes for this step:** Incomplete flag discovery leading to interactive prompts stalling headless
  execution.
- **Guardrails and early‑abort checks:** Validate that all agent runners support headless non-interactive flags before
  updating Dockerfile.

### Success & Discard Criteria

- **Success:** Complete compatibility matrix verified for all five agent packages with explicit non-interactive flags
  and npm configuration requirements documented.
- **Discard:** Discard and abort if any agent runner has introduced mandatory interactive authentication or breaking
  protocol changes incompatible with headless sandbox execution.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.30  |
| impact_pred         | 85.0  |
| cost_pred           | 1.6   |
| learning_value_pred | 4.0   |
| ev_pred             | 82.76 |

### Step Metrics Rationale

High probability (`0.97`) and low entropy (`0.30`) for a structured investigation. Cost is modest (`1.6`), and learning
value is high (`4.0`) as it resolves package dependency risks prior to compilation. Expected value derivation:
`EV = 0.97 * 85.0 + 0.5 * 4.0 - 0.3 * 0.30 - 1.6 = 82.45 + 2.0 - 0.09 - 1.6 = 82.76`.

---

## Step 2: Upgrade agent runner packages in Dockerfile (Bean 0112 Implementation)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standardized Dockerfile package definition upgrades with concrete target versions specified in the
  intent.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update `apps/sandbox-executor/Dockerfile` to pin all five agent CLI packages to their targeted latest
  stable releases with appropriate npm build caching and scripts configurations.
- **Git branch:**
  `I-1792215929-update-docker-agent-images-and-build-bake/P-1792216015-antigravity-agent-gemini-3.8-flash-medium/E-1792216045-upgrade-dockerfile-agents/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Open `apps/sandbox-executor/Dockerfile`.
2. In the Claude agent stage (`FROM holon-base AS agent-claude`):
   - Update `npm install` target to `@anthropic-ai/claude-code@2.1.302`.
   - Maintain `--mount=type=cache,id=npm-claude,uid=1000,gid=1000,target=/home/holon/.npm`.
3. In the Gemini agent stage (`FROM holon-base AS agent-gemini`):
   - Update `npm install` target to `@google/gemini-cli@0.64.0`.
   - Maintain `--mount=type=cache,id=npm-gemini,uid=1000,gid=1000,target=/home/holon/.npm`.
4. In the OpenCode agent stage (`FROM holon-base AS agent-opencode`):
   - Update `npm install` target to `opencode-ai@1.19.3`.
   - Maintain `--mount=type=cache,id=npm-opencode,uid=1000,gid=1000,target=/home/holon/.npm`.
5. In the Pi agent stage (`FROM holon-base AS agent-pi`):
   - Update `npm install` target to `@earendil-works/pi-coding-agent@1.3.0`.
   - Maintain `--mount=type=cache,id=npm-pi,uid=1000,gid=1000,target=/home/holon/.npm`.
6. In the Codex agent stage (`FROM holon-base AS agent-codex`):
   - Update `npm install` target to `@openai/codex@0.163.0`.
   - Maintain `--mount=type=cache,id=npm-codex,uid=1000,gid=1000,target=/home/holon/.npm`.
7. Verify that the Antigravity agent layer (`FROM holon-base AS agent-antigravity`) and base image (`holon-base`)
   configurations remain intact.
8. Validate Dockerfile formatting and syntax using `hadolint` or linter checks where available.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 (Project Layout: sub-packages under `apps/`), `docs/safety.md`
  §2 (Container sandbox standard).
- **Potential failure modes for this step:** Syntax typo in package version strings causing npm install failure during
  image build.
- **Guardrails and early‑abort checks:** Double-check exact version strings against Intent Q1 specifications before
  proceeding.

### Success & Discard Criteria

- **Success:** `apps/sandbox-executor/Dockerfile` pins the exact target versions: `@anthropic-ai/claude-code@2.1.302`,
  `@google/gemini-cli@0.64.0`, `opencode-ai@1.19.3`, `@earendil-works/pi-coding-agent@1.3.0`, and
  `@openai/codex@0.163.0`.
- **Discard:** Discard if any version string deviates from the intent definition or if Dockerfile syntax is invalid.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.35  |
| impact_pred         | 92.0  |
| cost_pred           | 2.2   |
| learning_value_pred | 3.0   |
| ev_pred             | 86.60 |

### Step Metrics Rationale

High probability (`0.95`) and moderate entropy (`0.35`) as package specifications are pinned deterministically. Directly
delivers core Bean 0112 upgrade value (`92.0`). Expected value derivation:
`EV = 0.95 * 92.0 + 0.5 * 3.0 - 0.3 * 0.35 - 2.2 = 87.40 + 1.5 - 0.105 - 2.2 = 86.595 -> 86.60`.

---

## Step 3: Implement writable tmpfs session mounts in cli.py and update unit test assertions (Bean 0112 Implementation & Test)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Modular Python enhancement within `cli.py` accompanied by unit test updates in `test_cli.py`.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update `get_agent_session_mounts` in `apps/sandbox-executor/src/sandbox_executor/cli.py` to add
  writable tmpfs overlays for Antigravity log and cache directories, and update unit tests in `test_cli.py` to verify
  the new mounts.
- **Git branch:**
  `I-1792215929-update-docker-agent-images-and-build-bake/P-1792216015-antigravity-agent-gemini-3.8-flash-medium/E-1792216060-tmpfs-session-mounts/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Open `apps/sandbox-executor/src/sandbox_executor/cli.py` and navigate to `get_agent_session_mounts(agent_id: str)`.
2. Locate the Antigravity agent mount resolution logic across all platform branches:
   - For macOS with `dedicated_session` present: add `--tmpfs /home/holon/.gemini/antigravity-cli/log:uid=1000,gid=1000`
     and `--tmpfs /home/holon/.gemini/antigravity-cli/cache:uid=1000,gid=1000`.
   - For Linux and other platforms with `dedicated_session` present: add
     `--tmpfs /home/holon/.gemini/antigravity-cli/log:uid=1000,gid=1000` and
     `--tmpfs /home/holon/.gemini/antigravity-cli/cache:uid=1000,gid=1000`.
   - For Linux and other platforms with `host_agy_cli` present: add
     `--tmpfs /home/holon/.gemini/antigravity-cli/log:uid=1000,gid=1000` and
     `--tmpfs /home/holon/.gemini/antigravity-cli/cache:uid=1000,gid=1000`.
3. Ensure the base host session directory remains strictly read-only (`-v .../antigravity-cli:ro`) to safeguard host
   credentials and prevent token leakage.
4. Open `apps/sandbox-executor/tests/test_cli.py`:
   - Update `test_antigravity_macos_existing_session_mounts` to assert that `--tmpfs` mounts include the log and cache
     paths with `uid=1000,gid=1000`.
   - Update `test_antigravity_linux_mounts_gemini_cli` to assert that `--tmpfs` mounts for `/log` and `/cache` are
     present.
   - Update `test_antigravity_linux_dbus_mount` to ensure tmpfs arguments are properly returned and do not conflict with
     dbus filtering.
   - Ensure `test_all_agent_session_mounts_are_read_only` continues to pass, validating that all directory bind mounts
     retain `:ro` suffixes while recognizing that tmpfs flags are overlay mounts rather than `-v` directories.
5. Run pytest on `test_cli.py`: `uv run pytest apps/sandbox-executor/tests/test_cli.py`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: complete credential isolation,
  read-only mounts), `holon-config/world/ruleset.md` §2 (PEP 8, type hints, docstrings), `holon-config/world/ruleset.md`
  §3 (Testing constraints).
- **Potential failure modes for this step:** Passing invalid tmpfs mount options to Docker; accidentally overriding the
  read-only policy of the root Antigravity config directory.
- **Guardrails and early‑abort checks:** Ensure tmpfs paths target `/home/holon/.gemini/antigravity-cli/log` and
  `/cache` exclusively, with `uid=1000,gid=1000` ownership flags. Verify that credentials in the root directory remain
  covered by the read-only bind mount.

### Success & Discard Criteria

- **Success:** `get_agent_session_mounts` produces `--tmpfs` mounts for `/log` and `/cache` across all active
  Antigravity execution paths, and all unit tests in `test_cli.py` pass cleanly.
- **Discard:** Discard if any test in `test_cli.py` fails or if read-only credential isolation is compromised.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.25  |
| impact_pred         | 90.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 85.83 |

### Step Metrics Rationale

High probability (`0.96`) and low entropy (`0.25`) because the changes are confined to well-isolated path helper
functions and test fixtures. Impact is high (`90.0`) by resolving runtime sandbox permission write errors. Expected
value derivation: `EV = 0.96 * 90.0 + 0.5 * 3.0 - 0.3 * 0.25 - 2.0 = 86.40 + 1.5 - 0.075 - 2.0 = 85.825 -> 85.83`.

---

## Step 4: Build and load all Docker images using Buildx Bake (Bean 0114 Implementation)

- **Sub‑intent recommendation:** STRONGLY_YES
- **Reasoning:** Critical bottleneck step involving multi-image compilation, extensive CPU/disk utilization, and Docker
  daemon loading. If build failures occur, the execution should be isolated to a dedicated sub-intent branch.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Docker Buildx Bake will concurrently build and load all eight images (`holon/base`,
  `holon/agent-antigravity`, `holon/agent-claude`, `holon/agent-codex`, `holon/agent-gemini`, `holon/agent-opencode`,
  `holon/agent-pi`, `holon/orchestrator`) into the local Docker daemon without exceeding memory limits or cache
  collisions.
- **Learning target:** Actual build durations, layer cache hit rates, local daemon load latencies, and peak resource
  utilization across Buildx targets.
- **Maximum acceptable cost for this learning:** 4.5 cost units.

### Intent & Git Integration

- **Step Intent:** Build all container targets defined in `apps/sandbox-executor/docker-bake.hcl` and load them into the
  local Docker engine using `apps/sandbox-executor/build_all_images.sh`.
- **Git branch:**
  `I-1792215929-update-docker-agent-images-and-build-bake/P-1792216015-antigravity-agent-gemini-3.8-flash-medium/E-1792216075-bake-build-images/_`
- **Sub‑intent:** NEW

### Implementation Details (No code blocks, only logic/steps)

1. Verify Docker daemon availability and Buildx plugin configuration:
   - Run `docker info` and `docker buildx version`.
   - Ensure BuildKit is enabled and builder instance is active.
2. Inspect `apps/sandbox-executor/docker-bake.hcl` to ensure all targets are properly mapped:
   - `base` -> `holon/base`
   - `agent-claude` -> `holon/agent-claude`
   - `agent-codex` -> `holon/agent-codex`
   - `agent-gemini` -> `holon/agent-gemini`
   - `agent-opencode` -> `holon/agent-opencode`
   - `agent-pi` -> `holon/agent-pi`
   - `agent-antigravity` -> `holon/agent-antigravity`
   - `orchestrator` -> `holon/orchestrator`
   - `default` group containing all eight targets.
3. Execute the build and loading process via `apps/sandbox-executor/build_all_images.sh --output-log` or
   `docker buildx bake -f apps/sandbox-executor/docker-bake.hcl --load`.
4. Capture build start time, individual target compilation times, cache hit/miss statistics, and total elapsed duration.
5. Confirm that all eight images are successfully exported and loaded into the local Docker daemon.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (If image compilation fails or packages fail to install, container smoke checks cannot proceed)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: container resource bounds),
  `docs/safety.md` §2 (Container sandbox standard).
- **Potential failure modes for this step:** Docker daemon out of disk space; network timeout during npm package
  download; architecture mismatch during Node.js compilation.
- **Guardrails and early‑abort checks:** Check disk space prior to build; monitor memory consumption; if any single
  image fails to compile, abort immediately and inspect Buildx logs.

### Success & Discard Criteria

- **Success:** All eight container images (`holon/base`, `holon/agent-antigravity`, `holon/agent-claude`,
  `holon/agent-codex`, `holon/agent-gemini`, `holon/agent-opencode`, `holon/agent-pi`, `holon/orchestrator`) are loaded
  into the local Docker engine with status code 0.
- **Discard:** Discard if image build fails after two retries or if Docker daemon runs out of storage.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.40  |
| impact_pred         | 95.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 3.5   |
| ev_pred             | 86.48 |

### Step Metrics Rationale

Bottleneck step with probability (`0.93`) and highest step entropy (`0.40`) due to external network and package
compilation dependencies. Carries the largest resource cost (`3.5`) and high impact (`95.0`). Expected value derivation:
`EV = 0.93 * 95.0 + 0.5 * 3.5 - 0.3 * 0.40 - 3.5 = 88.35 + 1.75 - 0.12 - 3.5 = 86.48`.

---

## Step 5: Container smoke checks, image inspection, and Docker Bake documentation (Bean 0114 Documentation & Test)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Validation and documentation updates detailing empirical build metrics and container verification
  checks.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Verify container smoke checks (entrypoint executor help and `sandbox_executor` module importability),
  verify image creation timestamps via `docker inspect`, and document build times and caching considerations in
  `apps/sandbox-executor/docs/docker_bake.md`.
- **Git branch:**
  `I-1792215929-update-docker-agent-images-and-build-bake/P-1792216015-antigravity-agent-gemini-3.8-flash-medium/E-1792216090-smoke-checks-docs/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Inspect loaded Docker images using `docker images` and `docker inspect`:
   - Verify image IDs, creation timestamps, and virtual sizes for all eight images.
   - Confirm proper tagging (`holon/base:latest`, `holon/orchestrator:latest`, etc.).
2. Execute container smoke checks:
   - Run entrypoint executor help verification on `holon/orchestrator`: execute container with
     `python -m sandbox_executor.cli --help` or entrypoint `--help` and verify exit code 0 with valid argument
     descriptions.
   - Run importability smoke check across all images: execute container with
     `python -c "import sandbox_executor; print('OK')"` and verify clean output.
   - Run agent CLI binary existence checks in respective agent images: verify `claude --version`, `gemini --version`,
     `opencode --version`, `pi --version`, and `codex --version`.
3. Update `apps/sandbox-executor/docs/docker_bake.md`:
   - Add empirical benchmark table documenting build times for fresh build (`--no-cache`) versus cached build.
   - Document layer caching considerations, npm cache mount scoping, and GHA cache behaviour.
   - Document tmpfs overlay architecture for agent logs and caches under read-only sandboxes.
4. Verify Markdown formatting with `npx prettier --check apps/sandbox-executor/docs/docker_bake.md`.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Docstring and documentation standards), `docs/safety.md` §2
  (Container sandbox smoke verification).
- **Potential failure modes for this step:** Module import error due to missing `PYTHONPATH` or missing dependencies in
  built container; unformatted documentation failing Prettier.
- **Guardrails and early‑abort checks:** Ensure smoke check commands execute with `--rm` flag to prevent container
  accumulation; validate documentation with Prettier immediately after editing.

### Success & Discard Criteria

- **Success:** All container smoke checks pass with exit code 0; image timestamps and sizes are verified;
  `apps/sandbox-executor/docs/docker_bake.md` contains accurate build benchmarks and caching documentation.
- **Discard:** Discard if any container smoke check fails (e.g., `sandbox_executor` module cannot be imported) or if
  documentation fails validation.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.20  |
| impact_pred         | 90.0  |
| cost_pred           | 1.8   |
| learning_value_pred | 3.0   |
| ev_pred             | 86.04 |

### Step Metrics Rationale

High probability (`0.96`) and low entropy (`0.20`) as this step verifies completed build artifacts and captures
documentation. Impact is high (`90.0`) by providing operational clarity for developers and CI pipelines. Expected value
derivation: `EV = 0.96 * 90.0 + 0.5 * 3.0 - 0.3 * 0.20 - 1.8 = 86.40 + 1.5 - 0.06 - 1.8 = 86.04`.

---

## Step 6: Execute end-to-end repository verification, test suites, and quality gates (Goal 5 Verification)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Comprehensive quality gate execution verifying all linters, formatting, and test suites across the
  repository.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Run all required static checks, linter suites, code formatting checks, documentation style
  validation, and hermetic pytest tests to guarantee a completely clean pass.
- **Git branch:**
  `I-1792215929-update-docker-agent-images-and-build-bake/P-1792216015-antigravity-agent-gemini-3.8-flash-medium/E-1792216105-quality-verification/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Execute `make lint` which verifies:
   - `uv lock --check`
   - `git diff --exit-code uv.lock`
   - `ruff check .`
   - `ruff format --check .`
2. Execute the hermetic pytest test suite:
   - `uv run pytest apps/sandbox-executor/tests -m 'not integration_test and not stress'`
3. Execute standalone Ruff linter and formatter checks across repository:
   - `uv run ruff check .`
   - `uv run ruff format --check .`
4. Execute Prettier documentation formatting checks:
   - `npx prettier --check '**/*.md'`
5. Verify Git status and diff integrity:
   - Confirm `apps/sandbox-executor/Dockerfile` contains updated agent package versions.
   - Confirm `apps/sandbox-executor/src/sandbox_executor/cli.py` contains tmpfs mount updates.
   - Confirm `apps/sandbox-executor/tests/test_cli.py` contains updated assertions.
   - Confirm `apps/sandbox-executor/docs/docker_bake.md` is updated and properly formatted.
   - Confirm `holon-knowledge/ledger/` files are completely untouched.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4, Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1, §2, §3, `docs/safety.md` §1 & §5 (Human review as final gate).
- **Potential failure modes for this step:** Unchecked formatting discrepancy in Markdown; unexpected regression in unit
  test assertions.
- **Guardrails and early‑abort checks:** Run all validation commands sequentially; halt immediately if any check fails
  and resolve root cause.

### Success & Discard Criteria

- **Success:** All verification commands (`make lint`, `uv run pytest`, `ruff check`, `ruff format --check`,
  `prettier --check`) complete with exit code 0.
- **Discard:** Discard intent execution if any quality gate cannot be satisfied cleanly.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.15  |
| impact_pred         | 92.0  |
| cost_pred           | 1.4   |
| learning_value_pred | 2.0   |
| ev_pred             | 89.72 |

### Step Metrics Rationale

High probability (`0.98`) and minimal entropy (`0.15`) since this step executes automated verification gates against
completed changes. Delivers final verification impact (`92.0`). Expected value derivation:
`EV = 0.98 * 92.0 + 0.5 * 2.0 - 0.3 * 0.15 - 1.4 = 90.16 + 1.0 - 0.045 - 1.4 = 89.715 -> 89.72`.
