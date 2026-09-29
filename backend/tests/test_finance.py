"""Tests for Module F: Corporate Financials, Metrics, Anomalies, and Grounded CFO Advisor."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.llm.grounding import validate_numeric_grounding
from app.main import app
from app.models import FinancialFinding, FinancialMetric, FinancialPeriod
from app.services.finance.advisor import generate_cfo_advisor_report
from app.services.finance.anomalies import detect_financial_anomalies
from app.services.finance.ingestion import seed_demo_financials
from app.services.finance.metrics import compute_all_metrics
from app.services.seed import get_default_organization, seed_market
from simulator import MarketSimulator, default_electronics_market
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture(scope="module")
def finance_session() -> Session:
    """Seeded session with market and demo corporate financials."""
    from app.db import get_engine, get_session_factory

    connection = get_engine().connect()
    transaction = connection.begin()
    session = get_session_factory()(bind=connection)

    config = default_electronics_market(seed=42, history_days=100, end_date=dt.date.today())
    seed_market(session, MarketSimulator(config).generate(), reset=True)
    org = get_default_organization(session)
    seed_demo_financials(session, org.id)
    compute_all_metrics(session, org.id)
    detect_financial_anomalies(session, org.id)
    session.flush()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="module")
def finance_client(finance_session: Session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: finance_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def auth_headers(finance_client: TestClient, settings) -> dict[str, str]:
    res = finance_client.post(
        "/api/auth/login",
        json={"email": settings.admin_email, "password": settings.admin_password},
    )
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def test_seed_and_financial_metrics(finance_session: Session) -> None:
    org = get_default_organization(finance_session)
    periods = list(
        finance_session.scalars(
            select(FinancialPeriod).where(FinancialPeriod.organization_id == org.id)
        )
    )
    assert len(periods) == 8

    # Verify metrics for latest period
    latest_p = periods[-1]
    metrics = {
        m.key: m.value
        for m in finance_session.scalars(
            select(FinancialMetric).where(FinancialMetric.period_id == latest_p.id)
        )
    }

    assert "gross_margin" in metrics
    assert 0.20 <= metrics["gross_margin"] <= 0.60
    assert "ebitda_margin" in metrics
    assert "cac" in metrics
    assert metrics["cac"] > 0
    assert "ccc" in metrics
    assert "runway_months" in metrics


def test_detect_anomalies(finance_session: Session) -> None:
    org = get_default_organization(finance_session)
    findings = list(
        finance_session.scalars(
            select(FinancialFinding).where(FinancialFinding.organization_id == org.id)
        )
    )
    # The seeded series has margin pressure and runway change in later quarters
    assert len(findings) > 0
    for f in findings:
        assert f.severity in ("info", "warning", "critical")
        assert len(f.title) > 0


def test_numeric_grounding_validator() -> None:
    facts = {
        "latest_period": {
            "gross_margin_pct": 29.5,
            "ebitda_margin_pct": 11.2,
            "runway_months": 14.2,
            "cac": 211.86,
        },
        "pricing_impact": {
            "projected_portfolio_margin_lift_pct": 5.4,
            "pending_recommendations_count": 18,
        },
    }

    # 1. Grounded narrative (all numbers match facts)
    grounded_text = (
        "Gross margin is 29.5% with EBITDA at 11.2%. "
        "Available cash provides 14.2 months of runway. "
        "Approving the 18 pending price recommendations delivers a +5.4% margin lift."
    )
    passed, violations = validate_numeric_grounding(grounded_text, facts)
    assert passed is True
    assert len(violations) == 0

    # 2. Hallucinated narrative (contains numbers not in facts)
    hallucinated_text = (
        "Gross margin is 29.5%, but our churn rate surged to 48.7% and marketing cost $999.00."
    )
    passed_h, violations_h = validate_numeric_grounding(hallucinated_text, facts)
    assert passed_h is False
    assert len(violations_h) >= 1


def test_generate_cfo_advisor_report(finance_session: Session) -> None:
    org = get_default_organization(finance_session)
    report = generate_cfo_advisor_report(
        finance_session, org.id, model_name="test-model", use_ollama=False
    )

    assert report.grounding_passed is True
    assert len(report.grounding_violations) == 0
    assert len(report.narrative) > 100
    assert len(report.recommendations) >= 1
    assert "latest_period" in report.facts


def test_finance_api(finance_client: TestClient, auth_headers: dict[str, str]) -> None:
    # 1. List periods
    res_p = finance_client.get("/api/finance/periods", headers=auth_headers)
    assert res_p.status_code == 200
    periods = res_p.json()
    assert len(periods) > 0
    first_p_id = periods[0]["id"]

    # 2. Get period detail
    res_det = finance_client.get(f"/api/finance/periods/{first_p_id}", headers=auth_headers)
    assert res_det.status_code == 200
    detail = res_det.json()
    assert len(detail["line_items"]) > 0
    assert len(detail["metrics"]) > 0

    # 3. List findings
    res_f = finance_client.get("/api/finance/findings", headers=auth_headers)
    assert res_f.status_code == 200
    assert len(res_f.json()) > 0

    # 4. Generate advisor report
    res_adv = finance_client.post(
        "/api/finance/advisor/generate?use_ollama=false", headers=auth_headers
    )
    assert res_adv.status_code == 200
    report_data = res_adv.json()
    assert report_data["grounding_passed"] is True

    # 5. Get latest advisor report
    res_latest = finance_client.get("/api/finance/advisor/latest", headers=auth_headers)
    assert res_latest.status_code == 200
    assert res_latest.json()["id"] == report_data["id"]
