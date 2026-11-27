# Plan Calibration Report: P-1795798652-ANA-G4N

- **Plan Reference:** [`plans/I-1795798629-stage4-resolver-touch-set/P-1795798652-ANA-G4N.md`](P-1795798652-ANA-G4N.md)
- **Execution ID:** `E-1795798908-ANA-G4N`
- **Execution Branch:** `I-1795798629-stage4-resolver-touch-set/P-1795798652-ANA-G4N/E-1795798908-ANA-G4N/_`
- **Intent Branch:** `I-1795798629-stage4-resolver-touch-set/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T17:32:43.000Z`
- **Evaluated Commit SHA:** `b1754a2f561c3afd8cbf12c9b1c1327cc7da05b8`
- **Base SHA:** `2e8e5d15ed72619523190263dc09a5aad8163b13`
- **Merge-Base SHA:** `2e8e5d15ed72619523190263dc09a5aad8163b13`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.80`             | `1.00`            | `0.20`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.70`             | `1.02`            | `2.68`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `80.00`            | `80.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `8.00`             | `6.80`            | `1.20`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `3.50`             | `3.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `56.64`            | `74.64`           | `18.00`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `56.64`            | `74.64`           | `18.00`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.80 - 1.00| = 0.20$
- **Entropy Error:** $|3.70 - 1.02| = 2.68$
- **Impact Error:** $|80.00 - 80.00| = 0.00$
- **Cost Error:** $|8.00 - 6.80| = 1.20$
- **Learning Value Error:** $|3.50 - 3.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.80 \times 80.0 + 0.5 \times 3.5 - 0.3 \times 3.70 - 8.00 = 56.64$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 80.0 + 0.5 \times 3.5 - 0.3 \times 1.02 - 6.80 = 74.64$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 80.0 + 0.5 \times 3.5 - 0.3 \times 1.02 - 6.80 = 74.64$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +18.00$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `3.3` (3 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795798652-ANA-G4N` executed with minimal error ($p\_success\_error = 0.20$, $cost\_error = 1.20$). The
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
comment(s) across 8 changed file(s).

- Changed files checked: 8
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 0
