"""API routes for dynamic pricing optimization and what-if sandbox (Modules C & D)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentOrg, CurrentUser, DbSession
from app.models import Category, PriceRecommendation, Product, SimulationRun
from app.models.enums import PricingObjective, RecommendationStatus
from app.schemas.pricing import (
    DecideRecommendationRequest,
    GenerateRecommendationsRequest,
    PriceRecommendationOut,
    ScenarioSimulationRequest,
    SimulationRunOut,
)
from app.services.optimization.engine import (
    decide_recommendation,
    generate_recommendations,
)
from app.services.simulation.scenario import run_scenario_simulation

router = APIRouter(prefix="/pricing", tags=["pricing"])


@router.post("/recommendations/generate", response_model=list[PriceRecommendationOut])
def trigger_recommendations_generation(
    req: GenerateRecommendationsRequest,
    session: DbSession,
    org: CurrentOrg,
    user: CurrentUser,
) -> list[PriceRecommendationOut]:
    """Generate price recommendations under a specified objective (margin, revenue, penetration)."""
    try:
        recs = generate_recommendations(
            session,
            org.id,
            objective=req.objective,
            product_ids=req.product_ids,
            apply_charm_rounding=req.apply_charm_rounding,
            actor_id=user.id,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # Load relations for display
    prods = {
        p.id: p
        for p in session.scalars(
            select(Product).where(Product.organization_id == org.id).options(selectinload(Product.category))
        )
    }

    results = []
    for r in recs:
        p = prods.get(r.product_id)
        results.append(
            PriceRecommendationOut(
                id=r.id,
                product_id=r.product_id,
                sku=p.sku if p else "—",
                product_name=p.name if p else "—",
                category_name=p.category.name if (p and p.category) else None,
                objective=r.objective,
                current_price=float(r.current_price),
                recommended_price=float(r.recommended_price),
                unconstrained_price=float(r.unconstrained_price) if r.unconstrained_price else None,
                price_floor=float(r.price_floor),
                price_ceiling=float(r.price_ceiling),
                expected_units=r.expected_units,
                expected_revenue=r.expected_revenue,
                expected_margin=r.expected_margin,
                baseline_revenue=r.baseline_revenue,
                baseline_margin=r.baseline_margin,
                elasticity_used=r.elasticity_used,
                confidence=r.confidence,
                binding_constraints=r.binding_constraints,
                rationale=r.rationale,
                explanation=r.explanation,
                status=r.status,
                created_at=r.created_at,
                decided_at=r.decided_at,
                decision_note=r.decision_note,
            )
        )
    return results


@router.get("/recommendations", response_model=list[PriceRecommendationOut])
def list_recommendations(
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
    rec_status: RecommendationStatus | None = Query(default=None, alias="status"),
    objective: PricingObjective | None = Query(default=None),
) -> list[PriceRecommendationOut]:
    """List price recommendations filtered by status and objective."""
    stmt = (
        select(PriceRecommendation, Product, Category)
        .join(Product, Product.id == PriceRecommendation.product_id)
        .outerjoin(Category, Category.id == Product.category_id)
        .where(PriceRecommendation.organization_id == org.id)
        .order_by(PriceRecommendation.created_at.desc())
    )
    if rec_status:
        stmt = stmt.where(PriceRecommendation.status == rec_status)
    if objective:
        stmt = stmt.where(PriceRecommendation.objective == objective)

    results = []
    for rec, prod, cat in session.execute(stmt):
        results.append(
            PriceRecommendationOut(
                id=rec.id,
                product_id=prod.id,
                sku=prod.sku,
                product_name=prod.name,
                category_name=cat.name if cat else None,
                objective=rec.objective,
                current_price=float(rec.current_price),
                recommended_price=float(rec.recommended_price),
                unconstrained_price=float(rec.unconstrained_price) if rec.unconstrained_price else None,
                price_floor=float(rec.price_floor),
                price_ceiling=float(rec.price_ceiling),
                expected_units=rec.expected_units,
                expected_revenue=rec.expected_revenue,
                expected_margin=rec.expected_margin,
                baseline_revenue=rec.baseline_revenue,
                baseline_margin=rec.baseline_margin,
                elasticity_used=rec.elasticity_used,
                confidence=rec.confidence,
                binding_constraints=rec.binding_constraints,
                rationale=rec.rationale,
                explanation=rec.explanation,
                status=rec.status,
                created_at=rec.created_at,
                decided_at=rec.decided_at,
                decision_note=rec.decision_note,
            )
        )
    return results


@router.post("/recommendations/{recommendation_id}/decide", response_model=PriceRecommendationOut)
def decide_recommendation_endpoint(
    recommendation_id: uuid.UUID,
    req: DecideRecommendationRequest,
    session: DbSession,
    org: CurrentOrg,
    user: CurrentUser,
) -> PriceRecommendationOut:
    """Approve, reject, or apply a price recommendation."""
    try:
        rec = decide_recommendation(
            session,
            recommendation_id,
            org.id,
            decision=req.status,
            actor_id=user.id,
            decision_note=req.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    prod = session.scalar(
        select(Product).where(Product.id == rec.product_id).options(selectinload(Product.category))
    )

    return PriceRecommendationOut(
        id=rec.id,
        product_id=rec.product_id,
        sku=prod.sku if prod else "—",
        product_name=prod.name if prod else "—",
        category_name=prod.category.name if (prod and prod.category) else None,
        objective=rec.objective,
        current_price=float(rec.current_price),
        recommended_price=float(rec.recommended_price),
        unconstrained_price=float(rec.unconstrained_price) if rec.unconstrained_price else None,
        price_floor=float(rec.price_floor),
        price_ceiling=float(rec.price_ceiling),
        expected_units=rec.expected_units,
        expected_revenue=rec.expected_revenue,
        expected_margin=rec.expected_margin,
        baseline_revenue=rec.baseline_revenue,
        baseline_margin=rec.baseline_margin,
        elasticity_used=rec.elasticity_used,
        confidence=rec.confidence,
        binding_constraints=rec.binding_constraints,
        rationale=rec.rationale,
        explanation=rec.explanation,
        status=rec.status,
        created_at=rec.created_at,
        decided_at=rec.decided_at,
        decision_note=rec.decision_note,
    )


@router.post("/sandbox/simulate", response_model=SimulationRunOut)
def trigger_scenario_simulation(
    req: ScenarioSimulationRequest,
    session: DbSession,
    org: CurrentOrg,
    user: CurrentUser,
) -> SimulationRunOut:
    """Run a Monte Carlo what-if pricing scenario simulation across the catalog."""
    try:
        run = run_scenario_simulation(
            session,
            org.id,
            name=req.name,
            horizon_days=req.horizon_days,
            price_overrides=req.price_overrides,
            competitor_reaction_pct=req.competitor_reaction_pct,
            n_monte_carlo=req.n_monte_carlo,
            actor_id=user.id,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return SimulationRunOut(
        id=run.id,
        name=run.name,
        created_at=run.created_at,
        horizon_days=run.horizon_days,
        scenario=run.scenario,
        results=run.results,
        n_monte_carlo=run.n_monte_carlo,
    )


@router.get("/sandbox/runs", response_model=list[SimulationRunOut])
def list_simulation_runs(
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
) -> list[SimulationRunOut]:
    """List historical what-if simulation runs."""
    stmt = (
        select(SimulationRun)
        .where(SimulationRun.organization_id == org.id)
        .order_by(SimulationRun.created_at.desc())
    )
    runs = list(session.scalars(stmt))
    return [
        SimulationRunOut(
            id=r.id,
            name=r.name,
            created_at=r.created_at,
            horizon_days=r.horizon_days,
            scenario=r.scenario,
            results=r.results,
            n_monte_carlo=r.n_monte_carlo,
        )
        for r in runs
    ]


@router.get("/sandbox/runs/{run_id}", response_model=SimulationRunOut)
def get_simulation_run(
    run_id: uuid.UUID,
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
) -> SimulationRunOut:
    """Retrieve results for a single saved simulation run."""
    run = session.scalar(
        select(SimulationRun).where(
            SimulationRun.id == run_id, SimulationRun.organization_id == org.id
        )
    )
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation run {run_id} not found.",
        )

    return SimulationRunOut(
        id=run.id,
        name=run.name,
        created_at=run.created_at,
        horizon_days=run.horizon_days,
        scenario=run.scenario,
        results=run.results,
        n_monte_carlo=run.n_monte_carlo,
    )
