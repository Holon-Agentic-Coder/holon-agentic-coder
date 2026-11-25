# Plan Calibration Report: P-1795567907-ANA-G4N

- **Plan Reference:** [`plans/I-1795567888-preflight-self-doc/P-1795567907-ANA-G4N.md`](P-1795567907-ANA-G4N.md)
- **Execution ID:** `E-1795568244-ANA-G4N`
- **Execution Branch:** `I-1795567888-preflight-self-doc/P-1795567907-ANA-G4N/E-1795568244-ANA-G4N/_`
- **Intent Branch:** `I-1795567888-preflight-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-25T01:10:55.000Z`
- **Evaluated Commit SHA:** `695813753b995b5a8686e59d2dbf5bacd16bc5af`
- **Base SHA:** `131c3b42ab86a7a40835123794cb74820099518d`
- **Merge-Base SHA:** `131c3b42ab86a7a40835123794cb74820099518d`
- **Merged Commits:** `1`
- **Patch Size Base:** `target_merge_base` (merge-base `131c3b42ab86a7a40835123794cb74820099518d` with the target
  branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.88`             | `1.00`            | `0.12`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `2.76`             | `0.71`            | `2.05`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `60.00`            | `60.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `6.50`             | `5.52`            | `0.98`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**                                 | `2.00`             | `2.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `46.47`            | `55.27`           | `8.80`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `46.47`            | `55.27`           | `8.80`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.88 - 1.00| = 0.12$
- **Entropy Error:** $|2.76 - 0.71| = 2.05$
- **Impact Error:** $|60.00 - 60.00| = 0.00$
- **Cost Error:** $|6.50 - 5.52| = 0.98$
- **Learning Value Error:** $|2.00 - 2.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.88 \times 60.0 + 0.5 \times 2.0 - 0.3 \times 2.76 - 6.50 = 46.47$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 60.0 + 0.5 \times 2.0 - 0.3 \times 0.71 - 5.52 = 55.27$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 60.0 + 0.5 \times 2.0 - 0.3 \times 0.71 - 5.52 = 55.27$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +8.80$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `2.2` (2 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795567907-ANA-G4N` executed with minimal error ($p\_success\_error = 0.12$, $cost\_error = 0.98$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
