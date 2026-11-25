# Plan Calibration Report: P-1795570970-ANA-G4N

- **Plan Reference:** [`plans/I-1795570951-github-pr-merge-self-doc/P-1795570970-ANA-G4N.md`](P-1795570970-ANA-G4N.md)
- **Execution ID:** `E-1795571328-ANA-G4N`
- **Execution Branch:** `I-1795570951-github-pr-merge-self-doc/P-1795570970-ANA-G4N/E-1795571328-ANA-G4N/_`
- **Intent Branch:** `I-1795570951-github-pr-merge-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-25T02:23:13.000Z`
- **Evaluated Commit SHA:** `69fae7f488b48d8431acaebdfb48a51b559b1d09`
- **Base SHA:** `c93bb9ebe85b71cab8a42c948ddd9d7232d7ff42`
- **Merge-Base SHA:** `c93bb9ebe85b71cab8a42c948ddd9d7232d7ff42`
- **Merged Commits:** `1`
- **Patch Size Base:** `target_merge_base` (merge-base `c93bb9ebe85b71cab8a42c948ddd9d7232d7ff42` with the target
  branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.92`             | `1.00`            | `0.08`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `2.43`             | `1.13`            | `1.30`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `58.00`            | `58.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `14.00`            | `11.90`           | `2.10`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `1.50`             | `1.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `39.38`            | `46.51`           | `7.13`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `39.38`            | `46.51`           | `7.13`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.92 - 1.00| = 0.08$
- **Entropy Error:** $|2.43 - 1.13| = 1.30$
- **Impact Error:** $|58.00 - 58.00| = 0.00$
- **Cost Error:** $|14.00 - 11.90| = 2.10$
- **Learning Value Error:** $|1.50 - 1.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.92 \times 58.0 + 0.5 \times 1.5 - 0.3 \times 2.43 - 14.00 = 39.38$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 58.0 + 0.5 \times 1.5 - 0.3 \times 1.13 - 11.90 = 46.51$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 58.0 + 0.5 \times 1.5 - 0.3 \times 1.13 - 11.90 = 46.51$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +7.13$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `3.6` (7 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795570970-ANA-G4N` executed with minimal error ($p\_success\_error = 0.08$, $cost\_error = 2.10$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
