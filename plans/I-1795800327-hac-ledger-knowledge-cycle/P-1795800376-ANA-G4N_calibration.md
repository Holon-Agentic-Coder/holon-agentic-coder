# Plan Calibration Report: P-1795800376-ANA-G4N

- **Plan Reference:** [`plans/I-1795800327-hac-ledger-knowledge-cycle/P-1795800376-ANA-G4N.md`](P-1795800376-ANA-G4N.md)
- **Execution ID:** `E-1795800568-ANA-G4N`
- **Execution Branch:** `I-1795800327-hac-ledger-knowledge-cycle/P-1795800376-ANA-G4N/E-1795800568-ANA-G4N/_`
- **Intent Branch:** `I-1795800327-hac-ledger-knowledge-cycle/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T17:44:09.000Z`
- **Evaluated Commit SHA:** `13bac606ea2df38a9a24050cb18995fde48a41bc`
- **Base SHA:** `d45b9b8083b6cd4c181fc76cd72e4df178cd4002`
- **Merge-Base SHA:** `d45b9b8083b6cd4c181fc76cd72e4df178cd4002`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.86`             | `1.00`            | `0.14`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `2.28`             | `0.75`            | `1.53`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `50.00`            | `50.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `7.50`             | `6.38`            | `1.12`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.00`             | `4.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `36.82`            | `45.40`           | `8.58`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `36.82`            | `45.40`           | `8.58`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.86 - 1.00| = 0.14$
- **Entropy Error:** $|2.28 - 0.75| = 1.53$
- **Impact Error:** $|50.00 - 50.00| = 0.00$
- **Cost Error:** $|7.50 - 6.38| = 1.12$
- **Learning Value Error:** $|4.00 - 4.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.86 \times 50.0 + 0.5 \times 4.0 - 0.3 \times 2.28 - 7.50 = 36.82$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 50.0 + 0.5 \times 4.0 - 0.3 \times 0.75 - 6.38 = 45.40$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 50.0 + 0.5 \times 4.0 - 0.3 \times 0.75 - 6.38 = 45.40$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +8.58$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `2.4` (4 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795800376-ANA-G4N` executed with minimal error ($p\_success\_error = 0.14$, $cost\_error = 1.12$). The
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
comment(s) across 9 changed file(s).

- Changed files checked: 9
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 0
