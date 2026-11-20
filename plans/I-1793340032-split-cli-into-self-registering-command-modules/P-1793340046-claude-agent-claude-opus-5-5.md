# Plan for I-1793340032-split-cli-into-self-registering-command-modules

- **Plan ID:** P-1793340046-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-30T06:00:47.667Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS (with two touch-set clarifications that the intent's own goals force; see "Touch-set
  clarifications" under Plan Description)
- **Reframed intent (if applicable):** N/A
- **Exploration stance:** conservative / refactor_heavy. This is a move-only split of a heavily patched module, so the
  plan optimises for behavioural identity (git-mv first, monkeypatch-compatible lookups, `--help` byte diff) over
  elegance; the only exploratory element is a cheap baseline snapshot that turns "unchanged" into a measurable diff.
- **Safety priority level:** elevated
- **Priority Justification:** `holon-config/world/constraints.md` §4 mandates deep models for refactoring core modules;
  `cli.py` is the single entry point of every `holon` command and the Docker/credential launch path (credential
  redaction, env-file secrets, network isolation). A silent behaviour change here could leak credentials or break every
  stage of the flow, and over 230 `monkeypatch.setattr(cli, ...)` call sites in tests outside the touch set (including
  `test_flow.py`, which must not be touched) depend on the module's attribute surface.

## Exploration

- **Proportion of steps that are exploratory:** 0.125 (1 of 8: Step 1 baseline capture is BALANCED; everything else
  EXPLOIT)
- **Justification:** The design space is small and well understood; the risk is in subtle Python import/patching
  semantics, which are handled by explicit guardrails rather than experimentation. The baseline snapshot is the one
  information-gathering step and is cheap.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 12.0  |
| impact_pred         | 85.0  |
| cost_pred           | 24.0  |
| learning_value_pred | 5.5   |
| ev_pred             | 41.45 |

`EV = 0.78 * 85.0 + 0.5 * 5.5 - 0.3 * 12.0 - 24.0 = 66.30 + 2.75 - 3.60 - 24.0 = 41.45`

## Plan Description & Strategy

### Findings from the current state (drive the design)

1. `apps/sandbox-executor/src/sandbox_executor/cli.py` is 2,962 lines. `main()` (line 1862,
   `# noqa: C901, PLR0912, PLR0915`) has no `argv` parameter, builds every sub-parser inline (intent, plan, execute,
   init, calibrate, flow, review with its aliases, doctor, ledger → query, consult, record, reconcile, promote, veto →
   list/lift, stats) and then dispatches through an `if args.command == ...` chain that ends each branch with
   `sys.exit(...)`.
2. **Monkeypatch surface.** Tests outside the touch set patch attributes on the `cli` module object: `run_flow_pipeline`
   (18), `subprocess` (16, including `cli.subprocess.run`), `run_preflight_probe` (16), `detect_holon_coherence` (15),
   `_wait_for_proxy` (15), `run_docker_container` (12), `get_agent_session_mounts` (11), `resolve_target_repo`,
   `generate_root_ca`, `dispatch_role_agent`, `run_calibrate`, `init_project`, `load_checkpoint`,
   `report_budgets_and_envelope`, `preflight_gate`, `env_file_dir`, `find_github_token` (`conftest.py`), `__file__`, and
   more. Files: `conftest.py`, `test_flow.py` (must not be touched), `test_token_reduction.py`, `test_preflight.py`,
   `test_url_message_fuzz.py`, `test_local_llm.py`, `test_review_loop.py`, `test_calibration.py`, `test_config_fuzz.py`,
   `test_init.py`. Consequence: **every moved handler must resolve patchable names through the `sandbox_executor.cli`
   package attribute at call time**, and the Docker/sidecar/credential helpers must stay physically in `cli/__init__.py`
   so that their internal calls to one another keep hitting the (patchable) package globals. This also satisfies goal 5
   ("STAY in this package for now").
3. **`__file__`-relative paths.** `_find_git_root` (line 822), `setup_token_reduction_proxy` (line 850,
   `token_reduction/mitm_addon.py`) and line 916 (`src_dir`) compute paths from `os.path.dirname(__file__)`. After
   `cli.py` becomes `cli/__init__.py`, `dirname(__file__)` is one level deeper, so these three sites must anchor on the
   parent directory to keep behaviour identical. Tests also patch `cli.__file__`; the anchoring must still derive from
   the module's `__file__` global so those patches keep working.
4. **`python -m sandbox_executor.cli`** is used by the root `holon` launcher script and by
   `docs/optimisation/token_reduction_ab_testing.md`; a package needs `cli/__main__.py` for this to keep working.
5. **Console script** `holon = "sandbox_executor.cli:main"` keeps the same target path (the package exposes `main`), so
   `pyproject.toml` is NOT changed; `test_cli.py` asserts this exact entry-point value.
6. **Subcommand order matters for `--help`.** argparse lists sub-commands in registration order; `pkgutil.iter_modules`
   yields alphabetical order. Each command module therefore declares an integer ordering key and discovery sorts by it,
   so `holon --help` stays byte-identical.
7. **Complexity gate.** `tests/test_complexity_gate.py` freezes an identity snapshot keyed by path, with seven
   `sandbox_executor/cli.py` entries (one of which is `main`). After the move the six surviving annotations live in
   `sandbox_executor/cli/__init__.py`; the snapshot entries must be renamed and `main`'s entry deleted (a ratchet down).
   The counters (`187`, `91`) only allow decreases, so they may be lowered by one each or left as ceilings, depending on
   how the gate compares them (read the test before editing).
8. **Domain enum.** `flow.FlowStage` (StrEnum) has `intent, plan, execute, review, calibrate, completed`; the flow
   `--from-stage` choices are the first five, in that order, so they derive as every member except `COMPLETED`, which
   keeps `--help` identical. `_AGENT_FLOW_STAGES` likewise derives from the enum.

### Target layout

- `sandbox_executor/cli/__init__.py`: everything in today's `cli.py` except the parser construction and the dispatch
  bodies (logging set-up, Docker launch, sidecar/proxy, credentials, env-file, doctor reporting helpers,
  `dispatch_role_agent`, `preflight_gate`, `run_doctor`, and so on, unchanged), plus a short `build_parser()` and a
  `main(argv: list[str] | None = None) -> None` of about 20 lines: configure logging, build the parser, parse `argv`,
  look up the command module recorded on the namespace by `set_defaults`, and `sys.exit(module.run(args))`. All names
  currently imported by `cli.py` (for example `run_flow_pipeline`, `load_checkpoint`, `run_calibrate`, `init_project`,
  `generate_root_ca`, `run_preflight_probe`, `query_ledger`, the knowledge functions, `subprocess`) remain imported
  there, so `cli.<name>` stays a valid patch target and re-export.
- `sandbox_executor/cli/__main__.py`: calls `main()`.
- `sandbox_executor/cli/options.py`: shared option-group helpers (repo dir with a caller-supplied default because
  defaults differ between commands, token-reduce plus mitm-web, agent/runner, model, skip-preflight, credentials), plus
  the help-text constants (`_TOKEN_REDUCE_HELP`, `_AGENT_HELP`, `_MODEL_HELP`, `_SKIP_PREFLIGHT_HELP`) which remain
  re-exported from the package.
- `sandbox_executor/cli/commands/__init__.py`: `discover()` using `pkgutil.iter_modules(__path__)`, skipping names that
  start with `_`, importing each module, sorting by its ordering key and returning modules that expose `register` and
  `run`.
- `sandbox_executor/cli/commands/<command>.py` for: `intent`, `plan`, `execute`, `init`, `calibrate`, `flow`, `review`
  (owns its `review-loop`/`pr-review-loop` aliases exactly as today), `doctor`, `ledger` (owns the nested `query`
  sub-parser), `consult`, `record`, `reconcile`, `promote`, `veto` (owns `list`/`lift`), `stats`. A private `_role.py`
  holds the shared plan/execute pre-dispatch block so that the code is not duplicated; underscore modules are ignored by
  discovery.

### Touch-set clarifications (forced by the intent's own goals; no other files touched)

- `apps/sandbox-executor/tests/test_complexity_gate.py`: path rename of the six surviving `cli.py` snapshot entries and
  deletion of `main`'s entry. Goal 6 ("the old blanket suppression on main() is removed") cannot pass the gate
  otherwise; the edit only tightens the ratchet and weakens nothing.
- `apps/sandbox-executor/README.md` and `apps/sandbox-executor/docs/*.md` lines that name `cli.py` as a path (allowed:
  "docs only where they name the cli module path"). `docs/audit_report.md` is a dated audit record and is left as-is.
- Explicitly NOT touched: `flow.py`, `test_flow.py`, `entrypoint/executor.py`, prompts, `pyproject.toml` (entry-point
  target unchanged), `uv.lock`.

### Execution shape

A single execution branch, in commit-sized steps, each kept green: (1) baseline, (2) pure rename to a package, (3)
shared options, (4) discovery and simple commands, (5) complex commands, (6) test split, (7) docs, (8) verification and
records. The intent's Q&A forbids sub-intents, and the plan fits the 14.0 budget, so there is no sub-intent list.

---

## Step 1: Capture the behavioural baseline (help snapshots, test inventory)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only and cheap; it produces the reference that every later step is checked against.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The `--help` output of the top-level parser and of every sub-parser (including nested
  `ledger query`, `veto list`, `veto lift` and the review aliases) is deterministic for a fixed terminal width, so a
  byte diff is a valid move-only oracle.
- **Learning target:** The exact sub-command registration order, the full list of `cli.<name>` attributes that tests and
  source import or patch, and the collected-test count of `tests/test_cli.py`.
- **Maximum acceptable cost for this learning:** about 1.0 cost unit; abort the exploration (keep the plain diff) if
  help output proves non-deterministic.

### Intent & Git Integration

- **Step Intent:** Record a before-state that turns "behaviour unchanged" into mechanical comparisons.
- **Git branch:**
  `I-1793340032-split-cli-into-self-registering-command-modules/P-1793340046-claude-agent-claude-opus-5-5/E-{timestamp}-split-cli/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. From the repository root with `COLUMNS=100` set, run `uv run holon --help` and `uv run holon <cmd> --help` for every
   sub-command, alias and nested sub-command, writing each output to a scratch directory outside the repository (for
   example under the system temporary directory). Do not commit the snapshots.
2. Record the ordered list of sub-commands as registered today (intent, plan, execute, init, calibrate, flow, review
   plus aliases, doctor, ledger, consult, record, reconcile, promote, veto, stats; confirm against the source).
3. Run `uv run pytest apps/sandbox-executor/tests/test_cli.py --collect-only -q` and save the node-id list and count;
   count assertions per test (grep for `assert` and `self.assert`) to compare after the split.
4. Grep the source and tests for every `cli.<name>` attribute access, `from sandbox_executor.cli import ...` and
   `patch("sandbox_executor.cli....")`, and save the union as the required re-export list.
5. Run `uv run task test` once to confirm the starting suite is green; note any pre-existing failures.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` (no secrets in artefacts), `holon-config/world/constraints.md` §2 (sandbox
  containment)
- **Potential failure modes for this step:** help text wraps differently by terminal width; a pre-existing red test.
- **Guardrails and early‑abort checks:** pin `COLUMNS`; if the starting suite is red, record the failing node ids and
  treat them as the baseline rather than "fixing" them.

### Success & Discard Criteria

- **Success:** Snapshot files for every parser, a saved test inventory and a re-export list exist.
- **Discard:** N/A (cannot block); if help output is non-deterministic, fall back to comparing the parser's action table
  (dest, option strings, choices, defaults, help) programmatically.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.3   |
| impact_pred         | 10.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 9.61  |

### Step Metrics Rationale

`EV = 0.97 * 10.0 + 0.5 * 2.0 - 0.3 * 0.3 - 1.0 = 9.70 + 1.00 - 0.09 - 1.0 = 9.61`. Read-only, so near-certain; impact
is indirect (it is the oracle for Step 8).

---

## Step 2: Convert `cli.py` into a `cli/` package with zero behaviour change

- **Sub‑intent recommendation:** NO
- **Reasoning:** A small mechanical rename that is verified by the existing suite; it isolates the path-dependent risks
  (`__file__`, `python -m`, the complexity gate) from the larger split.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make `sandbox_executor.cli` a package whose `__init__.py` is today's `cli.py`, with history kept.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `git mv` `src/sandbox_executor/cli.py` to `src/sandbox_executor/cli/__init__.py` so that `git log --follow` keeps the
   history.
2. Fix the three `__file__`-relative sites (`_find_git_root`, the `mitm_addon.py` path in `setup_token_reduction_proxy`,
   and the `src_dir` computation near line 916) so that they resolve to the same absolute paths as before: anchor on the
   parent of the package directory, still derived from the module-global `__file__` so the existing `cli.__file__`
   monkeypatches keep working. Check every test that patches `cli.__file__` and confirm the path it fakes still yields
   the expected result; if a test fakes a file path that assumes the old depth, the test belongs to `test_cli.py` and is
   updated in Step 6 only by adjusting the fake path's depth, never its assertion.
3. Move the trailing `if __name__ == "__main__": main()` into a new `cli/__main__.py` so that both the root `holon`
   launcher (`python3 -m sandbox_executor.cli`) and the documented `uv run python -m sandbox_executor.cli` still work.
4. Update `tests/test_complexity_gate.py`: rename the `sandbox_executor/cli.py` snapshot entries to
   `sandbox_executor/cli/__init__.py` (no other change yet).
5. Confirm wheel packaging: the hatch wheel target already includes the `sandbox_executor` package; check that sub-
   packages are picked up (no `pyproject.toml` edit is expected).
6. Run `uv run pytest apps/sandbox-executor/tests` (affected tests) and diff `--help` against Step 1.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (every later step builds on the package) — p is high, so no sub-intent is needed.

### Safety & Constraint Considerations

- **Relevant rules:** intent TOUCH SET; `holon-config/world/ruleset.md` §1 (packaging via `uv`, no new dependencies)
- **Potential failure modes for this step:** the token-reduction proxy cannot find `mitm_addon.py`; `src_dir` mounts the
  wrong directory into containers; `python -m` breaks.
- **Guardrails and early‑abort checks:** add an explicit check (in the Step 6 test split) that the resolved addon path
  exists and equals `sandbox_executor/token_reduction/mitm_addon.py`; run `uv run holon --version` and
  `uv run python -m sandbox_executor.cli --help`.

### Success & Discard Criteria

- **Success:** Full affected suite green with no test edits except the gate path rename; help diff empty.
- **Discard:** More than two unexplained test failures after the rename suggests hidden path coupling: stop, inventory
  it and re-plan before continuing.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.2   |
| impact_pred         | 20.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 17.54 |

### Step Metrics Rationale

`EV = 0.92 * 20.0 + 0.5 * 3.0 - 0.3 * 1.2 - 2.0 = 18.40 + 1.50 - 0.36 - 2.0 = 17.54`. The three `__file__` sites are the
only known behavioural hazard; they are enumerated, so the risk is low.

---

## Step 3: Extract shared option groups into `cli/options.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small and verified by the help diff; a prerequisite for de-duplicating the role commands.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** One home for the options that several sub-parsers repeat.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Inventory the repeated `add_argument` blocks in `main()`: `--repo-dir` (defaults differ: `None` for role commands,
   `"."` for knowledge commands — the helper takes the default and help text as parameters), `--token-reduce` and
   `--mitm-web`, `--agent` (runner), `--model`, `--skip-preflight`, and the credential-related flags.
2. Create `cli/options.py` with one typed, docstringed helper per group, e.g.
   `add_repo_dir_option(parser, default, help)`, `add_token_reduction_options(parser)`, `add_runner_options(parser)`
   (agent and model), `add_preflight_option(parser)`. Each helper reproduces the original `add_argument` calls verbatim:
   identical option strings, `dest`, `default`, `action`, `choices`, `metavar` and help text, in the original order.
3. Move the help-text constants (`_TOKEN_REDUCE_HELP`, `_AGENT_HELP`, `_MODEL_HELP`, `_SKIP_PREFLIGHT_HELP`) into
   `options.py` and import them back into `cli/__init__.py` so that existing imports keep working.
4. Only use a helper where the original block is identical; where a sub-parser differs (help text, default or flag
   order), keep its own `add_argument` call rather than adding a parameter just for that case.
5. `options.py` must not import from `cli/__init__.py` (avoids an import cycle).

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** move-only Q&A answer ("identical messages")
- **Potential failure modes for this step:** reordering options changes the `usage:` line; a default drifts.
- **Guardrails and early‑abort checks:** help diff after the step; programmatic comparison of each parser's actions
  (option strings, dest, default, choices, help) against Step 1.

### Success & Discard Criteria

- **Success:** Help diff empty; tests green.
- **Discard:** If a group cannot be shared without changing output, leave it inline in the command module (not a
  failure).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.0   |
| impact_pred         | 12.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 9.50  |

### Step Metrics Rationale

`EV = 0.90 * 12.0 + 0.5 * 2.0 - 0.3 * 1.0 - 2.0 = 10.80 + 1.00 - 0.30 - 2.0 = 9.50`. Help-diff oracle makes defects
visible immediately.

---

## Step 4: Add command discovery, slim `main(argv)` and move the simple commands

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the core architectural move but each module is a mechanical cut-and-paste; splitting it into a
  child intent would only add rebase overhead (and the intent forbids sub-intents).
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Build the self-registering framework and move the ten low-complexity commands onto it.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. **Module contract.** Each `cli/commands/<command>.py` exposes a module docstring, an integer ordering constant (for
   example `ORDER`, spaced by 10 in today's registration order), `register(subparsers) -> None` and
   `run(args: argparse.Namespace) -> int`. `register` creates the sub-parser with the original name, aliases, help and
   description and calls `set_defaults` with a reference to the module's `run` under a private dest (for example `_run`)
   that `main` reads. Check that this dest never shows in `--help` and is not read by any test asserting namespace
   equality.
2. **Discovery.** `cli/commands/__init__.py` provides `discover()` using `pkgutil.iter_modules(__path__)` and
   `importlib.import_module`, skipping names that start with `_`, requiring `register` and `run`, and sorting by the
   ordering constant then name. No hand-written list anywhere.
3. **Patchability rule (critical).** Command modules must not bind patch-target names at import time. They import the
   package (`from sandbox_executor import cli`) and call `cli.<name>(...)` for every name in the Step 1 re-export or
   patch list (for example `cli.run_calibrate`, `cli.init_project`, `cli.query_ledger`, `cli.run_doctor`,
   `cli.report_budgets_and_envelope`, `cli.print_encodable`, `cli.redact_cli_error`). Names that no test patches may be
   imported directly from their defining domain module. The only edit to moved lines is the `cli.` prefix and turning a
   final `sys.exit(code)` into `return code`; `sys.exit` calls nested inside `try` blocks stay as they are unless the
   surrounding `except` clauses provably do not catch `SystemExit` (none catch `BaseException`; confirm per block).
4. **Import-cycle avoidance.** `cli/__init__.py` imports `cli.commands` lazily inside `build_parser()`, so command
   modules can import the package at module level once it has finished initialising.
5. **New `main`.** `main(argv: list[str] | None = None) -> None`: `configure_cli_logging()`; `parser = build_parser()`;
   `args = parser.parse_args(argv)`; `sys.exit(args._run(args))`. `build_parser()` keeps the original program name,
   description, `-v/--version` (with the `importlib.metadata` fallback) and
   `add_subparsers(dest="command", required=True)`, then calls `register` on each discovered module. Calling `main()`
   with no arguments behaves exactly as before (it reads `sys.argv`), so the entry point and existing tests are
   unaffected.
6. **Move these commands** (bodies cut from the dispatch chain verbatim): `init` (choices `python`/`generic` stay
   literal; there is no domain enum for them, so do not invent one), `calibrate`, `doctor` (its `run` delegates to
   `cli.run_doctor(args)`, which stays in the package because tests import it), `ledger` (registers the nested
   `ledger_command` sub-parsers including `query`; the query choices stay literal unless a domain enum already exists in
   `ledger.py` with the same values in the same order — check, and use it only if it matches exactly), `consult`,
   `record`, `reconcile`, `promote`, `veto` (nested `list`/`lift`), `stats`.
7. Remove the moved branches from the old dispatch chain; keep the remaining (complex) branches temporarily in a private
   fallback in `cli/__init__.py` until Step 5, so that the tree stays green between commits.
8. Run `uv run ruff check apps/sandbox-executor` (complexity rules active, no new `noqa`), the help diff and
   `uv run pytest apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_ledger_query.py apps/sandbox-executor/tests/test_knowledge.py apps/sandbox-executor/tests/test_init.py apps/sandbox-executor/tests/test_calibration.py apps/sandbox-executor/tests/test_local_llm.py`.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** YES — the discovery and patchability contract is reused by Step 5.

### Safety & Constraint Considerations

- **Relevant rules:** move-only Q&A; intent goal 3 (discovery, not a list); `holon-config/world/ruleset.md` §2 (type
  hints and docstrings on public functions; no wildcard imports)
- **Potential failure modes for this step:** alphabetical registration order changes `--help`; a command module binds a
  patched name at import time so a monkeypatch silently stops intercepting (a test might then make a real Docker or
  network call); the `_run` default leaks into a namespace-equality assertion.
- **Guardrails and early‑abort checks:** help diff; temporarily run the affected tests with Docker absent from `PATH` to
  surface any un-intercepted real calls; grep each command module for `from sandbox_executor.cli import` (must be empty)
  and for direct use of names on the patch list.

### Success & Discard Criteria

- **Success:** Ten command modules discovered; help diff empty; affected tests green; ruff clean with no new `noqa`.
- **Discard:** If the patchability rule cannot be met for a name without editing tests outside the touch set, stop and
  record the blocker in the execution record rather than editing those tests.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.5   |
| impact_pred         | 25.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 17.50 |

### Step Metrics Rationale

`EV = 0.85 * 25.0 + 0.5 * 4.0 - 0.3 * 2.5 - 5.0 = 21.25 + 2.00 - 0.75 - 5.0 = 17.50`. Moderate entropy from about 11 new
files; the help diff and the patch-list grep catch the two characteristic failure modes.

---

## Step 5: Move the complex commands (intent, plan, execute, flow, review) under the complexity gate

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the highest-risk step, but it shares Step 4's contract and the intent forbids sub-intents; risk
  is contained by per-command commits and the help/test oracles.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Finish emptying `main()`, delete its blanket suppression, and keep every new function within
  `max-complexity = 10` and the PLR limits without `noqa`.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. **`intent`, `plan`, `execute`.** Move each sub-parser into its own module using the Step 3 option helpers. Move the
   shared plan/execute pre-dispatch block (today lines about 2901–2911: runner/model resolution and preflight gate)
   verbatim into `cli/commands/_role.py` as one function that both modules call, resolving
   `cli.resolve_runner_and_model`, `cli.preflight_gate`, `cli.resolve_target_repo`, `cli.dispatch_role_agent` and
   `cli.run_docker_container` through the package.
2. **`flow`.** Move the sub-parser; the `--from-stage` choices become the `FlowStage` values excluding
   `FlowStage.COMPLETED`, in enum order, which matches today's literal list exactly (verified by the help diff). Derive
   `_AGENT_FLOW_STAGES` in `cli/__init__.py` from the enum the same way (intent, plan, execute, review), keeping the
   name. The flow handler (about 150 lines, several `sys.exit(1)` guards) exceeds the gate, so extract contiguous blocks
   verbatim into private functions in the same module — argument validation, checkpoint and from-stage resolution,
   `FlowContext` construction, pipeline invocation and result reporting — each returning an exit code or `None` to
   continue. Preserve statement order, messages, stream (stdout/stderr) and exit codes. `run_flow_pipeline`,
   `load_checkpoint` and `detect_holon_coherence` are called as `cli.<name>` (tests in `test_flow.py`, which cannot be
   touched, patch them on `cli`).
3. **`review`.** Move the parser together with its `review-loop`/`pr-review-loop` aliases and the help for each; split
   the handler (about 60 lines) the same way: validation, the local path (`run_review_loop_cli`) and the containerised
   path (`cli.run_docker_container("review-loop", ...)`).
4. **Delete the old `main`** dispatch chain and the temporary fallback from Step 4; `main` is now about 20 lines and
   carries no `noqa`. Remove `main`'s entry from the `test_complexity_gate.py` snapshot and lower the baseline counters
   only if the gate's comparison requires it.
5. **Gate check.** Run `uv run ruff check apps/sandbox-executor`; any function still over a limit is split further by
   extracting contiguous blocks, never by adding `noqa` and never by changing a limit in `pyproject.toml`.
6. Run the help diff and
   `uv run pytest apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_preflight.py apps/sandbox-executor/tests/test_token_reduction.py apps/sandbox-executor/tests/test_url_message_fuzz.py apps/sandbox-executor/tests/test_complexity_gate.py apps/sandbox-executor/tests/test_config_fuzz.py`.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** YES (p = 0.80, above the 0.6 threshold, so no sub-intent is recommended)

### Safety & Constraint Considerations

- **Relevant rules:** move-only Q&A; do not touch `flow.py`, `test_flow.py`, the executor entrypoint or prompts;
  `docs/safety.md` (credential redaction paths must keep using `redact_cli_error`)
- **Potential failure modes for this step:** an extracted helper changes control flow (for example an early `return`
  where the original fell through), or an exception escapes a different `try` scope; `SystemExit` raised inside a helper
  is now caught by an enclosing `except` in the caller; error messages lose redaction.
- **Guardrails and early‑abort checks:** extract only whole `try` statements or whole guard blocks; diff each moved
  block against the original with whitespace ignored (only the `cli.` prefix and `return` in place of a final `sys.exit`
  may differ); keep `sys.exit` where it was if it sits inside a `try` whose handler could observe it.

### Success & Discard Criteria

- **Success:** `main()` about 20 lines and without suppression; zero new `noqa`; help diff empty; listed tests green,
  including the untouched `test_flow.py`.
- **Discard:** If keeping the flow handler under the gate needs anything beyond extracting contiguous blocks (that is, a
  logic rewrite), stop and record it; do not rewrite.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 3.2   |
| impact_pred         | 30.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 19.54 |

### Step Metrics Rationale

`EV = 0.80 * 30.0 + 0.5 * 5.0 - 0.3 * 3.2 - 6.0 = 24.00 + 2.50 - 0.96 - 6.0 = 19.54`. The bottleneck: the flow handler
is the densest code and the most heavily patched by tests that cannot be edited. The learning value is the reusable
pattern for later handler refactors.

---

## Step 6: Split `tests/test_cli.py` per command module with no test weakened

- **Sub‑intent recommendation:** NO
- **Reasoning:** A mechanical relocation verified by node-count and assertion-count equality.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Mirror the source split in tests so that future CLI changes touch one test file.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Classify each of the 43 top-level tests and classes in `test_cli.py` by the command or helper it exercises.
2. Create flat files under `apps/sandbox-executor/tests/` (flat names avoid a basename clash with the existing
   `test_flow.py`): `test_cli_<command>.py` for each command with tests, plus `test_cli_package.py` for tests of
   package-level helpers (Docker launch, credentials, env-file, sidecar, logging, entry point and version). Keep the
   `sandbox_executor.cli:main` entry-point assertion. Shared fixtures used by more than one new file go to the existing
   `tests/conftest.py` only if they are already generic; otherwise into a small `tests/cli_helpers.py` module imported
   explicitly (following the existing `review_fakes.py` / `hermetic_fixtures.py` pattern).
3. Move test bodies verbatim. Allowed edits: imports, and changing a patch target only where a test patched a symbol
   that now lives in a command module and the old patch would no longer intercept — prefer leaving the patch on `cli`
   (Step 4's rule makes that work). Never change an assertion, expected message, exit code or `pytest.raises` type.
4. Delete `tests/test_cli.py` with `git rm`.
5. Add a small number of new tests (strengthening only): discovery returns every module in `cli/commands` without
   underscore names; the registered sub-command order equals the pre-split order captured in Step 1; flow `--from-stage`
   choices equal the non-`COMPLETED` `FlowStage` values; `main(["--version"])` exits 0; the resolved `mitm_addon.py`
   path and the git-root fallback match the pre-move locations; `python -m sandbox_executor.cli --help` exits 0.
6. Verify: the collected node ids of the new files equal the Step 1 list (renamed module prefix only) plus the new
   tests; the assertion count is greater than or equal to the baseline.
7. Run `uv run pytest apps/sandbox-executor/tests -k cli` and then the files touched in Steps 4–5.

### Dependencies & Criticality

- **Depends on:** Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (test location mirrors source; test changes declared in the
  plan — declared here); intent goal 6 ("no test weakened"); `tests/test_ledger_append_only.py` and hermetic guards must
  stay green.
- **Potential failure modes for this step:** a test lost in the move; module-level fixtures or `setUp` state duplicated
  inconsistently; the hermetic `leak_env` tests (named in `apps/sandbox-executor/docs/hermetic_testing.md`) moved
  without their environment set-up.
- **Guardrails and early‑abort checks:** compare node ids and assertion counts mechanically; run the new files with
  `-p no:randomly` unaffected; the suite runs with `-n auto --dist worksteal` in Step 8.

### Success & Discard Criteria

- **Success:** All original tests present and passing in their new homes; new strengthening tests pass.
- **Discard:** Any original test that cannot be relocated without editing an assertion is left in a
  `test_cli_package.py` section unchanged and reported, never deleted.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.2   |
| impact_pred         | 18.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 12.68 |

### Step Metrics Rationale

`EV = 0.88 * 18.0 + 0.5 * 3.0 - 0.3 * 2.2 - 4.0 = 15.84 + 1.50 - 0.66 - 4.0 = 12.68`. About 2,300 lines move into about
12 files; mechanical, but large.

---

## Step 7: Update docs that name the cli module path

- **Sub‑intent recommendation:** NO
- **Reasoning:** Trivial text edits limited to path references.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Keep developer docs pointing at real files.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `apps/sandbox-executor/README.md`: the layout tree entry `cli.py` (line 16) becomes `cli/` with `__init__.py`,
   `options.py` and `commands/`; the `cli.py` bullet (line 45) describes the package and adds one sentence: "to add a
   command, add one module under `cli/commands/` exposing `register` and `run`" (Australian English).
2. `apps/sandbox-executor/docs/agy_sandbox_execution_solutions.md` (mermaid node `sandbox_executor/cli.py`) and
   `apps/sandbox-executor/docs/docker_bake.md` line 137: update the path to `sandbox_executor/cli/` (the code they
   describe stays in `cli/__init__.py`).
3. Leave `docs/audit_report.md` (a dated audit record), `docs/optimisation/token_reduction_ab_testing.md` (module path
   `sandbox_executor.cli` still valid via `__main__.py`) and all `plans/` and `executions/` history unchanged.
4. Run `uv run task lint-docs`.

### Dependencies & Criticality

- **Depends on:** Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent TOUCH SET (docs only where they name the module path); prettier formatting.
- **Potential failure modes for this step:** prettier reflow; a stale reference missed.
- **Guardrails and early‑abort checks:** grep for `cli\.py` across `*.md` outside `plans/`, `executions/` and the audit
  reports after editing.

### Success & Discard Criteria

- **Success:** No live doc names `sandbox_executor/cli.py`; `lint-docs` passes.
- **Discard:** N/A.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.4   |
| impact_pred         | 4.0   |
| cost_pred           | 0.8   |
| learning_value_pred | 1.0   |
| ev_pred             | 3.38  |

### Step Metrics Rationale

`EV = 0.95 * 4.0 + 0.5 * 1.0 - 0.3 * 0.4 - 0.8 = 3.80 + 0.50 - 0.12 - 0.8 = 3.38`.

---

## Step 8: Verify move-only equivalence, run the gates once, and record the execution

- **Sub‑intent recommendation:** NO
- **Reasoning:** Final acceptance gate; no new code.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove that the help output and behaviour are unchanged and that every repository gate passes.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Regenerate every `--help` snapshot with the same `COLUMNS` and diff byte-for-byte against Step 1; the diff must be
   empty.
2. Behaviour smoke checks without network or Docker: `uv run holon --version`;
   `uv run python -m sandbox_executor.cli --help`; argument-error paths (for example a missing required positional) give
   identical usage and exit code 2; `uv run holon ledger query --help` and `uv run holon veto list --help` are
   identical.
3. Static checks: `uv run ruff check .` reports no complexity violations; grep shows no new complexity `noqa` in
   `src/sandbox_executor/cli/` (only the six inherited annotations on the unchanged Docker/sidecar helpers); `main` has
   none.
4. `uv run task lint` (includes `uv lock --check`, proving no new dependencies) and `uv run task lint-docs`.
5. Run the full suite once with `uv run task test` (it already uses `-n auto --dist worksteal`); there is no timing
   goal, so no repeated runs. Report the pass/fail count and the pytest-reported time.
6. Confirm the touch set with `git diff --stat main`: only `cli/` (from `cli.py`), the split tests,
   `test_complexity_gate.py`, the three docs, the plan/execution artefacts and ledger appends. `pyproject.toml`,
   `flow.py`, `test_flow.py`, the entrypoint and prompts unchanged.
7. Write `executions/E-{timestamp}-claude-agent-claude-opus-5-5.md` with the actual metrics and append one line each to
   the ledgers through the repository's tooling; never rewrite existing lines and never add a fourth ledger. Use
   target-native paths only and no harness references.

### Dependencies & Criticality

- **Depends on:** Steps 6 and 7
- **Is Bottleneck:** YES (acceptance gate)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (append-only ledgers); intent goal 8 (test runner
  discipline, no PYTHONPATH); `docs/safety.md` (no secrets in artefacts).
- **Potential failure modes for this step:** a non-empty help diff; a flaky test unrelated to the change; lint
  formatting drift.
- **Guardrails and early‑abort checks:** a non-empty help diff is a hard stop (fix in Steps 3–5, never accept the diff);
  a red test unrelated to the change is checked against the Step 1 baseline before any action.

### Success & Discard Criteria

- **Success:** Empty help diff, full suite green, lint and lint-docs green, touch set respected, artefacts written.
- **Discard:** Any behavioural difference that cannot be removed without rewriting handler logic: stop and report.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.2   |
| impact_pred         | 15.0  |
| cost_pred           | 3.2   |
| learning_value_pred | 3.0   |
| ev_pred             | 11.44 |

### Step Metrics Rationale

`EV = 0.90 * 15.0 + 0.5 * 3.0 - 0.3 * 1.2 - 3.2 = 13.50 + 1.50 - 0.36 - 3.2 = 11.44`.

---
