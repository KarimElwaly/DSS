"""Dynamic pricing optimization engine (Module C).

Evaluates profit, revenue, and penetration objective curves across price grids,
incorporates cannibalization penalties from cross-elasticity estimates,
and passes candidate prices through the guardrail enforcement layer.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import (
    CrossElasticity,
    ElasticityEstimate,
    ModelRun,
    PriceRecommendation,
    Product,
    SalesFact,
)
from app.models.enums import (
    AuditAction,
    ModelKind,
    PricingObjective,
    RecommendationStatus,
)
from app.services.audit import record_audit_event
from app.services.market_view import load_market_positions
from app.services.optimization.guardrails import apply_guardrails

logger = get_logger(__name__)


def _evaluate_candidate(
    p: float,
    p0: float,
    cost: float,
    q0: float,
    elast: float,
    siblings: list[tuple[float, float, float]],  # (sibling_margin, sibling_q0, cross_elast)
    objective: PricingObjective,
) -> float:
    """Evaluate objective value for candidate price p.

    Higher returned value is always better (maximization).
    """
    if p <= cost:
        return -1e9

    rel_p = max(0.01, p / p0)
    q = max(0.05, q0 * (rel_p**elast))

    # Cannibalization loss: how much margin do we take from siblings if we change price?
    # If cross_elast > 0 (substitutes), lowering p (ln(rel_p) < 0) reduces sibling sales.
    log_rel = float(np.log(rel_p))
    cannibalization_margin_impact = 0.0
    for sib_margin, sib_q0, kappa in siblings:
        cannibalization_margin_impact += sib_margin * sib_q0 * kappa * log_rel

    if objective == PricingObjective.MARGIN:
        net_margin = (p - cost) * q + cannibalization_margin_impact
        return float(net_margin)

    elif objective == PricingObjective.REVENUE:
        revenue = p * q
        return float(revenue)

    elif objective == PricingObjective.PENETRATION:
        # Maximize volume subject to not losing money
        unit_margin = (p - cost) / p
        if unit_margin < 0.05:
            return -1e9
        return float(q)

    return float((p - cost) * q)


def optimize_sku_price(
    product: Product,
    estimate: ElasticityEstimate | None,
    cross_terms: list[CrossElasticity],
    siblings_by_id: dict[uuid.UUID, Product],
    competitor_index: Decimal | None,
    baseline_units: float,
    objective: PricingObjective,
    *,
    apply_charm_rounding: bool = True,
) -> PriceRecommendation:
    """Run optimization for a single SKU under an explicit pricing objective."""
    p0 = float(product.current_price)
    cost = float(product.unit_cost)
    q0 = baseline_units if baseline_units > 0 else 40.0
    elast = estimate.elasticity if estimate else -1.50
    ci_low = estimate.ci_low if estimate else (elast - 0.4)
    ci_high = estimate.ci_high if estimate else (elast + 0.4)
    confidence = estimate.r_squared if (estimate and estimate.r_squared) else 0.50

    # Build sibling cross-impact parameters
    sibling_params = []
    for ct in cross_terms:
        sib = siblings_by_id.get(ct.related_product_id)
        if sib and ct.is_significant and ct.elasticity > 0:
            sib_margin = float(sib.current_price - sib.unit_cost)
            sibling_params.append((sib_margin, q0 * 0.8, ct.elasticity))

    # Evaluate over a fine price grid: [0.70 * p0, 1.40 * p0]
    p_grid = np.linspace(max(cost * 1.02, p0 * 0.65), p0 * 1.45, 161)
    scores = [
        _evaluate_candidate(p, p0, cost, q0, elast, sibling_params, objective)
        for p in p_grid
    ]
    best_idx = int(np.argmax(scores))
    unconstrained_p = Decimal(str(round(float(p_grid[best_idx]), 2)))

    # Apply the guardrail layer
    guardrail_res = apply_guardrails(
        unconstrained_price=unconstrained_p,
        current_price=product.current_price,
        unit_cost=product.unit_cost,
        min_margin_pct=product.min_margin_pct,
        max_price_step_pct=product.max_price_step_pct,
        price_floor_override=product.price_floor_override,
        price_ceiling_override=product.price_ceiling_override,
        competitor_index=competitor_index,
        apply_charm_rounding=apply_charm_rounding,
    )

    final_p = float(guardrail_res.final_price)

    # Calculate expected outcomes
    rel_p_final = max(0.01, final_p / p0)
    exp_units = max(0.1, q0 * (rel_p_final**elast))
    exp_rev = final_p * exp_units
    exp_margin = (final_p - cost) * exp_units

    base_rev = p0 * q0
    base_margin = (p0 - cost) * q0

    rationale = {
        "objective": objective.value,
        "elasticity_used": round(elast, 3),
        "cannibalization_siblings_count": len(sibling_params),
        "competitor_index": float(competitor_index) if competitor_index else None,
        "delta_price_pct": round((final_p - p0) / p0, 4),
        "delta_volume_pct": round((exp_units - q0) / q0, 4),
        "delta_margin_pct": round((exp_margin - base_margin) / base_margin, 4) if base_margin > 0 else 0.0,
    }

    return PriceRecommendation(
        organization_id=product.organization_id,
        product_id=product.id,
        model_run_id=estimate.model_run_id if estimate else None,
        objective=objective,
        current_price=product.current_price,
        recommended_price=guardrail_res.final_price,
        unconstrained_price=guardrail_res.unconstrained_price,
        price_floor=guardrail_res.price_floor,
        price_ceiling=guardrail_res.price_ceiling,
        expected_units=round(exp_units, 2),
        expected_revenue=round(exp_rev, 2),
        expected_margin=round(exp_margin, 2),
        baseline_revenue=round(base_rev, 2),
        baseline_margin=round(base_margin, 2),
        elasticity_used=round(elast, 3),
        elasticity_ci_low=round(ci_low, 3) if ci_low is not None else None,
        elasticity_ci_high=round(ci_high, 3) if ci_high is not None else None,
        confidence=round(confidence, 3),
        binding_constraints=guardrail_res.binding_constraints,
        rationale=rationale,
        explanation=guardrail_res.explanation,
        status=RecommendationStatus.PROPOSED,
        valid_until=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=14),
    )


def generate_recommendations(
    session: Session,
    organization_id: uuid.UUID,
    *,
    objective: PricingObjective = PricingObjective.MARGIN,
    product_ids: list[uuid.UUID] | None = None,
    apply_charm_rounding: bool = True,
    actor_id: uuid.UUID | None = None,
) -> list[PriceRecommendation]:
    """Generate recommendations for catalog products and persist in DB."""
    # 1. Load products
    query = select(Product).where(Product.organization_id == organization_id)
    if product_ids:
        query = query.where(Product.id.in_(product_ids))
    products = list(session.scalars(query))
    if not products:
        return []

    # 2. Latest champion elasticity estimates
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
        ests = session.scalars(
            select(ElasticityEstimate).where(ElasticityEstimate.model_run_id == champion_run.id)
        )
        for e in ests:
            estimates_by_prod[e.product_id] = e

    # 3. Cross elasticity terms
    cross_by_prod: dict[uuid.UUID, list[CrossElasticity]] = {}
    if champion_run:
        cross_rows = session.scalars(
            select(CrossElasticity).where(CrossElasticity.model_run_id == champion_run.id)
        )
        for c in cross_rows:
            cross_by_prod.setdefault(c.product_id, []).append(c)

    # 4. Competitor market positions
    positions = load_market_positions(session, organization_id)

    # 5. Baseline volumes (90-day average)
    avg_sales_rows = session.execute(
        select(SalesFact.product_id, func.avg(SalesFact.units))
        .where(
            SalesFact.organization_id == organization_id,
            SalesFact.units > 0,
        )
        .group_by(SalesFact.product_id)
    ).all()
    avg_volume = {row[0]: float(row[1]) for row in avg_sales_rows}

    prods_by_id = {p.id: p for p in products}
    recommendations: list[PriceRecommendation] = []

    for product in products:
        est = estimates_by_prod.get(product.id)
        cross_terms = cross_by_prod.get(product.id, [])
        pos = positions.get(product.id)
        comp_raw = (pos.price_index if hasattr(pos, "price_index") else pos.competitor_price_index) if pos else None
        comp_idx = Decimal(str(round(comp_raw, 2))) if comp_raw is not None else None
        base_v = avg_volume.get(product.id, 45.0)

        rec = optimize_sku_price(
            product=product,
            estimate=est,
            cross_terms=cross_terms,
            siblings_by_id=prods_by_id,
            competitor_index=comp_idx,
            baseline_units=base_v,
            objective=objective,
            apply_charm_rounding=apply_charm_rounding,
        )
        session.add(rec)
        recommendations.append(rec)

    session.flush()

    record_audit_event(
        session,
        organization_id=organization_id,
        action=AuditAction.CREATE,
        entity_type="price_recommendation_batch",
        entity_id=uuid.uuid4(),
        actor_id=actor_id,
        payload={
            "n_recommendations": len(recommendations),
            "objective": objective.value,
        },
    )

    session.commit()
    logger.info(
        "Generated %d price recommendations (objective=%s)",
        len(recommendations),
        objective.value,
    )
    return recommendations


def decide_recommendation(
    session: Session,
    recommendation_id: uuid.UUID,
    organization_id: uuid.UUID,
    decision: RecommendationStatus,
    *,
    actor_id: uuid.UUID | None = None,
    decision_note: str = "",
) -> PriceRecommendation:
    """Approve, reject, or apply a price recommendation."""
    rec = session.scalar(
        select(PriceRecommendation).where(
            PriceRecommendation.id == recommendation_id,
            PriceRecommendation.organization_id == organization_id,
        )
    )
    if not rec:
        raise LookupError(f"Recommendation {recommendation_id} not found.")

    rec.status = decision
    rec.decided_by_id = actor_id
    rec.decided_at = dt.datetime.now(dt.timezone.utc)
    rec.decision_note = decision_note

    audit_action = AuditAction.APPROVE if decision == RecommendationStatus.APPROVED else (
        AuditAction.APPLY if decision == RecommendationStatus.APPLIED else AuditAction.REJECT
    )

    # If APPLIED, update the actual product price
    if decision == RecommendationStatus.APPLIED:
        prod = session.scalar(select(Product).where(Product.id == rec.product_id))
        if prod:
            prod.current_price = rec.recommended_price

    record_audit_event(
        session,
        organization_id=organization_id,
        action=audit_action,
        entity_type="price_recommendation",
        entity_id=rec.id,
        actor_id=actor_id,
        payload={
            "product_id": str(rec.product_id),
            "old_price": float(rec.current_price),
            "new_price": float(rec.recommended_price),
            "status": decision.value,
            "note": decision_note,
        },
    )

    session.commit()
    return rec
