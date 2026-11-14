# Plan Calibration Report: P-1794663302-17aba811-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1794663302-17aba811-claude-agent-claude-opus-5-5.md`](P-1794663302-17aba811-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1794663560-da6ecf9f-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1794663286-hygiene/P-1794663302-17aba811-claude-agent-claude-opus-5-5/E-1794663560-da6ecf9f-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1794663286-hygiene/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-11-14T14:00:41.000Z`
- **Evaluated Commit SHA:** `cd2355c80f9f9e1cb208ae959bdd1da87a59f77d`
- **Base SHA:** `d4726c6e4d7bc530e19e152316ddd7e1e4a19bdc`
- **Merge-Base SHA:** `d4726c6e4d7bc530e19e152316ddd7e1e4a19bdc`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.82`             | `1.00`            | `0.18`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `10.00`            | `3.04`            | `6.96`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `70.00`            | `70.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `14.00`            | `11.90`           | `2.10`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.00`             | `4.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `42.40`            | `59.19`           | `16.79`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `42.40`            | `59.19`           | `16.79`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.82 - 1.00| = 0.18$
- **Entropy Error:** $|10.00 - 3.04| = 6.96$
- **Impact Error:** $|70.00 - 70.00| = 0.00$
- **Cost Error:** $|14.00 - 11.90| = 2.10$
- **Learning Value Error:** $|4.00 - 4.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.82 \times 70.0 + 0.5 \times 4.0 - 0.3 \times 10.00 - 14.00 = 42.40$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 70.0 + 0.5 \times 4.0 - 0.3 \times 3.04 - 11.90 = 59.19$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 70.0 + 0.5 \times 4.0 - 0.3 \times 3.04 - 11.90 = 59.19$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +16.79$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (42 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794663302-17aba811-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.18$,
$cost\_error = 2.10$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
