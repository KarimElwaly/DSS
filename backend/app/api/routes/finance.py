"""API routes for Corporate Financials and Grounded CFO Advisor (Module F)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentOrg, CurrentUser, DbSession
from app.models import AdvisorReport, FinancialFinding, FinancialLineItem, FinancialMetric, FinancialPeriod
from app.schemas.common import MessageResponse
from app.schemas.finance import (
    AdvisorReportOut,
    FinancialFindingOut,
    FinancialLineItemOut,
    FinancialMetricOut,
    FinancialPeriodOut,
    PeriodDetailOut,
    UploadStatementsRequest,
)
from app.services.finance.advisor import generate_cfo_advisor_report
from app.services.finance.anomalies import detect_financial_anomalies
from app.services.finance.ingestion import ingest_financial_period, seed_demo_financials
from app.services.finance.metrics import compute_all_metrics, compute_period_metrics

router = APIRouter(prefix="/finance", tags=["finance"])


@router.post("/seed", response_model=MessageResponse)
def seed_demo_financials_endpoint(
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
) -> MessageResponse:
    """Seed trailing 8 quarters of financial statements, compute KPIs, and detect anomalies."""
    periods = seed_demo_financials(session, org.id)
    compute_all_metrics(session, org.id)
    detect_financial_anomalies(session, org.id)
    return MessageResponse(
        message=f"Successfully seeded {len(periods)} financial periods, calculated metrics, and evaluated anomalies."
    )


@router.post("/upload", response_model=FinancialPeriodOut)
def upload_financial_period_endpoint(
    req: UploadStatementsRequest,
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
) -> FinancialPeriodOut:
    """Upload/ingest a single normalized financial statement period."""
    period = ingest_financial_period(
        session,
        org.id,
        label=req.label,
        granularity=req.granularity,
        period_start=req.period_start,
        period_end=req.period_end,
        items=req.items,
        source="api_upload",
    )
    compute_period_metrics(session, period)
    detect_financial_anomalies(session, org.id)

    metrics = {
        m.key: m.value
        for m in session.scalars(
            select(FinancialMetric).where(FinancialMetric.period_id == period.id)
        )
    }

    return FinancialPeriodOut(
        id=period.id,
        label=period.label,
        granularity=period.granularity,
        period_start=period.period_start,
        period_end=period.period_end,
        currency=period.currency,
        metrics=metrics,
    )


@router.get("/periods", response_model=list[FinancialPeriodOut])
def list_financial_periods(
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
) -> list[FinancialPeriodOut]:
    """List all financial periods with summary KPIs."""
    periods = list(
        session.scalars(
            select(FinancialPeriod)
            .where(FinancialPeriod.organization_id == org.id)
            .order_by(FinancialPeriod.period_start.asc())
        )
    )

    metrics_rows = session.scalars(
        select(FinancialMetric).where(
            FinancialMetric.period_id.in_([p.id for p in periods])
        )
    )
    metrics_by_period: dict[uuid.UUID, dict[str, float]] = {}
    for m in metrics_rows:
        metrics_by_period.setdefault(m.period_id, {})[m.key] = m.value

    return [
        FinancialPeriodOut(
            id=p.id,
            label=p.label,
            granularity=p.granularity,
            period_start=p.period_start,
            period_end=p.period_end,
            currency=p.currency,
            metrics=metrics_by_period.get(p.id, {}),
        )
        for p in periods
    ]


@router.get("/periods/{period_id}", response_model=PeriodDetailOut)
def get_period_detail(
    period_id: uuid.UUID,
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
) -> PeriodDetailOut:
    """Retrieve full line items and derived metrics for a financial period."""
    period = session.scalar(
        select(FinancialPeriod).where(
            FinancialPeriod.id == period_id, FinancialPeriod.organization_id == org.id
        )
    )
    if not period:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Financial period {period_id} not found.",
        )

    lines = list(
        session.scalars(
            select(FinancialLineItem).where(FinancialLineItem.period_id == period.id)
        )
    )
    metrics = list(
        session.scalars(
            select(FinancialMetric).where(FinancialMetric.period_id == period.id)
        )
    )

    p_out = FinancialPeriodOut(
        id=period.id,
        label=period.label,
        granularity=period.granularity,
        period_start=period.period_start,
        period_end=period.period_end,
        currency=period.currency,
        metrics={m.key: m.value for m in metrics},
    )

    return PeriodDetailOut(
        period=p_out,
        line_items=[
            FinancialLineItemOut(
                id=l.id,
                statement_type=l.statement_type,
                canonical_key=l.canonical_key,
                source_label=l.source_label,
                amount=float(l.amount),
            )
            for l in lines
        ],
        metrics=[
            FinancialMetricOut(
                key=m.key,
                label=m.label,
                value=m.value,
                unit=m.unit,
                formula=m.formula,
            )
            for m in metrics
        ],
    )


@router.get("/findings", response_model=list[FinancialFindingOut])
def list_financial_findings(
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
) -> list[FinancialFindingOut]:
    """List financial anomaly findings."""
    stmt = (
        select(FinancialFinding, FinancialPeriod)
        .join(FinancialPeriod, FinancialPeriod.id == FinancialFinding.period_id)
        .where(FinancialFinding.organization_id == org.id)
        .order_by(FinancialFinding.created_at.desc())
    )

    results = []
    for f, p in session.execute(stmt):
        results.append(
            FinancialFindingOut(
                id=f.id,
                period_id=f.period_id,
                period_label=p.label,
                metric_key=f.metric_key,
                severity=f.severity,
                title=f.title,
                detail=f.detail,
                current_value=f.current_value,
                expected_value=f.expected_value,
                delta_pct=f.delta_pct,
                z_score=f.z_score,
            )
        )
    return results


@router.post("/advisor/generate", response_model=AdvisorReportOut)
def generate_advisor_report_endpoint(
    session: DbSession,
    org: CurrentOrg,
    user: CurrentUser,
    model_name: str = Query(default="llama3.2"),
    use_ollama: bool = Query(default=True),
) -> AdvisorReportOut:
    """Generate a CFO strategic report with numeric grounding validation."""
    report = generate_cfo_advisor_report(
        session,
        org.id,
        model_name=model_name,
        use_ollama=use_ollama,
        actor_id=user.id,
    )
    return AdvisorReportOut(
        id=report.id,
        period_id=report.period_id,
        model_name=report.model_name,
        facts=report.facts,
        narrative=report.narrative,
        recommendations=report.recommendations,
        grounding_passed=report.grounding_passed,
        grounding_violations=report.grounding_violations,
        latency_ms=report.latency_ms,
        created_at=report.created_at,
    )


@router.get("/advisor/latest", response_model=AdvisorReportOut | None)
def get_latest_advisor_report(
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
) -> AdvisorReportOut | None:
    """Retrieve the most recent CFO strategic report."""
    report = session.scalar(
        select(AdvisorReport)
        .where(AdvisorReport.organization_id == org.id)
        .order_by(AdvisorReport.created_at.desc())
        .limit(1)
    )
    if not report:
        return None

    return AdvisorReportOut(
        id=report.id,
        period_id=report.period_id,
        model_name=report.model_name,
        facts=report.facts,
        narrative=report.narrative,
        recommendations=report.recommendations,
        grounding_passed=report.grounding_passed,
        grounding_violations=report.grounding_violations,
        latency_ms=report.latency_ms,
        created_at=report.created_at,
    )
