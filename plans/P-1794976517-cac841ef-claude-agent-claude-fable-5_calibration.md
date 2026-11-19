# Plan Calibration Report: P-1794976517-cac841ef-claude-agent-claude-fable-5

- **Plan Reference:**
  [`plans/P-1794976517-cac841ef-claude-agent-claude-fable-5.md`](P-1794976517-cac841ef-claude-agent-claude-fable-5.md)
- **Execution ID:** `E-1794977034-a79b4aa6-claude-agent-claude-fable-5`
- **Execution Branch:**
  `I-1794976501-macgate/P-1794976517-cac841ef-claude-agent-claude-fable-5/E-1794977034-a79b4aa6-claude-agent-claude-fable-5/_`
- **Intent Branch:** `I-1794976501-macgate/_`
- **Evaluating Agent:** `claude-agent/claude-fable-5`
- **Evaluation Timestamp:** `2026-11-18T06:49:31.000Z`
- **Evaluated Commit SHA:** `b20667cfae1a36e8038f215b2eb20a5139b7fef2`
- **Base SHA:** `c20a301897cdd83f5a12f948700720454b3aa74b`
- **Merge-Base SHA:** `c20a301897cdd83f5a12f948700720454b3aa74b`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.78`             | `1.00`            | `0.22`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `11.00`            | `1.47`            | `9.53`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `85.00`            | `85.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `32.00`            | `27.20`           | `4.80`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `6.50`             | `6.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `34.25`            | `60.61`           | `26.36`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `34.25`            | `60.61`           | `26.36`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.78 - 1.00| = 0.22$
- **Entropy Error:** $|11.00 - 1.47| = 9.53$
- **Impact Error:** $|85.00 - 85.00| = 0.00$
- **Cost Error:** $|32.00 - 27.20| = 4.80$
- **Learning Value Error:** $|6.50 - 6.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.78 \times 85.0 + 0.5 \times 6.5 - 0.3 \times 11.00 - 32.00 = 34.25$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 85.0 + 0.5 \times 6.5 - 0.3 \times 1.47 - 27.20 = 60.61$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 85.0 + 0.5 \times 6.5 - 0.3 \times 1.47 - 27.20 = 60.61$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +26.36$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `4.8` (9 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794976517-cac841ef-claude-agent-claude-fable-5` executed with minimal error ($p\_success\_error = 0.22$,
$cost\_error = 4.80$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `90.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- `apps/sandbox-executor/tests/test_calibrate_final_head_ci.py`
