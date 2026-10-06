# Plan for I-1791190247-redact-credentials-closed-list

- **Plan ID:** P-1791190262-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-05T08:51:02.119Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical validation of line-scoped regex anchors, horizontal-only
  separator boundaries, and word-bounded credential alternations that prevent destructive token swallowing while
  safeguarding diagnostic retention.
- **Safety priority level:** elevated
- **Priority Justification:** Triggered by `holon-config/world/ruleset.md` §1 & §2 (Python `==3.13.*`, workspace
  isolation under `apps/sandbox-executor`, PEP 8, typing, docstrings), `holon-config/world/ruleset.md` §3 (Testing
  Constraints: `pytest==9.1.1`, test locations under `apps/sandbox-executor/tests/`, test changes declared in planning),
  `holon-config/world/constraints.md` §1 (Git Flow: branch isolation, commit boundaries),
  `holon-config/world/constraints.md` §2 (Sandbox containment: subprocess and filesystem boundaries),
  `holon-config/world/constraints.md` §3 (Ledger immutability: zero modifications to historical ledger entries),
  `docs/safety.md` §1, §2 & §4 (Git as safety boundary, sandboxing mandatory, entropy as safety signal), and
  `apps/sandbox-executor/docs/hermetic_testing.md` §1-§3 (Mandatory rules: every role entrypoint test must pin
  `HOLON_REPO_DIR` to fixture; Sandbox Safety Rail: never run full pytest discovery from `~/.holon-sandbox/workspace`).

## Exploration

- **Proportion of steps that are exploratory:** 0.50
- **Justification:** Steps 1 and 2 incorporate balanced exploration to empirically test line-scoped regex boundaries
  across URL query strings and nested key-value separators (JSON, YAML, INI) to eliminate newline bleeding without
  causing false positives or dropping legitimate diagnostic outputs.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.3   |
| impact_pred         | 95.0  |
| cost_pred           | 10.5  |
| learning_value_pred | 4.5   |
| ev_pred             | 78.76 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.92. Dictated by the bottleneck step (Step 3: 0.92), which requires verifying complex regex
  matching permutations across multiline YAML/JSON nestings, URL query strings, and bare credential keys while ensuring
  zero regressions on benign diagnostic keys (`sort_key`, `cache_key`, `compat`, `impact`).
- **entropy_pred**: 1.3. Derived as the maximum single-step risk profile (Step 3: 1.3), where extensive test cases and
  witness patterns are constructed. The sum of predicted step entropies is 4.0 (`1.0 + 1.2 + 1.3 + 0.5 = 4.0`), which
  comfortably satisfies the allocated entropy budget of 15.0.
- **impact_pred**: 95.0. Permanently eliminates Bean 0062 credential leak vectors in the sandbox execution telemetry
  pipeline, preventing secrets from leaking into execution markdown records and ledger lines, stopping multiline URL
  query bleeding, and resolving destructive YAML/JSON key-swallowing bugs.
- **cost_pred**: 10.5. Calculated as the direct sum of individual step costs (`2.5 + 3.0 + 3.5 + 1.5 = 10.5`).
- **learning_value_pred**: 4.5. Epistemic gain from formalizing robust horizontal-whitespace separator boundaries in
  regex redactors, word-bounded credential alternations, and hermetic synthetic test fixtures for push-protection
  compliance.
- **ev_pred**: 78.76. Computed strictly via the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system constants `lambda = 0.3`
  and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.92 * 95.0 + 0.5 * 4.5 - 0.3 * 1.3 - 10.5 = 87.40 + 2.25 - 0.39 - 10.5 = 78.76`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python target `==3.13.*`, workspace
    isolation under `apps/sandbox-executor`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, explicit static typing with
    `typing` module, complete docstrings for public functions, no wildcard imports).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test files located under
    `apps/sandbox-executor/tests/`, explicit planning of test changes).
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, rebase
    discipline, commit boundaries).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: strict filesystem containment, prevention of
    escape actions and unwhitelisted modifications).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modification of existing
    `holon-knowledge/ledger/*.jsonl` lines, append-only operations).
  - `docs/safety.md` §1, §2 & §4 (Git as safety boundary, sandboxing mandatory for execution, entropy as safety signal).
  - `apps/sandbox-executor/docs/hermetic_testing.md` §1-§3 (Mandatory Rules for Tests: Rule 1: Every role entrypoint
    test must pin `HOLON_REPO_DIR` to fixture; §3 Sandbox Safety Rail: never run full test discovery from
    `~/.holon-sandbox/workspace`).
- **Potential violations or edge cases:**
  - Over-masking benign diagnostic keys such as `sort_key`, `cache_key`, `primary_key`, `compat`, or `impact` if regex
    matching is unconstrained.
  - Regex catastrophic backtracking (ReDoS) on large multiline log outputs if patterns permit unbounded dot/whitespace
    cross-matching.
  - Destructive nested key-swallowing in YAML/JSON structures if key-value separators cross newline characters (`\n`,
    `\r`).
  - GitHub push protection triggers if real token shapes or sensitive credentials appear in test fixtures.
- **Mitigations built into the plan:**
  - Strict line scoping on URL query parameter anchors using `[^=\r\n]*=` and horizontal-whitespace-only key-value
    separators `[ \t]*(:[ \t]*|=)[ \t]*`.
  - Word-bounded bare key matching `\bkey\b` and explicit secret-adjacent key suffixes, guaranteeing `sort_key` and
    `cache_key` are retained.
  - Pre-existing input length guards (`_MAX_REDACT_INPUT_LEN`) combined with linear-time line-scoped regex patterns to
    prevent ReDoS.
  - All test fixtures use synthetic, clearly fake tokens (`_fake_token`) to comply with push protection invariants.
  - Strict compliance with sandbox safety rail during verification.
- **Residual risk accepted (and why):**
  - Unconventional, non-standard custom secret identifiers not adhering to any standard naming convention (e.g., bare
    obscure variable names in third-party libraries): accepted trade-off to maintain diagnostic retention for normal
    program logs without blind overmasking.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 4.0
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy sum of 4.0 is well below the 15.0
  allocated budget).

## Plan Description & Strategy

This plan hardens `redact_text` and redaction patterns in
`apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py` against key omission, multiline newline bleeding,
and nested anchor destruction, addressing the core vulnerabilities identified in Bean 0062:

1. **Line-Scoped URL Query Parameter Anchors (Bean 0062 Gap 2):** Update the URL query parameter redaction regex anchor
   from `[?&](token|api_key|...)[^=]*=` to `[?&](token|api_key|...)[^=\r\n]*=`. This ensures that anchor prefixes never
   traverse newline boundaries to attach to distant `=` signs on subsequent lines, preventing spurious multiline
   redactions.
2. **Line-Scoped Key/Value Separators & Nested Key Protection (Bean 0062 Gap 3):** Replace multiline-tolerant whitespace
   separators `\s*(:\s*|=)\s*` with line-scoped horizontal whitespace `[ \t]*(:[ \t]*|=)[ \t]*` in `redact_text`. This
   eliminates the destructive bug where a parent dictionary key (e.g. `secret:`) in YAML/JSON matches across newlines,
   swallowing a nested key name (such as `api_key:`) as its value and leaving the actual nested credential completely
   exposed.
3. **Widen Recognized Credential Alternations & CLI Flags with Diagnostic Retention (Bean 0062 Gap 1 & Gap 4):** Expand
   the accepted credential key names in `redact_text` to include word-bounded bare `\bkey\b`, `pwd`, `passwd`,
   `client_secret`, `db_password`, `credential`, and `credentials`. Restructure key alternations so that bare `key` is
   strictly word-bounded and does not over-mask benign variables like `cache_key`, `sort_key`, `primary_key`, `compat`,
   or `impact`. Update `SECRET_FLAGS` and `_is_secret_flag` in `executor.py` to match the expanded credential
   identifiers.
4. **Hermetic Regression Test Suite & Verification (Bean 0062 Gap 5):** Add comprehensive unit and regression tests in
   `apps/sandbox-executor/tests/test_executor.py` covering all three witness patterns (bare key/pwd/credential masking,
   multiline URL query anchor isolation, and multiline YAML/JSON nested key preservation with secret masking). Verify
   benign key retention and run full workspace validation: `uv run ruff check .`, `uv run ruff format --check .`,
   `uv run pytest -m "not integration_test and not stress"`, and `npx --yes prettier@3.8.4 --check "**/*.md"`.

---

## Step 1: Fix Line-Scoped URL Query Anchors and Key-Value Separators in executor.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Targeted, low-risk regex correction within `executor.py` that immediately eliminates multiline bleeding
  and nested token destruction with zero architectural side effects.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Restricting regex anchors in `redact_text` to horizontal-only whitespace (`[ \t]`) and
  excluding newline characters (`\r`, `\n`) from URL query parameter searches eliminates multiline newline bleeding and
  prevents parent YAML/JSON dictionary keys from swallowing nested child keys.
- **Learning target:** Characterize how strict horizontal whitespace separators behave across single-line and multiline
  structured logs (JSON, YAML, INI, command lines) to prevent destructive token swallowing.
- **Maximum acceptable cost for this learning:** `cost_pred` of 2.5 units.

### Intent & Git Integration

- **Step Intent:** Update regex anchors in `redact_text` in
  `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py` to enforce line scoping for URL query parameters
  and key-value separators.
- **Git branch:** `I-1791190247-redact-credentials-closed-list/step1-line-scoped-anchors-separators`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`, update `redact_text`:
  - Update the URL-query regex replacement:
    - Replace `r"([?&](?:token|api_key|access_token|secret|password|auth|bearer|auth_code|code)[^=]*=)[^\s&]+"` with a
      strictly line-scoped pattern where `[^=]*=` is replaced by `[^=\r\n]*=`.
    - Widen the query parameter keys to include additional sensitive parameters (`client_secret`, `db_password`,
      `credential`, `credentials`, `key`, `pwd`, `passwd`).
    - Verify that any URL parameter anchor stopping at a newline character cannot match a distant `=` on a following
      line.
  - Update the key-value separator in `redact_text`'s main pattern:
    - Currently, `pattern` uses `\s*(:\s*|=)\s*`, which permits matching newline characters before and after the
      colon/equal sign.
    - Replace `\s*(:\s*|=)\s*` with line-scoped horizontal whitespace: `[ \t]*(:[ \t]*|=)[ \t]*`.
    - This ensures that a parent key like `secret:` ending with a newline will never treat the indented line below it
      (such as `api_key: <cred>`) as a separator plus value.
    - Confirm that values spanning single lines with standard quoting (e.g. `key = "value"` or `token: 'secret'`) or
      unquoted characters continue to be matched and replaced cleanly with asterisks.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 & §2 (PEP 8, static typing, docstrings),
  `holon-config/world/constraints.md` §1 & §2 (Git flow and sandbox boundaries), `docs/safety.md` §1 & §2.
- **Potential failure modes for this step:** Accidental regression where valid single-line credentials with tab
  separators or spaces fail to redact; ReDoS if regex grouping is improperly quantified.
- **Guardrails and early‑abort checks:** Maintain explicit character class bounds `[ \t]` rather than open quantifiers;
  run fast unit tests on `test_redact_text` immediately after modification.

### Success & Discard Criteria

- **Success:** URL query parameter anchors never match across newlines; multiline YAML structures preserve parent keys
  and nested key names while properly masking the actual credential value.
- **Discard:** Discard if separator adjustments cause existing single-line secret masking tests to fail or if regex
  execution time degrades noticeably.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.0   |
| impact_pred         | 85.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 79.95 |

### Step Metrics Rationale

High probability implementation (0.95) with low entropy (1.0). Delivers substantial epistemic value (4.0) by resolving
structural anchor bleeding in streaming redactors. Expected Value derivation:
`EV = 0.95 * 85.0 + 0.5 * 4.0 - 0.3 * 1.0 - 2.5 = 80.75 + 2.0 - 0.30 - 2.5 = 79.95`.

---

## Step 2: Widen Recognized Credential Key Patterns & Flags with Diagnostic Retention in executor.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Localized dictionary and pattern expansion in `executor.py` requiring careful word boundary tuning to
  balance security against over-masking benign diagnostic keys.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Explicitly defining word-bounded bare `\bkey\b` and targeted secret-adjacent alternations
  (`api_key`, `secret_key`, `client_secret`, `db_password`, `pwd`, `passwd`, `credential`, `credentials`) while removing
  unanchored `_key` wildcards allows 100% masking of common credential names while preserving non-secret identifiers
  like `sort_key`, `cache_key`, `primary_key`, `compat`, and `impact`.
- **Learning target:** Measure false positive rate and diagnostic retention when distinguishing bare `key` and
  secret-adjacent compounds from benign identifiers.
- **Maximum acceptable cost for this learning:** `cost_pred` of 3.0 units.

### Intent & Git Integration

- **Step Intent:** Expand recognized credential keys and CLI flags in `executor.py` while safeguarding diagnostic key
  retention.
- **Git branch:** `I-1791190247-redact-credentials-closed-list/step2-widen-credential-key-patterns`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`, refine key pattern alternation:
  - Decompose the key-matching regex in `redact_text`:
    - Incorporate bare `\bkey\b` with strict word boundaries so that standalone `key = ...` or `"key": "..."` is masked,
      but words ending in `key` (such as `cache_key`, `sort_key`, `foreign_key`, `primary_key`, `monkey`, `donkey`) are
      NOT masked unless they match an explicit secret compound.
    - Expand accepted compound and prefix keys: include `pwd`, `passwd`, `client_secret`, `db_password`, `credential`,
      `credentials`, along with existing identifiers (`token`, `access_token`, `secret`, `password`, `api_key`, `auth`,
      `bearer`, `_pat`, `-pat`, `\bpat`, `secret_key`, `private_key`, `signing_key`, `encryption_key`, `auth_token`,
      `auth_code`).
    - Remove the open wildcard `[a-zA-Z0-9_-]*_key` that previously caused any word ending in `_key` (such as `sort_key`
      or `cache_key`) to be masked inadvertently. Restrict `_key` and `-key` matches to known secret prefixes or
      explicit secret-adjacent compound terms.
  - Update `SECRET_FLAGS` set and `_is_secret_flag` helper:
    - Add `--pwd`, `--passwd`, `--client-secret`, `--client_secret`, `--db-password`, `--db_password`, `--credential`,
      `--credentials`, `--key` to `SECRET_FLAGS` or dynamic suffix checks.
    - Ensure `_is_secret_flag` checks for these flags while maintaining exclusion for non-secret flags.
    - Ensure `redact_args` correctly applies masking to arguments following these newly recognized secret flags.
  - Verify docstrings and comments in `executor.py` are updated to explain the word-boundary rationale and diagnostic
    retention principles.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (PEP 8, docstrings, typing),
  `holon-config/world/constraints.md` §1 & §2, `docs/safety.md` §4 (Entropy budgets and blast radius containment).
- **Potential failure modes for this step:** Over-masking benign diagnostic keys (`sort_key`, `cache_key`) breaking
  downstream parsers or logging assertions; under-masking due to regex precedence order.
- **Guardrails and early‑abort checks:** Validate test cases with both positive secret strings and negative benign
  strings immediately after editing.

### Success & Discard Criteria

- **Success:** Bare `key = ...`, `pwd = ...`, `credential = ...`, and `client_secret = ...` are masked with `*******`;
  benign variables `sort_key = ...`, `cache_key = ...`, `compat = ...`, and `impact = ...` remain unmasked.
- **Discard:** Discard if benign diagnostic parameters like `sort_key` are masked or if legitimate command arguments are
  corrupted.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 1.2   |
| impact_pred         | 90.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 4.5   |
| ev_pred             | 82.59 |

### Step Metrics Rationale

High probability implementation (0.93) with modest entropy (1.2). Provides strong epistemic value (4.5) by establishing
precise word-boundary filtering that balances secret masking with diagnostic preservation. Expected Value derivation:
`EV = 0.93 * 90.0 + 0.5 * 4.5 - 0.3 * 1.2 - 3.0 = 83.70 + 2.25 - 0.36 - 3.0 = 82.59`.

---

## Step 3: Implement Comprehensive Hermetic Unit and Regression Tests in test_executor.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Hermetic test suite additions directly in `apps/sandbox-executor/tests/test_executor.py` verifying all
  witness patterns and edge cases without cross-module side effects.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add hermetic unit and regression tests in `apps/sandbox-executor/tests/test_executor.py` covering
  bare keys, line-scoped URL anchors, nested YAML/JSON structures, and benign diagnostic retention.
- **Git branch:** `I-1791190247-redact-credentials-closed-list/step3-hermetic-unit-regression-tests`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/tests/test_executor.py`:
  - Add test methods for Witness Pattern 1 (Bare key, pwd, and credentials masking):
    - Verify bare `key = "synthetic_val"`, `key: synthetic_val`, `key=val` are redacted to `key="*******"`,
      `key: *******`, `key=*******`.
    - Verify `pwd = "my_pass"`, `passwd = "secret"`, `client_secret = "cs_xyz"`, `db_password = "db_pass"`,
      `credential = "cred_abc"`, `credentials = "creds_123"` are redacted.
    - Verify CLI flag argument masking in `redact_args` for `--pwd`, `--passwd`, `--client-secret`, `--client_secret`,
      `--db-password`, `--credential`.
  - Add test methods for Witness Pattern 2 (Multiline URL-query anchor isolation):
    - Construct input strings where a URL query anchor like `https://example.com/api?token=\n` or
      `https://example.com/api?key=\n` appears on line 1, followed on line 2 by unrelated text containing an equals sign
      (e.g. `mode=debug` or `count=42`).
    - Assert that the anchor does NOT cross the newline boundary and does NOT swallow `mode=debug` or corrupt subsequent
      lines.
  - Add test methods for Witness Pattern 3 (Multiline YAML/JSON nested key preservation):
    - Construct nested YAML inputs: `cfg:\n  secret:\n    api_key: synthetic_secret_value\n`.
    - Assert that `secret:` is NOT treated as a key with `api_key:` as its value.
    - Assert that `api_key:` remains visible and intact as a key name, and that `synthetic_secret_value` is masked as
      `*******`.
    - Test nested JSON configurations with newlines to ensure similar key integrity.
  - Add test methods for Invariants & Diagnostic Retention:
    - Assert that benign variables such as `sort_key=asc`, `cache_key=123`, `primary_key=id`, `foreign_key=user_id`,
      `compat=1.0.0`, `compact=true`, `impact=high`, `monkey=banana`, `donkey=kong` remain completely unmasked and
      verbatim.
  - Update pre-existing `test_redact_text` in `test_executor.py`:
    - Update the assertion where `key=jkl` was formerly expected to remain unmasked, adjusting the expected value so
      `key=*******` is masked, while explicitly verifying that `sort_key=xyz` and `cache_key=xyz` are unmasked.
  - Push protection compliance:
    - Ensure all secret strings in tests use synthetic, non-real tokens (using `_fake_token` or dummy values like
      `synthetic_secret_123`) to prevent GitHub secret scanners or push protection from flagging test code.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (Plan success requires all witness pattern tests and diagnostic invariants to pass
  hermetically)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Testing Constraints: pytest==9.1.1, test locations under
  `apps/sandbox-executor/tests/`), `apps/sandbox-executor/docs/hermetic_testing.md` §1-§3 (pin `HOLON_REPO_DIR`, adhere
  to Sandbox Safety Rail).
- **Potential failure modes for this step:** Subtle regex edge cases where nested quotes or trailing spaces cause
  unexpected matching behavior; test assertion fragility.
- **Guardrails and early‑abort checks:** Run isolated pytest targeting `test_executor.py` exclusively before running the
  broader suite.

### Success & Discard Criteria

- **Success:** All new and existing unit tests in `test_executor.py` pass cleanly with zero regressions on benign keys
  and 100% masking on witness pattern secrets.
- **Discard:** Discard if test assertions cannot be satisfied without introducing ReDoS risks or degrading diagnostic
  retention.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.3   |
| impact_pred         | 92.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 3.5   |
| ev_pred             | 82.50 |

### Step Metrics Rationale

Bottleneck step (0.92 success probability) due to the need to satisfy all three witness patterns simultaneously while
preserving all diagnostic invariants. Step entropy (1.3) reflects comprehensive test verification. Expected Value
derivation: `EV = 0.92 * 92.0 + 0.5 * 3.5 - 0.3 * 1.3 - 3.5 = 84.64 + 1.75 - 0.39 - 3.5 = 82.50`.

---

## Step 4: Repository Conventions, Code Quality, Prettier, and Isolated Sandbox Rail Verification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standardized verification step ensuring compliance with coding standards, linters, and repository
  invariants.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Execute code quality, linting, formatting checks, and hermetic test suite verification across the
  workspace.
- **Git branch:** `I-1791190247-redact-credentials-closed-list/step4-verify-conventions-quality-gate`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Run `uv run ruff check .` to verify PEP 8 compliance, import sorting, and linting rules across all modified files.
- Run `uv run ruff format --check .` to guarantee Python formatting standards.
- Run `uv run pytest -m "not integration_test and not stress"` in `apps/sandbox-executor` to ensure all hermetic unit
  and regression tests pass without regression.
- Run `npx --yes prettier@3.8.4 --check "**/*.md"` to verify markdown hygiene and document formatting across all
  documentation and plan files.
- Verify ledger immutability: assert that no existing rows in `holon-knowledge/ledger/*.jsonl` have been altered or
  removed.
- Adhere strictly to the sandbox safety rail (`apps/sandbox-executor/docs/hermetic_testing.md`): ensure no tests execute
  outside the pinned hermetic fixtures.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1, §2, §3, `holon-config/world/constraints.md` §1, §2, §3,
  `docs/safety.md` §1-§5, `apps/sandbox-executor/docs/hermetic_testing.md` §3 (Sandbox Safety Rail).
- **Potential failure modes for this step:** Prettier formatting oscillations due to unescaped arithmetic symbols in
  markdown files; ruff formatting divergences.
- **Guardrails and early‑abort checks:** Ensure all mathematical derivations in markdown use code spans; run prettier
  check early.

### Success & Discard Criteria

- **Success:** Zero ruff lint errors, zero ruff format diffs, 100% test pass rate on unit test suite, and clean prettier
  check on all markdown files.
- **Discard:** Discard if unfixable style or test breakages arise that violate repository invariants.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.5   |
| impact_pred         | 75.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 72.60 |

### Step Metrics Rationale

High certainty validation step (0.98 probability) with negligible entropy (0.5). Finalizes integration and guarantees
system stability before merge. Expected Value derivation:
`EV = 0.98 * 75.0 + 0.5 * 1.5 - 0.3 * 0.5 - 1.5 = 73.50 + 0.75 - 0.15 - 1.5 = 72.60`.

---
