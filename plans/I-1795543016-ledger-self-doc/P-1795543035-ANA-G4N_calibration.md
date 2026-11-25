# Plan Calibration Report: P-1795543035-ANA-G4N

- **Plan Reference:** [`plans/I-1795543016-ledger-self-doc/P-1795543035-ANA-G4N.md`](P-1795543035-ANA-G4N.md)
- **Execution ID:** `E-1795543264-ANA-G4N`
- **Execution Branch:** `I-1795543016-ledger-self-doc/P-1795543035-ANA-G4N/E-1795543264-ANA-G4N/_`
- **Intent Branch:** `I-1795543016-ledger-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T19:10:46.000Z`
- **Evaluated Commit SHA:** `77a10476f311b42161193409e16c3a226298d606`
- **Base SHA:** `4fd386f34273506ad6fdf1cb3e4b80555c8ba196`
- **Merge-Base SHA:** `4fd386f34273506ad6fdf1cb3e4b80555c8ba196`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.88`             | `1.00`            | `0.12`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `5.20`             | `2.53`            | `2.67`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `70.00`            | `70.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `10.00`            | `8.50`            | `1.50`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `3.50`             | `3.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `51.79`            | `62.49`           | `10.70`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `51.79`            | `62.49`           | `10.70`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.88 - 1.00| = 0.12$
- **Entropy Error:** $|5.20 - 2.53| = 2.67$
- **Impact Error:** $|70.00 - 70.00| = 0.00$
- **Cost Error:** $|10.00 - 8.50| = 1.50$
- **Learning Value Error:** $|3.50 - 3.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.88 \times 70.0 + 0.5 \times 3.5 - 0.3 \times 5.20 - 10.00 = 51.79$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 70.0 + 0.5 \times 3.5 - 0.3 \times 2.53 - 8.50 = 62.49$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 70.0 + 0.5 \times 3.5 - 0.3 \times 2.53 - 8.50 = 62.49$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +10.70$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `8.3` (16 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795543035-ANA-G4N` executed with minimal error ($p\_success\_error = 0.12$, $cost\_error = 1.50$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `50.0%`

Changed files outside the touch set:

- `apps/sandbox-executor/src/sandbox_executor/cli/__init__.py`
- `apps/sandbox-executor/src/sandbox_executor/config_env_report.py`
- `apps/sandbox-executor/src/sandbox_executor/redaction.py`
- `apps/sandbox-executor/tests/test_approval_fingerprint.py`
- `apps/sandbox-executor/tests/test_calibration_diff_property.py`
- `apps/sandbox-executor/tests/test_repository_hygiene.py`
- `apps/sandbox-executor/tests/test_url_credential_redaction.py`
- `scripts/pre_commit_check.py`

Declared entries the diff did not touch:

- none
