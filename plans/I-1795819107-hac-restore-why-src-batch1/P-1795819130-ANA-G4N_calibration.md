# Plan Calibration Report: P-1795819130-ANA-G4N

- **Plan Reference:** [`plans/I-1795819107-hac-restore-why-src-batch1/P-1795819130-ANA-G4N.md`](P-1795819130-ANA-G4N.md)
- **Execution ID:** `E-1795819432-ANA-G4N`
- **Execution Branch:** `I-1795819107-hac-restore-why-src-batch1/P-1795819130-ANA-G4N/E-1795819432-ANA-G4N/_`
- **Intent Branch:** `I-1795819107-hac-restore-why-src-batch1/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T23:00:17.000Z`
- **Evaluated Commit SHA:** `394c583af11b404b2a2d2c8c692ad5bce8ec6352`
- **Base SHA:** `3cadadf85d3ddc5fbad5e32af4ab90c3b0c5e537`
- **Merge-Base SHA:** `3cadadf85d3ddc5fbad5e32af4ab90c3b0c5e537`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.89`             | `1.00`            | `0.11`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `2.15`             | `0.49`            | `1.66`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `55.00`            | `55.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `6.50`             | `5.52`            | `0.98`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**                                 | `5.00`             | `5.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `44.30`            | `51.83`           | `7.52`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `44.30`            | `51.83`           | `7.52`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.89 - 1.00| = 0.11$
- **Entropy Error:** $|2.15 - 0.49| = 1.66$
- **Impact Error:** $|55.00 - 55.00| = 0.00$
- **Cost Error:** $|6.50 - 5.52| = 0.98$
- **Learning Value Error:** $|5.00 - 5.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.89 \times 55.0 + 0.5 \times 5.0 - 0.3 \times 2.15 - 6.50 = 44.30$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 55.0 + 0.5 \times 5.0 - 0.3 \times 0.49 - 5.52 = 51.83$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 55.0 + 0.5 \times 5.0 - 0.3 \times 0.49 - 5.52 = 51.83$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +7.52$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `1.5` (7 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795819130-ANA-G4N` executed with minimal error ($p\_success\_error = 0.11$, $cost\_error = 0.98$). The
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
- **Scoped reviews:** `0`
- **Approval carry-overs:** `0`
- **Pipeline runs:** `1`
- **Pipeline reruns:** `0`
- **Non-defect churn (target merge):** `0`
- **Non-defect churn (bookkeeping):** `0`
- **Non-defect churn (CI noise):** `0`

---

## 7. Self-Explaining Code Counts

Against the merge base with origin/main: added 0 docstring(s), added 0 comment(s), removed 0 docstring(s) and removed 0
comment(s) across 12 changed file(s).

- Changed files checked: 12
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 0
