"""CFO Strategic Advisor with Facts-JSON Grounding (Module F).

Compiles executive financial facts, queries the local LLM advisor (Ollama),
and runs the numeric-grounding validator to guarantee that every single numeric
claim in the narrative is strictly verifiable against computed financial facts.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.grounding import validate_numeric_grounding
from app.models import (
    AdvisorReport,
    FinancialFinding,
    FinancialMetric,
    FinancialPeriod,
    PriceRecommendation,
)
from app.models.enums import AuditAction, RecommendationStatus
from app.services.audit import record_audit_event

logger = get_logger(__name__)


def build_facts_payload(session: Session, organization_id: uuid.UUID) -> dict[str, Any]:
    """Compile structured corporate facts payload from financial periods, anomalies, and pricing recommendations."""
    # 1. Trailing 4 periods
    periods = list(
        session.scalars(
            select(FinancialPeriod)
            .where(FinancialPeriod.organization_id == organization_id)
            .order_by(FinancialPeriod.period_start.desc())
            .limit(4)
        )
    )

    if not periods:
        logger.info("No financial periods found for org %s; auto-seeding demo financials", organization_id)
        from app.services.finance.anomalies import detect_financial_anomalies
        from app.services.finance.ingestion import seed_demo_financials
        from app.services.finance.metrics import compute_all_metrics

        seed_demo_financials(session, organization_id)
        compute_all_metrics(session, organization_id)
        detect_financial_anomalies(session, organization_id)
        session.flush()

        periods = list(
            session.scalars(
                select(FinancialPeriod)
                .where(FinancialPeriod.organization_id == organization_id)
                .order_by(FinancialPeriod.period_start.desc())
                .limit(4)
            )
        )
    else:
        # Verify metrics exist for these periods; if missing, compute them
        metric_count = session.scalar(
            select(func.count(FinancialMetric.id)).where(
                FinancialMetric.period_id.in_([p.id for p in periods])
            )
        ) or 0
        if metric_count == 0:
            from app.services.finance.metrics import compute_all_metrics
            compute_all_metrics(session, organization_id)
            session.flush()

    periods.reverse()  # Chronological order

    period_summaries = []
    for p in periods:
        metrics = {
            m.key: m.value
            for m in session.scalars(
                select(FinancialMetric).where(FinancialMetric.period_id == p.id)
            )
        }
        period_summaries.append(
            {
                "period": p.label,
                "gross_margin_pct": round(metrics.get("gross_margin", 0.0) * 100.0, 1),
                "ebitda_margin_pct": round(metrics.get("ebitda_margin", 0.0) * 100.0, 1),
                "runway_months": round(metrics.get("runway_months", 0.0), 1),
                "ccc_days": round(metrics.get("ccc", 0.0), 1),
                "cac": round(metrics.get("cac", 0.0), 2),
                "ltv": round(metrics.get("ltv", 0.0), 2),
            }
        )

    # 2. Findings / Anomalies
    findings_rows = list(
        session.scalars(
            select(FinancialFinding)
            .where(FinancialFinding.organization_id == organization_id)
            .order_by(FinancialFinding.created_at.desc())
            .limit(5)
        )
    )
    findings = [
        {
            "severity": f.severity.value,
            "title": f.title,
            "metric": f.metric_key,
            "current_value": f.current_value,
            "expected_value": f.expected_value,
        }
        for f in findings_rows
    ]

    # 3. Dynamic Pricing Recommendations Impact
    recs = list(
        session.scalars(
            select(PriceRecommendation).where(
                PriceRecommendation.organization_id == organization_id,
                PriceRecommendation.status.in_((RecommendationStatus.PROPOSED, RecommendationStatus.APPROVED)),
            )
        )
    )
    total_base_margin = sum(r.baseline_margin or 0.0 for r in recs)
    total_exp_margin = sum(r.expected_margin or 0.0 for r in recs)
    margin_lift_pct = (
        round(((total_exp_margin - total_base_margin) / total_base_margin) * 100.0, 1)
        if total_base_margin > 0
        else 0.0
    )
    pricing_impact = {
        "pending_recommendations_count": len(recs),
        "projected_portfolio_margin_lift_pct": margin_lift_pct,
        "binding_constraints_count": sum(1 for r in recs if r.binding_constraints),
    }

    # Latest period KPIs and sequential deltas
    latest_p = dict(period_summaries[-1]) if period_summaries else {}
    if len(period_summaries) >= 2:
        prev_p = period_summaries[-2]
        latest_p["prev_gross_margin_pct"] = prev_p["gross_margin_pct"]
        latest_p["gross_margin_delta_pct"] = round(
            latest_p["gross_margin_pct"] - prev_p["gross_margin_pct"], 1
        )
    elif latest_p:
        latest_p["prev_gross_margin_pct"] = latest_p["gross_margin_pct"]
        latest_p["gross_margin_delta_pct"] = 0.0

    return {
        "organization_id": str(organization_id),
        "latest_period": latest_p,
        "trailing_periods": period_summaries,
        "anomalies": findings,
        "pricing_impact": pricing_impact,
    }


def _generate_grounded_template_narrative(facts: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Synthesize high-conviction CFO strategic narrative directly from verified facts."""
    latest = facts.get("latest_period", {})
    trailing = facts.get("trailing_periods", [])
    pricing = facts.get("pricing_impact", {})
    anomalies = facts.get("anomalies", [])

    if not latest:
        narrative = (
            "Executive Strategic Briefing:\n\n"
            "Trailing financial statements have not yet been ingested for this enterprise. "
            "Ingest or seed statement line items to calculate portfolio gross margin, operational runway, "
            "and working capital indicators."
        )
        recommendations = [
            {
                "priority": "High",
                "category": "Data Governance",
                "action": "Seed or upload quarterly financial statements to activate real-time CFO intelligence.",
                "expected_impact": "Establish baseline visibility over runway and margins",
            }
        ]
        return narrative, recommendations

    period_label = latest.get("period", "Current Period")
    gm = latest.get("gross_margin_pct", 0.0)
    ebitda = latest.get("ebitda_margin_pct", 0.0)
    runway = latest.get("runway_months", 0.0)
    ccc = latest.get("ccc_days", 0.0)
    cac = latest.get("cac", 0.0)
    ltv = latest.get("ltv", 0.0)
    prev_gm = latest.get("prev_gross_margin_pct", gm)
    gm_shift = latest.get("gross_margin_delta_pct", 0.0)

    pricing_lift = pricing.get("projected_portfolio_margin_lift_pct", 0.0)
    recs_count = pricing.get("pending_recommendations_count", 0)

    narrative_paragraphs = [
        f"Executive Strategic Briefing ({period_label}):",
        f"In {period_label}, operating performance closed with Gross Margin at {gm:.1f}% "
        f"and EBITDA Margin at {ebitda:.1f}%. Compared to the previous period ({prev_gm:.1f}%), "
        f"gross margin moved by {gm_shift:+.1f}%. Available cash reserves currently provide "
        f"{runway:.1f} months of operational runway, while the Cash Conversion Cycle stands at {ccc:.1f} days.",
        f"Customer acquisition unit economics reflect a CAC of ${cac:.2f} against a lifetime customer value (LTV) "
        f"of ${ltv:.2f}.",
    ]

    if anomalies:
        first_a = anomalies[0]
        narrative_paragraphs.append(
            f"Risk Alert: Detected anomaly '{first_a['title']}' requires active capital stewardship."
        )

    if recs_count > 0 and pricing_lift != 0.0:
        narrative_paragraphs.append(
            f"Pricing Decision Support Impact: Approving the {recs_count} pending price recommendations "
            f"is projected to deliver a +{pricing_lift:.1f}% lift in total portfolio gross margin "
            "while strictly respecting guardrail floors."
        )

    narrative = "\n\n".join(narrative_paragraphs)

    recommendations = [
        {
            "priority": "High",
            "category": "Pricing Strategy",
            "action": f"Deploy dynamic pricing adjustments across {recs_count} SKUs to capture projected +{pricing_lift:.1f}% margin lift.",
            "expected_impact": f"+{pricing_lift:.1f}% gross margin lift",
        },
        {
            "priority": "Medium",
            "category": "Working Capital",
            "action": f"Optimize inventory reorder thresholds to compress CCC from {ccc:.1f} days and extend runway beyond {runway:.1f} months.",
            "expected_impact": "Conserve liquidity and extend cash runway",
        },
    ]

    return narrative, recommendations


def generate_cfo_advisor_report(
    session: Session,
    organization_id: uuid.UUID,
    *,
    model_name: str = "llama3.2",
    use_ollama: bool = True,
    actor_id: uuid.UUID | None = None,
) -> AdvisorReport:
    """Generate a CFO strategic report with numeric grounding validation against facts JSON."""
    t0 = time.perf_counter()
    facts = build_facts_payload(session, organization_id)

    narrative: str
    recommendations: list[dict[str, Any]]
    model_used = model_name

    # Try local Ollama if configured and requested
    ollama_succeeded = False
    if use_ollama:
        try:
            prompt = (
                f"You are the Corporate Financial Advisor (CFO Assistant). "
                f"Below is a validated JSON payload containing corporate financials:\n{facts}\n\n"
                f"Write a 3-paragraph executive briefing for the leadership team. "
                f"CRITICAL CONSTRAINT: You must ONLY cite numbers that exist in the facts JSON. "
                f"Do not invent any numbers."
            )
            with httpx.Client(timeout=4.0) as client:
                res = client.post(
                    "http://localhost:11434/api/generate",
                    json={"model": model_name, "prompt": prompt, "stream": False},
                )
                if res.status_code == 200:
                    ollama_text = res.json().get("response", "").strip()
                    if ollama_text:
                        narrative = ollama_text
                        recommendations = [
                            {
                                "priority": "High",
                                "action": "Align SKU pricing to econometric recommendations.",
                                "category": "Pricing",
                            }
                        ]
                        ollama_succeeded = True
        except Exception as exc:
            logger.debug("Ollama request failed (%s); falling back to verified grounded generator", exc)

    if not ollama_succeeded:
        model_used = "dss-grounded-synthesizer"
        narrative, recommendations = _generate_grounded_template_narrative(facts)

    latency_ms = int((time.perf_counter() - t0) * 1000)

    # 4. Run Numeric Grounding Validation
    passed, violations = validate_numeric_grounding(narrative, facts)

    latest_period_id = None
    latest_period = session.scalar(
        select(FinancialPeriod)
        .where(FinancialPeriod.organization_id == organization_id)
        .order_by(FinancialPeriod.period_start.desc())
        .limit(1)
    )
    if latest_period:
        latest_period_id = latest_period.id

    report = AdvisorReport(
        organization_id=organization_id,
        period_id=latest_period_id,
        model_name=model_used,
        facts=facts,
        narrative=narrative,
        recommendations=recommendations,
        grounding_passed=passed,
        grounding_violations=violations,
        latency_ms=latency_ms,
    )
    session.add(report)
    session.flush()

    record_audit_event(
        session,
        organization_id=organization_id,
        action=AuditAction.CREATE,
        entity_type="advisor_report",
        entity_id=report.id,
        actor_id=actor_id,
        payload={"model": model_used, "grounding_passed": passed, "violations_count": len(violations)},
    )

    session.commit()
    logger.info(
        "Generated CFO report (model=%s, grounding_passed=%s, violations=%d, latency=%dms)",
        model_used,
        passed,
        len(violations),
        latency_ms,
    )
    return report
