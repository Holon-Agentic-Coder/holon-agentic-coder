# Stage 3 Push Safeguards

The host pushes the execution branches only after the executor container has run (see the
[Flow Equivalence Guarantee](../git_flow.md#flow-equivalence-guarantee)). Two safeguards keep that push from silently
losing work.

## Token Scope Pre-flight

GitHub refuses a push that changes `.github/workflows/**` from a token without the `workflow` scope, and the host only
pushes after the executor has run. So when the plan's touch set or the intent's declared `scope` names a path that is or
may match a workflow file (`.github/workflows/ci.yml`, `.github/**`, `**`), Stage 3 reads the token's scopes right after
its dry-run and `--skip-push` checks (`flow/stages/token_scope_guard.py`, `flow/github/token_scopes.py`). A token whose
`X-OAuth-Scopes` header lacks `workflow`, or no token at all, fails the stage before admission, the plan refresh and the
executor container (`dispatched: false`, `token_scope: "missing"`, `workflow_paths`), naming
`gh auth refresh -h github.com -s workflow` (or `gh auth login -h github.com -s workflow`). A fine-grained, app or
user-to-server token, or a failed scope request, is unverifiable: the stage logs a note and goes on. The check runs only
when the worktree's remote pushes over HTTPS to github.com (`git remote get-url --push`); an SSH push is authorised by
the host's key, not the token, so it is skipped with a note. Plans that touch no workflow path make no request. The
token value is never logged or put in an error.

## A Refused Push Keeps the Commits

When the host's Stage 3 push is refused, every new execution branch's tip is pinned under
`refs/holon/recovery/<execution id>` in the worktree's repository, so it survives worktree removal and branch deletion,
and a fast-forward-only push to `origin` `holon-recovery/<execution id>` is attempted (it succeeds for a refusal tied to
the branch name, not for one tied to the commits' content, such as the missing `workflow` scope). The stage error names
each ref and the republish command (`git push origin refs/holon/recovery/<execution id>:refs/heads/<execution branch>`),
plus the refresh command for a workflow-scope refusal; the payload carries `recovery_refs` and `recovery_pushed`
(`flow/execution_recovery.py`).
