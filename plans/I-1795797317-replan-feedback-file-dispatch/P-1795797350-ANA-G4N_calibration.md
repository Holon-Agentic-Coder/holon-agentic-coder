# Plan Calibration Report: P-1795797350-ANA-G4N

- **Plan Reference:**
  [`plans/I-1795797317-replan-feedback-file-dispatch/P-1795797350-ANA-G4N.md`](P-1795797350-ANA-G4N.md)
- **Execution ID:** `E-1795797667-ANA-G4N`
- **Execution Branch:** `I-1795797317-replan-feedback-file-dispatch/P-1795797350-ANA-G4N/E-1795797667-ANA-G4N/_`
- **Intent Branch:** `I-1795797317-replan-feedback-file-dispatch/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T17:35:00.000Z`
- **Evaluated Commit SHA:** `ff2451ffbacde30d8eb2abb6af59da07c6e59459`
- **Base SHA:** `50bdef75f4e3b768ee092a13cc69ae467029e151`
- **Merge-Base SHA:** `50bdef75f4e3b768ee092a13cc69ae467029e151`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.84`             | `1.00`            | `0.16`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `5.00`             | `1.47`            | `3.53`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `95.00`            | `95.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `14.00`            | `11.90`           | `2.10`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.50`             | `4.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `66.55`            | `84.91`           | `18.36`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `66.55`            | `84.91`           | `18.36`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.84 - 1.00| = 0.16$
- **Entropy Error:** $|5.00 - 1.47| = 3.53$
- **Impact Error:** $|95.00 - 95.00| = 0.00$
- **Cost Error:** $|14.00 - 11.90| = 2.10$
- **Learning Value Error:** $|4.50 - 4.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.84 \times 95.0 + 0.5 \times 4.5 - 0.3 \times 5.00 - 14.00 = 66.55$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 95.0 + 0.5 \times 4.5 - 0.3 \times 1.47 - 11.90 = 84.91$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 95.0 + 0.5 \times 4.5 - 0.3 \times 1.47 - 11.90 = 84.91$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +18.36$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `4.8` (11 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795797350-ANA-G4N` executed with minimal error ($p\_success\_error = 0.16$, $cost\_error = 2.10$). The
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
- **Scoped reviews:** `1`
- **Approval carry-overs:** `0`
- **Pipeline runs:** `3`
- **Pipeline reruns:** `0`
- **Non-defect churn (target merge):** `0`
- **Non-defect churn (bookkeeping):** `0`
- **Non-defect churn (CI noise):** `0`

---

## 7. Self-Explaining Code Counts

Against the merge base with origin/main: added 0 docstring(s), added 0 comment(s), removed 0 docstring(s) and removed 0
comment(s) across 16 changed file(s).

- Changed files checked: 16
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 0
