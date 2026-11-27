# Plan Calibration Report: P-1795801632-ANA-G4N

- **Plan Reference:**
  [`plans/I-1795801610-tests-deferred-three-speedup/P-1795801632-ANA-G4N.md`](P-1795801632-ANA-G4N.md)
- **Execution ID:** `E-1795812746-ANA-G4N`
- **Execution Branch:** `I-1795801610-tests-deferred-three-speedup/P-1795801632-ANA-G4N/E-1795812746-ANA-G4N/_`
- **Intent Branch:** `I-1795801610-tests-deferred-three-speedup/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T21:43:52.000Z`
- **Evaluated Commit SHA:** `0f844cedbf97ddb3a6fec01f5fa45dbd863b5405`
- **Base SHA:** `5c35695ce0810d5f51270ac9eae5c2d8afeb30f4`
- **Merge-Base SHA:** `5c35695ce0810d5f51270ac9eae5c2d8afeb30f4`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.86`             | `1.00`            | `0.14`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.26`             | `1.37`            | `1.89`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `75.00`            | `75.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `10.00`            | `8.50`            | `1.50`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.00`             | `4.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `55.52`            | `68.09`           | `12.57`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `55.52`            | `68.09`           | `12.57`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.86 - 1.00| = 0.14$
- **Entropy Error:** $|3.26 - 1.37| = 1.89$
- **Impact Error:** $|75.00 - 75.00| = 0.00$
- **Cost Error:** $|10.00 - 8.50| = 1.50$
- **Learning Value Error:** $|4.00 - 4.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.86 \times 75.0 + 0.5 \times 4.0 - 0.3 \times 3.26 - 10.00 = 55.52$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 75.0 + 0.5 \times 4.0 - 0.3 \times 1.37 - 8.50 = 68.09$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 75.0 + 0.5 \times 4.0 - 0.3 \times 1.37 - 8.50 = 68.09$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +12.57$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `4.4` (3 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795801632-ANA-G4N` executed with minimal error ($p\_success\_error = 0.14$, $cost\_error = 1.50$). The
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

- **Full reviews:** `2`
- **Scoped reviews:** `0`
- **Approval carry-overs:** `0`
- **Pipeline runs:** `2`
- **Pipeline reruns:** `0`
- **Non-defect churn (target merge):** `0`
- **Non-defect churn (bookkeeping):** `0`
- **Non-defect churn (CI noise):** `0`

---

## 7. Self-Explaining Code Counts

Against the merge base with origin/main: added 0 docstring(s), added 0 comment(s), removed 7 docstring(s) and removed 3
comment(s) across 9 changed file(s).

- Changed files checked: 9
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 7
- Removed comments (not '# why:'): 3
