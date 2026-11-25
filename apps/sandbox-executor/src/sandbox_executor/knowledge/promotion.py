from __future__ import annotations

import hashlib
import logging
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.knowledge.index import DEFAULT_PROJECT_DB_PATH, ActionIndexDatabase
from sandbox_executor.knowledge.promotion_rules import (
    ADVISORY_STATES,
    FAILURE_LADDER_DEFAULT,
    FAILURE_LADDER_RULES,
    SUCCESS_LADDER_DEFAULT,
    SUCCESS_LADDER_RULES,
    VETO_STATES,
    LadderEvidence,
    classify,
    veto_decay_weight,
)

logger = logging.getLogger(__name__)

WILSON_Z_95 = 1.95996398454
_FINGERPRINT_HEX_LENGTH = 16
_MIN_BETA_PARAM = 0.1
_NO_DECAY_WEIGHT = 1.0


def compute_context_fingerprint(
    target: str = "",
    tool: str = "",
    command_prefix: str = "",
    error_type: str = "",
    file_extensions: list[str] | None = None,
) -> str:
    norm_target = target.strip().lower()
    norm_tool = tool.strip().lower()
    norm_cmd = command_prefix.strip().lower()
    norm_err = error_type.strip().lower()
    clean_exts = sorted({ext.strip().lower().lstrip(".") for ext in (file_extensions or []) if ext.strip()})
    ext_str = ",".join(clean_exts)

    raw = f"{norm_target}|{norm_tool}|{norm_cmd}|{norm_err}|{ext_str}".encode()
    return hashlib.sha256(raw).hexdigest()[:_FINGERPRINT_HEX_LENGTH]


@dataclass
class ContextFingerprint:
    target: str = ""
    tool: str = ""
    command_prefix: str = ""
    error_type: str = ""
    file_extensions: list[str] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
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
    alpha_post = max(_MIN_BETA_PARAM, prior_alpha + successes)
    beta_post = max(_MIN_BETA_PARAM, prior_beta + failures)
    posterior_mean = alpha_post / (alpha_post + beta_post)
    return alpha_post, beta_post, posterior_mean


def compute_wilson_score_interval(
    successes: int,
    failures: int,
    z: float = WILSON_Z_95,
) -> tuple[float, float]:
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
    signature_hash: str
    context_fingerprint: str
    success_count: int
    failure_count: int
    total_trials: int
    failure_ladder_state: str
    success_ladder_state: str
    posterior_mean: float
    wilson_lower: float
    wilson_upper: float
    has_active_veto: bool
    is_shadow_veto: bool
    veto_decay_weight: float
    advisory_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
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
    pct = (successes / total_trials * 100.0) if total_trials > 0 else 0.0
    return (
        f"Advisory ({ladder_state}): Historical executions matching this context pattern "
        f"succeeded in {successes}/{total_trials} ({pct:.1f}%) trials "
        f"(Wilson 95% lower bound: {wilson_lower:.2f}). "
        f"Consider evaluating proven approach: '{action}'."
    )


def _empty_evaluation(signature_hash: str) -> PromotionEvaluation:
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
        veto_decay_weight=_NO_DECAY_WEIGHT,
        advisory_text=None,
    )


def _fetch_signature(cur: sqlite3.Cursor, signature_hash: str) -> sqlite3.Row | None:
    cur.execute(
        """
        SELECT signature_hash, context_fingerprint, action_origin,
               success_count, failure_count, last_seen_ts
        FROM action_signatures
        WHERE signature_hash = ?;
        """,
        (signature_hash,),
    )
    return cur.fetchone()


def _fetch_active_veto(cur: sqlite3.Cursor, context_fp: str) -> sqlite3.Row | None:
    cur.execute(
        """
        SELECT context_fingerprint, reproduction_count, state, decay_weight, is_shadow, updated_at
        FROM active_vetoes
        WHERE context_fingerprint = ?;
        """,
        (context_fp,),
    )
    return cur.fetchone()


@dataclass(frozen=True)
class _VetoSubject:
    signature_hash: str
    context_fp: str
    evidence: LadderEvidence
    failure_ladder: str
    enforce_veto: bool


def _lift_veto(conn: sqlite3.Connection, context_fp: str) -> None:
    with conn:
        conn.execute(
            """
            UPDATE active_vetoes
            SET state = 'lifted', updated_at = ?
            WHERE context_fingerprint = ?;
            """,
            (datetime.now(UTC).isoformat(), context_fp),
        )


def _upsert_veto(conn: sqlite3.Connection, subject: _VetoSubject, decay: float, is_shadow: bool) -> None:
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
                subject.context_fp,
                subject.signature_hash,
                subject.evidence.failures,
                subject.failure_ladder,
                decay,
                1 if is_shadow else 0,
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat(),
            ),
        )


def _apply_veto_policy(
    conn: sqlite3.Connection, cur: sqlite3.Cursor, subject: _VetoSubject
) -> tuple[bool, bool, float]:
    is_shadow = not subject.enforce_veto
    veto_row = _fetch_active_veto(cur, subject.context_fp)
    has_contrary_success = subject.evidence.successes > 0
    if has_contrary_success and veto_row:
        _lift_veto(conn, subject.context_fp)
        return False, is_shadow, _NO_DECAY_WEIGHT
    if subject.failure_ladder not in VETO_STATES:
        return False, is_shadow, _NO_DECAY_WEIGHT
    decay = veto_decay_weight(veto_row["updated_at"]) if veto_row else _NO_DECAY_WEIGHT
    _upsert_veto(conn, subject, decay, is_shadow)
    return True, is_shadow, decay


def _success_advisory(
    cur: sqlite3.Cursor, context_fp: str, evidence: LadderEvidence, success_ladder: str
) -> str | None:
    if success_ladder not in ADVISORY_STATES:
        return None
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
    total_trials = evidence.successes + evidence.failures
    return format_non_imperative_advisory(
        sample_act, evidence.successes, total_trials, evidence.wilson_lower, success_ladder
    )


def evaluate_action_promotion(
    signature_hash: str,
    index_db: ActionIndexDatabase | None = None,
    enforce_veto: bool = False,
) -> PromotionEvaluation:
    db = index_db or ActionIndexDatabase(DEFAULT_PROJECT_DB_PATH)
    conn = db.get_connection()

    try:
        cur = conn.cursor()
        row = _fetch_signature(cur, signature_hash)
        if not row:
            return _empty_evaluation(signature_hash)

        context_fp = row["context_fingerprint"]
        successes = row["success_count"]
        failures = row["failure_count"]

        _, _, posterior_mean = compute_beta_posterior(successes, failures)
        wilson_lower, wilson_upper = compute_wilson_score_interval(successes, failures)
        evidence = LadderEvidence(successes=successes, failures=failures, wilson_lower=wilson_lower)

        failure_ladder = classify(FAILURE_LADDER_RULES, evidence, FAILURE_LADDER_DEFAULT)
        subject = _VetoSubject(signature_hash, context_fp, evidence, failure_ladder, enforce_veto)
        has_active_veto, is_shadow, veto_decay = _apply_veto_policy(conn, cur, subject)

        success_ladder = classify(SUCCESS_LADDER_RULES, evidence, SUCCESS_LADDER_DEFAULT)
        advisory_text = _success_advisory(cur, context_fp, evidence, success_ladder)

        return PromotionEvaluation(
            signature_hash=signature_hash,
            context_fingerprint=context_fp,
            success_count=successes,
            failure_count=failures,
            total_trials=successes + failures,
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
