# Plan Calibration Report: P-1795549518-ANA-G4N

- **Plan Reference:** [`plans/I-1795549499-executor-pkg-self-doc/P-1795549518-ANA-G4N.md`](P-1795549518-ANA-G4N.md)
- **Execution ID:** `E-1795549828-ANA-G4N`
- **Execution Branch:** `I-1795549499-executor-pkg-self-doc/P-1795549518-ANA-G4N/E-1795549828-ANA-G4N/_`
- **Intent Branch:** `I-1795549499-executor-pkg-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T20:08:50.000Z`
- **Evaluated Commit SHA:** `598f2cb98048d37f2e8fa865e16b84e4800648e6`
- **Base SHA:** `5545a8f1d17dda07cbf8bf8e046e80b05734b35d`
- **Merge-Base SHA:** `5545a8f1d17dda07cbf8bf8e046e80b05734b35d`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.84`             | `1.00`            | `0.16`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `4.11`             | `1.31`            | `2.80`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `60.00`            | `60.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `15.00`            | `12.75`           | `2.25`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `3.00`             | `3.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `35.67`            | `48.36`           | `12.69`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `35.67`            | `48.36`           | `12.69`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.84 - 1.00| = 0.16$
- **Entropy Error:** $|4.11 - 1.31| = 2.80$
- **Impact Error:** $|60.00 - 60.00| = 0.00$
- **Cost Error:** $|15.00 - 12.75| = 2.25$
- **Learning Value Error:** $|3.00 - 3.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.84 \times 60.0 + 0.5 \times 3.0 - 0.3 \times 4.11 - 15.00 = 35.67$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 60.0 + 0.5 \times 3.0 - 0.3 \times 1.31 - 12.75 = 48.36$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 60.0 + 0.5 \times 3.0 - 0.3 \times 1.31 - 12.75 = 48.36$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +12.69$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `4.2` (8 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795549518-ANA-G4N` executed with minimal error ($p\_success\_error = 0.16$, $cost\_error = 2.25$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
