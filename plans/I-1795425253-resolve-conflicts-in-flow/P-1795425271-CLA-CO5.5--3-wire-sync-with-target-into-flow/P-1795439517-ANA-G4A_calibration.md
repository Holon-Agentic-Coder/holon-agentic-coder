# Plan Calibration Report: P-1795439517-ANA-G4A

- **Plan Reference:**
  [`plans/I-1795425253-resolve-conflicts-in-flow/P-1795425271-CLA-CO5.5--3-wire-sync-with-target-into-flow/P-1795439517-ANA-G4A.md`](P-1795439517-ANA-G4A.md)
- **Execution ID:** `E-1795440035-ANA-G4A`
- **Execution Branch:**
  `I-1795425253-resolve-conflicts-in-flow/P-1795425271-CLA-CO5.5--3-wire-sync-with-target-into-flow/P-1795439517-ANA-G4A/E-1795440035-ANA-G4A/_`
- **Intent Branch:** `I-1795425253-resolve-conflicts-in-flow/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-argon`
- **Evaluation Timestamp:** `2026-11-23T15:00:10.000Z`
- **Evaluated Commit SHA:** `1e61e890f27121a7892d493bd4e37313c6b7f84a`
- **Base SHA:** `f49340f9bdfc60f35e95ff01acc5c80ecc2c57c5`
- **Merge-Base SHA:** `f49340f9bdfc60f35e95ff01acc5c80ecc2c57c5`
- **Merged Commits:** `3`
- **Patch Size Base:** `target_merge_base` (merge-base `f49340f9bdfc60f35e95ff01acc5c80ecc2c57c5` with the target
  branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.88`             | `1.00`            | `0.12`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `6.80`             | `3.04`            | `3.76`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `88.00`            | `88.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `9.50`             | `8.07`            | `1.43`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.50`             | `4.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `68.15`            | `81.27`           | `13.12`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `68.15`            | `81.27`           | `13.12`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.88 - 1.00| = 0.12$
- **Entropy Error:** $|6.80 - 3.04| = 3.76$
- **Impact Error:** $|88.00 - 88.00| = 0.00$
- **Cost Error:** $|9.50 - 8.07| = 1.43$
- **Learning Value Error:** $|4.50 - 4.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.88 \times 88.0 + 0.5 \times 4.5 - 0.3 \times 6.80 - 9.50 = 68.15$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 88.0 + 0.5 \times 4.5 - 0.3 \times 3.04 - 8.07 = 81.27$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 88.0 + 0.5 \times 4.5 - 0.3 \times 3.04 - 8.07 = 81.27$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +13.12$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (61 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795439517-ANA-G4A` executed with minimal error ($p\_success\_error = 0.12$, $cost\_error = 1.43$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `27.9%`

Changed files outside the touch set:

- `apps/sandbox-executor/entrypoint/role_dispatcher.sh`
- `apps/sandbox-executor/src/sandbox_executor/agent_runner.py`
- `apps/sandbox-executor/src/sandbox_executor/entrypoint/conflict_resolver.py`
- `apps/sandbox-executor/src/sandbox_executor/entrypoint/conflict_resolver_verify.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/conflict_classes.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/conflict_classes_lists.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/conflict_classes_resolve.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/conflict_classes_tokens.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/conflict_resolver_attempt.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/conflict_resolver_role.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/conflict_resolver_tiers.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/lazy_exports.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/merge_git.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/resume_guard.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/stage_conflict_resolution.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/stage_conflicts.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/target_probe.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/target_sync.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/target_sync_merge.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/target_sync_record.py`
- and 24 more

Declared entries the diff did not touch:

- none
