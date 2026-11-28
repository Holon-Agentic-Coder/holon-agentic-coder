# Plan Calibration Report: P-1795819062-ANA-G4N

- **Plan Reference:** [`plans/I-1795819038-hac-provider-cooldown/P-1795819062-ANA-G4N.md`](P-1795819062-ANA-G4N.md)
- **Execution ID:** `E-1795819299-ANA-G4N`
- **Execution Branch:** `I-1795819038-hac-provider-cooldown/P-1795819062-ANA-G4N/E-1795819299-ANA-G4N/_`
- **Intent Branch:** `I-1795819038-hac-provider-cooldown/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T23:14:46.000Z`
- **Evaluated Commit SHA:** `5ae68778819e5626c5f8ff7160633cf4cdcd1b5f`
- **Base SHA:** `8de203de52a1d5cb94fe49e566b42a1a55ab4bae`
- **Merge-Base SHA:** `8de203de52a1d5cb94fe49e566b42a1a55ab4bae`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.86`             | `1.00`            | `0.14`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `6.10`             | `1.56`            | `4.54`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `82.00`            | `82.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `16.00`            | `13.60`           | `2.40`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.50`             | `4.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `54.94`            | `70.18`           | `15.24`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `54.94`            | `70.18`           | `15.24`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.86 - 1.00| = 0.14$
- **Entropy Error:** $|6.10 - 1.56| = 4.54$
- **Impact Error:** $|82.00 - 82.00| = 0.00$
- **Cost Error:** $|16.00 - 13.60| = 2.40$
- **Learning Value Error:** $|4.50 - 4.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.86 \times 82.0 + 0.5 \times 4.5 - 0.3 \times 6.10 - 16.00 = 54.94$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 82.0 + 0.5 \times 4.5 - 0.3 \times 1.56 - 13.60 = 70.18$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 82.0 + 0.5 \times 4.5 - 0.3 \times 1.56 - 13.60 = 70.18$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +15.24$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `5.1` (8 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795819062-ANA-G4N` executed with minimal error ($p\_success\_error = 0.14$, $cost\_error = 2.40$). The
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

Against the merge base with origin/main: added 0 docstring(s), added 0 comment(s), removed 0 docstring(s) and removed 0
comment(s) across 13 changed file(s).

- Changed files checked: 13
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 0
