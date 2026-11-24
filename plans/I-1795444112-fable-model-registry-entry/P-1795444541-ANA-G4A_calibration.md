# Plan Calibration Report: P-1795444541-ANA-G4A

- **Plan Reference:** [`plans/I-1795444112-fable-model-registry-entry/P-1795444541-ANA-G4A.md`](P-1795444541-ANA-G4A.md)
- **Execution ID:** `E-1795444771-ANA-G4A`
- **Execution Branch:** `I-1795444112-fable-model-registry-entry/P-1795444541-ANA-G4A/E-1795444771-ANA-G4A/_`
- **Intent Branch:** `I-1795444112-fable-model-registry-entry/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-argon`
- **Evaluation Timestamp:** `2026-11-24T05:23:33.000Z`
- **Evaluated Commit SHA:** `14ac29ecda82e5397e6d48526e4a7528075646dc`
- **Base SHA:** `e563254efc25fb9f611e4cddd60906f826d00e16`
- **Merge-Base SHA:** `e563254efc25fb9f611e4cddd60906f826d00e16`
- **Merged Commits:** `2`
- **Patch Size Base:** `target_merge_base` (merge-base `e563254efc25fb9f611e4cddd60906f826d00e16` with the target
  branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.94`             | `1.00`            | `0.06`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.60`             | `0.75`            | `2.85`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `78.00`            | `78.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `5.50`             | `4.67`            | `0.83`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**                                 | `4.00`             | `4.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `68.74`            | `75.11`           | `6.37`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `68.74`            | `75.11`           | `6.37`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.94 - 1.00| = 0.06$
- **Entropy Error:** $|3.60 - 0.75| = 2.85$
- **Impact Error:** $|78.00 - 78.00| = 0.00$
- **Cost Error:** $|5.50 - 4.67| = 0.83$
- **Learning Value Error:** $|4.00 - 4.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.94 \times 78.0 + 0.5 \times 4.0 - 0.3 \times 3.60 - 5.50 = 68.74$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 78.0 + 0.5 \times 4.0 - 0.3 \times 0.75 - 4.67 = 75.11$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 78.0 + 0.5 \times 4.0 - 0.3 \times 0.75 - 4.67 = 75.11$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +6.37$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `2.4` (6 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795444541-ANA-G4A` executed with minimal error ($p\_success\_error = 0.06$, $cost\_error = 0.83$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `85.7%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- `apps/sandbox-executor/src/sandbox_executor/cli/commands/models.py`
