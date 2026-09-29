"""Financial statements ingestion & normalization (Module F).

Supports parsing P&L, Balance Sheet, and Cash Flow statement lines,
mapping them to canonical account keys, and seeding realistic demo financials.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import FinancialLineItem, FinancialPeriod
from app.models.enums import AuditAction, StatementType
from app.services.audit import record_audit_event

logger = get_logger(__name__)

CANONICAL_MAPPINGS = {
    # Income Statement
    "revenue": StatementType.INCOME_STATEMENT,
    "cogs": StatementType.INCOME_STATEMENT,
    "gross_profit": StatementType.INCOME_STATEMENT,
    "opex_sales_marketing": StatementType.INCOME_STATEMENT,
    "opex_rd": StatementType.INCOME_STATEMENT,
    "opex_ga": StatementType.INCOME_STATEMENT,
    "ebitda": StatementType.INCOME_STATEMENT,
    "operating_income": StatementType.INCOME_STATEMENT,
    "net_income": StatementType.INCOME_STATEMENT,
    # Balance Sheet
    "cash_and_equivalents": StatementType.BALANCE_SHEET,
    "accounts_receivable": StatementType.BALANCE_SHEET,
    "inventory": StatementType.BALANCE_SHEET,
    "accounts_payable": StatementType.BALANCE_SHEET,
    "total_debt": StatementType.BALANCE_SHEET,
    # Cash Flow
    "operating_cash_flow": StatementType.CASH_FLOW,
    "capex": StatementType.CASH_FLOW,
    "free_cash_flow": StatementType.CASH_FLOW,
    # Operating KPIs
    "customers_acquired": StatementType.OPERATING_KPI,
    "total_customers": StatementType.OPERATING_KPI,
}


def ingest_financial_period(
    session: Session,
    organization_id: uuid.UUID,
    *,
    label: str,
    granularity: str = "quarter",
    period_start: dt.date,
    period_end: dt.date,
    items: dict[str, float | Decimal],
    currency: str = "USD",
    source: str = "manual_upload",
    source_filename: str = "",
) -> FinancialPeriod:
    """Create or update a financial period with normalized statement line items."""
    # Check if period exists
    period = session.scalar(
        select(FinancialPeriod).where(
            FinancialPeriod.organization_id == organization_id,
            FinancialPeriod.label == label,
        )
    )

    if not period:
        period = FinancialPeriod(
            organization_id=organization_id,
            label=label,
            granularity=granularity,
            period_start=period_start,
            period_end=period_end,
            currency=currency,
            source=source,
            source_filename=source_filename,
        )
        session.add(period)
        session.flush()
    else:
        # Delete existing line items for re-ingest
        session.query(FinancialLineItem).filter(FinancialLineItem.period_id == period.id).delete()
        session.flush()

    for key, val in items.items():
        stmt_type = CANONICAL_MAPPINGS.get(key, StatementType.INCOME_STATEMENT)
        line = FinancialLineItem(
            period_id=period.id,
            statement_type=stmt_type,
            canonical_key=key,
            source_label=key.replace("_", " ").title(),
            amount=Decimal(str(round(float(val), 2))),
        )
        session.add(line)

    session.flush()
    return period


def seed_demo_financials(session: Session, organization_id: uuid.UUID) -> list[FinancialPeriod]:
    """Seed 8 trailing quarters of realistic consumer electronics retailer financials.

    Captures realistic trends: revenue growth, supply chain margin pressure,
    marketing expansion, and working capital dynamics.
    """
    quarters = [
        # (label, start, end, revenue, cogs, opex_sm, opex_rd, opex_ga, cash, ar, inv, ap, ocf, capex, new_cust)
        ("2024-Q3", dt.date(2024, 7, 1), dt.date(2024, 9, 30), 4200000, 2600000, 750000, 320000, 240000, 3800000, 680000, 1100000, 520000, 380000, 120000, 4800),
        ("2024-Q4", dt.date(2024, 10, 1), dt.date(2024, 12, 31), 5800000, 3600000, 1100000, 340000, 260000, 4200000, 920000, 1400000, 710000, 620000, 140000, 7200),
        ("2025-Q1", dt.date(2025, 1, 1), dt.date(2025, 3, 31), 4500000, 2800000, 820000, 350000, 250000, 3950000, 710000, 1150000, 540000, 310000, 110000, 4900),
        ("2025-Q2", dt.date(2025, 4, 1), dt.date(2025, 6, 30), 4900000, 3100000, 900000, 360000, 270000, 3800000, 790000, 1220000, 580000, 340000, 130000, 5300),
        ("2025-Q3", dt.date(2025, 7, 1), dt.date(2025, 9, 30), 5100000, 3350000, 980000, 380000, 280000, 3500000, 830000, 1340000, 610000, 260000, 150000, 5600),
        ("2025-Q4", dt.date(2025, 10, 1), dt.date(2025, 12, 31), 6900000, 4600000, 1350000, 410000, 310000, 3750000, 1120000, 1680000, 840000, 480000, 180000, 8100),
        ("2026-Q1", dt.date(2026, 1, 1), dt.date(2026, 3, 31), 5300000, 3600000, 1120000, 420000, 320000, 3200000, 890000, 1420000, 660000, 210000, 140000, 5700),
        ("2026-Q2", dt.date(2026, 4, 1), dt.date(2026, 6, 30), 5600000, 3950000, 1250000, 440000, 340000, 2650000, 950000, 1550000, 720000, 140000, 160000, 5900),
    ]

    periods = []
    for label, p_start, p_end, rev, cogs, sm_exp, rd, ga, cash, ar, inv, ap, ocf, capex, new_c in quarters:
        gross_p = rev - cogs
        ebitda = gross_p - (sm_exp + rd + ga)
        op_inc = ebitda - 120000  # D&A ~120k
        net_inc = op_inc - 35000  # tax & interest ~35k
        fcf = ocf - capex

        items = {
            "revenue": rev,
            "cogs": cogs,
            "gross_profit": gross_p,
            "opex_sales_marketing": sm_exp,
            "opex_rd": rd,
            "opex_ga": ga,
            "ebitda": ebitda,
            "operating_income": op_inc,
            "net_income": net_inc,
            "cash_and_equivalents": cash,
            "accounts_receivable": ar,
            "inventory": inv,
            "accounts_payable": ap,
            "operating_cash_flow": ocf,
            "capex": capex,
            "free_cash_flow": fcf,
            "customers_acquired": new_c,
            "total_customers": 45000 + (len(periods) * 4000),
        }

        p = ingest_financial_period(
            session,
            organization_id,
            label=label,
            period_start=p_start,
            period_end=p_end,
            items=items,
            source="demo_seed",
        )
        periods.append(p)

    record_audit_event(
        session,
        organization_id=organization_id,
        action=AuditAction.INGEST,
        entity_type="financial_period_batch",
        entity_id=uuid.uuid4(),
        payload={"n_periods": len(periods)},
    )
    session.commit()
    logger.info("Seeded %d trailing financial periods for org %s", len(periods), organization_id)
    return periods
