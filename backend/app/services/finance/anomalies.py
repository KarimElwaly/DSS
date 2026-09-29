"""Financial anomaly detection engine (Module F).

Detects margin compression, CAC spikes, runway warnings, and working capital
drags using robust z-scores (Median Absolute Deviation) and sequential delta checks.
"""

from __future__ import annotations

import uuid
from typing import Any

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import FinancialFinding, FinancialMetric, FinancialPeriod
from app.models.enums import AlertSeverity

logger = get_logger(__name__)


def _robust_zscore(values: list[float], current: float) -> float:
    """Compute modified z-score using Median Absolute Deviation (MAD)."""
    if len(values) < 3:
        return 0.0

    arr = np.array(values, dtype=float)
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))

    if mad < 1e-6:
        # Standard deviation fallback if MAD is 0
        std = float(np.std(arr))
        return (current - med) / std if std > 1e-6 else 0.0

    return 0.6745 * (current - med) / mad


def detect_financial_anomalies(
    session: Session, organization_id: uuid.UUID
) -> list[FinancialFinding]:
    """Analyze historical periods and raise findings on anomalies in the latest periods."""
    periods = list(
        session.scalars(
            select(FinancialPeriod)
            .where(FinancialPeriod.organization_id == organization_id)
            .order_by(FinancialPeriod.period_start)
        )
    )
    if len(periods) < 2:
        return []

    # Map period_id -> { metric_key -> value }
    all_metrics = list(
        session.scalars(
            select(FinancialMetric).where(
                FinancialMetric.period_id.in_([p.id for p in periods])
            )
        )
    )
    metric_map: dict[uuid.UUID, dict[str, float]] = {}
    for m in all_metrics:
        metric_map.setdefault(m.period_id, {})[m.key] = m.value

    # Delete previous findings for this organization to refresh
    session.query(FinancialFinding).filter(
        FinancialFinding.organization_id == organization_id
    ).delete()
    session.flush()

    findings: list[FinancialFinding] = []

    # Focus analysis on the last 2 periods against historical trajectory
    eval_periods = periods[-2:]
    for idx, period in enumerate(eval_periods):
        p_metrics = metric_map.get(period.id, {})
        # Find preceding periods up to current
        prev_periods = [p for p in periods if p.period_start < period.period_start]
        if not prev_periods:
            continue

        last_p = prev_periods[-1]
        last_metrics = metric_map.get(last_p.id, {})

        # 1. Gross Margin Compression Check
        gm_cur = p_metrics.get("gross_margin")
        gm_hist = [metric_map[p.id]["gross_margin"] for p in prev_periods if "gross_margin" in metric_map.get(p.id, {})]

        if gm_cur is not None and gm_hist:
            gm_prev = last_metrics.get("gross_margin", gm_hist[-1])
            delta_gm_pts = (gm_cur - gm_prev) * 100.0  # percentage points
            z = _robust_zscore(gm_hist, gm_cur)

            if delta_gm_pts <= -2.5 or z <= -1.8:
                sev = AlertSeverity.CRITICAL if delta_gm_pts <= -4.0 else AlertSeverity.WARNING
                findings.append(
                    FinancialFinding(
                        organization_id=organization_id,
                        period_id=period.id,
                        metric_key="gross_margin",
                        detector="robust_zscore",
                        severity=sev,
                        title=f"Gross Margin Compression in {period.label}",
                        detail=(
                            f"Gross margin dropped by {abs(delta_gm_pts):.1f} percentage points "
                            f"(from {gm_prev*100:.1f}% to {gm_cur*100:.1f}%, z={z:.2f}). "
                            "Indicates rising component landed costs or aggressive discounting."
                        ),
                        current_value=round(gm_cur, 4),
                        expected_value=round(gm_prev, 4),
                        delta_pct=round((gm_cur - gm_prev) / gm_prev, 4) if gm_prev > 0 else None,
                        z_score=round(z, 2),
                        context={"previous_period": last_p.label, "pts_drop": round(delta_gm_pts, 2)},
                    )
                )

        # 2. CAC Spike Check
        cac_cur = p_metrics.get("cac")
        cac_hist = [metric_map[p.id]["cac"] for p in prev_periods if "cac" in metric_map.get(p.id, {})]

        if cac_cur is not None and cac_hist:
            cac_prev = last_metrics.get("cac", cac_hist[-1])
            delta_cac_pct = (cac_cur - cac_prev) / cac_prev if cac_prev > 0 else 0.0
            z_cac = _robust_zscore(cac_hist, cac_cur)

            if delta_cac_pct >= 0.18 or z_cac >= 1.8:
                findings.append(
                    FinancialFinding(
                        organization_id=organization_id,
                        period_id=period.id,
                        metric_key="cac",
                        detector="robust_zscore",
                        severity=AlertSeverity.WARNING,
                        title=f"CAC Expansion Spike in {period.label}",
                        detail=(
                            f"Customer Acquisition Cost surged +{delta_cac_pct*100:.1f}% "
                            f"from ${cac_prev:.2f} to ${cac_cur:.2f} (z={z_cac:.2f}). "
                            "Marketing spend efficiency is deteriorating."
                        ),
                        current_value=round(cac_cur, 2),
                        expected_value=round(cac_prev, 2),
                        delta_pct=round(delta_cac_pct, 4),
                        z_score=round(z_cac, 2),
                        context={"previous_period": last_p.label},
                    )
                )

        # 3. Cash Runway Deterioration
        runway_cur = p_metrics.get("runway_months")
        if runway_cur is not None and runway_cur < 15.0:
            sev = AlertSeverity.CRITICAL if runway_cur < 6.0 else AlertSeverity.WARNING
            findings.append(
                FinancialFinding(
                    organization_id=organization_id,
                    period_id=period.id,
                    metric_key="runway_months",
                    detector="threshold",
                    severity=sev,
                    title=f"Cash Runway Tightening ({runway_cur:.1f} Months) in {period.label}",
                    detail=(
                        f"At current burn rates, available cash covers {runway_cur:.1f} months of operations. "
                        "Prioritize margin protection and working capital release."
                    ),
                    current_value=round(runway_cur, 1),
                    expected_value=18.0,
                    delta_pct=round((runway_cur - 18.0) / 18.0, 4),
                    z_score=None,
                    context={"threshold_months": 15.0},
                )
            )

        # 4. Working Capital & Cash Conversion Cycle (CCC) Expansion
        ccc_cur = p_metrics.get("ccc")
        ccc_prev = last_metrics.get("ccc")
        if ccc_cur is not None and ccc_prev is not None and (ccc_cur - ccc_prev) >= 12.0:
            delta_days = ccc_cur - ccc_prev
            findings.append(
                FinancialFinding(
                    organization_id=organization_id,
                    period_id=period.id,
                    metric_key="ccc",
                    detector="delta_threshold",
                    severity=AlertSeverity.WARNING,
                    title=f"Working Capital Drag: CCC +{delta_days:.0f} Days in {period.label}",
                    detail=(
                        f"Cash Conversion Cycle lengthened from {ccc_prev:.0f} to {ccc_cur:.0f} days (+{delta_days:.0f}d). "
                        "Working capital is being trapped in inventory accumulation or slower receivables."
                    ),
                    current_value=round(ccc_cur, 1),
                    expected_value=round(ccc_prev, 1),
                    delta_pct=round((ccc_cur - ccc_prev) / ccc_prev, 4) if ccc_prev > 0 else None,
                    z_score=None,
                    context={"previous_days": round(ccc_prev, 1), "days_added": round(delta_days, 1)},
                )
            )

    for f in findings:
        session.add(f)

    session.commit()
    logger.info("Detected %d financial findings for org %s", len(findings), organization_id)
    return findings
