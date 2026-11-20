# Plan for I-1794657793-mergemth

- **Plan ID:** P-1794657809-c6ebdbd6-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-14T12:03:30.582Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** Not reframed. One clarification of scope: the lookup lives in a new focused
  module `flow/github/merge_method.py` (because `github/merge.py` is already at its 299-line budget in
  `apps/sandbox-executor/flow_module_budgets.json`), and goal 3 requires edits to `docs/safety.md`, `docs/git_flow.md`,
  `docs/architecture.md`, `docs/examples.md` and two stage docstrings that the intent `scope` list does not name.
- **Exploration stance:** conservative. The change is a small, well-understood fix to a safety-relevant merge guard; the
  value is in not weakening the fail-closed checks, not in exploring alternatives.
- **Safety priority level:** elevated
- **Priority Justification:** `docs/safety.md#sub-intent-merge-rules` names `flow/github/merge.py` as the only code path
  that merges a pull request and fixes its flags; `holon-config/world/constraints.md` section 1 keeps `main`
  human-merged. The change relaxes the "merge commit only" rule, so every existing refusal (main, master, default
  branch, expected plan branch, head checks, `--match-head-commit`) must stay byte-for-byte unchanged and the new lookup
  must fail closed.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The failure is fully diagnosed (GitHub rejects `--merge` when `allow_merge_commit` is false) and
  the fix is prescribed by the intent. Step 1 is a short confirmation read, not exploration.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 4.5   |
| impact_pred         | 80.0  |
| cost_pred           | 14.5  |
| learning_value_pred | 4.0   |
| ev_pred             | 56.55 |

## Plan Description & Strategy

Today `merge_child_pr` (`apps/sandbox-executor/src/sandbox_executor/flow/github/merge.py`) always runs
`gh pr merge <n> --merge --match-head-commit <sha>`. On a squash-only repository this fails, and a decomposed flow stops
at Stage 3. The plan:

1. Confirm the facts the fix depends on (gh api output fields, test fakes, budgets, doc mentions).
2. Add a new, pure-plus-thin-IO module `flow/github/merge_method.py` that reads `gh api repos/{owner}/{repo}` through
   the existing injectable `GhRunner` and returns `--merge` when `allow_merge_commit` is exactly `true`, else `--squash`
   when `allow_squash_merge` is exactly `true`, else a refusal reason. A failed lookup is a refusal. Rebase is never
   chosen.
3. Wire it into `merge_child_pr` before anything is written (before the plan branch is brought into the child head), so
   a refusal leaves origin untouched; replace the hard-coded `--merge` with the chosen flag. All other guards are
   unchanged.
4. Extend the hermetic `FakeGitHub` (`tests/flow/fake_gh.py`) with an `api` handler and a real squash merge, and add
   tests for the five required cases plus edge cases.
5. Update the module docstrings and every document that says "merge commit only".
6. Verify: affected tests, the full suite once, `uv run task lint`, `uv run task lint-docs`.

Touch set (module granularity):

- `apps/sandbox-executor/src/sandbox_executor/flow/github/merge_method.py` (new)
- `apps/sandbox-executor/src/sandbox_executor/flow/github/merge.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/stages/integration.py` and `flow/stages/children.py` (docstring
  wording only, same line count)
- `apps/sandbox-executor/flow_module_budgets.json` (new entry at creation size; `github/merge.py` budget only if it
  cannot be kept at 299)
- `apps/sandbox-executor/tests/flow/fake_gh.py`, `tests/flow/github/test_merge.py`,
  `tests/flow/github/test_merge_method.py` (new)
- `docs/safety.md`, `docs/git_flow.md`, `docs/architecture.md`, `docs/examples.md`

No new dependencies, no ledger edits, no entrypoint imports, no change to who merges the root intent into `main`.

---

## Step 1: Confirm the merge-method facts and the test seams

- **Sub‑intent recommendation:** NO
- **Reasoning:** A read-only check of a handful of files; cheap and removes the main implementation uncertainties.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Pin down the exact data the lookup reads and every place that asserts the old merge flag.
- **Git branch:** `I-1794657793-mergemth/P-1794657809-c6ebdbd6-claude-agent-claude-opus-5-5/E-{timestamp}-mergemth/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Confirm that `gh api repos/{owner}/{repo}` (literal placeholders, which `gh` fills from the git remote of the working
  directory, the same way `gh repo view` in `lookup_default_branch` resolves the repository) returns JSON with boolean
  `allow_merge_commit`, `allow_squash_merge` and `allow_rebase_merge`. Note that these fields are present only for a
  token with push access; a missing or non-boolean field must therefore be treated as "not allowed", never as allowed.
- Grep tests for assertions on the merge flag: `tests/flow/github/test_merge.py` (`FORBIDDEN_FLAGS` contains `--squash`;
  one test asserts the exact `--merge` call), `tests/flow/stages/test_children.py` (asserts `--merge` and no `--squash`
  in the decomposed flow), `tests/flow/fake_gh.py` (`_pr_merge` refuses anything but `--merge`), and
  `tests/flow/decomposition_fakes.py` (patches `merge.run_gh` with the fake).
- Confirm `github/merge.py` is 299 lines against a 299 budget, and that `scripts/check_flow_module_budgets.py` requires
  an entry for every module under `flow/`.
- Check `tests/flow/test_package_exports.py` and `tests/flow/test_patch_targets.py` for rules that a new public module
  or a new patch target must satisfy.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 2 (no network from the sandbox; the facts come from
  the code and gh documentation, not from calling the real API).
- **Potential failure modes for this step:** Missing a test that pins `--merge`, so Step 4 or Step 6 finds it late.
- **Guardrails and early‑abort checks:** Read-only; no `gh` call against the real repository.

### Success & Discard Criteria

- **Success:** A list of every file asserting the merge flag and the confirmed JSON field names.
- **Discard:** Not applicable; if the field names differ, adjust Step 2 rather than abort.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.4   |
| impact_pred         | 10.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 10.08 |

### Step Metrics Rationale

`EV = 0.97 * 10.0 + 0.5 * 3.0 - 0.3 * 0.4 - 1.0 = 9.70 + 1.50 - 0.12 - 1.0 = 10.08`. Low entropy, low cost; the learning
value is the confirmed field semantics (push-access-only fields) that make the fail-closed rule correct.

---

## Step 2: Add the merge-method lookup module

- **Sub‑intent recommendation:** NO
- **Reasoning:** About 50 lines in one new, self-contained module; low risk and fully covered by Step 4 tests.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Own the "which merge method may the flow use" decision in one focused module.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `apps/sandbox-executor/src/sandbox_executor/flow/github/merge_method.py` with a module docstring in Australian
  English explaining: a child PR is merged with a merge commit when the repository allows it, otherwise with a squash;
  never a rebase; a failed lookup or no allowed method refuses (fail closed).
- Constants: `MERGE_FLAG = "--merge"`, `SQUASH_FLAG = "--squash"`, and the API path `repos/{owner}/{repo}`.
- Pure function `choose_merge_flag(settings: object) -> tuple[str | None, str]`: when `settings` is not a dict, return
  `(None, reason)` ("the repository merge settings could not be read"); when
  `settings.get("allow_merge_commit") is True` return `(MERGE_FLAG, "")`; elif
  `settings.get("allow_squash_merge") is True` return `(SQUASH_FLAG, "")`; else return
  `(None, "the repository allows neither merge commits nor squash merges ...")`. Strict `is True` so a missing, `null`
  or string value counts as not allowed. `allow_rebase_merge` is never consulted.
- Thin IO function `lookup_merge_flag(repo_dir: str, runner: GhRunner) -> tuple[str | None, str]`: call
  `runner(["api", "repos/{owner}/{repo}"], repo_dir)`; a non-zero return code, empty stdout or invalid JSON is a refusal
  that names the failed lookup (stderr passed through `redact_agent_secrets`, trimmed); otherwise return
  `choose_merge_flag(parsed)`. Keep imports one-directional: `merge.py` imports `merge_method.py`, never the reverse. So
  `merge_method.py` types its runner parameter with its own `collections.abc.Callable` alias and parses the JSON inline,
  rather than importing `GhRunner` or `_gh_json` from `merge.py`.
- Full type annotations and docstrings (inputs, outputs, side-effects) per `holon-config/world/ruleset.md` section 2;
  complexity well under `C901 <= 10`.
- Add `"github/merge_method.py": <line count>` to `apps/sandbox-executor/flow_module_budgets.json` at creation size, and
  append one sentence to its description, in the style of the existing ones.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (p_success 0.93, above 0.6, so no sub-intent needed)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md#sub-intent-merge-rules` (fail closed), ruleset section 2 (typing, docstrings), "no
  new dependencies" from the intent.
- **Potential failure modes for this step:** Treating a missing field as allowed (fail open); an import cycle with
  `merge.py`; leaking a token in a refusal reason.
- **Guardrails and early‑abort checks:** Strict `is True` checks; reasons pass through `redact_agent_secrets`; ruff
  clean before moving on.

### Success & Discard Criteria

- **Success:** The module imports cleanly, `ruff check` passes, and the pure function returns the right flag for every
  combination of the two booleans.
- **Discard:** If the design needs more than about 80 lines or a second gh call, stop and re-plan.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 1.0   |
| impact_pred         | 35.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 30.00 |

### Step Metrics Rationale

`EV = 0.93 * 35.0 + 0.5 * 1.5 - 0.3 * 1.0 - 3.0 = 32.55 + 0.75 - 0.30 - 3.0 = 30.00`. High impact because this is the
decision the acceptance criteria hinge on; small, isolated, well-understood code.

---

## Step 3: Use the looked-up flag in `merge_child_pr`, before anything is written

- **Sub‑intent recommendation:** NO
- **Reasoning:** A few lines in one function plus a docstring; the risk is the line budget and keeping the guards
  intact, both checked by lint and tests.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Merge with the repository's allowed method while leaving every existing guard unchanged.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `merge_child_pr`, after the idempotent "already `MERGED`" branch and before `_verified_head` (which brings the plan
  branch into the child head and pushes), call `lookup_merge_flag(repo_dir, gh)`. When it returns `None`, return
  `MergeResult(False, reason=f"... merge method ...: {reason}")` immediately, so a refusal writes nothing to origin.
- Replace the hard-coded `"--merge"` in the `gh pr merge` call with the looked-up flag; the argument list stays
  `["pr", "merge", <n>, <flag>, "--match-head-commit", <head_sha>]`. No `--auto`, `--admin`, `--delete-branch`,
  `--rebase` ever.
- Leave `merge_refusal_reason`, `_guard`, `_verified_head`, `_await_ready`, `merge_into_branch`, the head re-read and
  `_merged_result` unchanged. A PR whose base is `main` or the default branch is still refused by `_guard` (the lookup
  happening first does not change that outcome: either refusal returns `merged=False` and `gh pr merge` is never run).
- `_merged_result` reads `mergeCommit.oid`, which GitHub sets to the squash commit for a squash merge, so the recorded
  `merge_commit` and the next child's cut point (Stage 3 children) remain correct.
- Rewrite the module docstring: "merge commit when the repository allows it, otherwise squash (see `merge_method.py`);
  never `--rebase`, `--auto`, `--admin` or `--delete-branch`"; update the `merge_child_pr` docstring step list to
  mention the merge-method lookup and `gh pr merge <flag> --match-head-commit`.
- Keep `merge.py` at or below its 299-line budget by keeping the docstrings the same length (for example the one-line
  summary "merge commit or squash, CI green and mergeable, never `main`"). Only if that is impossible, raise the budget
  by the exact delta and add a sentence to the budgets description explaining why (the documented convention).
- `merge_into_branch` (used to bring the plan branch into a child and by Stage 3 integration) keeps its local
  `git merge --no-ff`; it pushes to a non-protected branch and is not affected by the repository's PR merge settings.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (p_success 0.90)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md#sub-intent-merge-rules`; `holon-config/world/constraints.md` section 1 (only
  humans merge to `main`); code placement rules (no entrypoint imports, complexity limits).
- **Potential failure modes for this step:** Placing the lookup after `_verified_head` (a refusal would then follow a
  push into the child head); accidentally editing a guard; exceeding the module budget; `PLR0911` (too many returns) in
  `merge_child_pr`.
- **Guardrails and early‑abort checks:** `git diff` of `merge.py` must show no change inside the guard functions;
  `python scripts/check_flow_module_budgets.py` and `ruff check` pass; if returns exceed the limit, fold the
  lookup-and-refuse into a small helper.

### Success & Discard Criteria

- **Success:** Existing tests in `tests/flow/github/test_merge.py` and `tests/flow/stages/test_children.py` still pass
  with the fake defaulting to "merge commits allowed" (after Step 4's fake extension), and the diff is limited to the
  lookup call, the flag, and docstrings.
- **Discard:** Any need to change a refusal message or guard behaviour to make tests pass; stop and re-plan.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.2   |
| impact_pred         | 35.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 28.89 |

### Step Metrics Rationale

`EV = 0.90 * 35.0 + 0.5 * 1.5 - 0.3 * 1.2 - 3.0 = 31.50 + 0.75 - 0.36 - 3.0 = 28.89`. Slightly lower success than Step 2
because the 299-line budget and the "lookup before any write" ordering are the two easy mistakes.

---

## Step 4: Extend the fake `gh` and add hermetic tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Test-only changes in `tests/flow/`, inside the intent scope; reusable fake already exists.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove each acceptance criterion with an injected gh runner and a real bare origin, no network.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- `tests/flow/fake_gh.py`: add attributes `allow_merge_commit = True`, `allow_squash_merge = True`,
  `allow_rebase_merge = False` and `repo_settings_fail = False`; add an `("api", "repos/{owner}/{repo}")` handler that
  returns those booleans as JSON, or exit code 1 with `HTTP 502` when `repo_settings_fail` is set. Defaults keep every
  existing test (including the decomposed-flow test that asserts two-parent merge commits) behaving as today.
- `_pr_merge`: accept exactly one of `--merge` or `--squash` and only when the matching `allow_*` setting is true
  (mirroring GitHub's "Merge commits are not allowed on this repository" rejection); refuse `--rebase` always. For
  `--squash`, perform a real squash on origin (`git merge --squash` of the PR head into the base, then a single-parent
  commit) and record its sha as `mergeCommit.oid`. Update the module docstring.
- `tests/flow/github/test_merge.py`: drop `--squash` from `FORBIDDEN_FLAGS` where it describes "never used" flags and
  instead assert per case; keep `--rebase`, `--auto`, `--admin`, `--delete-branch` forbidden in every merge call. Add
  tests:
  - merge commit allowed (both allowed and merge-only): exactly one call
    `["pr", "merge", n, "--merge", "--match-head-commit", head]`, plan branch tip is a two-parent commit.
  - squash only (`allow_merge_commit = False`): exactly one call with `--squash` and `--match-head-commit head`; the
    plan branch tip is a single-parent commit containing the child's file; `MergeResult.merged` is true and
    `merge_commit` equals the plan branch tip.
  - neither allowed (including rebase-only): refused with the "neither" reason, no `pr merge` call, plan branch and
    child head unchanged on origin (nothing pushed).
  - lookup failure (`repo_settings_fail`, plus parametrised invalid JSON and missing fields via a small wrapper runner):
    refused, no `pr merge` call, origin unchanged.
  - main still refused: base `main` with squash-only settings is refused with the existing "main, master or the
    repository default branch" reason and no `pr merge` call; same for the default-branch case (`default_branch`
    `trunk`).
- `tests/flow/github/test_merge_method.py` (new): parametrised pure tests of `choose_merge_flag` over the boolean grid
  and odd values (`None`, `"true"`, missing keys, a list instead of a dict), and `lookup_merge_flag` with a stub runner
  (non-zero exit, empty stdout, invalid JSON, redaction of a fake token in stderr, and the exact argument list
  `["api", "repos/{owner}/{repo}"]`).
- `tests/flow/stages/test_children.py`: keep the existing merge-commit assertions (default settings); optionally add one
  decomposed-flow case with `allow_merge_commit = False` asserting both children merge with `--squash` and Stage 3
  reaches integration, if the existing `decomposed_flow` helper exposes the fake before the run without new plumbing.
- All tests hermetic (repository `network_guard` and `gh_write_guard` fixtures remain in force); no real `gh` call.

### Dependencies & Criticality

- **Depends on:** Steps 2 and 3
- **Is Bottleneck:** NO (failures here point at fixable test or code bugs, not plan failure)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 3 (test changes declared in the plan: they are, here);
  `apps/sandbox-executor/docs/hermetic_testing.md`.
- **Potential failure modes for this step:** The fake's squash diverging from GitHub (for example leaving the child
  branch as an ancestor); slow tests from extra git clones; a test inadvertently relying on the real `run_gh`.
- **Guardrails and early‑abort checks:** Every new test passes `runner=gh` or patches `merge.run_gh`; assert the
  single-parent shape of a squash.

### Success & Discard Criteria

- **Success:** All five intent-required cases are covered and pass; existing tests in `tests/flow/` still pass.
- **Discard:** If making the fake realistic needs changes outside `tests/flow/`, stop and re-plan.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.0   |
| impact_pred         | 25.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 19.20 |

### Step Metrics Rationale

`EV = 0.88 * 25.0 + 0.5 * 2.0 - 0.3 * 1.0 - 3.5 = 22.00 + 1.00 - 0.30 - 3.5 = 19.20`. Most of the plan's cost is here
(fake extension plus about ten tests); the realistic-squash fake is the main source of rework risk.

---

## Step 5: Update docstrings and documents that say "merge commit only"

- **Sub‑intent recommendation:** NO
- **Reasoning:** Wording-only edits required by goal 3; risk is limited to prettier formatting.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Keep the safety documentation truthful about how child PRs are merged.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- `docs/safety.md` (Sub-intent merge rules, the "Merge commit only." bullet): rename to "Merge commit, else squash." and
  state that the method is read from `gh api repos/{owner}/{repo}` before anything is written; a merge commit is used
  when `allow_merge_commit` is true, otherwise a squash when `allow_squash_merge` is true; a failed lookup or neither
  allowed refuses; never `--rebase`, `--auto`, `--admin` or `--delete-branch`; note that a squash keeps the child's
  commits on its execution branch and PR rather than in the plan branch history.
- `docs/git_flow.md` around line 319, `docs/architecture.md` around lines 41 and 357, and `docs/examples.md` around line
  1798: replace "merge commit only" / "with a merge commit" for child PR merges with the same rule. Leave text that
  describes `merge_into_branch` and Stage 3 integration (local `--no-ff` merges) as is.
- Docstrings: `flow/stages/integration.py` line 1 ("every child's merge commit" becomes "every child's merge or squash
  commit") and `flow/stages/children.py` line 5 if it says merge commit, keeping the line count unchanged for the module
  budgets.
- Australian English throughout; run `npx prettier --write` only on the edited Markdown files, then
  `uv run task lint-docs`.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` is a governance document: the edit records the new behaviour only, and the human
  maintainer reviews it in the root intent PR before it reaches `main`.
- **Potential failure modes for this step:** Prettier reflow churn; a missed mention.
- **Guardrails and early‑abort checks:** A final grep for "merge commit only" and "--merge --match-head-commit" across
  `docs/` and `apps/sandbox-executor/src` returns nothing stale.

### Success & Discard Criteria

- **Success:** No stale "merge commit only" text; `uv run task lint-docs` passes.
- **Discard:** Not applicable.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.4   |
| impact_pred         | 8.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 6.23  |

### Step Metrics Rationale

`EV = 0.95 * 8.0 + 0.5 * 0.5 - 0.3 * 0.4 - 1.5 = 7.60 + 0.25 - 0.12 - 1.5 = 6.23`. Mechanical, low-risk edits.

---

## Step 6: Verify with the affected tests, the full suite once, and both lint tasks

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard verification; no timing or performance measurement is involved, so no repeated runs.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Demonstrate every acceptance criterion with the repository's own commands.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- From `apps/sandbox-executor`:
  `uv run pytest tests/flow/github/test_merge.py tests/flow/github/test_merge_method.py tests/flow/stages/test_children.py tests/flow/test_child_progress.py tests/flow/test_package_exports.py tests/flow/test_module_budgets.py tests/flow/test_patch_targets.py`
  first.
- Then the full suite once with `uv run task test` (which runs pytest via uv).
- Then `uv run task lint` (ruff, format check, `uv lock --check`, flow module budgets) and `uv run task lint-docs`.
- Confirm `git status` shows no change under `holon-knowledge/ledger/` and that `uv.lock` and `pyproject.toml` are
  unchanged (no new dependencies).

### Dependencies & Criticality

- **Depends on:** Steps 3, 4 and 5
- **Is Bottleneck:** YES (p_success 0.90)

### Safety & Constraint Considerations

- **Relevant rules:** Test-Run Discipline in the planner contract; constraints section 3 (ledgers append-only).
- **Potential failure modes for this step:** An unrelated flaky test in the full suite; a ruff complexity rule.
- **Guardrails and early‑abort checks:** Re-run only a failing test file once to separate flake from regression; never
  loop the full suite.

### Success & Discard Criteria

- **Success:** All selected tests and the full suite pass; both lint tasks pass.
- **Discard:** A regression in a guard test (main or default-branch refusal) that cannot be fixed without changing the
  guard: stop and report.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.5   |
| impact_pred         | 15.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 11.60 |

### Step Metrics Rationale

`EV = 0.90 * 15.0 + 0.5 * 1.5 - 0.3 * 0.5 - 2.5 = 13.50 + 0.75 - 0.15 - 2.5 = 11.60`. The full-suite run dominates the
cost; the risk is mainly unrelated flakiness and the module-budget check.

---

### Strategy Rationale

The intent is accepted as is: the bug is precise, the fix is narrow, and it unblocks every decomposed flow on a
squash-only repository (this repository included), so its impact is high relative to a small cost. The only structural
choice is placement: `github/merge.py` is exactly at its 299-line budget, so the merge-method decision goes in a new
focused module (`github/merge_method.py`), matching the repository's "one module per concern" rule and the budgets
file's convention of adding a new module at creation size rather than growing an existing one. The lookup runs before
any push, so a refusal is side-effect free, and every existing guard is left untouched.

Aggregation: entropy and cost are sums of the steps (`0.4 + 1.0 + 1.2 + 1.0 + 0.4 + 0.5 = 4.5`;
`1.0 + 3.0 + 3.0 + 3.5 + 1.5 + 2.5 = 14.5`). `p_success` is a qualitative judgement driven by the bottleneck Step 3
(0.90, the budget and ordering risks) and Step 4 (0.88, the realistic squash fake), not their product, because a failure
in either is fixable within the same execution. Impact (80) is judged for the whole intent rather than summed. Learning
value (4.0) is modest: the push-access-only semantics of the `allow_*` fields and a reusable squash-capable fake.
`EV = 0.88 * 80.0 + 0.5 * 4.0 - 0.3 * 4.5 - 14.5 = 70.40 + 2.00 - 1.35 - 14.5 = 56.55`.

Strong assumptions: `gh api repos/{owner}/{repo}` resolves the repository from the worktree's remote exactly as
`gh repo view` already does; the flow's token has push access (it already pushes branches and merges PRs), so the
`allow_*` fields are present; GitHub reports the squash commit as `mergeCommit.oid`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `docs/safety.md#sub-intent-merge-rules`: the only merge path, fail-closed base checks, ready-to-merge checks.
  - `holon-config/world/constraints.md` section 1: only human operators merge to `main`.
  - `holon-config/world/constraints.md` section 3: ledgers append-only (this plan touches none).
  - `holon-config/world/ruleset.md` sections 1 to 3: `uv`, no new dependencies, typing and docstrings, declared test
    changes.
  - `apps/sandbox-executor/flow_module_budgets.json`: per-module line budgets checked by `uv run task lint`.
- **Potential violations or edge cases:**
  - Fail-open lookup (missing field treated as allowed) would let the flow attempt a merge method the repository does
    not allow; harmless (GitHub rejects) but violates goal 2.
  - Lookup placed after the plan branch is pushed into the child head would leave a write behind a refusal.
  - A squash removes the child's individual commits from the plan branch history; the PR and execution branch keep them,
    and safety.md is updated to say so.
  - The intent `scope` names only `merge.py` and `tests/flow/`; this plan also touches a new sibling module, the budgets
    file, two stage docstrings and four docs, all required by goals 1 and 3 and the module-budget rule.
- **Mitigations built into the plan:** Strict `is True` checks; lookup before `_verified_head`; unchanged guard
  functions verified by diff; tests for lookup failure, neither allowed, rebase-only, main and default-branch refusal
  with squash-only settings; no `--rebase`, `--auto`, `--admin` or `--delete-branch` asserted in every merge call.
- **Residual risk accepted (and why):** Behaviour against the real GitHub API is verified only through the fake; the
  JSON fields are stable, documented REST fields and the call goes through the same injectable runner as the existing
  lookups, so the risk is low. Merging the root intent to `main` stays with the human maintainer.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 4.5
- **Budget Compliance:** The strategy fits within budget (4.5 of 15.0), so it runs as a single plan with no sub-intent
  list.
