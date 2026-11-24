# Conflict-Resolver Role

Detail for [Git Flow](git_flow.md) section 9.1: how the shared target sync resolves code conflicts.

The shared target sync (`sync_with_target`) resolves the mechanical conflict classes without an agent and hands the
remaining `CODE` paths to its injected code resolver. Its default resolves nothing, so a code conflict aborts the merge.
`conflict_code_resolver(context)` (`flow/conflict_resolver_role.py`) is the resolver that dispatches the
`conflict-resolver` role container instead. The flow boundaries start passing it when the boundary wiring of the
resolve-conflicts plan lands; until then every boundary keeps the fail-closed default.

- **Role.** The container mounts the host worktree with the merge in progress, renders
  `holon-config/prompts/conflict_resolver.template.md` and runs one agent turn. It may edit only the conflicted files
  and may run no git write. After the turn it runs `uv run task lint` and then `uv run task test` itself, in the
  foreground, and reports one verdict: `resolved`, `cannot_decide`, `lint_failed`, `tests_failed` or `agent_failed`.
- **Tier ladder.** Attempts start on the cheapest model of the configured runner and move up one tier at a time (for
  `claude`: haiku, sonnet, opus). A configured model outside the ranked families is the last tier, so a partly ranked
  runner (`pi-agent` configured for codex) still tries it, and a runner without ranked models gets a one-tier ladder of
  its configured model. There is one attempt per tier.
- **Escalation.** Failed lint, failed tests, cannot-decide, an agent failure (timeout, non-zero exit or no verdict) or a
  rejected edit moves to the next tier. A rejected edit is a change outside the conflict set (test files included), a
  new untracked file, or markers left in a conflicted file. Before the next tier, the out-of-set edits are reverted, the
  new files are deleted and the conflicted files get their merge state back.
- **Immediate fail-closed.** Any change to the index, `HEAD` or `MERGE_HEAD` (the agent ran `git add`, `git commit` or
  `git merge --abort`) stops at once, with no further tier. So does a conflict set holding
  `holon-config/hygiene/module_budgets.json` when the intent carries no `module_budget_change`; that case blocks before
  any dispatch.
- **Exhaustion.** When the top tier fails, the merge is aborted and one blocked `target_sync` row records the conflicted
  paths, the last tier, the attempt count and the last verdict with its failing tests. The row is committed locally and
  nothing is pushed. Each failed attempt's diff is kept under `tmp/conflict-resolver/` for the human who takes over.
