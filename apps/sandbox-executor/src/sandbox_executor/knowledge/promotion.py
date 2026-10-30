"""Asymmetric promotion engine: deterministic failure veto ladder and
statistical Beta-Wilson success advisories (Bean 0052 Goal 4).
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.knowledge.index import DEFAULT_PROJECT_DB_PATH, ActionIndexDatabase

logger = logging.getLogger(__name__)

# Standard z-score for 95% two-sided normal confidence interval
WILSON_Z_95 = 1.95996398454


def compute_context_fingerprint(
    target: str = "",
    tool: str = "",
    command_prefix: str = "",
    error_type: str = "",
    file_extensions: list[str] | None = None,
) -> str:
    """Compute deterministic SHA256 context fingerprint from normalized execution attributes."""
    norm_target = target.strip().lower()
    norm_tool = tool.strip().lower()
    norm_cmd = command_prefix.strip().lower()
    norm_err = error_type.strip().lower()
    clean_exts = sorted({ext.strip().lower().lstrip(".") for ext in (file_extensions or []) if ext.strip()})
    ext_str = ",".join(clean_exts)

    raw = f"{norm_target}|{norm_tool}|{norm_cmd}|{norm_err}|{ext_str}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass
class ContextFingerprint:
    """Helper wrapper holding components of a context fingerprint."""

    target: str = ""
    tool: str = ""
    command_prefix: str = ""
    error_type: str = ""
    file_extensions: list[str] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        """Calculate fingerprint hash."""
        return compute_context_fingerprint(
            self.target,
            self.tool,
            self.command_prefix,
            self.error_type,
            self.file_extensions,
        )


def compute_beta_posterior(
    successes: int,
    failures: int,
    prior_alpha: float = 1.0,
    prior_beta: float = 1.0,
) -> tuple[float, float, float]:
    """Calculate Beta(alpha, beta) posterior mean and parameter updates from trials.

    Returns:
        (alpha_post, beta_post, posterior_mean)
    """
    alpha_post = max(0.1, prior_alpha + successes)
    beta_post = max(0.1, prior_beta + failures)
    posterior_mean = alpha_post / (alpha_post + beta_post)
    return alpha_post, beta_post, posterior_mean


def compute_wilson_score_interval(
    successes: int,
    failures: int,
    z: float = WILSON_Z_95,
) -> tuple[float, float]:
    """Calculate the Wilson score 95% confidence interval for a binomial proportion.

    Returns:
        (lower_bound, upper_bound) strictly clamped within [0.0, 1.0].
    """
    n = successes + failures
    if n <= 0:
        return 0.0, 1.0

    p = successes / n
    denominator = 1.0 + (z**2) / n
    center_adjusted = p + (z**2) / (2.0 * n)
    spread = z * math.sqrt((p * (1.0 - p) + (z**2) / (4.0 * n)) / n)

    lower = max(0.0, min(1.0, (center_adjusted - spread) / denominator))
    upper = max(0.0, min(1.0, (center_adjusted + spread) / denominator))
    return lower, upper


@dataclass
class PromotionEvaluation:
    """Structured evaluation of an action's success evidence or failure veto status."""

    signature_hash: str
    context_fingerprint: str
    success_count: int
    failure_count: int
    total_trials: int
    failure_ladder_state: str  # none, observed, repeated, contested, proven-fail
    success_ladder_state: str  # none, observed, repeated, likely-success, proven-success
    posterior_mean: float
    wilson_lower: float
    wilson_upper: float
    has_active_veto: bool
    is_shadow_veto: bool
    veto_decay_weight: float
    advisory_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert evaluation to dictionary."""
        return {
            "signature_hash": self.signature_hash,
            "context_fingerprint": self.context_fingerprint,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "total_trials": self.total_trials,
            "failure_ladder_state": self.failure_ladder_state,
            "success_ladder_state": self.success_ladder_state,
            "posterior_mean": round(self.posterior_mean, 4),
            "wilson_lower": round(self.wilson_lower, 4),
            "wilson_upper": round(self.wilson_upper, 4),
            "has_active_veto": self.has_active_veto,
            "is_shadow_veto": self.is_shadow_veto,
            "veto_decay_weight": round(self.veto_decay_weight, 4),
            "advisory_text": self.advisory_text,
        }


def format_non_imperative_advisory(
    action: str,
    successes: int,
    total_trials: int,
    wilson_lower: float,
    ladder_state: str,
) -> str:
    """Format non-imperative advisory guidance for success patterns.

    INVARIANT: Never emit imperative text ('do this', 'must', 'veto') for successes.
    """
    pct = (successes / total_trials * 100.0) if total_trials > 0 else 0.0
    return (
        f"Advisory ({ladder_state}): Historical executions matching this context pattern "
        f"succeeded in {successes}/{total_trials} ({pct:.1f}%) trials "
        f"(Wilson 95% lower bound: {wilson_lower:.2f}). "
        f"Consider evaluating proven approach: '{action}'."
    )


def evaluate_action_promotion(  # noqa: C901, PLR0912, PLR0915
    signature_hash: str,
    index_db: ActionIndexDatabase | None = None,
    enforce_veto: bool = False,
) -> PromotionEvaluation:
    """Evaluate asymmetric promotion: deterministic failure veto vs statistical success evidence."""
    db = index_db or ActionIndexDatabase(DEFAULT_PROJECT_DB_PATH)
    conn = db.get_connection()

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT signature_hash, context_fingerprint, action_origin,
                   success_count, failure_count, last_seen_ts
            FROM action_signatures
            WHERE signature_hash = ?;
            """,
            (signature_hash,),
        )
        row = cur.fetchone()

        if not row:
            return PromotionEvaluation(
                signature_hash=signature_hash,
                context_fingerprint="",
                success_count=0,
                failure_count=0,
                total_trials=0,
                failure_ladder_state="none",
                success_ladder_state="none",
                posterior_mean=0.5,
                wilson_lower=0.0,
                wilson_upper=1.0,
                has_active_veto=False,
                is_shadow_veto=True,
                veto_decay_weight=1.0,
                advisory_text=None,
            )

        context_fp = row["context_fingerprint"]
        successes = row["success_count"]
        failures = row["failure_count"]
        total_trials = successes + failures

        _, _, posterior_mean = compute_beta_posterior(successes, failures)
        wilson_lower, wilson_upper = compute_wilson_score_interval(successes, failures)

        # 1. Failure Veto Promotion Ladder
        if failures == 0:
            failure_ladder = "none"
        elif failures == 1 and successes == 0:
            failure_ladder = "observed"
        elif failures >= 2 and successes == 0:
            failure_ladder = "repeated" if failures == 2 else "proven-fail"
        elif successes > 0 and failures > 0:
            failure_ladder = "contested"
        else:
            failure_ladder = "observed"

        # Check existing active veto in table
        cur.execute(
            """
            SELECT context_fingerprint, reproduction_count, state, decay_weight, is_shadow, updated_at
            FROM active_vetoes
            WHERE context_fingerprint = ?;
            """,
            (context_fp,),
        )
        veto_row = cur.fetchone()

        has_active_veto = False
        is_shadow = not enforce_veto
        veto_decay = 1.0

        # Automatic lifting: contrary success lifts active veto
        if successes > 0 and veto_row:
            with conn:
                conn.execute(
                    """
                    UPDATE active_vetoes
                    SET state = 'lifted', updated_at = ?
                    WHERE context_fingerprint = ?;
                    """,
                    (datetime.now(UTC).isoformat(), context_fp),
                )
            has_active_veto = False
        elif failure_ladder in ("repeated", "proven-fail"):
            has_active_veto = True
            is_shadow = not enforce_veto

            # Compute decay weight over time (exponential decay with 7-day half life)
            if veto_row and veto_row["updated_at"]:
                try:
                    last_up = datetime.fromisoformat(veto_row["updated_at"])
                    if last_up.tzinfo is None:
                        last_up = last_up.replace(tzinfo=UTC)
                    age_hours = (datetime.now(UTC) - last_up).total_seconds() / 3600.0
                    veto_decay = max(0.01, math.exp(-0.00412 * age_hours))
                except Exception:
                    veto_decay = 1.0

            with conn:
                conn.execute(
                    """
                    INSERT INTO active_vetoes (
                        context_fingerprint, signature_hash, reproduction_count,
                        state, decay_weight, is_shadow, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(context_fingerprint) DO UPDATE SET
                        reproduction_count = excluded.reproduction_count,
                        state = excluded.state,
                        decay_weight = excluded.decay_weight,
                        is_shadow = excluded.is_shadow,
                        updated_at = excluded.updated_at;
                    """,
                    (
                        context_fp,
                        signature_hash,
                        failures,
                        failure_ladder,
                        veto_decay,
                        1 if is_shadow else 0,
                        datetime.now(UTC).isoformat(),
                        datetime.now(UTC).isoformat(),
                    ),
                )

        # 2. Success Evidence Promotion Ladder
        if successes == 0:
            success_ladder = "none"
        elif successes == 1:
            success_ladder = "observed"
        elif successes >= 10 and wilson_lower >= 0.85:
            success_ladder = "proven-success"
        elif successes >= 5 and wilson_lower >= 0.70:
            success_ladder = "likely-success"
        elif successes >= 2:
            success_ladder = "repeated"
        else:
            success_ladder = "observed"

        # Generate non-imperative advisory text if success evidence warrants it
        advisory_text = None
        if success_ladder in ("repeated", "likely-success", "proven-success"):
            # Lookup sample action description
            cur.execute(
                """
                SELECT action FROM action_records
                WHERE context_fingerprint = ? AND (verdict = 'success' OR outcome = 'success')
                LIMIT 1;
                """,
                (context_fp,),
            )
            sample_act_row = cur.fetchone()
            sample_act = sample_act_row[0] if sample_act_row else "proven approach"
            advisory_text = format_non_imperative_advisory(
                sample_act, successes, total_trials, wilson_lower, success_ladder
            )

        return PromotionEvaluation(
            signature_hash=signature_hash,
            context_fingerprint=context_fp,
            success_count=successes,
            failure_count=failures,
            total_trials=total_trials,
            failure_ladder_state=failure_ladder,
            success_ladder_state=success_ladder,
            posterior_mean=posterior_mean,
            wilson_lower=wilson_lower,
            wilson_upper=wilson_upper,
            has_active_veto=has_active_veto,
            is_shadow_veto=is_shadow,
            veto_decay_weight=veto_decay,
            advisory_text=advisory_text,
        )
    finally:
        conn.close()
