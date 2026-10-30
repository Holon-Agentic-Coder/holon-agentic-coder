# Plan for I-1793349075-route-stage-4-reviewer-turns-through-review-loop-container

- **Plan ID:** P-1793349090-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.4.10)
- **Created At:** 2026-10-30T08:31:31.405Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with focus on robust sandbox role dispatch and verified result deserialisation.
- **Safety priority level:** critical
- **Priority Justification:** Reviewer agent turns must never execute directly on the host; untrusted reviewer turns and
  isolated container boundaries strictly mandated by world safety rules.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Architectural seams for role dispatch and the review-loop container already exist; the work
  involves direct integration and verification.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 2.80  |
| impact_pred         | 88.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 71.20 |

### Strategy Rationale

Host-side Holon flow Stage 4 currently fails closed with a refusal error (`HOST_REVIEW_REFUSAL`) because reviewer agent
turns cannot safely execute on the host. Under safety invariants, reviewer LLM turns must run strictly within an
isolated sandbox container.

This plan establishes containerised reviewer dispatch and a verified result transport:

1. `review_ensemble.py` provides verified serialisation and deserialisation for `ReviewerResult` and review outcomes,
   ensuring that unverified container outputs fail closed.
2. `entrypoint/review_loop.py` and `cli.py` accept a `--result-file` transport path and `--skip-post` flag, mounting the
   result file and returning structured review data from the container.
3. `flow.py` replaces the host refusal in Stage 4 with container dispatch through
   `_dispatch_role(context, "review-loop", ...)` when `not reviewers_sandboxed()`, while preserving host ownership of
   mergeability verification, CI status gating, official PR review posting, and canonical ledger recording.
4. Comprehensive unit and integration tests verify the container dispatch seam, result transport parsing, failure
   handling, and host integrity invariants.
5. Documentation updates and full test suite verification.

## Steps

### Step 1: Review outcome transport serialisation and deserialisation

Implement robust serialisation and deserialisation functions for `ReviewerResult` and review outcome payloads in
`sandbox_executor/review_ensemble.py`, ensuring strict schema validation that fails closed against malformed or
malicious data.

### Step 2: Review-loop entrypoint and CLI container transport wiring

Update `sandbox_executor/entrypoint/review_loop.py` and `sandbox_executor/cli.py` to support `--result-file` transport
and `--skip-post` flags, including bind-mount wiring for `result_file` in `run_docker_container`.

### Step 3: Flow Stage 4 container dispatch and result integration

Replace the host-side refusal in `sandbox_executor/flow.py` with containerised reviewer dispatch via
`_dispatch_role(context, "review-loop", ...)`. Wire verified result deserialisation into host Stage 4 while retaining
host ownership of mergeability, CI checks, PR review posting, and append-only ledger logging.

### Step 4: Unit and integration tests for container dispatch and host integrity

Update existing refusal tests in `tests/test_review_loop.py` to assert container dispatch and failure handling. Add
comprehensive unit and integration tests covering successful dispatch, container failures, result validation, and host
integrity gating.

### Step 5: Documentation, formatting, and full verification suite

Update architectural documentation in `docs/git_flow.md` and `docs/architecture.md`. Run formatting, linting, and full
test suite verification.
