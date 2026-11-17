# Issue tracking with beads (bd)

This repository carries its own issue tracker: [beads](https://github.com/steveyegge/beads) (`bd`), a Dolt-backed
tracker whose issue data syncs over the dedicated git ref `refs/dolt/data`. Every target repository gets exactly one
tracker, and this repository's issue prefix is `hac` — issue ids look like `hac-1a2b`.

The pinned `bd` binary ships in every `holon/agent-*` image: the `holon-base` stage of
`apps/sandbox-executor/Dockerfile` installs the release pinned by `ARG BD_VERSION` and proves it runs offline with an
in-build `bd version` check.

---

## Committed and ignored tracker state

- `.beads/config.yaml` is the only tracked tracker file. It sets `issue_prefix: hac` and `dolt.local-only: true`, so
  sandboxed role agents never attempt a Dolt network remote.
- `.beads/embeddeddolt/` is the embedded Dolt database — per-clone machine state. It is gitignored and must never be
  tracked.
- `node_id` is per-clone node identity. It must never be committed to `.beads/config.yaml`; bd keeps it in per-machine
  configuration.
- Never commit `issues.jsonl` snapshots. Sync happens through `refs/dolt/data` only (see below).

## Agent workflow

1. **Find work:** `bd ready` lists open issues with no active blockers.
2. **Claim before starting:** `bd update <id> --claim` atomically assigns the issue to you and moves it to
   `in_progress`. Claim exactly the issue you are about to work on, nothing more.
3. **Create when needed:** `bd create "title" -d "description"` files new work with an auto-assigned `hac-` id.
4. **Close when done:** `bd close <id> -r "reason"` when the work has landed.

## Sync over refs/dolt/data

Issue data lives in the Dolt database, not in source files, and moves between clones with `bd dolt push` and
`bd dolt pull` against the git ref `refs/dolt/data`. Source branches never carry issue-data changes.

With `dolt.local-only: true` committed, remote Dolt sync is disabled inside sandboxes: `bd dolt push`, `bd dolt pull`
and `bd sync` report remote sync as disabled, and `bd dolt remote add` is refused. Issue edits made in a sandbox stay in
the clone's local Dolt database until a context with sync access publishes them on `refs/dolt/data`.

### Two concurrent flow branches

Two flows working on separate source branches of the same repository share one tracker without touching each other's
source changes:

1. Each flow claims its own issues (`bd update <id> --claim`) and edits issue data only through `bd` commands.
2. Issue data merges at the Dolt data layer on `refs/dolt/data`, never through source-branch merges: pull before
   publishing (`bd dolt pull`, then `bd dolt push`), and bd reconciles concurrent issue edits in Dolt.
3. Because neither flow commits issue data to its source branch, rebases and merges of the flow branches never conflict
   on tracker state.

## The narrow write-path exception

The sole change path for this repository is the planned flow: source changes land only through plan/execution branches
and their reviewed merges. Direct `bd` writes to issue data are the one narrow exception, and it is a closed list:

- direct bd writes may touch exactly `refs/dolt/data` plus `.beads/` data (the local Dolt database under
  `.beads/embeddeddolt/`), and
- never source files — no code, configuration, documentation or test change may ride on the exception.

Changing `.beads/config.yaml` itself is a source change and goes through the normal flow, not through the exception.

---

## Related documents

- [`agents.md`](agents.md) — the agent model that performs this workflow
- [`git_flow.md`](git_flow.md) — the git discipline the exception is carved out of
- [`safety.md`](safety.md) — invariants the exception must not weaken
