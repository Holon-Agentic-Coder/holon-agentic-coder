# Plan Calibration Report: P-1795524814-ANA-G4N

- **Plan Reference:** [`plans/I-1795524794-launcher-proxy-self-doc/P-1795524814-ANA-G4N.md`](P-1795524814-ANA-G4N.md)
- **Execution ID:** `E-1795525056-ANA-G4N`
- **Execution Branch:** `I-1795524794-launcher-proxy-self-doc/P-1795524814-ANA-G4N/E-1795525056-ANA-G4N/_`
- **Intent Branch:** `I-1795524794-launcher-proxy-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T14:53:47.000Z`
- **Evaluated Commit SHA:** `c4195cb65b230930efd3b1f433849d8670410b77`
- **Base SHA:** `a8db8410188a32d886b2602a7097245423d66ce5`
- **Merge-Base SHA:** `a8db8410188a32d886b2602a7097245423d66ce5`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.88`             | `1.00`            | `0.12`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.65`             | `2.85`            | `0.80`                                | High (≤ 1.0)    | Overestimated Risk         |
| **Impact**                                         | `70.00`            | `70.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `12.00`            | `10.20`           | `1.80`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `3.50`             | `3.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `50.26`            | `60.70`           | `10.45`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `50.26`            | `60.70`           | `10.45`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.88 - 1.00| = 0.12$
- **Entropy Error:** $|3.65 - 2.85| = 0.80$
- **Impact Error:** $|70.00 - 70.00| = 0.00$
- **Cost Error:** $|12.00 - 10.20| = 1.80$
- **Learning Value Error:** $|3.50 - 3.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.88 \times 70.0 + 0.5 \times 3.5 - 0.3 \times 3.65 - 12.00 = 50.26$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 70.0 + 0.5 \times 3.5 - 0.3 \times 2.85 - 10.20 = 60.70$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 70.0 + 0.5 \times 3.5 - 0.3 \times 2.85 - 10.20 = 60.70$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +10.45$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `9.4` (21 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795524814-ANA-G4N` executed with minimal error ($p\_success\_error = 0.12$, $cost\_error = 1.80$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `47.6%`

Changed files outside the touch set:

- `apps/sandbox-executor/src/sandbox_executor/cli/__init__.py`
- `apps/sandbox-executor/src/sandbox_executor/config_env_report.py`
- `apps/sandbox-executor/tests/test_approval_fingerprint.py`
- `apps/sandbox-executor/tests/test_calibration_diff_property.py`
- `apps/sandbox-executor/tests/test_config_env.py`
- `apps/sandbox-executor/tests/test_credential_hermeticity.py`
- `apps/sandbox-executor/tests/test_executor_agent_output_redaction.py`
- `apps/sandbox-executor/tests/test_turn_supervisor.py`
- `apps/sandbox-executor/tests/test_turns.py`
- `apps/sandbox-executor/tests/test_url_credential_redaction.py`
- `scripts/pre_commit_check.py`

Declared entries the diff did not touch:

- none
