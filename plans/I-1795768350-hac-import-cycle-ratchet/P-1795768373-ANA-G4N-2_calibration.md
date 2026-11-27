# Plan Calibration Report: P-1795768373-ANA-G4N-2

- **Plan Reference:**
  [`plans/I-1795768350-hac-import-cycle-ratchet/P-1795768373-ANA-G4N-2.md`](P-1795768373-ANA-G4N-2.md)
- **Execution ID:** `E-1795768641-ANA-G4N`
- **Execution Branch:** `I-1795768350-hac-import-cycle-ratchet/P-1795768373-ANA-G4N-2/E-1795768641-ANA-G4N/_`
- **Intent Branch:** `I-1795768350-hac-import-cycle-ratchet/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T10:40:28.000Z`
- **Evaluated Commit SHA:** `6e81072c09c6babb806d1efab68e2ffa2e290cad`
- **Base SHA:** `be5b58f46502c5fbe73b52a8e8dedf6dd92d7447`
- **Merge-Base SHA:** `be5b58f46502c5fbe73b52a8e8dedf6dd92d7447`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.87`             | `1.00`            | `0.13`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `2.31`             | `0.54`            | `1.77`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `75.00`            | `75.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `13.00`            | `11.05`           | `1.95`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `3.50`             | `3.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `53.31`            | `65.54`           | `12.23`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `53.31`            | `65.54`           | `12.23`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.87 - 1.00| = 0.13$
- **Entropy Error:** $|2.31 - 0.54| = 1.77$
- **Impact Error:** $|75.00 - 75.00| = 0.00$
- **Cost Error:** $|13.00 - 11.05| = 1.95$
- **Learning Value Error:** $|3.50 - 3.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.87 \times 75.0 + 0.5 \times 3.5 - 0.3 \times 2.31 - 13.00 = 53.31$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 75.0 + 0.5 \times 3.5 - 0.3 \times 0.54 - 11.05 = 65.54$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 75.0 + 0.5 \times 3.5 - 0.3 \times 0.54 - 11.05 = 65.54$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +12.23$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `1.7` (1 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795768373-ANA-G4N-2` executed with minimal error ($p\_success\_error = 0.13$, $cost\_error = 1.95$). The
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
- **Pipeline runs:** `1`
- **Pipeline reruns:** `0`
- **Non-defect churn (target merge):** `0`
- **Non-defect churn (bookkeeping):** `0`
- **Non-defect churn (CI noise):** `0`

---

## 7. Self-Explaining Code Counts

Against the merge base with origin/main: added 0 docstring(s), added 0 comment(s), removed 0 docstring(s) and removed 0
comment(s) across 6 changed file(s).

- Changed files checked: 6
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 0
