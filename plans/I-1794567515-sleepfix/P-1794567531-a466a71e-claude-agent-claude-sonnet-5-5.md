# Plan for I-1794567515-sleepfix

- **Plan ID:** P-1794567531-a466a71e-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-13T10:58:52.060Z

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 6.0   |
| impact_pred         | 60.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 34.7  |

### Strategy Rationale

The intent is accepted as is. A survey of `apps/sandbox-executor/tests/` found exactly three process-wide patches, all
in `tests/flow/github/test_ci.py` (lines 91, 272 and 329): `sandbox_executor.flow.github.ci.time.sleep` and
`sandbox_executor.flow.review.loop.time.sleep` (twice). The other sleep mentions in the tests use injected `sleep=` or
`sleeper=` seams and are unaffected. `tests/test_planner_integration.py:114` and `tests/test_turn_supervisor.py:925`
call a real `time.sleep` inside helper children and are not patches. The fix is small, local to the tests, and needs no
production change. A shared helper plus an AST-based guard test is the same pattern as the existing
`tests/flow/lookup_patch.py` and `tests/flow/test_patch_targets.py`, so the design risk is low.

Overall metrics: the steps are sequential and each is small, so p_success is the product of the step probabilities
(`0.97 * 0.93 * 0.92 * 0.88 = 0.73`), nudged up to 0.80 because the steps are strongly correlated and mostly mechanical.
Step 4 (the in-container run) is the bottleneck because it depends on the review-loop image. Entropy is the sum of the
step entropies (`1.0 + 1.5 + 1.5 + 2.0 = 6.0`). Impact is the maximum: it removes a recurring halt of the resolver's
pre-push verification. Cost is the sum.
`EV = 0.80 * 60.0 + 0.5 * 5.0 - 0.3 * 6.0 - 14.0 = 48.0 + 2.5 - 1.8 - 14.0 = 34.7`, recorded as 34.7 (the table's
earlier placeholder is superseded).

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:** `holon-config/world/ruleset.md` (Python 3.13, `uv`,
  docstrings, typing, no new dependencies); `holon-config/world/constraints.md` section 3 (ledgers append-only, exactly
  three); `docs/safety.md` (git isolation, human-only merge to `main`).
- **Potential violations or edge cases:** the helper's stand-in for `time` must still expose `monotonic`, `time` and any
  other attribute the module reads, or code paths break; a guard that is too strict could flag legitimate real-sleep
  helpers or injected-seam patches; `patch("x.time.sleep")` strings might be built dynamically and evade a regex.
- **Mitigations built into the plan:** the stand-in delegates unknown attributes to the real `time` module through
  `__getattr__`; the guard parses with `ast` (string constants passed to `patch`, `patch.object` and
  `monkeypatch.setattr`) and is allow-list free; the guard has positive and negative self-tests on synthetic source.
- **Residual risk accepted (and why):** the in-container run cannot be proven from the host except by simulating a slow
  child; accepted, with a deterministic regression test (a real short-lived child run under the scoped patch) added.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 6.0
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

Add `tests/flow/sleep_patch.py` holding a `scoped_sleep_patch(module_path, sleep)` context manager. It patches the
module's own `time` name with a stand-in object whose `sleep` is the mock and whose other attributes read through to the
real `time` module. Convert the three offending patches, and add a guard test that scans every test file and fails on
any patch of a dotted path ending in `time.sleep`. Finish with targeted runs, then the full suite once, then lint. No
repeated runs are needed: the intent is deterministic correctness, not timing measurement.

---

## Step 1: Survey every process-wide sleep patch

- **Sub‑intent recommendation:** NO
- **Reasoning:** A read-only grep of a bounded directory; cheap and low risk.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm the complete set of dotted `time.sleep` patches, including quoting variants.
- **Git branch:** I-1794567515-sleepfix/P-1794567531-a466a71e-claude-agent-claude-sonnet-5-5/E-1794567531-survey
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Search the tests for `time.sleep`, `.sleep"`, `.sleep'`, `setattr` on `time`, and `patch.object(time, "sleep")`.
- Record each site and the module under test whose `time` name it should target (`flow.github.ci`, `flow.review.loop`).
  Confirm each module does `import time` and not `from time import sleep`.
- Confirm the sites to convert are the three in `tests/flow/github/test_ci.py`; note any additional ones found.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md`; scope limited to `apps/sandbox-executor/tests/`.
- **Potential failure modes for this step:** a missed indirect patch (for example a fixture in `conftest.py`).
- **Guardrails and early‑abort checks:** also grep `conftest.py` and helper modules, not just `test_*.py`.

### Success & Discard Criteria

- **Success:** a complete list of sites and target modules.
- **Discard:** none expected; stop if production code would need a hook.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 1.0   |
| impact_pred         | 10.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 9.4   |

### Step Metrics Rationale

`EV = 0.97 * 10.0 + 0.5 * 2.0 - 0.3 * 1.0 - 1.0 = 9.4`. Mechanical and cheap.

---

## Step 2: Add the shared scoped-sleep helper

- **Sub‑intent recommendation:** NO
- **Reasoning:** One small new test module beside `lookup_patch.py`; low risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Provide one helper that patches a module's own `time` reference with a scoped stand-in.
- **Git branch:** I-1794567515-sleepfix/P-1794567531-a466a71e-claude-agent-claude-sonnet-5-5/E-1794567531-helper
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `tests/flow/sleep_patch.py` with a module docstring that explains why: a dotted `time.sleep` patch replaces the
  real `time` module's attribute, so `subprocess`, `threading` and the ledger lock see the mock.
- Define a small stand-in class whose `sleep` attribute is the supplied mock and whose `__getattr__` delegates to the
  real `time` module. Typed and docstringed per the ruleset.
- Define a context manager taking the dotted module path (for example `sandbox_executor.flow.github.ci`) and the mock,
  which uses `patch(f"{module}.time", stand_in)` and yields the mock (creating a `MagicMock` if none is given).
- Add `tests/flow/test_sleep_patch.py` unit tests: the module's `time.sleep` is the mock; `time.monotonic` still works;
  the real `time.sleep` and `subprocess` are untouched, and a real short child (`git --version` or the Python
  interpreter) run inside the context records zero mock calls.
- Use Australian English in comments and docstrings; keep the file well under 400 lines.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** ruleset docstring and typing rules; hermetic-testing guidance in
  `apps/sandbox-executor/docs/hermetic_testing.md`.
- **Potential failure modes for this step:** the stand-in lacks an attribute a module reads; the self-test using a real
  child is itself network or environment dependent.
- **Guardrails and early‑abort checks:** use only `sys.executable` for the child; no network; no `gh`.

### Success & Discard Criteria

- **Success:** helper tests pass via `uv run pytest tests/flow/test_sleep_patch.py`.
- **Discard:** if `patch` of the module `time` name breaks a module that uses `time` in a type annotation or import-time
  constant, fall back to patching the callable reference the module reads.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 1.5   |
| impact_pred         | 25.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 20.9  |

### Step Metrics Rationale

`EV = 0.93 * 25.0 + 0.5 * 2.0 - 0.3 * 1.5 - 3.0 = 23.25 + 1.0 - 0.45 - 3.0 = 20.8`. Small and well understood.

---

## Step 3: Convert the three patches and add the guard test

- **Sub‑intent recommendation:** NO
- **Reasoning:** Three call sites plus one AST guard modelled on `test_patch_targets.py`; small and tightly coupled.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** an AST scan of string constants given to patch helpers reliably catches every dotted
  `time.sleep` patch without false positives on injected `sleep=` seams.
- **Learning target:** whether dynamic or f-string targets exist; how the scan treats `patch.object(time, "sleep")`.
- **Maximum acceptable cost for this learning:** one extra hour-equivalent (cost 1.0 above the estimate).

### Intent & Git Integration

- **Step Intent:** Remove every process-wide sleep patch and make reintroduction a test failure.
- **Git branch:** I-1794567515-sleepfix/P-1794567531-a466a71e-claude-agent-claude-sonnet-5-5/E-1794567531-convert
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `tests/flow/github/test_ci.py`, replace the three patches with the shared helper targeting
  `sandbox_executor.flow.github.ci` and `sandbox_executor.flow.review.loop`. Keep each `assert_not_called` or
  recorded-call assertion unchanged so its meaning ("this path never sleeps") is preserved. Remove the comment at line
  92 that explains the old workaround, and drop the `git rev-parse` stubbing only if the stub existed solely for this
  reason; otherwise leave it.
- Add `tests/flow/test_sleep_patch_guard.py`: walk every `*.py` under `tests/` with `ast`; flag a string constant passed
  as the first argument to `patch`, `mock.patch`, `monkeypatch.setattr` or `patch.object` when it ends in `.time.sleep`
  or equals `time.sleep` and names a real module; also flag `patch.object(time, "sleep")` and
  `monkeypatch.setattr(time, "sleep", ...)`. Exempt the helper module and the guard's own synthetic fixtures.
- Self-test the guard on synthetic source strings (one violating, one clean) so it provably fails on a violation.
- Assertion failure messages name the file, line, and the helper to use.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** scope limited to tests; complexity limits `C901 <= 10` so keep the AST visitor small, with no
  `# noqa`.
- **Potential failure modes for this step:** the guard flags the self-test strings; a converted test no longer
  intercepts the sleep because the module reads `time.sleep` through another alias.
- **Guardrails and early‑abort checks:** run
  `uv run pytest tests/flow/github/test_ci.py tests/flow/test_sleep_patch_guard.py` first; temporarily revert one
  conversion to confirm the guard fails (then restore it).

### Success & Discard Criteria

- **Success:** the converted tests pass, the guard passes on the tree and fails on a synthetic violation.
- **Discard:** if the cost exceeds 1.5x `cost_pred`, ship the conversion and a simpler regex guard.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.5   |
| impact_pred         | 40.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 32.8  |

### Step Metrics Rationale

`EV = 0.92 * 40.0 + 0.5 * 3.0 - 0.3 * 1.5 - 5.0 = 36.8 + 1.5 - 0.45 - 5.0 = 32.85`. This step carries the main impact:
it fixes the observed failures and prevents recurrence.

---

## Step 4: Verify on the host and in the container, then lint

- **Sub‑intent recommendation:** NO
- **Reasoning:** Verification only; the container run depends on the environment, not on new design.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the previously failing tests pass where they failed, and that lint passes.
- **Git branch:** I-1794567515-sleepfix/P-1794567531-a466a71e-claude-agent-claude-sonnet-5-5/E-1794567531-verify
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Run `uv run pytest tests/flow/github/test_ci.py tests/flow/test_sleep_patch.py tests/flow/test_sleep_patch_guard.py`
  from `apps/sandbox-executor`.
- Run the full suite once with `uv run task test`, then `uv run task lint` and `uv run task lint-docs`. No repeated runs
  and no timing measurement are needed, because the failure is a deterministic consequence of patching a shared module,
  not a race.
- Where the review-loop container is available, run the resolver's exact command (`uv run task test` inside the image).
  If it is not available, report that explicitly and rely on the deterministic helper test that runs a real child under
  the scoped patch.
- Fix lint findings (formatting, Australian English in comments) without `# noqa`.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** tests strictly via `uv run pytest`; no ledger edits; no merge to `main`.
- **Potential failure modes for this step:** unrelated flaky tests in the container; container unavailable; docs lint
  complaining about the new files if documentation is touched.
- **Guardrails and early‑abort checks:** if the full suite fails in unrelated tests, report them faithfully with their
  output rather than masking them.

### Success & Discard Criteria

- **Success:** all targeted tests, the full suite, `lint` and `lint-docs` pass; container result reported honestly.
- **Discard:** if failures trace to production code needing a hook, stop and propose a follow-up intent.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.0   |
| impact_pred         | 30.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 22.8  |

### Step Metrics Rationale

`EV = 0.88 * 30.0 + 0.5 * 4.0 - 0.3 * 2.0 - 5.0 = 26.4 + 2.0 - 0.6 - 5.0 = 22.8`. Lowest p_success because it depends on
the container environment and on unrelated suite state.

---
