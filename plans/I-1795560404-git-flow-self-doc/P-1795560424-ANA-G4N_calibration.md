# Plan Calibration Report: P-1795560424-ANA-G4N

- **Plan Reference:** [`plans/I-1795560404-git-flow-self-doc/P-1795560424-ANA-G4N.md`](P-1795560424-ANA-G4N.md)
- **Execution ID:** `E-1795560728-ANA-G4N`
- **Execution Branch:** `I-1795560404-git-flow-self-doc/P-1795560424-ANA-G4N/E-1795560728-ANA-G4N/_`
- **Intent Branch:** `I-1795560404-git-flow-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T23:11:14.000Z`
- **Evaluated Commit SHA:** `c434f994dbe0a3ffb00c094eea7019a2933a5d91`
- **Base SHA:** `c78dae7d2bc2e85d3a598955294fcddfc7774e69`
- **Merge-Base SHA:** `c78dae7d2bc2e85d3a598955294fcddfc7774e69`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.87`             | `1.00`            | `0.13`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `2.80`             | `1.16`            | `1.64`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `65.00`            | `65.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `15.00`            | `12.75`           | `2.25`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `2.00`             | `2.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `41.71`            | `52.90`           | `11.19`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `41.71`            | `52.90`           | `11.19`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.87 - 1.00| = 0.13$
- **Entropy Error:** $|2.80 - 1.16| = 1.64$
- **Impact Error:** $|65.00 - 65.00| = 0.00$
- **Cost Error:** $|15.00 - 12.75| = 2.25$
- **Learning Value Error:** $|2.00 - 2.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.87 \times 65.0 + 0.5 \times 2.0 - 0.3 \times 2.80 - 15.00 = 41.71$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 65.0 + 0.5 \times 2.0 - 0.3 \times 1.16 - 12.75 = 52.90$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 65.0 + 0.5 \times 2.0 - 0.3 \times 1.16 - 12.75 = 52.90$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +11.19$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `3.7` (2 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795560424-ANA-G4N` executed with minimal error ($p\_success\_error = 0.13$, $cost\_error = 2.25$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
