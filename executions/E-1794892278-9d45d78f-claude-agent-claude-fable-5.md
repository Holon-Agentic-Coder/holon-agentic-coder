# Execution Record: E-1794892278-9d45d78f-claude-agent-claude-fable-5

- **Plan:** P-1794892061-52a8e074-claude-agent-claude-fable-5
- **Intent:** I-1794891082-beadspil
- **Agent:** claude-agent (claude-fable-5)

## Summary

Pilot the beads (`bd`) issue tracker in this repository: commit `.beads/config.yaml` (prefix `hac`, dolt local-only,
no `node_id`), gitignore `.beads/embeddeddolt/`, pin `bd` in the `holon-base` Docker stage via `ARG BD_VERSION` with a
checksum-verified install and an in-build `bd version` smoke check, add agent guidance in `docs/issue_tracking.md`
(linked from `docs/agents.md`), and fence the committed surface with hermetic tests in the new declared module
`apps/sandbox-executor/tests/test_beads_integration.py`.

Execution notes so far:

- Step 1 probe result: the `bd` binary is **absent** in the execution sandbox (`command -v bd` finds nothing), so the
  config was authored by documented shape rather than taken from `bd init` output.
- Pin selection: the beads release feed reports latest release `v1.3.4` with assets
  `beads_1.3.4_linux_amd64.tar.gz`, `beads_1.3.4_linux_arm64.tar.gz` and a published `checksums.txt`; the upstream
  repository `steveyegge/beads` now redirects to `gastownhall/beads`.
- Config key shapes confirmed from upstream documentation: `issue_prefix` (set only at `bd init --prefix`),
  `dolt.local-only: true` (keeps Dolt data off the network, refuses remote wiring), `node_id` is per-machine state that
  must never live in the git-tracked `.beads/config.yaml`.
- The workspace has no YAML library in its dependency groups and the plan forbids new dependencies, so the
  config-shape tests assert the exact textual shape of the committed file instead of parsing it with a YAML library.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

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
  `beads_${BD_VERSION}_linux_${ARCH}.tar.gz` plus the release's `checksums.txt`, verify with `sha256sum -c`, extract
  and install the `bd` binary (tolerating root-level or one-directory-deep layout, as the upstream installer does), and
  finish with an in-build `bd version` smoke check so a broken install fails the layer.
- `apps/sandbox-executor/docker-bake.hcl`: exposed an overridable `BD_VERSION` variable; when empty (the default), no
  arg is passed, keeping the Dockerfile's `ARG BD_VERSION` default as the single pin.
- `apps/sandbox-executor/build_all_images.sh` forwards no build args today (bake reads its variables from the
  environment), so it is intentionally left unchanged, as the plan allows.
- No image build was attempted (network-isolated sandbox); correctness is enforced textually by Step 4's tests and at
  the next real image build.
