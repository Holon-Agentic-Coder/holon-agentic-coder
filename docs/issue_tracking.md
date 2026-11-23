# Issue tracking with GitHub Issues

Work items for this repository live in its GitHub Issues, with the web view and the Project board as the places to
browse and plan them.

---

## Where work items live

Issue data lives outside the git tree:

- nothing tracker-related is tracked in the repository;
- there is no tracker ref, so branches never carry issue data and cross-branch tracker merges cannot happen;
- nothing extra is installed in the `holon/agent-*` images for issue work.

## Referencing work

- Reference a work item as `#N` in commit messages and pull request text.
- A `Closes #N` line in the pull request body closes the issue when the pull request merges into the default branch.

## Type and priority

- **Type:** exactly one label from `type:bug`, `type:feature` and `type:task`.
- **Priority:** exactly one label from `priority:P0` to `priority:P3`, where `priority:P0` is the highest.

## Dependencies

Dependencies between work items use GitHub issue dependencies (blocked by / blocking) and sub-issues. No dependency data
lives in the repository.

## Who talks to GitHub Issues

Tracker access belongs to the host side:

- of the system's components, only the host-side flow engine reads and writes issues;
- issue text reaches role agents (planner, executor, reviewers) through the intent they are given;
- role containers therefore need neither `gh` nor a token for issue work.

## Change path

There is a single change path for this repository: the planned flow. Source changes land only through plan and execution
branches and their reviewed merges. Because issues live outside the tree, issue edits never touch the repository and
need no separate write path.

---

## Related documents

- [`agents.md`](agents.md) — the agent model that receives issue text through the intent
- [`git_flow.md`](git_flow.md) — the git discipline that `#N` references and `Closes #N` lines sit within
- [`safety.md`](safety.md) — the invariants the single change path upholds
