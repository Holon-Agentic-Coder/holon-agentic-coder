# Execution Record: E-1794892278-9d45d78f-claude-agent-claude-fable-5

- **Plan:** P-1794892061-52a8e074-claude-agent-claude-fable-5
- **Intent:** I-1794891082-beadspil
- **Agent:** claude-agent (claude-fable-5)

## Summary

Pilot the beads (`bd`) issue tracker in this repository: commit `.beads/config.yaml` (prefix `hac`, dolt local-only, no
`node_id`), gitignore `.beads/embeddeddolt/`, pin `bd` in the `holon-base` Docker stage via `ARG BD_VERSION` with a
checksum-verified install and an in-build `bd version` smoke check, add agent guidance in `docs/issue_tracking.md`
(linked from `docs/agents.md`), and fence the committed surface with hermetic tests in the new declared module
`apps/sandbox-executor/tests/test_beads_integration.py`.

Execution notes so far:

- Step 1 probe result: the `bd` binary is **absent** in the execution sandbox (`command -v bd` finds nothing), so the
  config was authored by documented shape rather than taken from `bd init` output.
- Pin selection: the beads release feed reports latest release `v1.3.4` with assets `beads_1.3.4_linux_amd64.tar.gz`,
  `beads_1.3.4_linux_arm64.tar.gz` and a published `checksums.txt`; the upstream repository `steveyegge/beads` now
  redirects to `gastownhall/beads`.
- Config key shapes confirmed from upstream documentation: `issue_prefix` (set only at `bd init --prefix`),
  `dolt.local-only: true` (keeps Dolt data off the network, refuses remote wiring), `node_id` is per-machine state that
  must never live in the git-tracked `.beads/config.yaml`.
- The workspace has no YAML library in its dependency groups and the plan forbids new dependencies, so the config-shape
  tests assert the exact textual shape of the committed file instead of parsing it with a YAML library.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.95  |
| entropy_actual        | 10.0  |
| impact_actual         | 80.0  |
| cost_actual           | 20.0  |
| learning_value_actual | 6.0   |

Metric notes: `success_actual = 0.95` — all five steps completed, affected tests and the single full-suite run green,
`task lint` green; docked from 1.0 only because `task lint-docs` still fails on 39 pre-existing files outside the touch
set (identical to `main`), so that acceptance criterion holds for the touched surface, not repository-wide.
`entropy_actual = 10.0`, slightly under the predicted 12.0: the hygiene-helper import and the config shape both landed
on the first attempt. `cost_actual = 20.0` versus 24.0 predicted: the bd probe failed fast (binary absent) and no test
or lint gate needed a second substantive attempt beyond the planned prettier fix-up. `learning_value_actual = 6.0`:
confirmed bd is absent from the sandbox (bootstrap must author config by shape), confirmed the upstream repository moved
to `gastownhall/beads` with `v1.3.4` latest and goreleaser-style assets plus `checksums.txt`, and gathered direct
evidence that `hac-` ids are invisible to every hygiene rejection pattern.

## Steps Executed

### Step 1: Probe bd offline and commit the tracker config

- Probe: `command -v bd` found no binary in the sandbox, so `.beads/config.yaml` was hand-authored to the documented
  shape instead of being taken from `bd init` output. Learning recorded: target-repository sandboxes cannot assume a
  host bd; bootstrap must tolerate authoring the config directly.
- Wrote `.beads/config.yaml` with `issue_prefix: hac` (bd stores the prefix without the dash; rendered ids are
  `hac-...`), `dolt.local-only: true`, and no `node_id` key; a header comment states the `refs/dolt/data` sync rule and
  the no-snapshot rule.
- Appended `.beads/embeddeddolt/` to the root `.gitignore` before any other tracker artefact could exist.
- `git status` confirmed only `.beads/config.yaml`, `.gitignore` and this record changed.

### Step 2: Pin bd in the agent images

- `apps/sandbox-executor/Dockerfile`: added top-of-file `ARG BD_VERSION=1.3.4` next to `UV_VERSION` (v1.3.4 is the
  latest published beads release, verified against the upstream release feed at execution time), and an install step in
  the `holon-base` stage following the existing Node.js dpkg-arch pattern: download
  `beads_${BD_VERSION}_linux_${ARCH}.tar.gz` plus the release's `checksums.txt`, verify with `sha256sum -c`, extract and
  install the `bd` binary (tolerating root-level or one-directory-deep layout, as the upstream installer does), and
  finish with an in-build `bd version` smoke check so a broken install fails the layer.
- `apps/sandbox-executor/docker-bake.hcl`: exposed an overridable `BD_VERSION` variable; when empty (the default), no
  arg is passed, keeping the Dockerfile's `ARG BD_VERSION` default as the single pin.
- `apps/sandbox-executor/build_all_images.sh` forwards no build args today (bake reads its variables from the
  environment), so it is intentionally left unchanged, as the plan allows.
- No image build was attempted (network-isolated sandbox); correctness is enforced textually by Step 4's tests and at
  the next real image build.

### Step 3: Agent guidance for issue tracking

- Created `docs/issue_tracking.md`: committed vs ignored tracker state (`.beads/config.yaml` tracked;
  `.beads/embeddeddolt/` and `node_id` never committed; no `issues.jsonl` snapshots), the agent workflow (`bd ready`,
  `bd update <id> --claim`, `bd create`, `bd close`), sync via `refs/dolt/data` only, the two-concurrent-flow-branches
  procedure (issue data merges at the Dolt data layer, never via source branches), and the write-path exception stated
  as the intent's closed list: exactly `refs/dolt/data` plus `.beads/` data, never source files.
- Added a pointer sentence in `docs/agents.md` (agent communication section) and an entry in its related-documents list;
  no other changes to that document.

### Step 4: Hermetic tests in the new declared module

- Created `apps/sandbox-executor/tests/test_beads_integration.py` (the module the prior attempt wrote undeclared; this
  plan declares it in the touch set). Five groups: committed config shape (`issue_prefix: hac`, `dolt.local-only`,
  `node_id` absent — asserted textually because the workspace carries no YAML library and the plan forbids new
  dependencies), the `.gitignore` entry, the Dockerfile pin (`ARG BD_VERSION` concrete semver, checksum-verified install
  in the `holon-base` stage, trailing in-build `bd version` smoke check, bake-file variable), hygiene non-collision
  (`hac-1`, `hac-0042`, `hac-1a2b` and prose forms through the imported `find_hygiene_violations` helper of
  `test_repository_hygiene.py`, which stays unmodified, plus clean scans of `.beads/config.yaml` and
  `docs/issue_tracking.md`), and a skip-if-bd-missing fully-offline create/claim/close round-trip in a temporary
  repository (isolated `HOME`, `BD_DOLT_LOCAL_ONLY=true`).
- Importing the hygiene helper via `from tests.test_repository_hygiene import ...` worked cleanly (the suite's
  established convention), so the planned fallback of re-declaring patterns locally was not needed.
- First targeted run:
  `uv run pytest apps/sandbox-executor/tests/test_beads_integration.py apps/sandbox-executor/tests/test_repository_hygiene.py`
  → 42 passed, 1 skipped (the bd smoke test; no binary in the sandbox) in 0.47s.

### Step 5: Verification

- Ran the affected tests, the full suite once (nothing in this intent measures performance or timing, so test-run
  discipline requires exactly one full-suite pass), `uv run task lint`, and `uv run task lint-docs`; results in
  `## Tests Run` below.
- `uv run task lint-docs` flagged the three markdown files this execution wrote or edited; `npx prettier --write` on
  exactly those three fixed them (the only reflow in `docs/agents.md` was the sentence this execution added), and the
  beads + hygiene tests were re-run green afterwards.
- `uv run task lint-docs` still fails on 39 historical files (`plans/`, old `executions/`, `.agents/holon_flow.md`,
  `docs/optimisation/token_reduction_measurement_plan.md`, `holon-config/metrics/README.md`). Every one of them is
  byte-identical to `main` (verified with `git diff --quiet main -- <file>` per file), so these are pre-existing
  failures outside the declared touch set. They are reported honestly rather than patched around: reformatting them
  would repeat the undeclared-write violation that caused this re-plan. Everything this execution touched passes
  prettier.
- Final `git status` check: the working tree contains exactly the declared touch-set files plus this execution record;
  `apps/sandbox-executor/build_all_images.sh` is declared but intentionally unchanged (it forwards no build args; bake
  reads its variables from the environment, as the plan allows).

## Tests Run

All runs with `uv run` from the repository root; the full suite ran exactly once (no performance or timing measurement
in this intent, so no repetitions; median of a single run is that run).

| command                                                                    | result                                                                                                   | time   |
| -------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | ------ |
| `pytest .../test_beads_integration.py .../test_repository_hygiene.py`      | 42 passed, 1 skipped                                                                                     | 0.47s  |
| `pytest` affected: beads, hygiene, container_images, build_all, formatting | 79 passed, 15 skipped                                                                                    | 0.58s  |
| `task test` (full suite, `-n auto --dist worksteal`, single run; median)   | 4274 passed, 2 skipped, 8 warnings                                                                       | 18.48s |
| `task lint`                                                                | pass (ruff clean, lock clean, budgets)                                                                   | —      |
| `task lint-docs`                                                           | fail: 39 pre-existing files, all byte-identical to `main`, outside the touch set; all touched files pass | —      |
| `pytest` beads + hygiene re-run after prettier reflow                      | 42 passed, 1 skipped                                                                                     | 0.44s  |

Acceptance criteria that need a real image build or two live flows ("bd works offline inside the images", "issue sync
survives two concurrent flow branches") are enforced structurally (in-build `bd version` smoke check; documented
`refs/dolt/data` data-layer sync procedure) and will be exercised at the next image build and first real flows, as the
plan states openly.
