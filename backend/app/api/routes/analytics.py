"""Analytics API routes: elasticity estimation, demand curves, and forecasting (Module B)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentOrg, CurrentUser, DbSession
from app.models import Category, CrossElasticity, ElasticityEstimate, ForecastPoint, ModelRun, Product, SalesFact
from app.models.enums import ModelKind
from app.schemas.analytics import (
    CrossElasticityOut,
    DemandCurvePoint,
    ElasticityEstimateOut,
    FitElasticityReport,
    FitForecastReport,
    ForecastPointOut,
    SkuForecastOut,
    SkuWorkbenchOut,
)
from app.services.elasticity import calculate_demand_curve, fit_elasticity_model
from app.services.forecasting import run_demand_forecast

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/elasticity/fit", response_model=FitElasticityReport)
def trigger_elasticity_fit(
    session: DbSession,
    org: CurrentOrg,
    user: CurrentUser,
    min_observations: int = Query(default=30, ge=10, le=180),
) -> FitElasticityReport:
    """Fit econometric demand models across all products in the catalog."""
    try:
        model_run = fit_elasticity_model(
            session, org.id, min_observations=min_observations, actor_id=user.id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return FitElasticityReport(
        model_run_id=model_run.id,
        label=model_run.label,
        created_at=model_run.created_at,
        n_products=model_run.n_products,
        metrics=model_run.metrics,
    )


@router.get("/elasticity", response_model=list[ElasticityEstimateOut])
def list_elasticity_estimates(
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
) -> list[ElasticityEstimateOut]:
    """List elasticity estimates from the latest champion model run."""
    champion_run = session.scalar(
        select(ModelRun)
        .where(
            ModelRun.organization_id == org.id,
            ModelRun.kind == ModelKind.ELASTICITY,
            ModelRun.is_champion.is_(True),
        )
        .order_by(ModelRun.created_at.desc())
        .limit(1)
    )
    if not champion_run:
        return []

    stmt = (
        select(ElasticityEstimate, Product, Category)
        .join(Product, Product.id == ElasticityEstimate.product_id)
        .outerjoin(Category, Category.id == Product.category_id)
        .where(ElasticityEstimate.model_run_id == champion_run.id)
        .order_by(Product.sku)
    )

    results = []
    for est, prod, cat in session.execute(stmt):
        results.append(
            ElasticityEstimateOut(
                id=est.id,
                product_id=prod.id,
                sku=prod.sku,
                product_name=prod.name,
                category_name=cat.name if cat else None,
                method=est.method,
                elasticity=est.elasticity,
                std_error=est.std_error,
                ci_low=est.ci_low,
                ci_high=est.ci_high,
                p_value=est.p_value,
                r_squared=est.r_squared,
                n_observations=est.n_observations,
                price_variation_cv=est.price_variation_cv,
                is_reliable=est.is_reliable,
                current_price=float(prod.current_price),
                reference_price=float(est.reference_price) if est.reference_price else None,
                diagnostics=est.diagnostics or {},
            )
        )
    return results


@router.get("/elasticity/{product_id}", response_model=SkuWorkbenchOut)
def get_sku_workbench(
    product_id: uuid.UUID,
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
) -> SkuWorkbenchOut:
    """Get econometric demand curve and cross-elasticity terms for a single SKU."""
    product = session.scalar(
        select(Product)
        .where(Product.id == product_id, Product.organization_id == org.id)
        .options(selectinload(Product.category))
    )
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found in catalog.",
        )

    # Latest champion elasticity run
    champion_run = session.scalar(
        select(ModelRun)
        .where(
            ModelRun.organization_id == org.id,
            ModelRun.kind == ModelKind.ELASTICITY,
            ModelRun.is_champion.is_(True),
        )
        .order_by(ModelRun.created_at.desc())
        .limit(1)
    )

    estimate_out: ElasticityEstimateOut | None = None
    demand_points: list[DemandCurvePoint] = []
    cross_out: list[CrossElasticityOut] = []

    if champion_run:
        est = session.scalar(
            select(ElasticityEstimate).where(
                ElasticityEstimate.product_id == product.id,
                ElasticityEstimate.model_run_id == champion_run.id,
            )
        )
        if est:
            estimate_out = ElasticityEstimateOut(
                id=est.id,
                product_id=product.id,
                sku=product.sku,
                product_name=product.name,
                category_name=product.category.name if product.category else None,
                method=est.method,
                elasticity=est.elasticity,
                std_error=est.std_error,
                ci_low=est.ci_low,
                ci_high=est.ci_high,
                p_value=est.p_value,
                r_squared=est.r_squared,
                n_observations=est.n_observations,
                price_variation_cv=est.price_variation_cv,
                is_reliable=est.is_reliable,
                current_price=float(product.current_price),
                reference_price=float(est.reference_price) if est.reference_price else None,
                diagnostics=est.diagnostics or {},
            )

            # Average daily volume over last 90 days to anchor demand curve
            recent_units_avg = session.scalar(
                select(func.avg(SalesFact.units)).where(
                    SalesFact.product_id == product.id,
                    SalesFact.units > 0,
                )
            )
            base_v = float(recent_units_avg or 40.0)

            curve_raw = calculate_demand_curve(
                est,
                float(product.current_price),
                float(product.unit_cost),
                base_v,
            )
            demand_points = [DemandCurvePoint(**p) for p in curve_raw]

        # Cross elasticities
        cross_stmt = (
            select(CrossElasticity, Product)
            .join(Product, Product.id == CrossElasticity.related_product_id)
            .where(
                CrossElasticity.product_id == product.id,
                CrossElasticity.model_run_id == champion_run.id,
            )
            .order_by(CrossElasticity.elasticity.desc())
        )
        for ce, rel_p in session.execute(cross_stmt):
            cross_out.append(
                CrossElasticityOut(
                    id=ce.id,
                    related_product_id=rel_p.id,
                    related_sku=rel_p.sku,
                    related_name=rel_p.name,
                    elasticity=ce.elasticity,
                    std_error=ce.std_error,
                    p_value=ce.p_value,
                    is_significant=ce.is_significant,
                )
            )

    return SkuWorkbenchOut(
        product_id=product.id,
        sku=product.sku,
        name=product.name,
        brand=product.brand,
        category_name=product.category.name if product.category else None,
        current_price=float(product.current_price),
        unit_cost=float(product.unit_cost),
        min_margin_pct=float(product.min_margin_pct),
        estimate=estimate_out,
        demand_curve=demand_points,
        cross_elasticities=cross_out,
    )


@router.post("/forecast/fit", response_model=FitForecastReport)
def trigger_forecast_fit(
    session: DbSession,
    org: CurrentOrg,
    user: CurrentUser,
    horizon_days: int = Query(default=30, ge=7, le=90),
    backtest_days: int = Query(default=30, ge=7, le=60),
) -> FitForecastReport:
    """Fit demand forecasts and evaluate WAPE backtest."""
    try:
        model_run = run_demand_forecast(
            session,
            org.id,
            horizon_days=horizon_days,
            backtest_days=backtest_days,
            actor_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return FitForecastReport(
        model_run_id=model_run.id,
        label=model_run.label,
        created_at=model_run.created_at,
        n_products=model_run.n_products,
        metrics=model_run.metrics,
    )


@router.get("/forecast/{product_id}", response_model=SkuForecastOut)
def get_sku_forecast(
    product_id: uuid.UUID,
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
) -> SkuForecastOut:
    """Get 30-day demand forecast points and prediction intervals for a SKU."""
    product = session.scalar(
        select(Product).where(Product.id == product_id, Product.organization_id == org.id)
    )
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found in catalog.",
        )

    champion_run = session.scalar(
        select(ModelRun)
        .where(
            ModelRun.organization_id == org.id,
            ModelRun.kind == ModelKind.FORECAST,
            ModelRun.is_champion.is_(True),
        )
        .order_by(ModelRun.created_at.desc())
        .limit(1)
    )

    if not champion_run:
        return SkuForecastOut(
            product_id=product.id,
            sku=product.sku,
            model_run_id=uuid.uuid4(),
            horizon_days=0,
            points=[],
            backtest_wape=None,
        )

    points_stmt = (
        select(ForecastPoint)
        .where(
            ForecastPoint.product_id == product.id,
            ForecastPoint.model_run_id == champion_run.id,
        )
        .order_by(ForecastPoint.target_date)
    )
    points = list(session.scalars(points_stmt))

    per_sku_wape = champion_run.metrics.get("per_sku_wape", {})
    sku_wape = per_sku_wape.get(product.sku)

    return SkuForecastOut(
        product_id=product.id,
        sku=product.sku,
        model_run_id=champion_run.id,
        horizon_days=len(points),
        points=[
            ForecastPointOut(
                target_date=p.target_date,
                predicted_units=p.predicted_units,
                lower_units=p.lower_units,
                upper_units=p.upper_units,
                assumed_price=float(p.assumed_price) if p.assumed_price else None,
            )
            for p in points
        ],
        backtest_wape=sku_wape,
    )
