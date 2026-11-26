# Ledger Pending Fragments

How the canonical ledgers in [Ledger Schema](ledger_schema.md) gain pending fragment files, and how readers merge them.

## Read side

A ledger may also have pending fragment files that are not yet promoted into the canonical file:
`holon-knowledge/ledger/pending/<ledger-stem>/<root-intent-id>.jsonl` (for example
`holon-knowledge/ledger/pending/plans/I-1795584436-ledger-fragments-and-promotion.jsonl`). The root intent id is the
first `/` segment of the row's `branch`, `intent_branch`, `plan_branch` or `parent_branch`, without a `--N-...` child
suffix, and `_unscoped` when the row has none (`sandbox_executor.ledger.fragments`).

Every reader of a canonical ledger reads the logical ledger: the canonical file plus its fragments
(`sandbox_executor.ledger.read_merged_rows` for a working tree, `read_merged_rows_at_ref` for a git ref, which lists
`pending/<stem>/` with `git ls-tree`).

- **Canonical wins.** Fragment rows are de-duplicated by row identity against the canonical file and against earlier
  fragments (fragment files are read in file-name order). Canonical rows are never de-duplicated against each other. The
  identities are:
  - plans: `(intent_branch, plan_id, ledger_revision)`, so revision rows stay distinct;
  - intents: `(branch or intent_id, ledger_revision, entry_type)`;
  - executions: `action_id` when present, otherwise
    `(plan_branch, execution_id, entry_type, ledger_revision, created_at)`;
  - any other row: its whole JSON object.
- **Instant ordering.** Canonical rows keep their file order. Each fragment row is placed after the longest canonical
  prefix whose latest `created_at` instant is not later than its own, so a canonical file written out of order places it
  before the first canonical row that pushes that running maximum past it. `Z`, `+00:00` and other offsets compare as
  UTC instants, and a naive timestamp is UTC. On a tie the canonical row comes first, then the fragment rows in input
  order. Fragment rows without a parseable `created_at` come last.
- **No fragments, no change.** Without fragment files a reader returns exactly the canonical rows (or the raw canonical
  text for text-based parsers), in file order, skipping blank and malformed lines as before. A path outside
  `holon-knowledge/ledger/` (for example a scratch ledger in `/tmp`) never reads fragments.

## Write path

Every append goes through `sandbox_executor.ledger.atomic_ledger_append` (the `rows.py` append functions,
`knowledge.records.append_action_event` and the stage writers all call it), and no append writes a canonical ledger
file.

- **Buffered, routed on close.** The handle buffers the written lines and routes them when the `with` block exits
  normally. An exception inside the block writes nothing. A line for a canonical ledger
  (`holon-knowledge/ledger/<name>.jsonl`) goes to `pending/<stem>/<root-intent-id>.jsonl`, with the root intent id taken
  from the row's branch fields as described in [Read side](#read-side). A row without branch fields (most action rows)
  goes to the root intent of the branch checked out in that repository, read from its `HEAD` file. It goes to
  `_unscoped` when `HEAD` is detached or the branch is not an `I-...` intent branch. A line that is not a JSON object
  also goes to `_unscoped`. One handle may write rows of several root intents: each target gets its rows in their
  written order.
- **Lock and prefix check per target.** Each fragment is appended under its own `LedgerLock`, followed by
  `assert_ledger_prefix`. A path outside `holon-knowledge/ledger/` (a scratch ledger, a test file) receives the buffered
  text unchanged, under the same lock and check.
- **Z timestamps.** A routed row without `created_at` is stamped with the current UTC instant as
  `YYYY-MM-DDTHH:MM:SS.ffffffZ` (`ledger.order.utc_now_z`). A parseable `created_at` with an offset is rewritten as the
  same instant in that form. A value that already ends in `Z`, or does not parse, is kept, and the line is then written
  byte for byte. `ActionRecord` and `SummaryRecord` default to the same form.
- **Uniqueness on the merged view.** `validate_ledger_file_unique_ids` reads `read_merged_rows`, so the post-append
  checks (`append_summary_record`, the planner and `flow/git_refs.py`) see canonical plus fragment rows, also when no
  canonical file exists yet.
- **Committing fragments.** Engine commits stage `ledger_commit_paths(repo_dir, *ledger_names)` from
  `ledger/fragments.py`: each named ledger's canonical file and `pending/<stem>/` directory, only those that exist. The
  planner, intent creator, executor, `sync_conflict`, `flow/git_refs.py`, Stage 4's ledger commit, Stage 5 and
  target-sync commits use it. Agent-side pathspecs exclude the pending directory (`PENDING_EXCLUDE_PATHSPEC`,
  `ENGINE_OWNED_LEDGER_DIRS`), so an agent or resolver commit never carries engine-owned fragments.
- **Canonical files change only through promotion or merge-time repair.** Conflict resolution of a canonical file
  (`ledger/reconcile.py`, `flow/ledger_interleave.py`) still rewrites it under the lock (see
  [Ledger Schema](ledger_schema.md)). `tests/test_ledger_no_canonical_write.py` checks that the append APIs leave
  canonical bytes unchanged, and that no module outside the ledger package uses `LedgerLock` or opens a ledger for
  append.

## Promotion

`holon ledger promote [--force] [--every N] [--check-only] [--json] [--target-worktree PATH]`
(`sandbox_executor.ledger.promote`) moves pending fragment rows into the canonical ledgers. It only changes files under
`holon-knowledge/ledger/` and runs no git command; committing the result is the caller's job.

- **Threshold.** Promotion runs when at least `N` distinct root intents have a fragment in any of the three
  `pending/<stem>/` directories. `N` is `--every`, else `HOLON_LEDGER_PROMOTE_EVERY`, else `10`; a non-integer or a
  value below 1 in the variable or in `--every` is an error (exit 2). `_unscoped` does not count toward the threshold,
  but its fragments are promoted with the rest once the threshold is met. `--force` promotes whatever the count, and
  `--check-only` prints `root intents with fragments: k / N (threshold met|not met)` and changes nothing.
- **Additions only, verbatim.** For each ledger, the rows of every fragment whose identity (the reader's
  `ledger.merged.row_identity`, see [Read side](#read-side)) is not already in the canonical file or an earlier fragment
  are appended as their original line text, ordered by `created_at` instant (ties in fragment file-name and line order,
  rows without a parseable instant last). The append runs under `LedgerLock` with `assert_ledger_prefix`; a canonical
  file that does not end in a newline first gains one.
- **Append, then delete.** Promoted fragments are deleted only after the canonical append, and empty `pending/<stem>/`
  and `pending/` directories are removed. A crash in between leaves duplicates that readers hide and that the next run
  skips before finishing the deletion, so promotion is idempotent: a second run changes zero bytes.
- **Held fragments.** A fragment that is not UTF-8 or has a non-blank line that is not a JSON object is neither promoted
  nor deleted. It is reported as a warning on stderr (and in `held` with `--json`), and the exit code stays 0.
- **Snapshot check.** Each fragment's bytes are recorded when the promotion is planned and re-read before the ledger is
  written. Every ledger is checked before any is written: if any fragment of any ledger changed, nothing is appended to
  any ledger, no fragment is deleted, and the command exits 1.
- **Run it on a quiescent checkout.** Run promotion only on a checkout with no active flows (normally `main`). A
  fragment writer blocked on a fragment's lock at the moment the fragment is deleted would append to the deleted file.
- **Reader view.** `read_merged_rows` returns the same identity multiset before and after promotion. The order is also
  the same when no promoted row's instant is earlier than the canonical file's latest instant; otherwise only the
  promoted rows' positions change, because they now sit at the end of the canonical file.
