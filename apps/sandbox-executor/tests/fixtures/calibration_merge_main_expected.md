# Plan Calibration Report: P-1795409242-REG

- **Plan Reference:** [`plans/I-1795409225-regress/P-1795409242-REG.md`](P-1795409242-REG.md)
- **Execution ID:** `E-1795409573-REG`
- **Execution Branch:** `I-1795409225-regress/P-1795409242-REG/E-1795409573-REG/_`
- **Intent Branch:** `I-1795409225-regress/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-11-23T05:45:26.000Z`
- **Evaluated Commit SHA:** `<approved_head>`
- **Base SHA:** `<main_head>`
- **Merge-Base SHA:** `<main_head>`
- **Merged Commits:** `1`
- **Patch Size Base:** `target_merge_base` (merge-base `<main_head>` with the target branch)

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating   | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :---------------- | :------------------------- |
| **$P(\text{success})$**   | `0.90`             | `1.00`            | `0.10`                                | Moderate          | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `2.00`             | `0.17`            | `1.83`                                | Moderate          | Overestimated Risk         |
| **Impact**                | `40.00`            | `40.00`           | `0.00`                                | Exact             | Perfectly Calibrated       |
| **Cost**                  | `5.00`             | `4.25`            | `0.75`                                | High (≤ 1.0)      | Highly Accurate            |
| **Learning Value**        | `2.00`             | `2.00`            | `0.00`                                | Exact             | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)** | `31.40`            | `36.70`           | `5.30`                                | Moderate          | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `31.40`            | `36.70`           | `5.30`                                | Moderate          | Conservative Underestimate |
| **Bug EV Drag**           | `0.00`             | `0.00`           | `0.00`                                | -                 | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.90 - 1.00| = 0.10$
- **Entropy Error:** $|2.00 - 0.17| = 1.83$
- **Impact Error:** $|40.00 - 40.00| = 0.00$
- **Cost Error:** $|5.00 - 4.25| = 0.75$
- **Learning Value Error:** $|2.00 - 2.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.90 \times 40.0 + 0.5 \times 2.0 - 0.3 \times 2.00 - 5.00 = 31.40$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 40.0 + 0.5 \times 2.0 - 0.3 \times 0.17 - 4.25 = 36.70$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 40.0 + 0.5 \times 2.0 - 0.3 \times 0.17 - 4.25 = 36.70$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$
  $$\Delta EV = +5.30$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `0.4` (2 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795409242-REG` executed with minimal error ($p\_success\_error = 0.10$, $cost\_error = 0.75$). The calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

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

- **Review churn:** not recorded

