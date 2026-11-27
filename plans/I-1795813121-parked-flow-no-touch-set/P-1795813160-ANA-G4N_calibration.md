# Plan Calibration Report: P-1795813160-ANA-G4N

- **Plan Reference:** [`plans/I-1795813121-parked-flow-no-touch-set/P-1795813160-ANA-G4N.md`](P-1795813160-ANA-G4N.md)
- **Execution ID:** `E-1795813552-ANA-G4N`
- **Execution Branch:** `I-1795813121-parked-flow-no-touch-set/P-1795813160-ANA-G4N/E-1795813552-ANA-G4N/_`
- **Intent Branch:** `I-1795813121-parked-flow-no-touch-set/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T22:05:18.000Z`
- **Evaluated Commit SHA:** `2566814c92ef8e74a5e526f68a10cb71a99fde63`
- **Base SHA:** `dfb0f1f1c0b410919310308b22fbd3225e94a5dd`
- **Merge-Base SHA:** `dfb0f1f1c0b410919310308b22fbd3225e94a5dd`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.88`             | `1.00`            | `0.12`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `4.06`             | `0.91`            | `3.15`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `85.00`            | `85.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `14.00`            | `11.90`           | `2.10`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.00`             | `4.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `61.58`            | `74.83`           | `13.25`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `61.58`            | `74.83`           | `13.25`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.88 - 1.00| = 0.12$
- **Entropy Error:** $|4.06 - 0.91| = 3.15$
- **Impact Error:** $|85.00 - 85.00| = 0.00$
- **Cost Error:** $|14.00 - 11.90| = 2.10$
- **Learning Value Error:** $|4.00 - 4.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.88 \times 85.0 + 0.5 \times 4.0 - 0.3 \times 4.06 - 14.00 = 61.58$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 85.0 + 0.5 \times 4.0 - 0.3 \times 0.91 - 11.90 = 74.83$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 85.0 + 0.5 \times 4.0 - 0.3 \times 0.91 - 11.90 = 74.83$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +13.25$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `2.9` (6 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795813160-ANA-G4N` executed with minimal error ($p\_success\_error = 0.12$, $cost\_error = 2.10$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none

---

## 6. Review Churn

- **Full reviews:** `1`
- **Scoped reviews:** `1`
- **Approval carry-overs:** `0`
- **Pipeline runs:** `2`
- **Pipeline reruns:** `0`
- **Non-defect churn (target merge):** `0`
- **Non-defect churn (bookkeeping):** `0`
- **Non-defect churn (CI noise):** `0`

---

## 7. Self-Explaining Code Counts

Against the merge base with origin/main: added 0 docstring(s), added 0 comment(s), removed 0 docstring(s) and removed 0
comment(s) across 11 changed file(s).

- Changed files checked: 11
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 0
