"""Tests for Modules C & D: Optimization engine, guardrails, and scenario sandbox."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import PriceRecommendation, Product, SimulationRun
from app.models.enums import PricingObjective, RecommendationStatus
from app.services.elasticity import fit_elasticity_model
from app.services.matching import run_matching
from app.services.optimization.engine import (
    decide_recommendation,
    generate_recommendations,
)
from app.services.optimization.guardrails import apply_guardrails
from app.services.seed import get_default_organization, seed_market
from app.services.simulation.scenario import run_scenario_simulation
from simulator import MarketSimulator, default_electronics_market
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture(scope="module")
def opt_session() -> Session:
    """Seeded session with elasticity models fitted."""
    from app.db import get_engine, get_session_factory

    connection = get_engine().connect()
    transaction = connection.begin()
    session = get_session_factory()(bind=connection)

    config = default_electronics_market(seed=42, history_days=150, end_date=dt.date.today())
    seed_market(session, MarketSimulator(config).generate(), reset=True)
    org = get_default_organization(session)
    run_matching(session, org.id)
    fit_elasticity_model(session, org.id, min_observations=30)
    session.flush()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="module")
def opt_client(opt_session: Session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: opt_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def auth_headers(opt_client: TestClient, settings) -> dict[str, str]:
    res = opt_client.post(
        "/api/auth/login",
        json={"email": settings.admin_email, "password": settings.admin_password},
    )
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def test_guardrails_hard_floor_invariant() -> None:
    """Invariant test: final_price >= price_floor ALWAYS."""
    test_cases = [
        (Decimal("5.00"), Decimal("100.00"), Decimal("70.00"), Decimal("0.10"), Decimal("0.15")),
        (Decimal("0.00"), Decimal("50.00"), Decimal("30.00"), Decimal("0.15"), Decimal("0.20")),
        (Decimal("200.00"), Decimal("100.00"), Decimal("60.00"), Decimal("0.08"), Decimal("0.15")),
        (Decimal("75.40"), Decimal("80.00"), Decimal("70.00"), Decimal("0.08"), Decimal("0.15")),
    ]

    for unconstrained, current, cost, min_margin, max_step in test_cases:
        res = apply_guardrails(
            unconstrained_price=unconstrained,
            current_price=current,
            unit_cost=cost,
            min_margin_pct=min_margin,
            max_price_step_pct=max_step,
            apply_charm_rounding=True,
        )
        assert res.final_price >= res.price_floor, f"Violated floor {res.price_floor} with {res.final_price}"
        assert res.final_price <= res.price_ceiling
        assert len(res.explanation) > 0


def test_guardrail_charm_rounding() -> None:
    res = apply_guardrails(
        unconstrained_price=Decimal("94.20"),
        current_price=Decimal("100.00"),
        unit_cost=Decimal("60.00"),
        min_margin_pct=Decimal("0.10"),
        max_price_step_pct=Decimal("0.20"),
        apply_charm_rounding=True,
    )
    # Should snap to .99 ending
    cents = str(res.final_price).split(".")[-1]
    assert cents == "99"
    assert "charm_rounding_99" in res.binding_constraints


def test_recommendation_generation_and_decision(opt_session: Session) -> None:
    org = get_default_organization(opt_session)

    # 1. Generate margin objective recommendations
    recs = generate_recommendations(
        opt_session,
        org.id,
        objective=PricingObjective.MARGIN,
        apply_charm_rounding=True,
    )
    assert len(recs) > 0

    first_rec = recs[0]
    assert first_rec.recommended_price >= first_rec.price_floor
    assert first_rec.status == RecommendationStatus.PROPOSED
    assert "objective" in first_rec.rationale

    # 2. Approve recommendation
    rec_approved = decide_recommendation(
        opt_session,
        first_rec.id,
        org.id,
        decision=RecommendationStatus.APPROVED,
        decision_note="Looks solid for Q4 margin target.",
    )
    assert rec_approved.status == RecommendationStatus.APPROVED

    # 3. Apply recommendation -> updates product current_price
    old_prod_price = opt_session.scalar(
        select(Product.current_price).where(Product.id == first_rec.product_id)
    )
    rec_applied = decide_recommendation(
        opt_session,
        first_rec.id,
        org.id,
        decision=RecommendationStatus.APPLIED,
        decision_note="Applied to catalog.",
    )
    assert rec_applied.status == RecommendationStatus.APPLIED

    new_prod_price = opt_session.scalar(
        select(Product.current_price).where(Product.id == first_rec.product_id)
    )
    assert new_prod_price == first_rec.recommended_price


def test_scenario_sandbox_simulation(opt_session: Session) -> None:
    org = get_default_organization(opt_session)
    products = list(opt_session.scalars(select(Product).where(Product.organization_id == org.id)))
    assert len(products) >= 3

    # Override prices for 2 products: one +10%, one -5%
    p1 = products[0]
    p2 = products[1]
    overrides = {
        p1.id: float(p1.current_price) * 1.10,
        p2.id: float(p2.current_price) * 0.95,
    }

    run = run_scenario_simulation(
        opt_session,
        org.id,
        name="Audio Category Price Optimization Test",
        horizon_days=30,
        price_overrides=overrides,
        competitor_reaction_pct=0.40,
        n_monte_carlo=200,
    )

    assert run.name == "Audio Category Price Optimization Test"
    assert run.n_monte_carlo == 200
    res = run.results

    assert "baseline" in res
    assert "simulated" in res
    assert "quantiles" in res
    assert "delta" in res

    q_rev = res["quantiles"]["revenue"]
    assert q_rev["p05"] <= q_rev["p50"] <= q_rev["p95"]

    q_margin = res["quantiles"]["margin"]
    assert q_margin["p05"] <= q_margin["p50"] <= q_margin["p95"]


def test_pricing_api(opt_client: TestClient, auth_headers: dict[str, str]) -> None:
    # 1. Trigger generate
    res_gen = opt_client.post(
        "/api/pricing/recommendations/generate",
        headers=auth_headers,
        json={"objective": "margin", "apply_charm_rounding": True},
    )
    assert res_gen.status_code == 200
    recs = res_gen.json()
    assert len(recs) > 0
    rec_id = recs[0]["id"]

    # 2. List recommendations
    res_list = opt_client.get("/api/pricing/recommendations", headers=auth_headers)
    assert res_list.status_code == 200
    assert len(res_list.json()) > 0

    # 3. Decide recommendation
    res_decide = opt_client.post(
        f"/api/pricing/recommendations/{rec_id}/decide",
        headers=auth_headers,
        json={"status": "approved", "note": "Approved via API"},
    )
    assert res_decide.status_code == 200
    assert res_decide.json()["status"] == "approved"

    # 4. Run sandbox simulation
    res_sim = opt_client.post(
        "/api/pricing/sandbox/simulate",
        headers=auth_headers,
        json={
            "name": "API Test Scenario",
            "horizon_days": 30,
            "competitor_reaction_pct": 0.25,
            "n_monte_carlo": 150,
            "price_overrides": {},
        },
    )
    assert res_sim.status_code == 200
    sim_data = res_sim.json()
    assert "results" in sim_data
    assert "quantiles" in sim_data["results"]

    # 5. List saved simulation runs
    res_runs = opt_client.get("/api/pricing/sandbox/runs", headers=auth_headers)
    assert res_runs.status_code == 200
    assert len(res_runs.json()) > 0
