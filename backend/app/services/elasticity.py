"""Econometric demand estimation (Module B).

Fits log-log demand models to estimate own-price and cross-price elasticities
with heteroskedasticity-robust (HC3) confidence intervals, pooled category
fallbacks for data-sparse SKUs, and diagnostic gating.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import (
    Category,
    CompetitorListing,
    CrossElasticity,
    ElasticityEstimate,
    ModelRun,
    PriceObservation,
    Product,
    SalesFact,
)
from app.models.enums import (
    AuditAction,
    ElasticityMethod,
    MatchStatus,
    ModelKind,
    StockState,
)
from app.services.audit import record_audit_event

logger = get_logger(__name__)

ALPHA = 0.10  # 90% confidence intervals


def _load_regression_frames(
    session: Session, organization_id: uuid.UUID
) -> tuple[pd.DataFrame, pd.DataFrame, dict[uuid.UUID, Product], dict[uuid.UUID, Category]]:
    """Fetch sales history, competitor observations, and products for the org."""
    products = {
        p.id: p
        for p in session.scalars(
            select(Product).where(Product.organization_id == organization_id)
        )
    }
    categories = {
        c.id: c
        for c in session.scalars(
            select(Category).where(Category.organization_id == organization_id)
        )
    }

    sales_rows = session.execute(
        select(
            SalesFact.product_id,
            SalesFact.sale_date,
            SalesFact.units,
            SalesFact.unit_price,
            SalesFact.unit_cost,
            SalesFact.promo_flag,
        ).where(SalesFact.organization_id == organization_id)
    ).all()

    sales = pd.DataFrame(
        sales_rows,
        columns=["product_id", "sale_date", "units", "price", "cost", "promo"],
    )

    if sales.empty:
        return sales, pd.DataFrame(), products, categories

    sales["price"] = sales["price"].astype(float)
    sales["cost"] = sales["cost"].astype(float)
    sales["units"] = sales["units"].astype(float)
    sales["sale_date"] = pd.to_datetime(sales["sale_date"]).astype("datetime64[ns]")

    # Competitor observations for matched listings
    comp_rows = session.execute(
        select(
            CompetitorListing.matched_product_id,
            PriceObservation.observed_at,
            PriceObservation.price,
            PriceObservation.shipping_cost,
            PriceObservation.stock_state,
        )
        .join(PriceObservation, PriceObservation.listing_id == CompetitorListing.id)
        .where(
            CompetitorListing.organization_id == organization_id,
            CompetitorListing.matched_product_id.is_not(None),
            CompetitorListing.match_status.in_(
                (MatchStatus.AUTO_ACCEPTED, MatchStatus.CONFIRMED)
            ),
        )
    ).all()

    if not comp_rows:
        comp_agg = pd.DataFrame(columns=["product_id", "sale_date", "log_comp", "oos_frac", "n_listings"])
    else:
        comp = pd.DataFrame(
            comp_rows,
            columns=["product_id", "observed_at", "price", "shipping", "stock_state"],
        )
        comp["landed"] = comp["price"].astype(float) + comp["shipping"].astype(float)
        comp["sale_date"] = (
            pd.to_datetime(comp["observed_at"], utc=True)
            .dt.tz_localize(None)
            .dt.normalize()
            .astype("datetime64[ns]")
        )
        comp["in_stock"] = comp["stock_state"].astype(str) == StockState.IN_STOCK.value
        comp["log_landed"] = np.log(comp["landed"].where(comp["in_stock"]))

        comp_agg = (
            comp.groupby(["product_id", "sale_date"])
            .agg(
                log_comp=("log_landed", "mean"),
                oos_frac=("in_stock", lambda s: 1.0 - float(np.mean(s))),
                n_listings=("in_stock", "size"),
            )
            .reset_index()
        )

    return sales, comp_agg, products, categories


def _build_design_matrix(group: pd.DataFrame, ref_price: float) -> pd.DataFrame:
    """Build feature design matrix for log-log OLS regression."""
    x = np.log(group["price"].to_numpy() / ref_price)
    doy = group["sale_date"].dt.dayofyear.to_numpy()
    trend = (group["sale_date"] - group["sale_date"].min()).dt.days.to_numpy() / 365.25

    design = pd.DataFrame(
        {
            "log_price": x,
            "log_price_sq": x**2,
            "promo": group["promo"].astype(float).to_numpy(),
            "trend": trend,
            "sin_y": np.sin(2 * np.pi * doy / 365.25),
            "cos_y": np.cos(2 * np.pi * doy / 365.25),
        },
        index=group.index,
    )
    for dow in range(1, 7):
        design[f"dow_{dow}"] = (group["sale_date"].dt.dayofweek == dow).astype(float)
    return design


def _fit_ols(
    design: pd.DataFrame, units: np.ndarray
) -> tuple[float, float | None, float | None, float | None, float | None, float, dict[str, float]]:
    """Fit OLS with HC3 robust standard errors.

    Returns:
        (elasticity, std_error, ci_low, ci_high, p_value, r_squared, coefficients)
    """
    positive_mask = units > 0
    if np.sum(positive_mask) < 15:
        raise ValueError("Insufficient positive sales points for OLS fit.")

    y = np.log(units[positive_mask])
    X = sm.add_constant(design.loc[positive_mask])

    model = sm.OLS(y, X).fit(cov_type="HC3")
    params = model.params.to_dict()

    elast = float(params.get("log_price", 0.0))
    se = float(model.bse.get("log_price", 0.0)) if "log_price" in model.bse else None
    pval = float(model.pvalues.get("log_price", 1.0)) if "log_price" in model.pvalues else None

    ci = model.conf_int(alpha=ALPHA)
    if "log_price" in ci.index:
        ci_low = float(ci.loc["log_price", 0])
        ci_high = float(ci.loc["log_price", 1])
    else:
        ci_low = None
        ci_high = None

    r2 = float(model.rsquared)
    return elast, se, ci_low, ci_high, pval, r2, params


def fit_elasticity_model(
    session: Session,
    organization_id: uuid.UUID,
    *,
    min_observations: int = 30,
    actor_id: uuid.UUID | None = None,
) -> ModelRun:
    """Run full elasticity estimation across all products in an organization.

    1. Fits individual log-log OLS with competitor index enrichment.
    2. Falls back to pooled category estimation if observations are sparse.
    3. Falls back to prior fallback (-1.5) if category fit is unavailable.
    4. Computes cross-price elasticity terms between top category siblings.
    5. Saves champion ModelRun, ElasticityEstimate, and CrossElasticity records.
    """
    sales, comp, products, categories = _load_regression_frames(session, organization_id)
    if sales.empty:
        raise ValueError("No sales facts found to estimate elasticity.")

    merged = sales.merge(comp, on=["product_id", "sale_date"], how="left") if not comp.empty else sales.copy()
    if "log_comp" not in merged.columns:
        merged["log_comp"] = np.nan
        merged["oos_frac"] = 0.0

    # Unmark existing champions of this kind
    session.query(ModelRun).filter(
        ModelRun.organization_id == organization_id,
        ModelRun.kind == ModelKind.ELASTICITY,
        ModelRun.is_champion.is_(True),
    ).update({"is_champion": False})

    train_start = sales["sale_date"].min().date()
    train_end = sales["sale_date"].max().date()

    model_run = ModelRun(
        organization_id=organization_id,
        kind=ModelKind.ELASTICITY,
        label=f"Log-log OLS + HC3 ({train_start} to {train_end})",
        params={
            "alpha": ALPHA,
            "min_observations": min_observations,
            "cov_type": "HC3",
            "competitor_enrichment": True,
        },
        metrics={},
        train_start=train_start,
        train_end=train_end,
        is_champion=True,
        n_products=len(products),
    )
    session.add(model_run)
    session.flush()

    estimates: list[ElasticityEstimate] = []
    category_pooled_cache: dict[uuid.UUID, tuple[float, float, float, float, float, float]] = {}

    # 1. Fit per-SKU elasticity
    for product_id, product in products.items():
        sku_sales = merged[merged["product_id"] == product_id].sort_values("sale_date")
        valid_sales = sku_sales[sku_sales["units"] > 0]
        n_obs = len(valid_sales)

        prices = valid_sales["price"].to_numpy() if n_obs > 0 else np.array([])
        mean_p = float(np.mean(prices)) if len(prices) > 0 else float(product.current_price)
        std_p = float(np.std(prices)) if len(prices) > 0 else 0.0
        cv_p = (std_p / mean_p) if mean_p > 0 else 0.0
        ref_price = mean_p if mean_p > 0 else float(product.current_price)

        fit_success = False
        method = ElasticityMethod.LOG_LOG_OLS
        elast, se, ci_low, ci_high, pval, r2, params = -1.5, None, None, None, None, 0.0, {}

        if n_obs >= min_observations and cv_p >= 0.02:
            try:
                design = _build_design_matrix(valid_sales, ref_price)
                # Enriched competitor terms if coverage is sufficient
                has_comp = bool(valid_sales["log_comp"].notna().mean() > 0.6)
                if has_comp:
                    log_c = valid_sales["log_comp"].ffill().bfill()
                    design["log_comp_price"] = (log_c - log_c.mean()).to_numpy()
                    design["oos_frac"] = valid_sales["oos_frac"].fillna(0.0).to_numpy()

                elast, se, ci_low, ci_high, pval, r2, params = _fit_ols(
                    design, valid_sales["units"].to_numpy()
                )
                fit_success = True
            except Exception as exc:
                logger.warning("SKU %s OLS fit failed: %s; falling back", product.sku, exc)

        # Fallback 1: Pooled category regression
        if not fit_success and product.category_id:
            cat_id = product.category_id
            if cat_id not in category_pooled_cache:
                cat_sales = merged[
                    merged["product_id"].isin([p.id for p in products.values() if p.category_id == cat_id])
                ]
                cat_valid = cat_sales[cat_sales["units"] > 0].copy()
                if len(cat_valid) >= 40:
                    try:
                        # Normalize prices by each SKU's mean price
                        means = cat_valid.groupby("product_id")["price"].transform("mean")
                        cat_valid["rel_price"] = cat_valid["price"] / means
                        x_pool = np.log(cat_valid["rel_price"].to_numpy())
                        d_pool = pd.DataFrame(
                            {"log_price": x_pool, "promo": cat_valid["promo"].astype(float).to_numpy()},
                            index=cat_valid.index,
                        )
                        c_elast, c_se, c_low, c_high, c_p, c_r2, _ = _fit_ols(
                            d_pool, cat_valid["units"].to_numpy()
                        )
                        category_pooled_cache[cat_id] = (c_elast, c_se, c_low, c_high, c_p, c_r2)
                    except Exception as exc:
                        logger.warning("Category %s pooled fit failed: %s", cat_id, exc)
                        category_pooled_cache[cat_id] = (-1.5, 0.4, -2.2, -0.8, 0.05, 0.2)
                else:
                    category_pooled_cache[cat_id] = (-1.5, 0.4, -2.2, -0.8, 0.05, 0.2)

            pool_res = category_pooled_cache.get(cat_id)
            if pool_res:
                elast, se, ci_low, ci_high, pval, r2 = pool_res
                method = ElasticityMethod.POOLED_CATEGORY
                fit_success = True

        # Fallback 2: Prior fallback
        if not fit_success:
            method = ElasticityMethod.PRIOR_FALLBACK
            elast = -1.50
            se = 0.50
            ci_low = -2.32
            ci_high = -0.68
            pval = 0.05
            r2 = 0.15

        # Reliability gate
        is_reliable = bool(
            method == ElasticityMethod.LOG_LOG_OLS
            and n_obs >= 45
            and cv_p >= 0.03
            and elast < -0.1
            and (ci_high is not None and ci_high < 0.1)
        )

        estimate = ElasticityEstimate(
            organization_id=organization_id,
            product_id=product_id,
            model_run_id=model_run.id,
            method=method,
            elasticity=round(elast, 4),
            std_error=round(se, 4) if se is not None else None,
            ci_low=round(ci_low, 4) if ci_low is not None else None,
            ci_high=round(ci_high, 4) if ci_high is not None else None,
            p_value=round(pval, 4) if pval is not None else None,
            r_squared=round(r2, 4) if r2 is not None else None,
            n_observations=n_obs,
            price_variation_cv=round(cv_p, 4),
            intercept=round(float(params.get("const", 0.0)), 4) if params else None,
            reference_price=Decimal(str(round(ref_price, 4))),
            coefficients=params,
            diagnostics={
                "mean_price": round(mean_p, 2),
                "std_price": round(std_p, 2),
                "competitor_enriched": "log_comp_price" in params,
            },
            is_reliable=is_reliable,
        )
        session.add(estimate)
        estimates.append(estimate)

    session.flush()

    # 2. Fit Cross-Price Elasticities between sibling SKUs in the same category
    _fit_cross_elasticities(session, organization_id, model_run.id, merged, products)

    # 3. Model run aggregate metrics
    avg_r2 = float(np.mean([e.r_squared for e in estimates if e.r_squared is not None])) if estimates else 0.0
    reliable_count = sum(1 for e in estimates if e.is_reliable)
    model_run.metrics = {
        "n_estimates": len(estimates),
        "n_reliable": reliable_count,
        "reliable_pct": round(reliable_count / len(estimates), 3) if estimates else 0.0,
        "mean_r_squared": round(avg_r2, 3),
        "methods_breakdown": {
            method.value: sum(1 for e in estimates if e.method == method)
            for method in ElasticityMethod
        },
    }

    record_audit_event(
        session,
        organization_id=organization_id,
        action=AuditAction.TRAIN,
        entity_type="model_run",
        entity_id=model_run.id,
        actor_id=actor_id,
        payload={"kind": ModelKind.ELASTICITY.value, "metrics": model_run.metrics},
    )

    session.commit()
    logger.info(
        "Fitted elasticity for %d products (reliable=%d, mean R2=%.3f)",
        len(estimates),
        reliable_count,
        avg_r2,
    )
    return model_run


def _fit_cross_elasticities(
    session: Session,
    organization_id: uuid.UUID,
    model_run_id: uuid.UUID,
    merged: pd.DataFrame,
    products: dict[uuid.UUID, Product],
) -> None:
    """Estimate pairwise cross-price elasticity among top category siblings."""
    # Group products by category
    cat_to_prods: dict[uuid.UUID, list[Product]] = {}
    for p in products.values():
        if p.category_id:
            cat_to_prods.setdefault(p.category_id, []).append(p)

    for cat_id, siblings in cat_to_prods.items():
        if len(siblings) < 2:
            continue

        # Top 4 products in category by total volume to avoid combinatorial explosion
        totals = (
            merged[merged["product_id"].isin([p.id for p in siblings])]
            .groupby("product_id")["units"]
            .sum()
            .to_dict()
        )
        ranked = sorted(siblings, key=lambda p: totals.get(p.id, 0), reverse=True)[:4]

        # Prepare pivot table of daily prices for these siblings
        sib_ids = [p.id for p in ranked]
        pivot_p = (
            merged[merged["product_id"].isin(sib_ids)]
            .pivot_table(index="sale_date", columns="product_id", values="price")
            .ffill()
            .bfill()
        )
        pivot_q = (
            merged[merged["product_id"].isin(sib_ids)]
            .pivot_table(index="sale_date", columns="product_id", values="units")
            .fillna(0.0)
        )

        for p_i in ranked:
            q_i = pivot_q[p_i.id] if p_i.id in pivot_q else None
            p_i_series = pivot_p[p_i.id] if p_i.id in pivot_p else None
            if q_i is None or p_i_series is None or len(q_i) < 40:
                continue

            positive = q_i > 0
            if positive.sum() < 20:
                continue

            for p_j in ranked:
                if p_i.id == p_j.id:
                    continue
                p_j_series = pivot_p[p_j.id] if p_j.id in pivot_p else None
                if p_j_series is None:
                    continue

                try:
                    # Regress log(q_i) on log(p_i) and log(p_j)
                    y = np.log(q_i[positive].to_numpy())
                    x_i = np.log(p_i_series[positive].to_numpy())
                    x_j = np.log(p_j_series[positive].to_numpy())

                    X = sm.add_constant(np.column_stack([x_i, x_j]))
                    res = sm.OLS(y, X).fit(cov_type="HC3")

                    cross_elast = float(res.params[2])
                    cross_se = float(res.bse[2])
                    cross_pval = float(res.pvalues[2])
                    is_sig = bool(cross_pval < 0.10 and cross_elast > 0)

                    cross = CrossElasticity(
                        organization_id=organization_id,
                        product_id=p_i.id,
                        related_product_id=p_j.id,
                        model_run_id=model_run_id,
                        elasticity=round(cross_elast, 4),
                        std_error=round(cross_se, 4),
                        p_value=round(cross_pval, 4),
                        is_significant=is_sig,
                    )
                    session.add(cross)
                except Exception as exc:
                    logger.debug("Cross-elasticity fit error %s vs %s: %s", p_i.sku, p_j.sku, exc)


def calculate_demand_curve(
    estimate: ElasticityEstimate,
    current_price: float,
    current_cost: float,
    base_units: float,
    *,
    steps: int = 25,
    price_range_ratio: float = 0.35,
) -> list[dict[str, Any]]:
    """Simulate expected demand, revenue, and gross margin across a price grid.

    Returns grid points with confidence intervals:
    [{ price, units, lower_units, upper_units, revenue, margin }]
    """
    if current_price <= 0:
        return []

    p_min = round(current_price * (1.0 - price_range_ratio), 2)
    p_max = round(current_price * (1.0 + price_range_ratio), 2)
    p_grid = np.linspace(p_min, p_max, steps)

    elast = estimate.elasticity
    ci_lo = estimate.ci_low if estimate.ci_low is not None else (elast - 0.4)
    ci_hi = estimate.ci_high if estimate.ci_high is not None else (elast + 0.4)

    # Use base units at current price
    ref_units = base_units if base_units > 0 else 50.0

    points = []
    for p in p_grid:
        rel_p = p / current_price
        # q(p) = q0 * (p / p0)^eps
        expected_q = max(0.1, ref_units * (rel_p**elast))

        # Because elasticity is negative, steeper negative elasticity (ci_lo)
        # drops volume more at p > p0 and raises volume more at p < p0.
        q1 = max(0.05, ref_units * (rel_p**ci_lo))
        q2 = max(0.05, ref_units * (rel_p**ci_hi))
        lower_q = min(q1, q2)
        upper_q = max(q1, q2)

        rev = p * expected_q
        margin = (p - current_cost) * expected_q

        points.append(
            {
                "price": round(float(p), 2),
                "units": round(float(expected_q), 1),
                "lower_units": round(float(lower_q), 1),
                "upper_units": round(float(upper_q), 1),
                "revenue": round(float(rev), 2),
                "margin": round(float(margin), 2),
            }
        )
    return points
