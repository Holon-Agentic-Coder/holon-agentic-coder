# Plan Calibration Report: P-1791553791-antigravity-agent-gemini-3.8-flash-medium

- **Plan Reference:**
  [`plans/P-1791553791-antigravity-agent-gemini-3.8-flash-medium.md`](P-1791553791-antigravity-agent-gemini-3.8-flash-medium.md)
- **Execution ID:** `E-1791554348-antigravity-agent-gemini-3.8-flash-medium`
- **Execution Branch:**
  `I-1791553778-add-gh-and-openssl-prerequisite-checks/P-1791553791-antigravity-agent-gemini-3.8-flash-medium/E-1791554348-antigravity-agent-gemini-3.8-flash-medium/_`
- **Intent Branch:** `I-1791553778-add-gh-and-openssl-prerequisite-checks/_`
- **Evaluating Agent:** `antigravity-agent/gemini-3.8-flash-medium`
- **Evaluation Timestamp:** `2026-10-10T20:00:23.000Z`
- **Evaluated Commit SHA:** `c9cf99010172e1ecb2927d6eb40835a892d391b9`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.99`             | `1.00`            | `0.01`                                | High (≤ 0.05)   | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `0.20`             | `3.04`            | `2.84`                                | Moderate        | Underestimated Risk        |
| **Impact**                | `75.00`            | `75.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `1.00`             | `0.85`            | `0.15`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**        | `1.50`             | `1.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `73.94`            | `73.99`           | `0.05`                                | High            | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.99 - 1.00| = 0.01$
- **Entropy Error:** $|0.20 - 3.04| = 2.84$
- **Impact Error:** $|75.00 - 75.00| = 0.00$
- **Cost Error:** $|1.00 - 0.85| = 0.15$
- **Learning Value Error:** $|1.50 - 1.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.99 \times 75.0 + 0.5 \times 1.5 - 0.3 \times 0.20 - 1.00 = 73.94$$
  $$EV_{\text{actual}} = 1.00 \times 75.0 + 0.5 \times 1.5 - 0.3 \times 3.04 - 0.85 = 73.99$$ $$\Delta EV = +0.05$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (37 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1791553791-antigravity-agent-gemini-3.8-flash-medium` executed with minimal error ($p\_success\_error = 0.01$,
$cost\_error = 0.15$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.

---

## 5. Addendum: the `actual` column is measured from the wrong basis

Written by the harness operator after generating this report with
`./holon calibrate <execution_branch> --no-commit --json`, and committed alongside it so the artifact and its correction
travel together. **Do not read section 1 as a measurement of this change.**

Calibration derives `actual.files_changed`, `insertions` and `deletions` from the current head against the default
branch tip. This branch had already forward-merged `main` (`609bb0b`, 35 files, `+901/-572`) to resolve a ledger
conflict before the review loop finished, so that merge is counted as work done by this change:

| Basis                                         | Files | Insertions | Deletions |
| :-------------------------------------------- | :---- | :--------- | :-------- |
| Recorded above (post-merge head)              | `37`  | `+1677`    | `-568`    |
| This change only (merge-base → pre-merge tip) | `7`   | `+734`     | `-4`      |

Reproducible: `git diff --shortstat "$(git merge-base origin/main 9e11928)" 9e11928` gives
`7 files changed, 734 insertions(+), 4 deletions(-)`.

Because `SSA` is defined as `min(10.0, files*0.2 + churn*0.005)` and entropy as `0.30*SSA + 0.10*NOV`
(`apps/sandbox-executor/src/sandbox_executor/calibration.py:778`), the inflation propagates rather than staying in one
cell:

| Quantity             | As recorded | On the honest basis |
| :------------------- | :---------- | :------------------ |
| `SSA` observed       | `10.0`      | `5.09`              |
| Entropy observed     | `3.04`      | `1.57`              |
| Entropy error        | `2.84`      | `1.37`              |
| $EV_{\text{actual}}$ | `73.99`     | `74.43`             |
| $\Delta EV$          | `+0.05`     | `+0.49`             |

Two corrections follow, one of them mine.

1. **Section 3's `SSA 10.0` is an artifact of the clamp as much as of the work.** The merge pushed the raw value to
   `18.6`, so the reported figure is the ceiling, not a reading. On the correct basis `SSA` is `5.09`.
2. **The qualitative finding survives, at less than half the magnitude.** The plan predicted `SSA 0.6` for a change that
   went through four review iterations and nineteen pushes; against `5.09` that is still an order of magnitude of
   understated spec ambiguity, and it is the only number in this report that says anything about the planning. The
   headline $\Delta EV$ of `+0.05` -- the metric the flow reports as calibration quality -- conceals it entirely, and
   the honest recomputation moves it to `+0.49` without changing the sign of that judgement.

Fixing the measurement basis is tracked as `holon-agentic-coder-ref-metadata-0083`. Until that lands, every `actual`
block produced after a main-merge is inflated by the size of the merge, and this addendum is the only thing
distinguishing a real figure from a coincidental one.
