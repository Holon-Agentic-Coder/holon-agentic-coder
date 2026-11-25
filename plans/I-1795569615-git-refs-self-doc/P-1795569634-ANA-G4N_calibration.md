# Plan Calibration Report: P-1795569634-ANA-G4N

- **Plan Reference:** [`plans/I-1795569615-git-refs-self-doc/P-1795569634-ANA-G4N.md`](P-1795569634-ANA-G4N.md)
- **Execution ID:** `E-1795569984-ANA-G4N`
- **Execution Branch:** `I-1795569615-git-refs-self-doc/P-1795569634-ANA-G4N/E-1795569984-ANA-G4N/_`
- **Intent Branch:** `I-1795569615-git-refs-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-25T01:49:23.000Z`
- **Evaluated Commit SHA:** `234b0ebe5082c094a382816f755edab345c49214`
- **Base SHA:** `5f3b0ba9455928d35d4896c8fc37daad9e2acb00`
- **Merge-Base SHA:** `5f3b0ba9455928d35d4896c8fc37daad9e2acb00`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.88`             | `1.00`            | `0.12`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `1.70`             | `0.87`            | `0.83`                                | High (≤ 1.0)    | Overestimated Risk         |
| **Impact**                                         | `54.00`            | `54.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `7.00`             | `5.95`            | `1.05`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `2.00`             | `2.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `41.01`            | `48.79`           | `7.78`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `41.01`            | `48.79`           | `7.78`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.88 - 1.00| = 0.12$
- **Entropy Error:** $|1.70 - 0.87| = 0.83$
- **Impact Error:** $|54.00 - 54.00| = 0.00$
- **Cost Error:** $|7.00 - 5.95| = 1.05$
- **Learning Value Error:** $|2.00 - 2.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.88 \times 54.0 + 0.5 \times 2.0 - 0.3 \times 1.70 - 7.00 = 41.01$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 54.0 + 0.5 \times 2.0 - 0.3 \times 0.87 - 5.95 = 48.79$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 54.0 + 0.5 \times 2.0 - 0.3 \times 0.87 - 5.95 = 48.79$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +7.78$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `2.8` (2 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795569634-ANA-G4N` executed with minimal error ($p\_success\_error = 0.12$, $cost\_error = 1.05$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
