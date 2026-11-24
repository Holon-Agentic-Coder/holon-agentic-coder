# Plan Calibration Report: P-1795533043-ANA-G4N

- **Plan Reference:** [`plans/I-1795533021-cli-commands-misc-self-doc/P-1795533043-ANA-G4N.md`](P-1795533043-ANA-G4N.md)
- **Execution ID:** `E-1795533304-ANA-G4N`
- **Execution Branch:** `I-1795533021-cli-commands-misc-self-doc/P-1795533043-ANA-G4N/E-1795533304-ANA-G4N/_`
- **Intent Branch:** `I-1795533021-cli-commands-misc-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T15:59:00.000Z`
- **Evaluated Commit SHA:** `ff8f1b09aabdb556458c6dd3b2af7a18e717ab6a`
- **Base SHA:** `b24c0ac641a5ea4e74b9e71e312f38ff7da41c77`
- **Merge-Base SHA:** `b24c0ac641a5ea4e74b9e71e312f38ff7da41c77`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.86`             | `1.00`            | `0.14`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `4.80`             | `1.33`            | `3.47`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `60.00`            | `60.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `7.50`             | `6.38`            | `1.12`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `1.70`             | `1.70`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `43.51`            | `54.07`           | `10.56`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `43.51`            | `54.07`           | `10.56`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.86 - 1.00| = 0.14$
- **Entropy Error:** $|4.80 - 1.33| = 3.47$
- **Impact Error:** $|60.00 - 60.00| = 0.00$
- **Cost Error:** $|7.50 - 6.38| = 1.12$
- **Learning Value Error:** $|1.70 - 1.70| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.86 \times 60.0 + 0.5 \times 1.7 - 0.3 \times 4.80 - 7.50 = 43.51$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 60.0 + 0.5 \times 1.7 - 0.3 \times 1.33 - 6.38 = 54.07$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 60.0 + 0.5 \times 1.7 - 0.3 \times 1.33 - 6.38 = 54.07$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +10.56$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `4.3` (13 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795533043-ANA-G4N` executed with minimal error ($p\_success\_error = 0.14$, $cost\_error = 1.12$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
