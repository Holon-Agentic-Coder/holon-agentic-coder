# Plan for I-1791812918-executor-toolbelt-and-lint-targets

- **Plan ID:** P-1791812932-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-12T13:48:52.994Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with empirical validation of multi-architecture static binary container integration,
  Makefile lint targets, and hermetic mock verification across host and sandbox execution boundaries.
- **Safety priority level:** standard
- **Priority Justification:** Triggered by `holon-config/world/ruleset.md` §1 & §2 (Python runtime `==3.13.*`, workspace
  isolation under `apps/sandbox-executor`, strict PEP 8 formatting, static type annotations with `typing` module,
  docstring maintenance), `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test placement under
  `apps/sandbox-executor/tests/`, test modifications declared in planning), `holon-config/world/constraints.md` §1 (Git
  Flow & Branch Constraints: prefix-based branch isolation, rebase discipline, commit boundaries),
  `holon-config/world/constraints.md` §2 (Sandbox containment: subprocess and filesystem containment, no unwhitelisted
  subprocesses, process sandbox `< 10` entropy), `holon-config/world/constraints.md` §3 (Ledger immutability:
  append-only ledgers), `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory for execution), and Bean
  0085 (OS toolbelt, linters, and front-loaded intent questions).

## Exploration

- **Proportion of steps that are exploratory:** 0.67
- **Justification:** Steps 1, 3, 4, and 5 incorporate balanced exploration to empirically test intent question
  validation schemas, multi-architecture static binary extraction (`amd64` and `arm64` `yq`), cross-platform shell and
  markup linter execution in Makefile targets, and hermetic mock binary test execution without leaking host environment
  tools.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.70  |
| impact_pred         | 95.0  |
| cost_pred           | 9.7   |
| learning_value_pred | 5.5   |
| ev_pred             | 81.19 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.93. Dictated by the bottleneck step (Step 3: 0.93), where multi-architecture container base
  layers (`apps/sandbox-executor/Dockerfile`), static binary packaging for `yq` across `amd64` and `arm64`, and package
  manager dependencies (`shellcheck`, `yamllint`) must cleanly build across host architectures without network
  instability or platform mismatch.
- **entropy_pred**: 0.70. Evaluated as the maximum single-step risk profile (Step 3: 0.70), where Dockerfile base layer
  modifications and binary installations take place. The sum of predicted step entropies across all six steps is 2.80
  (`0.60 + 0.20 + 0.70 + 0.60 + 0.50 + 0.20 = 2.80`), which conforms strictly to the allocated entropy budget of 15.0.
- **impact_pred**: 95.0. Fulfills Bean 0085 by enforcing a baked-in OS toolbelt (`jq`, `yq`, `shellcheck`, `yamllint`,
  `grep`, `awk`, `sed`, `diff`, `cmp`, `gh`), forbidding throwaway scripts, adding five standardized Makefile lint
  targets (`lint`, `lint-json`, `lint-yaml`, `lint-shell`, `lint-md`), providing a `tools` inspection target,
  eliminating per-command `export VAR=... &&` environment surgery, and front-loading clarifying questions into Stage 1
  to guarantee unattended execution across downstream stages.
- **cost_pred**: 9.7. Calculated as the direct sum of individual step costs (`2.0 + 0.8 + 2.2 + 2.0 + 2.2 + 0.5 = 9.7`).
- **learning_value_pred**: 5.5. Epistemic gain from establishing standardized toolbelt detection, zero-overhead
  container path conventions, declarative Makefile linting across heterogeneous formats (JSON, JSONL, YAML, Shell,
  Markdown), and unambiguous intent question resolution schemas.
- **ev_pred**: 81.19. Computed strictly via the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system constants `lambda = 0.3`
  and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.93 * 95.0 + 0.5 * 5.5 - 0.3 * 0.70 - 9.7 = 88.35 + 2.75 - 0.21 - 9.7 = 81.19`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python target `==3.13.*`, package
    management via `uv`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, static typing with `typing`
    module, docstring maintenance).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test files placed in `tests/`, all test
    changes explicitly declared in the planning step).
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, rebase
    discipline, commit boundaries).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: filesystem containment, no unwhitelisted
    subprocesses, process sandbox `< 10` entropy).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modifications to historical ledger entries,
    append-only ledger operations).
  - `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory for execution).
  - Bean 0085 (OS toolbelt mandate, Makefile lint targets, container tool discoverability, front-loaded intent
    questions).
- **Potential violations or edge cases:**
  - Non-standard JSON or JSONL files in vendor, virtual environment, or git directories failing `lint-json`.
  - Non-standard YAML syntax or multi-document YAML files in configuration directories failing `lint-yaml`.
  - Shell scripts containing harmless platform idioms triggering shellcheck warnings without appropriate exclusion or
    severity filters.
  - Architecture detection failures on non-x86_64 host environments when downloading static `yq` binaries.
  - Flaky or non-hermetic Makefile tests depending on tools installed in the host developer's environment rather than
    test fixtures.
  - Downstream unattended stages hanging if an unresolvable intent question slips past Stage 1 validation.
- **Mitigations built into the plan:**
  - Prune `.git`, `.venv`, and `holon-coherence` directories from all Makefile find commands for JSON, YAML, and Shell
    linters.
  - Support multi-architecture detection (`dpkg --print-architecture` mapping `amd64` and `arm64`) with fail-fast
    assertions in Dockerfile.
  - Add explicit hermetic test fixtures with isolated `PATH` directories in `test_makefile.py`, verifying both
    successful tool execution and clean failure on deliberately malformed test fixtures.
  - Fail fast in `flow.py` and `intent_creator.py` if any open, unclarified questions exist in the intent payload,
    recording answers in `intents.jsonl`.
  - Enforce conservative default choices for downstream stages (Plan, Execute, Review, Calibrate) so unattended flows
    never stall waiting for interactive user input.
- **Residual risk accepted (and why):**
  - Third-party submodule files (`holon-coherence`) maintaining their own linting standards; accepted as they are
    isolated git submodules excluded by directory pruning.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 0.70
- **Budget Compliance:** The strategy fits within budget (`0.70 <= 15.0`).

## Plan Description & Strategy

This plan implements Bean 0085 across `holon-agentic-coder`:

1. **Front-Load Questions in Intent Stage (`flow.py` & `intent_creator.py`):**
   - Front-load all clarifying questions in Stage 1 (Intent stage).
   - In `flow.py` (`run_intent_stage`) and `intent_creator.py` (`main`), validate that intent payloads do not carry
     unresolved ambiguities or open questions.
   - If an intent JSON specifies open questions, fail fast before proceeding to Plan or Execute unless resolved answers
     are provided.
   - Record resolved questions and answers in `holon-knowledge/ledger/intents.jsonl` alongside the intent metadata.
   - Enforce unattended execution across downstream stages (Plan, Execute, Review, Calibrate), mandating conservative
     defaults rather than stalling on questions.

2. **Agent Instructions for Baked-in OS Toolbelt (`.agents/AGENTS.md`):**
   - Update `.agents/AGENTS.md` with strict rules instructing all agent roles to probe for tools before writing scripts
     (`command -v <tool>`).
   - Forbid writing one-off Python or Node throwaway scripts to inspect, filter, pretty-print, or validate JSON, YAML,
     or text.
   - Mandate using baked-in tools: `jq` (`jq -r`, `jq -s`, `jq -e .`), `yq` for YAML, `grep`/`awk`/`sed` for text
     extraction, `diff`/`cmp` for file comparisons, and `gh api --jq`.
   - Restrict scratch scripts strictly to `/tmp` or `todo/`, requiring immediate cleanup.
   - Forbid per-command `export VAR=... &&` prefixes.

3. **Bake Linters into Sandbox Image & Minimize Runtime Environment Injection (`Dockerfile`):**
   - In `apps/sandbox-executor/Dockerfile`, add `shellcheck` and `yamllint` to the `holon-base` package list.
   - Add multi-architecture static binary download for `yq` (mikefarah/yq for `amd64` and `arm64`) into
     `/usr/local/bin/yq`.
   - Ensure all toolbelt binaries are discoverable through `ENV PATH`, completely removing the need for per-command
     `export` prefixes.

4. **Add Root Makefile Targets (`tools`, `lint`, `lint-json`, `lint-yaml`, `lint-shell`, `lint-md`) & CI Wiring:**
   - In root `Makefile`, add `tools` target that prints the absolute resolved path and version of every required
     toolbelt tool (`git`, `jq`, `yq`, `shellcheck`, `yamllint`, `uv`, `gh`, `npx`, `openssl`).
   - Add individual lint targets: `lint-json` (invoking `jq -e .`), `lint-yaml` (invoking `yq -e 'true'`), `lint-shell`
     (invoking `shellcheck`), `lint-md` (invoking `npx prettier --check`).
   - Add composite `lint` target running all lint targets plus Python linting (`uv run task lint`).
   - Wire `make lint` into `.github/workflows/test-hygiene.yml` and Makefile prerequisite/check paths.
   - Update `make help` to describe all new targets.

5. **Hermetic Unit and Regression Tests:**
   - In `apps/sandbox-executor/tests/test_makefile.py`, assert new targets exist, are listed in `make help`, support
     dry-run (`make -n`), and fail cleanly on deliberately malformed temporary fixtures.
   - In `apps/sandbox-executor/tests/test_flow.py` and `test_intent_creator.py`, assert intent question validation,
     fail-fast behavior, and ledger payload persistence.

6. **Full Verification and Quality Gate Convergence:**
   - Run complete unit test suite, ruff checks, formatting, and markdown prettier convergence.

---

## Step 1: Implement Front-Loaded Intent Question Validation and Unattended Downstream Defaults

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core Stage 1 validation and ledger payload extension encapsulated within `flow.py` and
  `intent_creator.py` with straightforward unit test coverage.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Validating open questions at Stage 1 and recording resolved answers in `intents.jsonl`
  prevents downstream stage stalls while preserving backward compatibility with legacy intent payloads.
- **Learning target:** Determine the optimal intent question payload schema (`questions` list and `answers` mapping)
  that allows early validation without rejecting valid intents lacking questions.
- **Maximum acceptable cost for this learning:** 2.0

### Intent & Git Integration

- **Step Intent:** Add question validation and fail-fast checks in `flow.py` and `intent_creator.py`, record resolved
  answers in `intents.jsonl`, and mandate conservative unattended options for downstream stages.
- **Git branch:**
  `I-1791812918-executor-toolbelt-and-lint-targets/P-1791812932-antigravity-agent-gemini-3.8-flash-medium/E-1-front-load-intent-questions/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - In `run_intent_stage(context: FlowContext)`:
     - Inspect `context.intent_data` for question-related fields: `questions`, `open_questions`, `clarifications`.
     - Check if any open questions are marked as unresolved or lack matching keys in `answers` / `resolved_questions`.
     - If unresolved questions exist without corresponding answers:
       - Fail fast: set stage status to `StageStatus.FAILED`, construct an actionable error message naming the
         unresolved questions, log to context, and return early.
     - If questions are present and all have corresponding answers:
       - Ensure `questions` and `answers` are recorded directly in `intent_data` so they are persisted to
         `holon-knowledge/ledger/intents.jsonl`.
     - If no questions are present, proceed with normal intent creation.
   - In `run_plan_stage`, `run_execute_stage`, `run_review_stage`, and `run_calibrate_stage`:
     - Ensure all stages operate unattended: when selecting options or handling ambiguous conditions, pick conservative,
       non-blocking defaults rather than prompting for user interaction or stalling the pipeline.
2. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/intent_creator.py`:
   - In `main()`:
     - Inspect `intent_data` loaded from `/tmp/intent.json`.
     - Check for open questions without answers.
     - If unresolved questions are detected, print an informative error to `sys.stderr` stating that open questions must
       be resolved prior to Stage 1 completion, and exit with status 1.
     - Ensure resolved answers are preserved in `intent_data` prior to appending the entry to
       `holon-knowledge/ledger/intents.jsonl`.
     - Include a summary of resolved questions in the git commit message if present.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Docstrings, static typing),
  `holon-config/world/constraints.md` §3 (Ledger immutability: append-only ledgers), `docs/safety.md` §5 (Human review
  boundaries).
- **Potential failure modes for this step:** Rejecting legacy intent JSONs that omit question fields; ledger format
  divergence.
- **Guardrails and early‑abort checks:** Treat missing question fields as valid (zero questions); only fail fast when
  open questions are explicitly declared without answers.

### Success & Discard Criteria

- **Success:** Intent stage validates open questions, fails fast on unresolvable ambiguities, records resolved answers
  in `intents.jsonl`, and downstream stages run unattended.
- **Discard:** Discard if valid intent payloads without questions are rejected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.60  |
| impact_pred         | 92.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 4.5   |
| ev_pred             | 87.47 |

### Step Metrics Rationale

Calculated via: `EV = 0.95 * 92.0 + 0.5 * 4.5 - 0.3 * 0.60 - 2.0 = 87.40 + 2.25 - 0.18 - 2.0 = 87.47`. High success
probability and clear fail-fast semantics eliminating downstream stalls.

---

## Step 2: Update Agent Instructions for Baked-in OS Toolbelt in .agents/AGENTS.md

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure instruction update in `.agents/AGENTS.md` governing agent behavior across all roles.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update `.agents/AGENTS.md` with strict rules instructing roles to probe for tools before writing
  scripts, forbidding throwaway scripts, mandating baked-in tools, restricting scratch scripts, and forbidding inline
  environment injection.
- **Git branch:**
  `I-1791812918-executor-toolbelt-and-lint-targets/P-1791812932-antigravity-agent-gemini-3.8-flash-medium/E-2-agent-instructions-os-toolbelt/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `.agents/AGENTS.md`:
   - Add a dedicated section: "OS Toolbelt & Script Hygiene Rules":
     - Mandate tool probing: Always probe for tools before writing scripts using `command -v <tool>` (e.g.
       `command -v jq`, `command -v yq`).
     - Forbid throwaway scripts: Strictly forbid writing one-off Python or Node throwaway scripts to inspect, filter,
       pretty-print, or validate JSON, YAML, or text files.
     - Mandate baked-in tools:
       - Require `jq` for JSON and JSONL inspection, transformation, and validation (`jq -r`, `jq -s`, `jq -e .`).
       - Require `yq` for YAML inspection, transformation, and validation (`yq -e 'true' <file>`).
       - Require `grep`, `awk`, and `sed` for text and line extraction.
       - Require `diff` and `cmp` for file comparisons.
       - Require `gh api --jq` for GitHub CLI queries and filtering.
     - Scratch script boundary: Explicitly restrict scratch scripts to `/tmp` or `todo/`, and mandate that they are
       deleted immediately after use.
     - Forbid per-command environment injection: Forbid per-command `export VAR=... &&` prefixes. Instruct agents to
       rely on standard `ENV PATH` discoverability.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Docstrings & documentation standards), Markdown formatting
  hygiene.
- **Potential failure modes for this step:** Prettier markdown formatting discrepancies.
- **Guardrails and early‑abort checks:** Format updated document using `npx prettier --write .agents/AGENTS.md`.

### Success & Discard Criteria

- **Success:** `.agents/AGENTS.md` contains clear, enforceable rules governing tool probing, baked-in tool usage,
  scratch script limits, and zero inline environment surgery.
- **Discard:** Discard if rules allow arbitrary throwaway scripts in repository working tree.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.20  |
| impact_pred         | 88.0  |
| cost_pred           | 0.8   |
| learning_value_pred | 2.5   |
| ev_pred             | 87.51 |

### Step Metrics Rationale

Calculated via: `EV = 0.99 * 88.0 + 0.5 * 2.5 - 0.3 * 0.20 - 0.8 = 87.12 + 1.25 - 0.06 - 0.8 = 87.51`. Highly
predictable documentation update establishing repo-wide agent hygiene.

---

## Step 3: Bake Linters and Multi-Arch Static yq into Sandbox Image & Minimize Runtime Environment Injection

- **Sub‑intent recommendation:** NO
- **Reasoning:** Dockerfile enhancements in `apps/sandbox-executor/Dockerfile` adding packages to base image layers and
  verifying PATH discoverability.
- **Step Type:** CONFIG
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Installing `shellcheck` and `yamllint` via apt and downloading static `yq` into
  `/usr/local/bin` provides full multi-architecture toolbelt discoverability without requiring runtime environment
  injection.
- **Learning target:** Verify static binary release URLs and architecture naming for `yq` (`amd64` vs `arm64`) to ensure
  seamless Docker image builds across both host architectures.
- **Maximum acceptable cost for this learning:** 2.2

### Intent & Git Integration

- **Step Intent:** Add `shellcheck`, `yamllint`, and static `yq` (amd64/arm64) to `apps/sandbox-executor/Dockerfile`,
  ensure all tools are discoverable on `ENV PATH`, and eliminate inline environment injection requirements.
- **Git branch:**
  `I-1791812918-executor-toolbelt-and-lint-targets/P-1791812932-antigravity-agent-gemini-3.8-flash-medium/E-3-bake-linters-into-sandbox-image/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/Dockerfile`:
   - In the `holon-base` stage:
     - Update the `apt-get install` command to include `shellcheck` and `yamllint` alongside
       `git curl jq build-essential ca-certificates ssh vim`.
     - Add multi-architecture installation of `yq` (mikefarah static binary):
       - Extract architecture using `dpkgArch="$(dpkg --print-architecture)"`.
       - Map architecture: `amd64` to `amd64`, `arm64` to `arm64`, exiting with code 1 if unsupported.
       - Download static binary: `https://github.com/mikefarah/yq/releases/latest/download/yq_linux_${ARCH}` directly to
         `/usr/local/bin/yq`.
       - Make binary executable via `chmod +x /usr/local/bin/yq`.
     - Verify environment discoverability:
       - Confirm that `/usr/local/bin` and standard system paths are on `PATH`.
       - Confirm that `USER holon` inherits
         `PATH="/home/holon/.local/bin:/home/holon/.local/node_modules/.bin:${PATH}"`, ensuring `/usr/local/bin/yq`,
         `/usr/bin/jq`, `/usr/bin/shellcheck`, `/usr/bin/yamllint`, and `git` are immediately resolved without needing
         `export VAR=... &&` prefixes.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (Downstream Makefile lint execution inside executor containers relies on baked-in tool
  availability)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §2 (Sandbox containment), Bean 0085 (Baked-in linters and OS
  toolbelt).
- **Potential failure modes for this step:** GitHub release URL rate limiting or connectivity during Docker build;
  architecture mapping discrepancy on ARM runners.
- **Guardrails and early‑abort checks:** Use static release binaries, verify permissions with `chmod +x`, test binary
  execution with `--version`.

### Success & Discard Criteria

- **Success:** Dockerfile cleanly installs `shellcheck`, `yamllint`, and multi-arch `yq` into standard `/usr/local/bin`
  and `/usr/bin` locations discoverable via `PATH`.
- **Discard:** Discard if static binary fails to execute or architecture mapping fails.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.70  |
| impact_pred         | 94.0  |
| cost_pred           | 2.2   |
| learning_value_pred | 5.0   |
| ev_pred             | 87.51 |

### Step Metrics Rationale

Calculated via: `EV = 0.93 * 94.0 + 0.5 * 5.0 - 0.3 * 0.70 - 2.2 = 87.42 + 2.50 - 0.21 - 2.2 = 87.51`. Bottleneck step
handling container multi-arch binary layers.

---

## Step 4: Add Root Makefile Lint and Tools Targets and Wire into CI Hygiene

- **Sub‑intent recommendation:** NO
- **Reasoning:** Root Makefile additions providing `tools`, `lint`, `lint-json`, `lint-yaml`, `lint-shell`, `lint-md`
  targets and CI hygiene workflow wiring.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Direct invocation of `jq -e .`, `yq -e 'true'`, `shellcheck`, and `npx prettier --check`
  across tracked files provides fast, robust linting with zero throwaway script overhead.
- **Learning target:** Determine the cleanest file discovery and exclusion rules (pruning `.git`, `.venv`, and
  submodules) so Makefile lint targets run fast and reliably across both Linux and macOS.
- **Maximum acceptable cost for this learning:** 2.0

### Intent & Git Integration

- **Step Intent:** Add `tools`, `lint`, `lint-json`, `lint-yaml`, `lint-shell`, `lint-md` targets to root `Makefile`,
  wire into `make help` and `check`, and integrate `make lint` into `.github/workflows/test-hygiene.yml`.
- **Git branch:**
  `I-1791812918-executor-toolbelt-and-lint-targets/P-1791812932-antigravity-agent-gemini-3.8-flash-medium/E-4-makefile-lint-and-tools-targets/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In root `Makefile`:
   - Add `.PHONY` declarations for `tools`, `lint`, `lint-json`, `lint-yaml`, `lint-shell`, `lint-md`.
   - Update `help` target:
     - Add formatted lines describing `tools` ("Print absolute resolved path and version of every required toolbelt
       tool."), `lint` ("Run all linters (json, yaml, shell, md, python)."), `lint-json` ("Validate all JSON and JSONL
       files with jq."), `lint-yaml` ("Validate all YAML files with yq."), `lint-shell` ("Validate shell scripts with
       shellcheck."), and `lint-md` ("Check markdown formatting with Prettier.").
   - Implement `tools` target:
     - Check dry-run mode (`if [ -n "$(DRY_RUN)" ]; then exit 0; fi;`).
     - Iterate through required tools: `git`, `jq`, `yq`, `shellcheck`, `yamllint`, `uv`, `gh`, `npx`, `openssl`.
     - For each tool, resolve absolute path via `command -v <tool>` and capture version output (`<tool> --version` or
       appropriate version flag).
     - Print formatted output showing tool name, resolved path, and version string.
     - If any tool is missing or unusable, exit non-zero with descriptive advice.
   - Implement `lint-json` target:
     - Check dry-run mode.
     - Discover JSON and JSONL files using `find` excluding `.git`, `.venv`, and `holon-coherence`.
     - Validate each file directly with `jq -e . "$$file" >/dev/null`.
     - Fail fast with non-zero exit and file path on parsing failure.
   - Implement `lint-yaml` target:
     - Check dry-run mode.
     - Discover YAML files (`*.yml`, `*.yaml`) excluding `.git`, `.venv`, and `holon-coherence`.
     - Validate each file with `yq -e 'true' "$$file" >/dev/null` or `yamllint`.
     - Fail fast with non-zero exit on syntax or schema errors.
   - Implement `lint-shell` target:
     - Check dry-run mode.
     - Discover shell scripts (`*.sh`) excluding `.git`, `.venv`, and `holon-coherence`.
     - Run `shellcheck` directly over all discovered shell scripts.
     - Fail fast on shellcheck diagnostics.
   - Implement `lint-md` target:
     - Check dry-run mode.
     - Run `npx prettier --check "**/*.md"`.
   - Implement composite `lint` target:
     - Depend on or sequentially execute `lint-json`, `lint-yaml`, `lint-shell`, `lint-md`, and python lint
       (`uv run task lint`).
   - Wire `check-prerequisites` / `check` targets:
     - Include advisory or prerequisite references to the new targets where appropriate.
2. In `.github/workflows/test-hygiene.yml`:
   - Add step executing `make lint` (or ensure required linters are present and invoked via `make lint`) to enforce
     toolbelt lint hygiene on every pull request and push to main.

### Dependencies & Criticality

- **Depends on:** Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 (Tooling), GNU Make conventions, dry-run handling.
- **Potential failure modes for this step:** Shell find commands choking on filenames with spaces; non-GNU make
  differences; dry-run flags misfiring.
- **Guardrails and early‑abort checks:** Consult `MF_OPTION_CLUSTER` for dry-run detection; quote find exec arguments
  safely.

### Success & Discard Criteria

- **Success:** Makefile provides `tools` and all five `lint` targets, passes dry-run cleanly, accurately flags malformed
  files, and integrates into CI hygiene.
- **Discard:** Discard if make targets depend on throwaway Python/Node helper scripts.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.60  |
| impact_pred         | 93.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 4.5   |
| ev_pred             | 87.49 |

### Step Metrics Rationale

Calculated via: `EV = 0.94 * 93.0 + 0.5 * 4.5 - 0.3 * 0.60 - 2.0 = 87.42 + 2.25 - 0.18 - 2.0 = 87.49`. High impact
establishing direct OS toolbelt lint targets.

---

## Step 5: Implement Hermetic Unit and Regression Tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Comprehensive test expansion in `test_makefile.py`, `test_flow.py`, and `test_intent_creator.py`
  asserting target presence, dry-run safety, malformed fixture failures, and intent question handling.
- **Step Type:** TEST
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Hermetic test fixtures with isolated PATH and deliberately malformed temporary files
  accurately prove that Makefile targets and Stage 1 question validators fail loud and clear.
- **Learning target:** Confirm test coverage across all five lint targets, the `tools` target, and Stage 1 question
  validation without relying on developer shell state or network access.
- **Maximum acceptable cost for this learning:** 2.2

### Intent & Git Integration

- **Step Intent:** Add hermetic unit tests in `apps/sandbox-executor/tests` covering `make tools`, `make lint*`, dry-run
  behavior, malformed fixtures, and Stage 1 intent question validation.
- **Git branch:**
  `I-1791812918-executor-toolbelt-and-lint-targets/P-1791812932-antigravity-agent-gemini-3.8-flash-medium/E-5-hermetic-unit-and-regression-tests/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_makefile.py`:
   - Expand `test_makefile_help_lists_core_targets`: assert that `tools`, `lint`, `lint-json`, `lint-yaml`,
     `lint-shell`, and `lint-md` are listed in `make help` output.
   - Expand `test_makefile_dry_run_core_targets`: assert that `make -n tools`, `make -n lint`, `make -n lint-json`,
     `make -n lint-yaml`, `make -n lint-shell`, and `make -n lint-md` all exit with returncode 0 without executing
     underlying probes or linters.
   - Add test `test_tools_target_reports_paths_and_versions`:
     - Provide mock environment with stubs for `git`, `jq`, `yq`, `shellcheck`, `yamllint`, `uv`, `gh`, `npx`,
       `openssl`.
     - Run `make tools`, assert exit code 0, and assert stdout includes resolved paths and version numbers for each
       tool.
   - Add test `test_lint_json_fails_on_malformed_fixture`:
     - Create a temporary directory with a malformed JSON file (e.g. trailing comma or missing brace).
     - Run `make lint-json` targeting the fixture, assert non-zero returncode.
   - Add test `test_lint_yaml_fails_on_malformed_fixture`:
     - Create a temporary directory with a malformed YAML file (e.g. invalid indentation).
     - Run `make lint-yaml` targeting the fixture, assert non-zero returncode.
   - Add test `test_lint_shell_fails_on_malformed_fixture`:
     - Create a temporary shell script with shellcheck syntax errors or unquoted expansions.
     - Run `make lint-shell` targeting the fixture, assert non-zero returncode.
   - Add test `test_lint_md_fails_on_unformatted_fixture`:
     - Create a temporary unformatted markdown file.
     - Run `make lint-md`, assert non-zero returncode.
2. In `apps/sandbox-executor/tests/test_flow.py`:
   - Add `test_intent_stage_fails_fast_on_unresolved_open_questions`:
     - Set up `FlowContext` with `intent_data` containing `open_questions` without answers.
     - Run `run_intent_stage(context)`, assert result status is `StageStatus.FAILED` with descriptive error.
   - Add `test_intent_stage_persists_resolved_questions_to_ledger`:
     - Set up `FlowContext` with `intent_data` containing questions and matching answers.
     - Run `run_intent_stage(context)`, assert status is `StageStatus.SUCCESS`, assert ledger payload includes questions
       and answers.
   - Add `test_downstream_stages_run_unattended`:
     - Assert that `run_plan_stage`, `run_execute_stage`, `run_review_stage`, and `run_calibrate_stage` execute without
       interactive prompts.
3. In `apps/sandbox-executor/tests/test_intent_creator.py`:
   - Add unit test verifying `intent_creator.main()` rejects intent JSON specifying unresolvable questions and succeeds
     when answers are present.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 3, Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Testing constraints),
  `apps/sandbox-executor/docs/hermetic_testing.md` (Hermetic testing guidelines).
- **Potential failure modes for this step:** Tests mutating live repository state or inheriting host PATH binaries.
- **Guardrails and early‑abort checks:** Use `tmp_path` fixtures for all test files; isolate PATH using
  `_setup_mock_env`.

### Success & Discard Criteria

- **Success:** All new tests in `test_makefile.py`, `test_flow.py`, and `test_intent_creator.py` pass cleanly under
  isolated hermetic environments.
- **Discard:** Discard if tests require real network access or mutate live workspace files.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.50  |
| impact_pred         | 92.0  |
| cost_pred           | 2.2   |
| learning_value_pred | 5.0   |
| ev_pred             | 87.55 |

### Step Metrics Rationale

Calculated via: `EV = 0.95 * 92.0 + 0.5 * 5.0 - 0.3 * 0.50 - 2.2 = 87.40 + 2.50 - 0.15 - 2.2 = 87.55`. Comprehensive
test coverage locking in hermetic behavior and preventing regressions.

---

## Step 6: Full Verification and Quality Gate Convergence

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard quality gate convergence verifying unit tests, linters, formatters, and markdown hygiene.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Execute full test suite, ruff checks, formatters, and markdown prettier convergence to ensure clean
  quality gates.
- **Git branch:**
  `I-1791812918-executor-toolbelt-and-lint-targets/P-1791812932-antigravity-agent-gemini-3.8-flash-medium/E-6-verification/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run unit test suite: `uv run pytest apps/sandbox-executor/tests -m "not integration_test and not stress"`.
2. Run ruff linter check: `uv run ruff check .`.
3. Run ruff format check: `uv run ruff format --check .`.
4. Run prettier markdown check: `npx prettier --check "**/*.md"`.
5. Run Makefile dry-run checks: `make -n lint`, `make -n tools`.
6. Verify exit status 0 across all verification commands.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4, Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 & §2 (Formatting, linting, Python standards).
- **Potential failure modes for this step:** Formatting oscillations or lint diagnostics.
- **Guardrails and early‑abort checks:** Format before final check using `uv run ruff format .` and
  `npx prettier --write "**/*.md"`.

### Success & Discard Criteria

- **Success:** All test suites, ruff checks, makefile dry-runs, and prettier checks exit with status 0.
- **Discard:** Discard if any check fails.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.20  |
| impact_pred         | 75.0  |
| cost_pred           | 0.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 74.44 |

### Step Metrics Rationale

Calculated via: `EV = 0.99 * 75.0 + 0.5 * 1.5 - 0.3 * 0.20 - 0.5 = 74.25 + 0.75 - 0.06 - 0.5 = 74.44`. Deterministic
verification step ensuring zero regression across the repository.
