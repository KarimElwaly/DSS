"""Tests for Module B: Elasticity estimation, demand curves, and forecasting."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import (
    CrossElasticity,
    ElasticityEstimate,
    ForecastPoint,
    ModelRun,
    Product,
)
from app.models.enums import ElasticityMethod, ModelKind
from app.services.elasticity import calculate_demand_curve, fit_elasticity_model
from app.services.forecasting import run_demand_forecast
from app.services.matching import run_matching
from app.services.seed import get_default_organization, seed_market
from simulator import MarketSimulator, default_electronics_market
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture(scope="module")
def analytics_session() -> Session:
    """Provide a seeded transaction for analytics testing."""
    from app.db import get_engine, get_session_factory

    connection = get_engine().connect()
    transaction = connection.begin()
    session = get_session_factory()(bind=connection)

    # 180 days of history gives sufficient sample size for OLS and backtests
    config = default_electronics_market(seed=42, history_days=180, end_date=dt.date.today())
    seed_market(session, MarketSimulator(config).generate(), reset=True)
    org = get_default_organization(session)
    run_matching(session, org.id)
    session.flush()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="module")
def analytics_client(analytics_session: Session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: analytics_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def auth_headers(analytics_client: TestClient, settings) -> dict[str, str]:
    res = analytics_client.post(
        "/api/auth/login",
        json={"email": settings.admin_email, "password": settings.admin_password},
    )
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def test_elasticity_estimation_and_ci(analytics_session: Session) -> None:
    org = get_default_organization(analytics_session)
    model_run = fit_elasticity_model(analytics_session, org.id, min_observations=30)

    assert model_run.kind == ModelKind.ELASTICITY
    assert model_run.is_champion is True
    assert model_run.n_products > 0
    assert "mean_r_squared" in model_run.metrics

    estimates = list(
        analytics_session.scalars(
            select(ElasticityEstimate).where(ElasticityEstimate.model_run_id == model_run.id)
        )
    )
    assert len(estimates) == model_run.n_products

    # Most SKUs should have a negative price elasticity (law of demand)
    negative_count = sum(1 for e in estimates if e.elasticity < 0)
    assert negative_count >= len(estimates) * 0.85

    for est in estimates:
        if est.ci_low is not None and est.ci_high is not None:
            assert est.ci_low <= est.ci_high


def test_cross_elasticity_records(analytics_session: Session) -> None:
    org = get_default_organization(analytics_session)
    cross_terms = list(
        analytics_session.scalars(
            select(CrossElasticity).where(CrossElasticity.organization_id == org.id)
        )
    )
    # Categories with multiple products should have pairwise cross elasticity estimates
    assert len(cross_terms) > 0
    for term in cross_terms:
        assert term.product_id != term.related_product_id


def test_calculate_demand_curve() -> None:
    fake_est = ElasticityEstimate(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        product_id=uuid.uuid4(),
        model_run_id=uuid.uuid4(),
        method=ElasticityMethod.LOG_LOG_OLS,
        elasticity=-1.5,
        ci_low=-1.8,
        ci_high=-1.2,
        is_reliable=True,
    )
    points = calculate_demand_curve(fake_est, current_price=100.0, current_cost=60.0, base_units=50.0)
    assert len(points) == 25

    # Verify downward sloping demand: price increases => units decrease
    first_pt = points[0]
    last_pt = points[-1]
    assert first_pt["price"] < last_pt["price"]
    assert first_pt["units"] > last_pt["units"]
    assert first_pt["lower_units"] <= first_pt["upper_units"]


def test_demand_forecasting_and_backtest(analytics_session: Session) -> None:
    org = get_default_organization(analytics_session)
    forecast_run = run_demand_forecast(
        analytics_session, org.id, horizon_days=30, backtest_days=21
    )

    assert forecast_run.kind == ModelKind.FORECAST
    assert forecast_run.is_champion is True
    assert forecast_run.metrics["wape_ets"] >= 0.0
    assert forecast_run.metrics["n_forecast_points"] > 0

    points = list(
        analytics_session.scalars(
            select(ForecastPoint).where(ForecastPoint.model_run_id == forecast_run.id)
        )
    )
    assert len(points) > 0
    for p in points:
        assert p.predicted_units >= 0.0
        if p.lower_units is not None and p.upper_units is not None:
            assert p.lower_units <= p.upper_units


def test_analytics_api(
    analytics_client: TestClient,
    analytics_session: Session,
    auth_headers: dict[str, str],
) -> None:
    # 1. Trigger fit
    res_elast = analytics_client.post(
        "/api/analytics/elasticity/fit?min_observations=30", headers=auth_headers
    )
    assert res_elast.status_code == 200
    report = res_elast.json()
    assert report["n_products"] > 0

    # 2. List estimates
    res_list = analytics_client.get("/api/analytics/elasticity", headers=auth_headers)
    assert res_list.status_code == 200
    items = res_list.json()
    assert len(items) > 0
    sample_prod_id = items[0]["product_id"]

    # 3. Get SKU Workbench
    res_wb = analytics_client.get(
        f"/api/analytics/elasticity/{sample_prod_id}", headers=auth_headers
    )
    assert res_wb.status_code == 200
    wb_data = res_wb.json()
    assert wb_data["sku"] == items[0]["sku"]
    assert len(wb_data["demand_curve"]) > 0

    # 4. Trigger forecast
    res_fc = analytics_client.post(
        "/api/analytics/forecast/fit?horizon_days=30", headers=auth_headers
    )
    assert res_fc.status_code == 200

    # 5. Get SKU forecast
    res_sku_fc = analytics_client.get(
        f"/api/analytics/forecast/{sample_prod_id}", headers=auth_headers
    )
    assert res_sku_fc.status_code == 200
    sku_fc = res_sku_fc.json()
    assert len(sku_fc["points"]) == 30
