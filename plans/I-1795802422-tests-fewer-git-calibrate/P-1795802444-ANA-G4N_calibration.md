# Plan Calibration Report: P-1795802444-ANA-G4N

- **Plan Reference:** [`plans/I-1795802422-tests-fewer-git-calibrate/P-1795802444-ANA-G4N.md`](P-1795802444-ANA-G4N.md)
- **Execution ID:** `E-1795812801-ANA-G4N`
- **Execution Branch:** `I-1795802422-tests-fewer-git-calibrate/P-1795802444-ANA-G4N/E-1795812801-ANA-G4N/_`
- **Intent Branch:** `I-1795802422-tests-fewer-git-calibrate/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T22:06:03.000Z`
- **Evaluated Commit SHA:** `70611fbe27665f0feec9bc8a0b63cd1bb11d35dd`
- **Base SHA:** `0f07ea7da5cebc94f48792ff81b961cde9925788`
- **Merge-Base SHA:** `0f07ea7da5cebc94f48792ff81b961cde9925788`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.86`             | `1.00`            | `0.14`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `7.40`             | `3.04`            | `4.36`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `78.00`            | `78.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `17.00`            | `14.45`           | `2.55`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `5.50`             | `5.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `50.61`            | `65.39`           | `14.78`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `50.61`            | `65.39`           | `14.78`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.86 - 1.00| = 0.14$
- **Entropy Error:** $|7.40 - 3.04| = 4.36$
- **Impact Error:** $|78.00 - 78.00| = 0.00$
- **Cost Error:** $|17.00 - 14.45| = 2.55$
- **Learning Value Error:** $|5.50 - 5.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.86 \times 78.0 + 0.5 \times 5.5 - 0.3 \times 7.40 - 17.00 = 50.61$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 78.0 + 0.5 \times 5.5 - 0.3 \times 3.04 - 14.45 = 65.39$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 78.0 + 0.5 \times 5.5 - 0.3 \times 3.04 - 14.45 = 65.39$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +14.78$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (11 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795802444-ANA-G4N` executed with minimal error ($p\_success\_error = 0.14$, $cost\_error = 2.55$). The
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

Against the merge base with origin/main: added 0 docstring(s), added 0 comment(s), removed 7 docstring(s) and removed 6
comment(s) across 16 changed file(s).

- Changed files checked: 16
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 7
- Removed comments (not '# why:'): 6
