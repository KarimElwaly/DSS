"""Populate the database from a :class:`~simulator.market.GeneratedMarket`."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import hash_password
from app.models import (
    AdvisorReport,
    Alert,
    AuditEvent,
    Category,
    Competitor,
    CompetitorListing,
    CostHistory,
    CrossElasticity,
    ElasticityEstimate,
    FinancialFinding,
    FinancialLineItem,
    FinancialMetric,
    FinancialPeriod,
    ForecastPoint,
    GroundTruth,
    MatchCandidate,
    ModelRun,
    Organization,
    PriceObservation,
    PriceRecommendation,
    Product,
    SalesFact,
    SimulationRun,
    User,
)
from app.models.enums import (
    AuditAction,
    ConnectorType,
    MatchMethod,
    MatchStatus,
    StockState,
    UserRole,
)
from app.services.audit import record_event
from simulator import GeneratedMarket, MarketSimulator, default_electronics_market

logger = get_logger(__name__)

CHUNK = 5_000

#: Deleted in FK-safe order by ``reset_database``.
_PURGE_ORDER = (
    AuditEvent,
    AdvisorReport,
    FinancialFinding,
    FinancialMetric,
    FinancialLineItem,
    FinancialPeriod,
    SimulationRun,
    PriceRecommendation,
    ForecastPoint,
    CrossElasticity,
    ElasticityEstimate,
    ModelRun,
    Alert,
    PriceObservation,
    MatchCandidate,
    CompetitorListing,
    Competitor,
    SalesFact,
    CostHistory,
    GroundTruth,
    Product,
    Category,
    User,
    Organization,
)


def reset_database(session: Session) -> None:
    """Remove all rows while keeping the schema intact."""
    for model in _PURGE_ORDER:
        session.execute(delete(model))
    session.flush()


def _bulk_insert(session: Session, model: type, rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), CHUNK):
        session.execute(insert(model), rows[start : start + CHUNK])


def seed_market(
    session: Session,
    market: GeneratedMarket | None = None,
    *,
    reset: bool = True,
) -> dict[str, Any]:
    """Insert an entire synthetic market (catalog, history, competitors, truth)."""
    settings = get_settings()
    market = market or MarketSimulator(default_electronics_market()).generate()
    cfg = market.config

    if reset:
        reset_database(session)

    org = Organization(
        name=cfg.organization_name, slug=cfg.organization_slug, currency=cfg.currency
    )
    session.add(org)
    session.flush()

    admin = User(
        organization_id=org.id,
        email=settings.admin_email,
        full_name="DSS Administrator",
        password_hash=hash_password(settings.admin_password),
        role=UserRole.ADMIN,
        is_active=True,
    )
    session.add(admin)

    # -- categories ---------------------------------------------------------
    categories = {
        spec["slug"]: Category(organization_id=org.id, name=spec["name"], slug=spec["slug"])
        for spec in market.categories
    }
    session.add_all(list(categories.values()))
    session.flush()

    # -- products -----------------------------------------------------------
    products: dict[str, Product] = {}
    for row in market.products:
        product = Product(
            organization_id=org.id,
            category_id=categories[row["category_slug"]].id,
            sku=row["sku"],
            name=row["name"],
            brand=row["brand"],
            gtin=row["gtin"],
            description=row["description"],
            attributes=row["attributes"],
            current_price=Decimal(str(row["current_price"])),
            unit_cost=Decimal(str(row["unit_cost"])),
            msrp=Decimal(str(row["msrp"])),
            map_price=Decimal(str(row["map_price"])),
            min_margin_pct=Decimal(str(row["min_margin_pct"])),
            max_price_step_pct=Decimal(str(row["max_price_step_pct"])),
            launch_date=row["launch_date"],
        )
        products[row["sku"]] = product
    session.add_all(list(products.values()))
    session.flush()

    sku_to_id = {sku: product.id for sku, product in products.items()}

    # -- cost history -------------------------------------------------------
    # `to_dict("records")` rather than `itertuples`: the latter's fields are typed
    # as a wide union, which defeats static checking on every lookup below.
    cost_rows: list[dict[str, Any]] = [
        {
            "id": uuid.uuid4(),
            "product_id": sku_to_id[str(row["sku"])],
            "valid_from": row["valid_from"],
            "unit_cost": Decimal(str(row["unit_cost"])),
            "note": str(row["note"]),
        }
        for row in market.cost_history.to_dict("records")
    ]
    _bulk_insert(session, CostHistory, cost_rows)

    # -- sales facts --------------------------------------------------------
    sales_rows: list[dict[str, Any]] = [
        {
            "id": uuid.uuid4(),
            "organization_id": org.id,
            "product_id": sku_to_id[str(row["sku"])],
            "sale_date": row["sale_date"],
            "channel": str(row["channel"]),
            "units": int(row["units"]),
            "unit_price": Decimal(str(row["unit_price"])),
            "unit_cost": Decimal(str(row["unit_cost"])),
            "promo_flag": bool(row["promo_flag"]),
        }
        for row in market.sales.to_dict("records")
    ]
    _bulk_insert(session, SalesFact, sales_rows)

    # -- competitors + listings --------------------------------------------
    competitors = {
        row["slug"]: Competitor(
            organization_id=org.id,
            name=row["name"],
            slug=row["slug"],
            domain=row["domain"],
            connector_type=ConnectorType.SYNTHETIC,
            connector_config={"price_index": row["price_index"]},
        )
        for row in market.competitors
    }
    session.add_all(list(competitors.values()))
    session.flush()

    gtin_index = {row["gtin"]: row["sku"] for row in market.products if row["gtin"]}

    listings: dict[tuple[str, str], CompetitorListing] = {}
    for row in market.listings:
        listing = CompetitorListing(
            organization_id=org.id,
            competitor_id=competitors[row["competitor_slug"]].id,
            external_id=row["external_id"],
            title=row["title"],
            brand=row["brand"],
            gtin=row["gtin"],
            description=row["description"],
            url=row["url"],
            # The true link is kept out of matched_product_id on purpose: the
            # matching pipeline must discover it. It is stored for scoring only.
            match_explanation={"ground_truth_sku": row["true_product_sku"]},
        )
        # Module A step 1 -- exact GTIN short-circuit. Everything else stays
        # pending until the embedding pipeline runs.
        matched_sku = gtin_index.get(row["gtin"]) if row["gtin"] else None
        if matched_sku is not None:
            listing.matched_product_id = products[matched_sku].id
            listing.match_confidence = 1.0
            listing.match_method = MatchMethod.GTIN
            listing.match_status = MatchStatus.AUTO_ACCEPTED
        listings[(row["competitor_slug"], row["external_id"])] = listing
    session.add_all(list(listings.values()))
    session.flush()

    listing_ids = {key: listing.id for key, listing in listings.items()}

    # -- price observations -------------------------------------------------
    observation_rows: list[dict[str, Any]] = [
        {
            "id": uuid.uuid4(),
            "organization_id": org.id,
            "listing_id": listing_ids[(str(row["competitor_slug"]), str(row["external_id"]))],
            "observed_at": row["observed_at"],
            "price": Decimal(str(row["price"])),
            "shipping_cost": Decimal(str(row["shipping_cost"])),
            "currency": cfg.currency,
            "stock_state": StockState(str(row["stock_state"])),
            "promo_flag": bool(row["promo_flag"]),
            "is_outlier": False,
            "raw_payload": {},
        }
        for row in market.observations.to_dict("records")
    ]
    _bulk_insert(session, PriceObservation, observation_rows)

    # -- ground truth (validation only) -------------------------------------
    truth_rows = [
        {
            "id": uuid.uuid4(),
            "organization_id": org.id,
            "product_id": sku_to_id[row["sku"]],
            "generator_version": row["generator_version"],
            "seed": row["seed"],
            "own_elasticity": row["own_elasticity"],
            "competitor_cross_elasticity": row["competitor_cross_elasticity"],
            "base_demand": row["base_demand"],
            "reference_price": row["reference_price"],
            "promo_lift": row["promo_lift"],
            "trend_per_year": row["trend_per_year"],
            "seasonal_amplitude": row["seasonal_amplitude"],
            "noise_sigma": row["noise_sigma"],
            "sibling_cross_elasticities": row["sibling_cross_elasticities"],
            "params": row["params"],
        }
        for row in market.ground_truth
    ]
    _bulk_insert(session, GroundTruth, truth_rows)

    summary = market.summary()
    record_event(
        session,
        organization_id=org.id,
        action=AuditAction.INGEST,
        entity_type="organization",
        entity_id=org.id,
        summary=f"Seeded synthetic market '{cfg.organization_name}'",
        payload={"seed": cfg.seed, **summary},
        actor_label="cli:seed",
    )

    logger.info("Seeded organization %s: %s", org.slug, summary)
    return {"organization_id": str(org.id), "admin_email": admin.email, **summary}


def get_default_organization(session: Session) -> Organization:
    """MVP helper: the single tenant. Replace with request-scoped tenancy later."""
    org = session.scalars(select(Organization).order_by(Organization.created_at)).first()
    if org is None:
        raise LookupError("No organization found. Run `dss seed` first.")
    return org
