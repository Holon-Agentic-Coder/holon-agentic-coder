# Plan for I-1792211166-standardise-license-apache-2-and-australian-spelling

- **Plan ID:** P-1792211663-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-17T04:34:23.098Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with empirical AST and regex boundary mapping to cleanly isolate Australian English
  spelling transformations from programmatic tokens, CLI parameters, and immutable historical ledger lines.
- **Safety priority level:** elevated
- **Priority Justification:** Triggered by `docs/safety.md` §1 (Git as safety boundary), §2 (Mandatory execution
  sandboxing), `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modification policy for files in
  `holon-knowledge/ledger/`), and `holon-config/world/ruleset.md` §1 & §2 (Python runtime `==3.13.*`, workspace layout
  under `apps/sandbox-executor`, strict static typing, PEP 8 compliance). Elevated safety guarantees that
  repository-wide spelling sweeps never alter append-only ledger archives, corrupt wire-schema identifiers or external
  API keys, or mutate the singular legal exception for "Apache License, Version 2.0".

## Exploration

- **Proportion of steps that are exploratory:** 0.17
- **Justification:** Step 2 conducts a balanced exploratory discovery audit across the codebase and documentation to
  construct a verified false-positive boundary map, testing pattern edge cases against Python AST node types and regex
  tokenisers before making global document or code changes.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.40  |
| impact_pred         | 92.0  |
| cost_pred           | 10.6  |
| learning_value_pred | 5.0   |
| ev_pred             | 78.26 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.94. Governed by the bottleneck step (Step 4: 0.94), where AST docstring and inline comment
  refactoring across Python packages must cleanly update natural language commentary without altering any runtime
  variable, function, class, or argument identifier.
- **entropy_pred**: 0.40. Evaluated as the maximum single-step risk profile (Step 4: 0.40), where refactoring code
  comments introduces AST parsing and formatting surface area. The cumulative sum of predicted step entropies across all
  six steps is 1.65 (`0.20 + 0.35 + 0.30 + 0.40 + 0.25 + 0.15 = 1.65`), fitting comfortably within the allocated entropy
  budget of 15.0.
- **impact_pred**: 92.0. Concurrently satisfies Bean 0104 (formal Apache 2.0 open-source licensing, root and sub-package
  package metadata declaration, and README badging) and Bean 0105 (systematic Australian English `en-AU` orthography
  across docs, docstrings, and comments while strictly respecting programmatic and legal exceptions).
- **cost_pred**: 10.6. Calculated as the direct sum of individual step costs
  (`1.2 + 1.5 + 2.2 + 2.5 + 1.8 + 1.4 = 10.6`).
- **learning_value_pred**: 5.0. High epistemic value derived from constructing reusable regex and AST filter heuristics
  that distinguish programmatic symbols from natural language docstrings and comments, safeguarding immutable ledger
  assets and preserving legal terms.
- **ev_pred**: 78.26. Computed strictly via the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system constants `lambda = 0.3`
  and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.94 * 92.0 + 0.5 * 5.0 - 0.3 * 0.40 - 10.6 = 86.48 + 2.5 - 0.12 - 10.6 = 78.26`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: branch prefix isolation, commit scoping,
    rebase discipline).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modification policy; historical files in
    `holon-knowledge/ledger/` such as `intents.jsonl`, `plans.jsonl`, and `events.jsonl` are immutable).
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python `==3.13.*`, package management via
    `uv`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8, static type hints, docstring
    preservation).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test placement under
    `apps/sandbox-executor/tests/`, test hermeticity).
  - `docs/safety.md` §1 & §2 (Git as safety boundary, mandatory sandboxing, blast radius containment).
  - Intent Q2 Answer: "Apache License, Version 2.0 is the singular exception retaining the original American English
    'License' spelling."
- **Potential violations or edge cases:**
  - Inadvertently modifying immutable ledger records (`holon-knowledge/ledger/*.jsonl`) or historical raw agent logs
    (`docs/agent_selection/logs/`) during global search-and-replace routines.
  - Breaking Python code runtime or syntax by renaming programmatic identifiers (e.g. standard library methods,
    third-party library imports, CLI arguments like `--token-reduce`, or wire schema payload keys).
  - Misspelling the legal name of the Apache 2.0 license by replacing "License" with "Licence" in legal notices or
    headers.
  - Overwriting or breaking PEP 621 package table definitions in `pyproject.toml`.
  - Introducing Markdown formatting oscillations or lint errors that fail `prettier --check` or `ruff check`.
- **Mitigations built into the plan:**
  - Explicitly exclude `holon-knowledge/ledger/`, `docs/agent_selection/logs/`, `.git/`, and virtual environment paths
    from all spellcheck sweeps.
  - Constrain code modifications strictly to Python docstrings (`ast.Expr` containing string constants) and inline `#`
    comments via targeted AST inspection, leaving code tokens completely untouched.
  - Enforce regex word boundary guards and strict whitelists for "Apache License, Version 2.0" and SPDX identifier
    "Apache-2.0".
  - Preserve noun vs verb distinction in Australian English: use `licence` as the noun and `license` as the verb, except
    for the explicit "Apache License, Version 2.0" title exception.
  - Run full suite of quality gates (`make lint`, `uv run pytest`, `ruff check`, `ruff format --check`, and
    `prettier --check`) at every step and in final verification.
- **Residual risk accepted (and why):**
  - Minor edge-case colloquialisms in external documentation examples; accepted because the core lexicon covers all
    standard Australian English orthographic transformations.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 1.65
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

This plan implements Bean 0104 and Bean 0105 across `holon-agentic-coder`:

1. **Bean 0104 (License Standardization to Apache 2.0):**
   - Add the standard Apache License Version 2.0 text to `LICENSE` at repository root with explicit copyright:
     `Copyright 2026 Holon Agentic Coder Contributors`.
   - Update `pyproject.toml` in repository root and `apps/sandbox-executor/pyproject.toml` to declare standard PEP 621
     metadata: `license = { text = "Apache-2.0" }`.
   - Update `README.md` to add the Apache 2.0 license badge in the header and an informative `## Licence` / `## License`
     notice section near the bottom of the document.

2. **Bean 0105 (Australian English Spelling Adoption):**
   - Adopt Australian English (`en-AU`) spelling across all repository documentation, Markdown specifications,
     docstrings, and inline code comments.
   - Core transformations include: `-ize` to `-ise` (e.g. `standardise`, `synchronise`, `optimise`, `initialise`,
     `prioritise`, `categorise`, `summarise`), `-yze` to `-yse` (e.g. `analyse`), `-or` to `-our` (e.g. `behaviour`,
     `colour`), `-er` to `-re` (e.g. `centre`), double-l inflections (e.g. `cancelled`), and noun `licence` vs verb
     `license`.
   - Enforce strict exceptions: retain "Apache License, Version 2.0" and the SPDX string "Apache-2.0" verbatim; retain
     all programmatic API identifiers, CLI parameter names, environment variables, and external wire schema keys
     untouched.
   - Safeguard ledger immutability: exclude all files under `holon-knowledge/ledger/` and historical raw telemetry under
     `docs/agent_selection/logs/` from any text modification.

3. **Goal 3 (Verification & Quality Gates):**
   - Validate the entire repository against `make lint` (`uv lock --check`, `git diff --exit-code uv.lock`,
     `ruff check .`, `ruff format --check .`).
   - Run the full hermetic test suite:
     `uv run pytest apps/sandbox-executor/tests -m 'not integration_test and not stress'`.
   - Validate Markdown consistency with `npx prettier --check '**/*.md'`.

---

## Step 1: Standardise repository licensing to Apache 2.0 across root LICENSE, pyproject manifests, and README (Bean 0104)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Self-contained configuration and documentation updates with well-defined artifacts and negligible risk.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Create root `LICENSE` file with full Apache 2.0 text and copyright notice, update root and
  sub-package `pyproject.toml` files with PEP 621 license metadata, and update `README.md` with license badge and
  notice.
- **Git branch:**
  `I-1792211166-standardise-license-apache-2-and-australian-spelling/P-1792211663-antigravity-agent-gemini-3.8-flash-medium/E-1792211701-license-apache-2/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `LICENSE` at the repository root containing the canonical text of the Apache License, Version 2.0 (January
   2004, http://www.apache.org/licenses/).
2. In `LICENSE`, append the standard copyright appendix:
   - Include `Copyright 2026 Holon Agentic Coder Contributors`.
   - Include standard Apache boilerplate grant: "Licensed under the Apache License, Version 2.0 (the 'License'); you may
     not use this file except in compliance with the License. You may obtain a copy of the License at
     http://www.apache.org/licenses/LICENSE-2.0 ...".
3. Update root `pyproject.toml` under the `[project]` table:
   - Add `license = { text = "Apache-2.0" }`.
4. Update `apps/sandbox-executor/pyproject.toml` under the `[project]` table:
   - Add `license = { text = "Apache-2.0" }`.
5. Update `README.md`:
   - Add Apache 2.0 license badge near the top below the main repository heading and repository link.
   - Add a `## License` (or `## Licence`) section towards the end of `README.md` stating that Holon is open-source
     software licensed under the Apache License, Version 2.0, with a link to `LICENSE` and copyright notice
     `Copyright 2026 Holon Agentic Coder Contributors`.
6. Verify file formatting with `npx prettier --check README.md` and check `pyproject.toml` syntax with
   `uv lock --check`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 (Package Management: declare dependencies and metadata in
  `pyproject.toml`), `docs/safety.md` §1 (Git isolation).
- **Potential failure modes for this step:** Syntax error in `pyproject.toml` invalidating uv lock; incorrect copyright
  year or entity name.
- **Guardrails and early‑abort checks:** Validate `uv lock --check` immediately after updating `pyproject.toml`; verify
  exact match for "Copyright 2026 Holon Agentic Coder Contributors".

### Success & Discard Criteria

- **Success:** `LICENSE` file exists at repository root; `pyproject.toml` in both root and `apps/sandbox-executor`
  contain `license = { text = "Apache-2.0" }`; `README.md` includes license badge and notice.
- **Discard:** Discard and abort if `uv lock --check` fails or if license text diverges from official Apache 2.0
  canonical text.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.20  |
| impact_pred         | 85.0  |
| cost_pred           | 1.2   |
| learning_value_pred | 2.0   |
| ev_pred             | 83.89 |

### Step Metrics Rationale

High probability of success (`0.99`) and low entropy (`0.20`) reflect standard configuration additions with minimal
ambiguity. Cost is low (`1.2`), and impact is substantial (`85.0`) as it formally establishes open-source licensing for
the project. Expected value is: `EV = 0.99 * 85.0 + 0.5 * 2.0 - 0.3 * 0.20 - 1.2 = 84.15 + 1.0 - 0.06 - 1.2 = 83.89`.

---

## Step 2: Lexicon analysis, token boundary definition, and false-positive boundary mapping (Bean 0105 Discovery)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Focused exploratory analysis step that identifies word patterns and defines boundaries without altering
  code state.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Systematic regex boundary rules combined with AST token classification can achieve 100%
  precision in distinguishing natural language text from programmatic identifiers, external schema keys, and immutable
  ledger lines.
- **Learning target:** Comprehensive mapping of en-AU transformation targets, protected programmatic symbols, and
  edge-case exceptions across documentation and Python files.
- **Maximum acceptable cost for this learning:** 2.0 cost units.

### Intent & Git Integration

- **Step Intent:** Build an empirical catalogue of Australian English conversion candidates and identify protected
  symbols to prevent syntax corruption or ledger violations.
- **Git branch:**
  `I-1792211166-standardise-license-apache-2-and-australian-spelling/P-1792211663-antigravity-agent-gemini-3.8-flash-medium/E-1792211715-en-au-boundary-mapping/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Scan all documentation files (`docs/**/*.md`, `*.md`, `apps/**/*.md`) and Python files (`apps/**/*.py`) for candidate
   American English orthographic roots:
   - `-ize`/`-ization` vs `-ise`/`-isation` (e.g. `standardize`, `synchronize`, `optimize`, `initialize`, `prioritize`,
     `categorize`, `summarize`, `organization`, `customization`).
   - `-yze` vs `-yse` (e.g. `analyze`).
   - `-or` vs `-our` (e.g. `behavior`, `color`, `favor`).
   - `-er` vs `-re` (e.g. `center`).
   - Double-l inflections (e.g. `canceled`, `canceling`).
   - Noun `license` vs noun `licence`.
2. Construct a strict protection inventory containing:
   - Singular legal exception: "Apache License, Version 2.0" and SPDX string "Apache-2.0".
   - Immutable directories: `holon-knowledge/ledger/` and historical agent logs under `docs/agent_selection/logs/`.
   - Python keywords and standard library modules/attributes (e.g. `asyncio`, `typing`, `logging`, `sys`, `json`).
   - External dependencies, tool names, and CLI flags (e.g. `ruff`, `pytest`, `prettier`, `--token-reduce`).
   - Wire schema keys and serialization dictionaries (e.g. event types, ledger entry JSON fields).
3. Validate word contextual usage for `licence` (noun) vs `license` (verb) in general English sentences (e.g. "a driver
   licence" [noun] vs "to license software" [verb]).
4. Produce a reference replacement map for subsequent documentation and code comment updates.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (Ledger Immutability), `docs/safety.md` §4 (Blast radius
  containment).
- **Potential failure modes for this step:** Incomplete mapping resulting in missed spelling updates or false positives
  in code tokens.
- **Guardrails and early‑abort checks:** Ensure zero file modifications take place during this discovery step; all
  analysis is read-only.

### Success & Discard Criteria

- **Success:** Detailed replacement map compiled with unambiguous boundaries between natural language
  docstrings/comments and code identifiers.
- **Discard:** Discard if programmatic tokens cannot be cleanly separated from commentary without risking runtime
  errors.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.35  |
| impact_pred         | 80.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 6.0   |
| ev_pred             | 77.40 |

### Step Metrics Rationale

Balanced exploration yields elevated learning value (`6.0`) by defining the safe operational envelope for en-AU
conversion. Cost is low (`1.5`) and entropy is modest (`0.35`). Expected value derivation:
`EV = 0.95 * 80.0 + 0.5 * 6.0 - 0.3 * 0.35 - 1.5 = 76.0 + 3.0 - 0.105 - 1.5 = 77.395 -> 77.40`.

---

## Step 3: Adopt Australian English spelling across all documentation, guidelines, and markdown specifications (Bean 0105)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure documentation transformation with low risk and automated Prettier verification.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Convert documentation text in `docs/`, `README.md`, `SUBMODULES.md`, and application guides to
  Australian English, retaining Apache 2.0 and programmatic keys as exceptions.
- **Git branch:**
  `I-1792211166-standardise-license-apache-2-and-australian-spelling/P-1792211663-antigravity-agent-gemini-3.8-flash-medium/E-1792211728-docs-australian-spelling/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Apply Australian English orthographic changes across all root Markdown files (`README.md`, `SUBMODULES.md`):
   - Replace occurrences of `standardize`/`standardization` with `standardise`/`standardisation`.
   - Replace `synchronize`/`synchronization` with `synchronise`/`synchronisation`.
   - Replace `behavior`/`behaviors` with `behaviour`/`behaviours`.
   - Replace `optimize`/`optimization` with `optimise`/`optimisation`.
   - Replace `initialize`/`initialization` with `initialise`/`initialisation`.
   - Replace `prioritize`/`prioritization` with `prioritise`/`prioritisation`.
   - Replace `analyze`/`analysis` with `analyse`/`analysis` where appropriate.
   - Replace `summarize` with `summarise`, `categorize` with `categorise`.
   - Replace `license` (noun) with `licence` when referring to general software licensing, while strictly keeping
     "Apache License, Version 2.0" and `license = { text = "Apache-2.0" }`.
2. Apply the same Australian English conversions across all documentation files in `docs/`:
   - `docs/architecture.md`, `docs/core_concepts.md`, `docs/git_flow.md`, `docs/metrics.md`, `docs/safety.md`,
     `docs/agents.md`, `docs/agent_design.md`, `docs/appendix.md`, `docs/faq.md`, `docs/audit_report.md`,
     `docs/directory_structure.md`, `docs/examples.md`, `docs/knowledge_sharing.md`, `docs/knowledgebase_schema.md`,
     `docs/ledger_schema.md`, `docs/planning_and_evaluation.md`, `docs/wisdombase_schema.md`.
   - Subdirectories: `docs/optimisation/*.md`, `docs/planner/*.md`, `docs/executor/*.md`, `docs/sandbox/*.md`,
     `docs/intent/*.md`, `docs/testing/*.md`.
3. Apply conversions across `apps/sandbox-executor/README.md` and `apps/sandbox-executor/docs/*.md`.
4. Ensure files in `holon-knowledge/ledger/` and historical logs in `docs/agent_selection/logs/` remain completely
   untouched to preserve ledger immutability and audit reproducibility.
5. Format all updated Markdown documents with `npx prettier --write '**/*.md'`.
6. Verify compliance with `npx prettier --check '**/*.md'`.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (Ledger Immutability: zero changes to
  `holon-knowledge/ledger/`), `docs/safety.md` §1 & §4.
- **Potential failure modes for this step:** Prettier formatting failure or accidental modification of ledger files.
- **Guardrails and early‑abort checks:** Explicit path filter excludes `holon-knowledge/` and `logs/`; run
  `npx prettier --check '**/*.md'` immediately after transformation.

### Success & Discard Criteria

- **Success:** All target Markdown documentation files use Australian English spelling consistently; Prettier checks
  pass cleanly; zero files in `holon-knowledge/ledger/` are touched.
- **Discard:** Discard if diff shows changes to files under `holon-knowledge/ledger/` or if "Apache License, Version
  2.0" was corrupted.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.30  |
| impact_pred         | 88.0  |
| cost_pred           | 2.2   |
| learning_value_pred | 3.0   |
| ev_pred             | 83.69 |

### Step Metrics Rationale

High probability (`0.96`) and low entropy (`0.30`) because documentation changes do not alter runtime semantics. Impact
is high (`88.0`) across all user-facing and architectural documentation. Expected value derivation:
`EV = 0.96 * 88.0 + 0.5 * 3.0 - 0.3 * 0.30 - 2.2 = 84.48 + 1.5 - 0.09 - 2.2 = 83.69`.

---

## Step 4: Refactor Python source docstrings and inline comments in apps/sandbox-executor to Australian English (Bean 0105)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped refactor confined strictly to comments and docstrings in Python sources, leaving AST symbols and
  runtime code unaltered.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update module, class, function docstrings and inline comments across `apps/sandbox-executor/src/` to
  Australian English, ensuring code tokens and API contracts remain untouched.
- **Git branch:**
  `I-1792211166-standardise-license-apache-2-and-australian-spelling/P-1792211663-antigravity-agent-gemini-3.8-flash-medium/E-1792211741-src-docstrings-en-au/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/`:
   - Inspect Python files: `cli.py`, `flow.py`, `ledger.py`, `agent_runner.py`, `context_cleaner.py`, `local_llm.py`,
     `token_reduction.py`, and sub-packages like `knowledge/`.
2. Target only comment tokens (`# ...`) and docstring string literals (`ast.Constant` within `ast.Expr` at module,
   class, or function header):
   - Replace American spellings (`standardize`, `synchronize`, `behavior`, `optimize`, `initialize`, `prioritize`,
     `analyze`, `license` [noun]) with Australian English counterparts (`standardise`, `synchronise`, `behaviour`,
     `optimise`, `initialise`, `prioritise`, `analyse`, `licence` [noun]).
3. Strictly preserve all programmatic elements:
   - Function definitions, argument names, and return type annotations.
   - Variable names, class names, and imported module names.
   - Dictionary keys corresponding to external JSON payloads or ledger structures.
   - CLI option strings (e.g. `--token-reduce`).
4. Run `uv run ruff check .` and `uv run ruff format --check .` to ensure linting and code formatting rules remain
   intact.

### Dependencies & Criticality

- **Depends on:** Step 2, Step 3
- **Is Bottleneck:** YES (Risk of touching code tokens could cause syntax or runtime failures; successful execution
  ensures code integrity)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 (Python `==3.13.*`), §2 (PEP 8, docstrings, type annotations).
- **Potential failure modes for this step:** Accidental token modification altering a variable name or string literal
  used as a dictionary lookup.
- **Guardrails and early‑abort checks:** Execute `git diff` inspection focused on code identifiers; verify that
  `uv run ruff check .` reports zero errors.

### Success & Discard Criteria

- **Success:** Docstrings and inline comments adhere to Australian English; all source files pass Ruff linting and
  formatting without modification to runtime code tokens.
- **Discard:** Discard and revert step if any functional Python code or API signature is modified.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.40  |
| impact_pred         | 88.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 3.5   |
| ev_pred             | 81.85 |

### Step Metrics Rationale

Slightly higher entropy (`0.40`) and cost (`2.5`) due to precision required to avoid modifying active code identifiers
while rewriting docstrings. Represents the plan bottleneck (`p_success_pred = 0.94`). Expected value derivation:
`EV = 0.94 * 88.0 + 0.5 * 3.5 - 0.3 * 0.40 - 2.5 = 82.72 + 1.75 - 0.12 - 2.5 = 81.85`.

---

## Step 5: Align test suite docstrings and comments in apps/sandbox-executor/tests to Australian English (Bean 0105)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped updates to test docstrings and test commentary with test execution validation to ensure
  assertions remain unchanged.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update docstrings and inline comments in `apps/sandbox-executor/tests/` to Australian English while
  ensuring all test assertions and fixture payloads remain completely unchanged.
- **Git branch:**
  `I-1792211166-standardise-license-apache-2-and-australian-spelling/P-1792211663-antigravity-agent-gemini-3.8-flash-medium/E-1792211755-tests-docstrings-en-au/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Review test modules in `apps/sandbox-executor/tests/`:
   - `test_flow.py`, `test_ledger_query.py`, `test_planner.py`, `test_executor.py`, `test_cli.py`,
     `test_context_cleaner.py`, `test_knowledge.py`, `test_formatting.py`, `test_makefile.py`, etc.
2. Update test function docstrings and inline test comments to Australian English:
   - Convert words such as `behavior`, `initialize`, `standardize`, `synchronize`, `optimize`, `licence` (noun).
3. Strictly protect test logic:
   - Do not alter expected string values in `assert` statements if they test external tool output or raw format strings.
   - Do not rename test function names if they correspond to specific conventions or markers.
   - Do not alter test fixtures or mock payloads.
4. Run `uv run pytest apps/sandbox-executor/tests -m 'not integration_test and not stress'` to verify all tests continue
   to pass.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test files match source
  files, test assertions must remain hermetic and valid).
- **Potential failure modes for this step:** Modifying an assertion string literal instead of a docstring, causing
  false-positive test failures.
- **Guardrails and early‑abort checks:** Immediate test run via `uv run pytest` after comment updates; revert if any
  test fails.

### Success & Discard Criteria

- **Success:** Test commentary conforms to Australian English; all unit tests pass with zero failures.
- **Discard:** Discard if any test assertion or fixture is unintentionally altered.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.25  |
| impact_pred         | 84.0  |
| cost_pred           | 1.8   |
| learning_value_pred | 2.5   |
| ev_pred             | 80.02 |

### Step Metrics Rationale

High probability (`0.96`) and low entropy (`0.25`) because changes are limited to test descriptions. Cost is moderate
(`1.8`). Expected value derivation:
`EV = 0.96 * 84.0 + 0.5 * 2.5 - 0.3 * 0.25 - 1.8 = 80.64 + 1.25 - 0.075 - 1.8 = 80.015 -> 80.02`.

---

## Step 6: Execute end-to-end quality gates, linting, formatting, and test suite verification (Goal 3 Verification)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Final comprehensive verification step validating all quality gates without adding new feature logic.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Run all required static checks, linter suites, code formatting checks, documentation style
  validation, and hermetic unit tests to guarantee clean pass.
- **Git branch:**
  `I-1792211166-standardise-license-apache-2-and-australian-spelling/P-1792211663-antigravity-agent-gemini-3.8-flash-medium/E-1792211768-quality-verification/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Execute `make lint` which verifies:
   - `uv lock --check`
   - `git diff --exit-code uv.lock`
   - `ruff check .`
   - `ruff format --check .`
2. Execute hermetic unit tests:
   - `uv run pytest apps/sandbox-executor/tests -m 'not integration_test and not stress'`
3. Execute standalone Ruff checks across repository:
   - `uv run ruff check .`
   - `uv run ruff format --check .`
4. Execute Markdown Prettier verification:
   - `npx prettier --check '**/*.md'`
5. Verify Git status and diff integrity:
   - Confirm `LICENSE` exists and matches Apache 2.0.
   - Confirm `pyproject.toml` files contain valid `license = { text = "Apache-2.0" }`.
   - Confirm `README.md` includes license badge and notice.
   - Confirm `holon-knowledge/ledger/` files are completely untouched.
   - Confirm "Apache License, Version 2.0" exception is intact.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 3, Step 4, Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1, §2, §3, `docs/safety.md` §1 & §5 (Human review as final gate).
- **Potential failure modes for this step:** Linter regression, unformatted Markdown file, or failed unit test.
- **Guardrails and early‑abort checks:** Run all validation commands sequentially; halt if any check fails and identify
  root cause.

### Success & Discard Criteria

- **Success:** All verification commands (`make lint`, `uv run pytest`, `ruff check`, `ruff format --check`,
  `prettier --check`) complete with exit code 0.
- **Discard:** Discard intent execution if any quality gate cannot be resolved cleanly.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.15  |
| impact_pred         | 90.0  |
| cost_pred           | 1.4   |
| learning_value_pred | 2.0   |
| ev_pred             | 87.76 |

### Step Metrics Rationale

High probability (`0.98`) and minimal entropy (`0.15`) since this step executes automated validation against
already-completed changes. Delivers final verification impact (`90.0`). Expected value derivation:
`EV = 0.98 * 90.0 + 0.5 * 2.0 - 0.3 * 0.15 - 1.4 = 88.20 + 1.0 - 0.045 - 1.4 = 87.755 -> 87.76`.
