# Plan Calibration Report: P-1795801622-ANA-G4N

- **Plan Reference:**
  [`plans/I-1795801599-hac-turns-run-turn-decompose/P-1795801622-ANA-G4N.md`](P-1795801622-ANA-G4N.md)
- **Execution ID:** `E-1795801870-ANA-G4N`
- **Execution Branch:** `I-1795801599-hac-turns-run-turn-decompose/P-1795801622-ANA-G4N/E-1795801870-ANA-G4N/_`
- **Intent Branch:** `I-1795801599-hac-turns-run-turn-decompose/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T20:44:56.000Z`
- **Evaluated Commit SHA:** `fdfa4d9270788421973d4a1ed435b6e9f1a5103a`
- **Base SHA:** `4342c376ad31aa6b0be3082e8564fcdd8c2bb19e`
- **Merge-Base SHA:** `4342c376ad31aa6b0be3082e8564fcdd8c2bb19e`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.82`             | `1.00`            | `0.18`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `4.70`             | `1.04`            | `3.66`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `80.00`            | `80.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `14.00`            | `11.90`           | `2.10`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `5.00`             | `5.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `52.69`            | `70.29`           | `17.60`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `52.69`            | `70.29`           | `17.60`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.82 - 1.00| = 0.18$
- **Entropy Error:** $|4.70 - 1.04| = 3.66$
- **Impact Error:** $|80.00 - 80.00| = 0.00$
- **Cost Error:** $|14.00 - 11.90| = 2.10$
- **Learning Value Error:** $|5.00 - 5.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.82 \times 80.0 + 0.5 \times 5.0 - 0.3 \times 4.70 - 14.00 = 52.69$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 80.0 + 0.5 \times 5.0 - 0.3 \times 1.04 - 11.90 = 70.29$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 80.0 + 0.5 \times 5.0 - 0.3 \times 1.04 - 11.90 = 70.29$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +17.60$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `3.4` (2 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795801622-ANA-G4N` executed with minimal error ($p\_success\_error = 0.18$, $cost\_error = 2.10$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `66.7%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- `apps/sandbox-executor/tests/test_turn_supervisor.py`

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
comment(s) across 7 changed file(s).

- Changed files checked: 7
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 0
