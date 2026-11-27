# Plan Calibration Report: P-1795817878-ANA-G4N

- **Plan Reference:** [`plans/I-1795817852-hac-restore-why-tests/P-1795817878-ANA-G4N.md`](P-1795817878-ANA-G4N.md)
- **Execution ID:** `E-1795818137-ANA-G4N`
- **Execution Branch:** `I-1795817852-hac-restore-why-tests/P-1795817878-ANA-G4N/E-1795818137-ANA-G4N/_`
- **Intent Branch:** `I-1795817852-hac-restore-why-tests/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T22:39:55.000Z`
- **Evaluated Commit SHA:** `6153c5466a7010ca0a675b885101c1fabc150134`
- **Base SHA:** `01018118e1c6f3fda7f6c9290df3f5beb2b83711`
- **Merge-Base SHA:** `01018118e1c6f3fda7f6c9290df3f5beb2b83711`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.91`             | `1.00`            | `0.09`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `2.10`             | `0.53`            | `1.57`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `45.00`            | `45.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `6.50`             | `5.52`            | `0.98`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**                                 | `3.00`             | `3.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `35.32`            | `40.82`           | `5.50`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `35.32`            | `40.82`           | `5.50`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.91 - 1.00| = 0.09$
- **Entropy Error:** $|2.10 - 0.53| = 1.57$
- **Impact Error:** $|45.00 - 45.00| = 0.00$
- **Cost Error:** $|6.50 - 5.52| = 0.98$
- **Learning Value Error:** $|3.00 - 3.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.91 \times 45.0 + 0.5 \times 3.0 - 0.3 \times 2.10 - 6.50 = 35.32$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 45.0 + 0.5 \times 3.0 - 0.3 \times 0.53 - 5.52 = 40.82$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 45.0 + 0.5 \times 3.0 - 0.3 \times 0.53 - 5.52 = 40.82$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +5.50$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `1.6` (8 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795817878-ANA-G4N` executed with minimal error ($p\_success\_error = 0.09$, $cost\_error = 0.98$). The
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
comment(s) across 13 changed file(s).

- Changed files checked: 13
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 0
