# Plan Calibration Report: P-1795780678-ANA-G4N

- **Plan Reference:**
  [`plans/I-1795780652-promote-ledger-debounce-alert/P-1795780678-ANA-G4N.md`](P-1795780678-ANA-G4N.md)
- **Execution ID:** `E-1795780889-ANA-G4N`
- **Execution Branch:** `I-1795780652-promote-ledger-debounce-alert/P-1795780678-ANA-G4N/E-1795780889-ANA-G4N/_`
- **Intent Branch:** `I-1795780652-promote-ledger-debounce-alert/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T12:14:27.000Z`
- **Evaluated Commit SHA:** `f43c08db7379e7801cf5a97f0539ebc41dfeef50`
- **Base SHA:** `8931e938ed5ec0792f875579dad3aba3a944c2e2`
- **Merge-Base SHA:** `8931e938ed5ec0792f875579dad3aba3a944c2e2`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.81`             | `1.00`            | `0.19`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.41`             | `0.39`            | `3.02`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `90.00`            | `90.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `18.00`            | `15.30`           | `2.70`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.00`             | `4.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `55.88`            | `76.58`           | `20.70`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `55.88`            | `76.58`           | `20.70`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.81 - 1.00| = 0.19$
- **Entropy Error:** $|3.41 - 0.39| = 3.02$
- **Impact Error:** $|90.00 - 90.00| = 0.00$
- **Cost Error:** $|18.00 - 15.30| = 2.70$
- **Learning Value Error:** $|4.00 - 4.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.81 \times 90.0 + 0.5 \times 4.0 - 0.3 \times 3.41 - 18.00 = 55.88$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 90.0 + 0.5 \times 4.0 - 0.3 \times 0.39 - 15.30 = 76.58$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 90.0 + 0.5 \times 4.0 - 0.3 \times 0.39 - 15.30 = 76.58$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +20.70$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `1.2` (3 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795780678-ANA-G4N` executed with minimal error ($p\_success\_error = 0.19$, $cost\_error = 2.70$). The
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

Against the merge base with origin/main: added 0 docstring(s), added 0 comment(s), removed 0 docstring(s) and removed 8
comment(s) across 8 changed file(s).

- Changed files checked: 8
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 8
