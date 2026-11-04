# Execution Record: E-1793778741-claude-agent-claude-opus-5-5

## Summary

Execution of plan `P-1793778433-claude-agent-claude-opus-5-5` for intent
`I-1793778409-render-review-consensus-comment-from-reviewer-data`. The posted Stage 4 review is now one consensus
comment rendered from real reviewer data. The review prompt, its scaffold twin and `build_review_consensus_report()`
share one posted shape. A `holon-review-details` block in each reviewer report carries roles, per-role verdicts,
recommendations and evidenced verification through flow (including container transport) into the builder. The fixed Bean
0034 notice and the `status`-driven verdict fallback are gone, and unmeasured probes render as `unmeasured`.
`review_ensemble.py` and its trailer contract are unchanged.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 1.0   |
| entropy_actual        | 4.2   |
| impact_actual         | 80.0  |
| cost_actual           | 16.0  |
| learning_value_actual | 5.0   |

## Steps Executed

### Step 1: Baseline and consumer map

- `uv` was not on `PATH` in the sandbox; installed it with `pip install --user uv` (tooling only, no repository change).
- Baseline: `uv run pytest` over `test_review_report.py`, `test_prompt_templates.py`, `test_review_loop.py`,
  `test_flow.py`, `test_review_ensemble.py` and `test_init.py` with `-n auto --dist worksteal`: 504 passed in 7.67s.
- Constraints outside the touch set:
  - `test_review_ensemble.py::test_prompt_carries_diff_description_report_path_and_contract` needs exactly one `{diff}`
    and one `{pr_description}` in the prompt, keeps `{reviewer_1_id}` and needs `gh pr merge` present.
  - `test_init.py` checks that scaffolding writes `holon-config/prompts/review_loop.template.md`.
  - `review_ensemble.deserialize_reviewer_result` (out of scope) drops unknown keys and sets `report_text` to `""`, so
    reviewers dispatched to the review container reach the host without their report text. The seam must re-attach the
    details on the host side in `flow._dispatch_review_ensemble`.
- In-scope assertions pinning the old format: every report assertion in `test_review_report.py`;
  `test_flow.py::test_consensus_report_renders_mergeability_note` (`**Mergeability:**`). `test_review_loop.py:1317`
  asserts `report_text` never appears in the loop outcome, so the new per-reviewer key is `details`.

### Step 2: Prompt rewrite and scaffold twin

- Rewrote `holon-config/prompts/review_loop.template.md` from 309 to 196 lines: one `## Loop invariants` list
  (unanimity, zero Critical/Important/open Nit, green CI, mergeability before posting or calibration, increment review,
  fresh context and never pausing, ledger API only, human-only merging, namespaced `.subagent/` artefacts), commit
  trailers, the `holon-review-details` block contract, `## Posted review shape` with two fenced blocks (approved and
  changes requested), the on-disk trailer, the reviewer turn contract and the evaluation context.
- No persona registry, activation matrix, severity legend or Bean notice remain (grep returns 0).
- `npx prettier --write` reformatted the embedded markdown tables and wrapped the `**Checks:**` example line; the
  formatted text was then copied into `scaffold.REVIEW_LOOP_TEMPLATE`, which is byte-identical to the file.
- `test_review_ensemble.py`, `test_init.py` and `test_prompt_templates.py`: 107 passed.

### Step 3: Reviewer details seam (probe)

- Probe result: a report carrying a `<!-- holon-review-details -->` JSON block before the trailer parses unchanged
  through `review_ensemble.parse_review_trailer`, because `_extract_trailer_block` only reads from the last
  `holon-review-trailer` opening marker to the final line. The first placement worked; no fallback was needed.
- Added to `review_report.py`: `DETAILS_OPEN`/`DETAILS_CLOSE`, frozen `RoleReview`, `VerifiedItem` and `ReviewerDetails`
  (with `as_dict()`), `parse_reviewer_details()` (last block, 64 KiB cap, empty details on any error),
  `details_from_mapping()`, `render_reviewer_details()` and `finding_id()`. Every string goes through `_inline` with
  bounds (role 60, review 160, recommendation 300, claim 200, evidence 200); verdicts outside `PASS`/`CHANGES` and
  claims without path-like evidence are dropped.
- Finding: `deserialize_reviewer_result` (out of scope) sets `report_text=""` on the container path, so the details must
  travel as data in the loop outcome and be re-attached by `flow` (Step 5).

### Step 4: Contract-shaped builder

- `build_review_consensus_report()` now emits heading, evaluation paragraph, role table, verified bullets or findings
  table, one `**Checks:**` line, breakdown footer and `CLOSING_NOTICE`, in template order. The heading follows
  `ensemble_verdict == "APPROVED"` only; the `status` fallback is gone. `n` counts completed reviewers.
- Findings are de-duplicated on `(severity, normalised path, line)` (or casefolded summary when `line` is null); the
  first occurrence keeps its `FIND-R{index}-{iteration:03d}-{n:02d}` id and the Finding cell names every contributor.
- `HUMAN_MERGE_NOTICE`, `_ci_line`, `_mergeability_line`, `_reviewer_section` and `render_finding_lines` were removed;
  `render_reviewer_line` now renders the breakdown bullet. Configured reviewers with no result render as `FAILED`.
- `ruff check` and `ruff format` clean on the module, with no `noqa`.

### Step 5: Flow wiring

- `flow._reviewer_finding_ids` now calls `review_report.finding_id` (imported as `review_finding_id`, because local loop
  variables named `finding_id` would otherwise shadow it), so ledger `ACT-FIND` ids and posted ids share one formula.
- `_reviewer_summary` gains a `details` key (the sanitised `ReviewerDetails.as_dict()` for completed reviewers, `None`
  for failed ones). The ledger row shapes are unchanged.
- New `_with_transported_details()` re-attaches each transported reviewer's `details` as a rendered details block in
  `report_text` after `deserialize_review_outcome`, which closes the container-path gap found in Step 1 without touching
  `review_ensemble.py`.
- `consensus_data` already carried `iterations`, `ensemble_size`, `ensemble_verdict`, `ci_checks`, `mergeability`,
  `mergeability_note`, `target_commit`, `base_branch`, `agent` and `model`, and `reviewers=` was already the deciding
  iteration's list; nothing was added. The `--approve` gate is unchanged, with a comment that the heading comes from
  `ensemble_verdict` in the builder.

### Step 6: Reviewer entrypoint

- Verified, unchanged: `entrypoint/review_loop.py` writes and prints the loop outcome with `json.dump(loop_res)` without
  filtering keys, so `reviewers[*].details` passes through. Its failure path has `"reviewers": []` and
  `"ensemble_verdict": "FAILED"`, and the entrypoint never posts a body itself.

### Step 7: Contract tests

- `test_review_report.py` rewritten around one shared fixture (reviewers 1 and 3 raise the same finding; reviewer 3 has
  no details block). It includes the template-derived drift guard (`assert_follows_shape`, parametrised over both posted
  blocks), and tests for role activation, de-duplication, evidenced verification, verdict-only heading, mergeability and
  CI probes, failed and missing reviewers, malformed and oversized details, injection, trailer absence, the alias and
  the trailer-parser probe.
- `test_prompt_templates.py`: twin parity, under 288 lines, banned phrases absent, invariant phrases present, exactly
  two posted shapes.
- `test_flow.py`: mergeability-note test moved to the Checks line; seam tests run the real `run_review_loop` with a
  faked `gh` and assert role rows, recommendations, merged contributors, ledger ids equal to posted ids, and `--approve`
  only for an APPROVED `ensemble_verdict`.
- `test_review_loop.py`: the entrypoint's result file carries `reviewers[*].details`, and host-side deserialisation plus
  `_with_transported_details` renders the role row and recommendation.
- Mutation check by hand: moving the `**Checks:**` line after the breakdown in the builder made both parity tests fail
  (`template line '### 🗳️ Ensemble Review Breakdown' is missing from the body or out of order`). Reverted afterwards.

### Step 8: Verification

- Diff against the plan commit touches only scope files and this record: `flow.py`, `review_report.py`, `scaffold.py`,
  `review_loop.template.md`, `test_flow.py`, `test_prompt_templates.py`, `test_review_loop.py`, `test_review_report.py`
  and `executions/E-1793778741-claude-agent-claude-opus-5-5.md`. `entrypoint/review_loop.py` and the ledgers were not
  changed. No harness path or tracker id appears in the diff.
- `git diff main...HEAD` was not available (no local `main` ref), so the diff was taken against the plan commit
  `2e6140a`.

## Tests Run

No timing is measured by this intent, so every command ran once (`N = 1`).

| command                                                                                           | result                             | pytest time |
| ------------------------------------------------------------------------------------------------- | ---------------------------------- | ----------- |
| baseline: `uv run pytest` on the six affected files with `-n auto --dist worksteal`               | 504 passed, 0 failed               | 7.67s       |
| `uv run pytest` on `test_review_ensemble.py`, `test_init.py`, `test_prompt_templates.py` (Step 2) | 107 passed, 0 failed               | 2.69s       |
| `uv run pytest apps/sandbox-executor/tests/test_review_report.py` (Step 7)                        | 47 passed, 0 failed                | 0.10s       |
| final: `uv run pytest` on the six affected files with `-n auto --dist worksteal`                  | 546 passed, 0 failed               | 6.93s       |
| `uv run task test` (full suite, `-n auto --dist worksteal`)                                       | 2925 passed, 0 failed              | 45.95s      |
| `uv run task lint`                                                                                | clean                              | n/a         |
| `uv run task lint-docs`                                                                           | clean after formatting this record | n/a         |

Median of the full-suite runs: `45.95s` (one run). The two runs of the six affected files are separate commits' figures
(`504` before and `546` after the change), not repetitions.
