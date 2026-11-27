# Plan Calibration Report: P-1795780654-ANA-G4N

- **Plan Reference:** [`plans/I-1795780631-self-doc-ast-behavior-gate/P-1795780654-ANA-G4N.md`](P-1795780654-ANA-G4N.md)
- **Execution ID:** `E-1795780921-ANA-G4N`
- **Execution Branch:** `I-1795780631-self-doc-ast-behavior-gate/P-1795780654-ANA-G4N/E-1795780921-ANA-G4N/_`
- **Intent Branch:** `I-1795780631-self-doc-ast-behavior-gate/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T12:40:39.000Z`
- **Evaluated Commit SHA:** `7f119d5fe8509ab9c509c6d67157363509bd4ccb`
- **Base SHA:** `c03385aff0d71fb61a391c94ab21cbc34d262b9f`
- **Merge-Base SHA:** `c03385aff0d71fb61a391c94ab21cbc34d262b9f`
- **Merged Commits:** `1`
- **Patch Size Base:** `target_merge_base` (merge-base `c03385aff0d71fb61a391c94ab21cbc34d262b9f` with the target
  branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.85`             | `1.00`            | `0.15`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.91`             | `1.25`            | `2.66`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `80.00`            | `80.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `9.50`             | `8.07`            | `1.43`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.00`             | `4.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `59.33`            | `73.55`           | `14.22`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `59.33`            | `73.55`           | `14.22`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.85 - 1.00| = 0.15$
- **Entropy Error:** $|3.91 - 1.25| = 2.66$
- **Impact Error:** $|80.00 - 80.00| = 0.00$
- **Cost Error:** $|9.50 - 8.07| = 1.43$
- **Learning Value Error:** $|4.00 - 4.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.85 \times 80.0 + 0.5 \times 4.0 - 0.3 \times 3.91 - 9.50 = 59.33$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 80.0 + 0.5 \times 4.0 - 0.3 \times 1.25 - 8.07 = 73.55$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 80.0 + 0.5 \times 4.0 - 0.3 \times 1.25 - 8.07 = 73.55$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +14.22$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `4.0` (4 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795780654-ANA-G4N` executed with minimal error ($p\_success\_error = 0.15$, $cost\_error = 1.43$). The
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
- **Approval carry-overs:** `2`
- **Pipeline runs:** `2`
- **Pipeline reruns:** `0`
- **Non-defect churn (target merge):** `2`
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
