# Plan Calibration Report: P-1794891094-eede5d91-claude-agent-claude-fable-5

- **Plan Reference:**
  [`plans/P-1794891094-eede5d91-claude-agent-claude-fable-5.md`](P-1794891094-eede5d91-claude-agent-claude-fable-5.md)
- **Execution ID:** `E-1794891502-b04b8aae-claude-agent-claude-fable-5`
- **Execution Branch:**
  `I-1794891078-s5rerun/P-1794891094-eede5d91-claude-agent-claude-fable-5/E-1794891502-b04b8aae-claude-agent-claude-fable-5/_`
- **Intent Branch:** `I-1794891078-s5rerun/_`
- **Evaluating Agent:** `claude-agent/claude-fable-5`
- **Evaluation Timestamp:** `2026-11-17T09:06:30.000Z`
- **Evaluated Commit SHA:** `0bf09a07f10630cc9d1e534d0eb734ed4ea45a35`
- **Base SHA:** `8c159164ff4cac531d82efb0e604fe48d6354a2b`
- **Merge-Base SHA:** `8c159164ff4cac531d82efb0e604fe48d6354a2b`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.80`             | `1.00`            | `0.20`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `12.50`            | `1.69`            | `10.81`                               | Moderate        | Overestimated Risk         |
| **Impact**                                         | `88.00`            | `88.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `50.00`            | `42.50`           | `7.50`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `5.00`             | `5.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `19.15`            | `47.49`           | `28.34`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `19.15`            | `47.49`           | `28.34`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.80 - 1.00| = 0.20$
- **Entropy Error:** $|12.50 - 1.69| = 10.81$
- **Impact Error:** $|88.00 - 88.00| = 0.00$
- **Cost Error:** $|50.00 - 42.50| = 7.50$
- **Learning Value Error:** $|5.00 - 5.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.80 \times 88.0 + 0.5 \times 5.0 - 0.3 \times 12.50 - 50.00 = 19.15$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 88.0 + 0.5 \times 5.0 - 0.3 \times 1.69 - 42.50 = 47.49$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 88.0 + 0.5 \times 5.0 - 0.3 \times 1.69 - 42.50 = 47.49$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +28.34$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `5.5` (9 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794891094-eede5d91-claude-agent-claude-fable-5` executed with minimal error ($p\_success\_error = 0.20$,
$cost\_error = 7.50$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `56.2%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- `apps/sandbox-executor/src/sandbox_executor/flow/stages/calibrate.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/review/posting.py`
- `apps/sandbox-executor/src/sandbox_executor/calibration.py`
- `apps/sandbox-executor/tests/test_calibrate_sync.py`
- `apps/sandbox-executor/tests/test_calibration.py`
- `apps/sandbox-executor/tests/test_review_loop.py`
- `apps/sandbox-executor/tests/flow/test_package_exports.py`
