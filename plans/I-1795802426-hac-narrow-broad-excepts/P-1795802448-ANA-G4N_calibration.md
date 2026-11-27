# Plan Calibration Report: P-1795802448-ANA-G4N

- **Plan Reference:** [`plans/I-1795802426-hac-narrow-broad-excepts/P-1795802448-ANA-G4N.md`](P-1795802448-ANA-G4N.md)
- **Execution ID:** `E-1795812780-ANA-G4N`
- **Execution Branch:** `I-1795802426-hac-narrow-broad-excepts/P-1795802448-ANA-G4N/E-1795812780-ANA-G4N/_`
- **Intent Branch:** `I-1795802426-hac-narrow-broad-excepts/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T21:09:39.000Z`
- **Evaluated Commit SHA:** `7ce17670255fbabd176212895e50fb81f629515f`
- **Base SHA:** `2d1d14447dff76e0f06f549c7fc9612d5c96e5d6`
- **Merge-Base SHA:** `2d1d14447dff76e0f06f549c7fc9612d5c96e5d6`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.86`             | `1.00`            | `0.14`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.20`             | `0.95`            | `2.25`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `55.00`            | `55.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `6.50`             | `5.52`            | `0.98`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**                                 | `2.50`             | `2.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `41.09`            | `50.45`           | `9.36`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `41.09`            | `50.45`           | `9.36`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.86 - 1.00| = 0.14$
- **Entropy Error:** $|3.20 - 0.95| = 2.25$
- **Impact Error:** $|55.00 - 55.00| = 0.00$
- **Cost Error:** $|6.50 - 5.52| = 0.98$
- **Learning Value Error:** $|2.50 - 2.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.86 \times 55.0 + 0.5 \times 2.5 - 0.3 \times 3.20 - 6.50 = 41.09$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 55.0 + 0.5 \times 2.5 - 0.3 \times 0.95 - 5.52 = 50.45$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 55.0 + 0.5 \times 2.5 - 0.3 \times 0.95 - 5.52 = 50.45$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +9.36$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `3.0` (10 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795802448-ANA-G4N` executed with minimal error ($p\_success\_error = 0.14$, $cost\_error = 0.98$). The
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
comment(s) across 15 changed file(s).

- Changed files checked: 15
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 0
