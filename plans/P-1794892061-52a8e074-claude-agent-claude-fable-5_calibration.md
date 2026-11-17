# Plan Calibration Report: P-1794892061-52a8e074-claude-agent-claude-fable-5

- **Plan Reference:**
  [`plans/P-1794892061-52a8e074-claude-agent-claude-fable-5.md`](P-1794892061-52a8e074-claude-agent-claude-fable-5.md)
- **Execution ID:** `E-1794892278-9d45d78f-claude-agent-claude-fable-5`
- **Execution Branch:**
  `I-1794891082-beadspil/P-1794892061-52a8e074-claude-agent-claude-fable-5/E-1794892278-9d45d78f-claude-agent-claude-fable-5/_`
- **Intent Branch:** `I-1794891082-beadspil/_`
- **Evaluating Agent:** `claude-agent/claude-fable-5`
- **Evaluation Timestamp:** `2026-11-17T08:47:04.000Z`
- **Evaluated Commit SHA:** `4afb5f00a80c11a952c6153f3c97180999e4dc2e`
- **Base SHA:** `8a20a12bf8b56480888355cda5c3e4e60e934adb`
- **Merge-Base SHA:** `8a20a12bf8b56480888355cda5c3e4e60e934adb`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.80`             | `1.00`            | `0.20`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `12.00`            | `0.86`            | `11.14`                               | Moderate        | Overestimated Risk         |
| **Impact**                                         | `80.00`            | `80.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `24.00`            | `20.40`           | `3.60`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `6.50`             | `6.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `39.65`            | `62.59`           | `22.94`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `39.65`            | `62.59`           | `22.94`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.80 - 1.00| = 0.20$
- **Entropy Error:** $|12.00 - 0.86| = 11.14$
- **Impact Error:** $|80.00 - 80.00| = 0.00$
- **Cost Error:** $|24.00 - 20.40| = 3.60$
- **Learning Value Error:** $|6.50 - 6.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.80 \times 80.0 + 0.5 \times 6.5 - 0.3 \times 12.00 - 24.00 = 39.65$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 80.0 + 0.5 \times 6.5 - 0.3 \times 0.86 - 20.40 = 62.59$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 80.0 + 0.5 \times 6.5 - 0.3 \times 0.86 - 20.40 = 62.59$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +22.94$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `2.7` (7 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794892061-52a8e074-claude-agent-claude-fable-5` executed with minimal error ($p\_success\_error = 0.20$,
$cost\_error = 3.60$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `87.5%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- `apps/sandbox-executor/build_all_images.sh`
