"""Scenario Sandbox with Monte Carlo parameter sampling (Module D).

Simulates what-if pricing changes across the portfolio, drawing elasticity
parameters from econometric confidence intervals to project revenue and margin
distributions across percentiles (5th, 50th, 95th).
"""

from __future__ import annotations

import uuid
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import (
    CrossElasticity,
    ElasticityEstimate,
    ModelRun,
    Product,
    SalesFact,
    SimulationRun,
)
from app.models.enums import AuditAction, ModelKind
from app.services.audit import record_audit_event

logger = get_logger(__name__)


def run_scenario_simulation(
    session: Session,
    organization_id: uuid.UUID,
    *,
    name: str = "What-if Pricing Scenario",
    horizon_days: int = 30,
    price_overrides: dict[uuid.UUID, float],  # product_id -> new_price
    competitor_reaction_pct: float = 0.30,  # 0.0 = no reaction, 1.0 = full price matching
    n_monte_carlo: int = 500,
    actor_id: uuid.UUID | None = None,
) -> SimulationRun:
    """Execute a Monte Carlo what-if pricing simulation across the catalog."""
    products = {
        p.id: p
        for p in session.scalars(
            select(Product).where(Product.organization_id == organization_id)
        )
    }
    if not products:
        raise ValueError("No products found in organization.")

    # 1. Latest champion elasticity estimates
    champion_run = session.scalar(
        select(ModelRun)
        .where(
            ModelRun.organization_id == organization_id,
            ModelRun.kind == ModelKind.ELASTICITY,
            ModelRun.is_champion.is_(True),
        )
        .order_by(ModelRun.created_at.desc())
        .limit(1)
    )

    estimates_by_prod: dict[uuid.UUID, ElasticityEstimate] = {}
    if champion_run:
        for e in session.scalars(
            select(ElasticityEstimate).where(ElasticityEstimate.model_run_id == champion_run.id)
        ):
            estimates_by_prod[e.product_id] = e

    # 2. Sibling cross elasticities
    cross_by_prod: dict[uuid.UUID, list[CrossElasticity]] = {}
    if champion_run:
        for c in session.scalars(
            select(CrossElasticity).where(CrossElasticity.model_run_id == champion_run.id)
        ):
            cross_by_prod.setdefault(c.product_id, []).append(c)

    # 3. Baseline daily volume (average across historical facts)
    sales_avg = session.execute(
        select(SalesFact.product_id, func.avg(SalesFact.units))
        .where(
            SalesFact.organization_id == organization_id,
            SalesFact.units > 0,
        )
        .group_by(SalesFact.product_id)
    ).all()
    avg_daily_v = {row[0]: float(row[1]) for row in sales_avg}

    # 4. Compute baseline portfolio performance over horizon_days
    base_rev_daily = 0.0
    base_margin_daily = 0.0
    base_units_daily = 0.0

    sku_baselines: dict[uuid.UUID, dict[str, float]] = {}
    for p_id, p in products.items():
        p0 = float(p.current_price)
        cost = float(p.unit_cost)
        q0 = avg_daily_v.get(p_id, 35.0)

        daily_r = p0 * q0
        daily_m = (p0 - cost) * q0

        base_rev_daily += daily_r
        base_margin_daily += daily_m
        base_units_daily += q0

        sku_baselines[p_id] = {
            "p0": p0,
            "cost": cost,
            "q0": q0,
            "daily_revenue": daily_r,
            "daily_margin": daily_m,
        }

    # 5. Monte Carlo parameter sampling and scenario simulation
    mc_revenues = np.zeros(n_monte_carlo)
    mc_margins = np.zeros(n_monte_carlo)
    mc_units = np.zeros(n_monte_carlo)

    # Pre-generate random normals for elasticity uncertainty and noise
    rng = np.random.default_rng(2026)

    # Per-SKU expected simulated outcome trackers
    sku_expected_sim: dict[uuid.UUID, dict[str, float]] = {}

    for p_id, p in products.items():
        base = sku_baselines[p_id]
        p0 = base["p0"]
        cost = base["cost"]
        q0 = base["q0"]

        new_p = float(price_overrides.get(p_id, p0))
        rel_p = max(0.01, new_p / p0)

        est = estimates_by_prod.get(p_id)
        mu_elast = est.elasticity if est else -1.50
        sigma_elast = float(est.std_error) if (est and est.std_error) else 0.35

        # Monte Carlo draws for this product's elasticity
        drawn_elast = rng.normal(mu_elast, max(0.05, sigma_elast), n_monte_carlo)
        # Ensure elasticity stays non-positive
        drawn_elast = np.clip(drawn_elast, -4.5, -0.05)

        # Competitor reaction effect: if competitors react, price gap narrows,
        # effectively dampening the volume loss from price increases or volume gain from price cuts.
        effective_delta_rel = np.log(rel_p) * (1.0 - 0.40 * competitor_reaction_pct)

        # Multiplicative volume noise
        noise = rng.normal(0.0, 0.04, n_monte_carlo)

        sim_q = q0 * np.exp(drawn_elast * effective_delta_rel + noise)
        sim_q = np.clip(sim_q, 0.1, None)

        sim_rev = new_p * sim_q
        sim_margin = (new_p - cost) * sim_q

        mc_revenues += sim_rev * horizon_days
        mc_margins += sim_margin * horizon_days
        mc_units += sim_q * horizon_days

        exp_q = float(np.mean(sim_q))
        sku_expected_sim[p_id] = {
            "new_price": new_p,
            "price_delta_pct": (new_p - p0) / p0,
            "exp_units": exp_q,
            "units_delta_pct": (exp_q - q0) / q0,
            "exp_revenue": new_p * exp_q * horizon_days,
            "exp_margin": (new_p - cost) * exp_q * horizon_days,
        }

    # Sibling cannibalization adjustment
    for p_id, ct_list in cross_by_prod.items():
        new_p_i = float(price_overrides.get(p_id, sku_baselines[p_id]["p0"]))
        p0_i = sku_baselines[p_id]["p0"]
        if abs(new_p_i - p0_i) < 0.01:
            continue

        log_p_ratio = float(np.log(new_p_i / p0_i))
        for ct in ct_list:
            if ct.is_significant and ct.elasticity > 0 and ct.related_product_id in sku_baselines:
                sib = sku_baselines[ct.related_product_id]
                sib_margin = sib["p0"] - sib["cost"]
                cannibalization_loss = sib_margin * sib["q0"] * ct.elasticity * log_p_ratio * horizon_days
                mc_margins += cannibalization_loss

    # 6. Quantile summaries
    total_base_rev = base_rev_daily * horizon_days
    total_base_margin = base_margin_daily * horizon_days
    total_base_units = base_units_daily * horizon_days

    quantiles = {
        "revenue": {
            "p05": round(float(np.percentile(mc_revenues, 5)), 2),
            "p25": round(float(np.percentile(mc_revenues, 25)), 2),
            "p50": round(float(np.percentile(mc_revenues, 50)), 2),
            "p75": round(float(np.percentile(mc_revenues, 75)), 2),
            "p95": round(float(np.percentile(mc_revenues, 95)), 2),
        },
        "margin": {
            "p05": round(float(np.percentile(mc_margins, 5)), 2),
            "p25": round(float(np.percentile(mc_margins, 25)), 2),
            "p50": round(float(np.percentile(mc_margins, 50)), 2),
            "p75": round(float(np.percentile(mc_margins, 75)), 2),
            "p95": round(float(np.percentile(mc_margins, 95)), 2),
        },
        "units": {
            "p05": round(float(np.percentile(mc_units, 5)), 1),
            "p50": round(float(np.percentile(mc_units, 50)), 1),
            "p95": round(float(np.percentile(mc_units, 95)), 1),
        },
    }

    median_rev = quantiles["revenue"]["p50"]
    median_margin = quantiles["margin"]["p50"]
    median_units = quantiles["units"]["p50"]

    results = {
        "baseline": {
            "revenue": round(total_base_rev, 2),
            "margin": round(total_base_margin, 2),
            "units": round(total_base_units, 1),
            "margin_rate": round(total_base_margin / total_base_rev, 4) if total_base_rev > 0 else 0.0,
        },
        "simulated": {
            "revenue": median_rev,
            "margin": median_margin,
            "units": median_units,
            "margin_rate": round(median_margin / median_rev, 4) if median_rev > 0 else 0.0,
        },
        "delta": {
            "revenue_abs": round(median_rev - total_base_rev, 2),
            "revenue_pct": round((median_rev - total_base_rev) / total_base_rev, 4) if total_base_rev > 0 else 0.0,
            "margin_abs": round(median_margin - total_base_margin, 2),
            "margin_pct": round((median_margin - total_base_margin) / total_base_margin, 4) if total_base_margin > 0 else 0.0,
            "units_abs": round(median_units - total_base_units, 1),
            "units_pct": round((median_units - total_base_units) / total_base_units, 4) if total_base_units > 0 else 0.0,
        },
        "quantiles": quantiles,
        "sku_impacts": [
            {
                "product_id": str(p_id),
                "sku": products[p_id].sku,
                "name": products[p_id].name,
                "current_price": round(sku_baselines[p_id]["p0"], 2),
                "new_price": round(sku_expected_sim[p_id]["new_price"], 2),
                "price_delta_pct": round(sku_expected_sim[p_id]["price_delta_pct"], 4),
                "units_delta_pct": round(sku_expected_sim[p_id]["units_delta_pct"], 4),
                "expected_revenue": round(sku_expected_sim[p_id]["exp_revenue"], 2),
                "expected_margin": round(sku_expected_sim[p_id]["exp_margin"], 2),
            }
            for p_id in products
            if p_id in price_overrides  # Focus on changed SKUs or list all
        ],
    }

    scenario_definition = {
        "price_overrides_count": len(price_overrides),
        "competitor_reaction_pct": competitor_reaction_pct,
        "horizon_days": horizon_days,
        "overrides": {str(k): v for k, v in price_overrides.items()},
    }

    run = SimulationRun(
        organization_id=organization_id,
        created_by_id=actor_id,
        name=name,
        horizon_days=horizon_days,
        scenario=scenario_definition,
        results=results,
        n_monte_carlo=n_monte_carlo,
    )
    session.add(run)
    session.flush()

    record_audit_event(
        session,
        organization_id=organization_id,
        action=AuditAction.SIMULATE,
        entity_type="simulation_run",
        entity_id=run.id,
        actor_id=actor_id,
        payload={"name": name, "n_monte_carlo": n_monte_carlo, "delta": results["delta"]},
    )

    session.commit()
    logger.info(
        "Simulation '%s' finished: Margin Δ=%+.2f%%, Revenue Δ=%+.2f%%",
        name,
        results["delta"]["margin_pct"] * 100,
        results["delta"]["revenue_pct"] * 100,
    )
    return run
