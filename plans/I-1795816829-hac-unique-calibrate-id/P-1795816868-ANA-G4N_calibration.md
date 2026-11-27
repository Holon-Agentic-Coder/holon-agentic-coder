# Plan Calibration Report: P-1795816868-ANA-G4N

- **Plan Reference:** [`plans/I-1795816829-hac-unique-calibrate-id/P-1795816868-ANA-G4N.md`](P-1795816868-ANA-G4N.md)
- **Execution ID:** `E-1795817078-ANA-G4N`
- **Execution Branch:** `I-1795816829-hac-unique-calibrate-id/P-1795816868-ANA-G4N/E-1795817078-ANA-G4N/_`
- **Intent Branch:** `I-1795816829-hac-unique-calibrate-id/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T22:34:25.000Z`
- **Evaluated Commit SHA:** `36cbadb4bcd4a05b5fcb86ef9e45d038b61034ce`
- **Base SHA:** `ef70759baac06237de8ee729347b365641b30d68`
- **Merge-Base SHA:** `ef70759baac06237de8ee729347b365641b30d68`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.89`             | `1.00`            | `0.11`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `1.80`             | `0.36`            | `1.44`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `65.00`            | `65.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `8.00`             | `6.80`            | `1.20`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `1.50`             | `1.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `50.06`            | `58.84`           | `8.78`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `50.06`            | `58.84`           | `8.78`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.89 - 1.00| = 0.11$
- **Entropy Error:** $|1.80 - 0.36| = 1.44$
- **Impact Error:** $|65.00 - 65.00| = 0.00$
- **Cost Error:** $|8.00 - 6.80| = 1.20$
- **Learning Value Error:** $|1.50 - 1.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.89 \times 65.0 + 0.5 \times 1.5 - 0.3 \times 1.80 - 8.00 = 50.06$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 65.0 + 0.5 \times 1.5 - 0.3 \times 0.36 - 6.80 = 58.84$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 65.0 + 0.5 \times 1.5 - 0.3 \times 0.36 - 6.80 = 58.84$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +8.78$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `1.1` (2 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795816868-ANA-G4N` executed with minimal error ($p\_success\_error = 0.11$, $cost\_error = 1.20$). The
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

- **Full reviews:** `3`
- **Scoped reviews:** `0`
- **Approval carry-overs:** `0`
- **Pipeline runs:** `2`
- **Pipeline reruns:** `0`
- **Non-defect churn (target merge):** `0`
- **Non-defect churn (bookkeeping):** `0`
- **Non-defect churn (CI noise):** `0`

---

## 7. Self-Explaining Code Counts

Against the merge base with origin/main: added 0 docstring(s), added 0 comment(s), removed 0 docstring(s) and removed 0
comment(s) across 7 changed file(s).

- Changed files checked: 7
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 0
