# Plan Calibration Report: P-1795440538-ANA-G4A

- **Plan Reference:**
  [`plans/I-1795437183-sidecar-scratch-outside-worktree/P-1795440538-ANA-G4A.md`](P-1795440538-ANA-G4A.md)
- **Execution ID:** `E-1795440894-ANA-G4A`
- **Execution Branch:** `I-1795437183-sidecar-scratch-outside-worktree/P-1795440538-ANA-G4A/E-1795440894-ANA-G4A/_`
- **Intent Branch:** `I-1795437183-sidecar-scratch-outside-worktree/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-argon`
- **Evaluation Timestamp:** `2026-11-23T14:40:59.000Z`
- **Evaluated Commit SHA:** `c7bbf41e334796a00da1c1c09316ddb49a25e662`
- **Base SHA:** `f49340f9bdfc60f35e95ff01acc5c80ecc2c57c5`
- **Merge-Base SHA:** `f49340f9bdfc60f35e95ff01acc5c80ecc2c57c5`
- **Merged Commits:** `1`
- **Patch Size Base:** `target_merge_base` (merge-base `f49340f9bdfc60f35e95ff01acc5c80ecc2c57c5` with the target
  branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.90`             | `1.00`            | `0.10`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `5.80`             | `1.61`            | `4.19`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `85.00`            | `85.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `8.50`             | `7.22`            | `1.28`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.50`             | `4.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `68.51`            | `79.55`           | `11.04`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `68.51`            | `79.55`           | `11.04`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.90 - 1.00| = 0.10$
- **Entropy Error:** $|5.80 - 1.61| = 4.19$
- **Impact Error:** $|85.00 - 85.00| = 0.00$
- **Cost Error:** $|8.50 - 7.22| = 1.28$
- **Learning Value Error:** $|4.50 - 4.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.90 \times 85.0 + 0.5 \times 4.5 - 0.3 \times 5.80 - 8.50 = 68.51$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 85.0 + 0.5 \times 4.5 - 0.3 \times 1.61 - 7.22 = 79.55$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 85.0 + 0.5 \times 4.5 - 0.3 \times 1.61 - 7.22 = 79.55$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +11.04$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `5.2` (13 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795440538-ANA-G4A` executed with minimal error ($p\_success\_error = 0.10$, $cost\_error = 1.28$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
