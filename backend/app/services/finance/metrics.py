"""Financial metric engine (Module F).

Derives corporate financial KPIs (Gross Margin, EBITDA, CAC, LTV, Runway,
Cash Conversion Cycle) from normalized statement line items.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.core.logging import get_logger
from app.models import FinancialLineItem, FinancialMetric, FinancialPeriod

logger = get_logger(__name__)


def compute_period_metrics(
    session: Session, period: FinancialPeriod
) -> list[FinancialMetric]:
    """Compute and persist derived financial KPIs for a single period."""
    # Load line items
    items = {
        item.canonical_key: float(item.amount)
        for item in session.scalars(
            select(FinancialLineItem).where(FinancialLineItem.period_id == period.id)
        )
    }

    revenue = items.get("revenue", 0.0)
    cogs = items.get("cogs", 0.0)
    ebitda = items.get("ebitda", 0.0)
    operating_income = items.get("operating_income", 0.0)
    cash = items.get("cash_and_equivalents", 0.0)
    ar = items.get("accounts_receivable", 0.0)
    inv = items.get("inventory", 0.0)
    ap = items.get("accounts_payable", 0.0)
    fcf = items.get("free_cash_flow", 0.0)
    sm_exp = items.get("opex_sales_marketing", 0.0)
    new_cust = items.get("customers_acquired", 0.0)
    total_cust = items.get("total_customers", 1000.0)

    metrics_to_save: list[tuple[str, str, float, str, str]] = []

    # 1. Gross Margin
    gross_margin = (revenue - cogs) / revenue if revenue > 0 else 0.0
    metrics_to_save.append(
        ("gross_margin", "Gross Margin", round(gross_margin, 4), "ratio", "(Revenue - COGS) / Revenue")
    )

    # 2. EBITDA Margin
    ebitda_margin = ebitda / revenue if revenue > 0 else 0.0
    metrics_to_save.append(
        ("ebitda_margin", "EBITDA Margin", round(ebitda_margin, 4), "ratio", "EBITDA / Revenue")
    )

    # 3. Operating Margin
    op_margin = operating_income / revenue if revenue > 0 else 0.0
    metrics_to_save.append(
        ("operating_margin", "Operating Margin", round(op_margin, 4), "ratio", "Operating Income / Revenue")
    )

    # 4. Customer Acquisition Cost (CAC)
    cac = sm_exp / new_cust if new_cust > 0 else 0.0
    metrics_to_save.append(
        ("cac", "Customer Acquisition Cost (CAC)", round(cac, 2), "currency", "S&M Opex / New Customers")
    )

    # 5. Customer Lifetime Value (LTV proxy)
    # ARPU * Gross Margin / estimated quarterly churn (~5%)
    arpu = (revenue / total_cust) if total_cust > 0 else 0.0
    ltv = (arpu * gross_margin / 0.05) if gross_margin > 0 else 0.0
    metrics_to_save.append(
        ("ltv", "Customer Lifetime Value (LTV)", round(ltv, 2), "currency", "ARPU * Gross Margin / Churn")
    )

    # 6. LTV / CAC Ratio
    ltv_cac = (ltv / cac) if cac > 0 else 0.0
    metrics_to_save.append(
        ("ltv_cac_ratio", "LTV / CAC Ratio", round(ltv_cac, 2), "ratio", "LTV / CAC")
    )

    # 7. Monthly Cash Burn & Runway
    months_in_period = 3.0 if period.granularity == "quarter" else 1.0
    # Net burn if free cash flow is negative
    monthly_burn = max(0.0, -fcf / months_in_period) if fcf < 0 else 0.0
    metrics_to_save.append(
        ("monthly_burn", "Monthly Burn Rate", round(monthly_burn, 2), "currency", "-Free Cash Flow / Months")
    )

    runway = (cash / monthly_burn) if monthly_burn > 0 else 99.0
    metrics_to_save.append(
        ("runway_months", "Cash Runway", round(min(runway, 99.0), 1), "months", "Cash / Monthly Burn")
    )

    # 8. Working Capital & Cash Conversion Cycle (CCC)
    days = 90.0 if period.granularity == "quarter" else 30.0
    dso = (ar / revenue) * days if revenue > 0 else 0.0
    dio = (inv / cogs) * days if cogs > 0 else 0.0
    dpo = (ap / cogs) * days if cogs > 0 else 0.0
    ccc = dso + dio - dpo

    metrics_to_save.append(("dso", "Days Sales Outstanding (DSO)", round(dso, 1), "days", "(AR / Revenue) * Days"))
    metrics_to_save.append(("dio", "Days Inventory Outstanding (DIO)", round(dio, 1), "days", "(Inventory / COGS) * Days"))
    metrics_to_save.append(("dpo", "Days Payable Outstanding (DPO)", round(dpo, 1), "days", "(AP / COGS) * Days"))
    metrics_to_save.append(("ccc", "Cash Conversion Cycle (CCC)", round(ccc, 1), "days", "DSO + DIO - DPO"))

    # Remove existing metrics for period
    session.query(FinancialMetric).filter(FinancialMetric.period_id == period.id).delete()
    session.flush()

    saved_entities = []
    for key, label, val, unit, formula in metrics_to_save:
        m = FinancialMetric(
            period_id=period.id,
            key=key,
            label=label,
            value=val,
            unit=unit,
            formula=formula,
        )
        session.add(m)
        saved_entities.append(m)

    session.flush()
    return saved_entities


def compute_all_metrics(session: Session, organization_id: uuid.UUID) -> dict[str, int]:
    """Compute financial metrics across all periods for an organization."""
    periods = list(
        session.scalars(
            select(FinancialPeriod)
            .where(FinancialPeriod.organization_id == organization_id)
            .order_by(FinancialPeriod.period_start)
        )
    )

    total_metrics = 0
    for p in periods:
        m_list = compute_period_metrics(session, p)
        total_metrics += len(m_list)

    session.commit()
    logger.info("Computed %d financial metrics across %d periods", total_metrics, len(periods))
    return {"periods": len(periods), "metrics_count": total_metrics}
