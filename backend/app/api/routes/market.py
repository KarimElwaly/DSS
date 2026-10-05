"""Competitive intelligence endpoints (Module A read side)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import CurrentOrg, CurrentUser, DbSession, Pagination
from app.models.catalog import Product
from app.models.enums import AlertStatus, AuditAction, MatchStatus
from app.models.market import Alert, Competitor, CompetitorListing, PriceObservation
from app.schemas.common import Page
from app.schemas.market import (
    AlertOut,
    AlertStatusUpdate,
    CompetitorOut,
    CompetitorPricePoint,
    LatestObservation,
    ListingOut,
    MarketSnapshot,
    PriceObservationOut,
)
from app.services.audit import record_event
from app.services.market_view import load_market_positions

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/competitors", response_model=list[CompetitorOut])
def list_competitors(session: DbSession, org: CurrentOrg, _: CurrentUser) -> list[Competitor]:
    stmt = select(Competitor).where(Competitor.organization_id == org.id).order_by(Competitor.name)
    return list(session.scalars(stmt))


@router.get("/snapshot", response_model=Page[MarketSnapshot])
def market_snapshot(
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
    page: Pagination,
    search: str | None = Query(default=None, min_length=1, max_length=120),
) -> Page[MarketSnapshot]:
    """Our price versus the competitor index, for every matched SKU."""
    filters = [Product.organization_id == org.id]
    if search:
        pattern = f"%{search.strip()}%"
        filters.append(Product.name.ilike(pattern) | Product.sku.ilike(pattern))

    total = session.scalar(select(func.count()).select_from(Product).where(*filters)) or 0
    products = list(
        session.scalars(
            select(Product)
            .where(*filters)
            .order_by(Product.sku)
            .limit(page.limit)
            .offset(page.offset)
        )
    )
    positions = load_market_positions(session, org.id, [p.id for p in products])

    items: list[MarketSnapshot] = []
    for product in products:
        position = positions.get(product.id)
        our_price = float(product.current_price)
        cheapest = position.cheapest if position else None
        items.append(
            MarketSnapshot(
                product_id=product.id,
                sku=product.sku,
                name=product.name,
                our_price=our_price,
                competitor_price_index=position.price_index if position else None,
                cheapest_competitor_price=cheapest.landed_price if cheapest else None,
                price_gap_pct=position.price_gap_pct(our_price) if position else None,
                n_listings=position.n_listings if position else 0,
                n_out_of_stock=position.n_out_of_stock if position else 0,
                points=[
                    CompetitorPricePoint(
                        competitor_slug=p.competitor_slug,
                        competitor_name=p.competitor_name,
                        observed_at=p.observed_at,
                        price=p.price,
                        landed_price=p.landed_price,
                        stock_state=p.stock_state,
                        promo_flag=p.promo_flag,
                        url=p.url,
                    )
                    for p in (position.points if position else ())
                ],
            )
        )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/listings", response_model=Page[ListingOut])
def list_listings(
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
    page: Pagination,
    match_status: str | None = None,
    competitor_id: uuid.UUID | None = None,
) -> Page[ListingOut]:
    filters = [CompetitorListing.organization_id == org.id]
    if match_status:
        try:
            status_enum = MatchStatus(match_status.lower().strip())
            filters.append(CompetitorListing.match_status == status_enum)
        except ValueError:
            pass
    if competitor_id is not None:
        filters.append(CompetitorListing.competitor_id == competitor_id)

    total = session.scalar(select(func.count()).select_from(CompetitorListing).where(*filters)) or 0

    latest = (
        select(
            PriceObservation.listing_id,
            PriceObservation.observed_at,
            PriceObservation.price,
            PriceObservation.shipping_cost,
            PriceObservation.stock_state,
            PriceObservation.promo_flag,
        )
        .distinct(PriceObservation.listing_id)
        .order_by(PriceObservation.listing_id, PriceObservation.observed_at.desc())
        .subquery("latest")
    )

    stmt = (
        select(
            CompetitorListing,
            Competitor.name,
            Product.sku,
            latest.c.observed_at,
            latest.c.price,
            latest.c.shipping_cost,
            latest.c.stock_state,
            latest.c.promo_flag,
        )
        .join(Competitor, Competitor.id == CompetitorListing.competitor_id)
        .outerjoin(Product, Product.id == CompetitorListing.matched_product_id)
        .outerjoin(latest, latest.c.listing_id == CompetitorListing.id)
        .where(*filters)
        .order_by(CompetitorListing.title)
        .limit(page.limit)
        .offset(page.offset)
    )

    items: list[ListingOut] = []
    for (
        listing,
        competitor_name,
        sku,
        observed_at,
        price,
        shipping,
        stock,
        promo,
    ) in session.execute(stmt):
        out = ListingOut.model_validate(listing)
        out.competitor_name = competitor_name
        out.matched_product_sku = sku
        if observed_at is not None:
            out.latest = LatestObservation(
                price=float(price),
                shipping_cost=float(shipping),
                landed_price=float(price) + float(shipping),
                stock_state=stock,
                promo_flag=bool(promo),
                observed_at=observed_at,
            )
        items.append(out)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/listings/{listing_id}/history", response_model=list[PriceObservationOut])
def listing_history(
    listing_id: uuid.UUID,
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
    limit: int = Query(default=180, ge=1, le=1095),
) -> list[PriceObservation]:
    exists = session.scalar(
        select(func.count())
        .select_from(CompetitorListing)
        .where(CompetitorListing.id == listing_id, CompetitorListing.organization_id == org.id)
    )
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found.")

    stmt = (
        select(PriceObservation)
        .where(PriceObservation.listing_id == listing_id)
        .order_by(PriceObservation.observed_at.desc())
        .limit(limit)
    )
    return list(reversed(list(session.scalars(stmt))))


@router.get("/alerts", response_model=Page[AlertOut])
def list_alerts(
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
    page: Pagination,
    alert_status: AlertStatus | None = Query(default=AlertStatus.OPEN, alias="status"),
) -> Page[AlertOut]:
    filters = [Alert.organization_id == org.id]
    if alert_status is not None:
        filters.append(Alert.status == alert_status)

    total = session.scalar(select(func.count()).select_from(Alert).where(*filters)) or 0
    stmt = (
        select(Alert)
        .where(*filters)
        .order_by(Alert.created_at.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    items = [AlertOut.model_validate(a) for a in session.scalars(stmt)]
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/alerts/{alert_id}/status", response_model=AlertOut)
def update_alert_status(
    alert_id: uuid.UUID,
    payload: AlertStatusUpdate,
    session: DbSession,
    org: CurrentOrg,
    user: CurrentUser,
) -> Alert:
    """Acknowledge or resolve an alert.

    Resolving clears the dedupe key, so the same condition can raise a fresh
    alert next time it is detected rather than being suppressed forever.
    """
    alert = session.get(Alert, alert_id)
    if alert is None or alert.organization_id != org.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found.")

    alert.status = payload.status
    record_event(
        session,
        organization_id=org.id,
        action=AuditAction.UPDATE,
        entity_type="alert",
        entity_id=alert.id,
        summary=f"Alert marked {payload.status.value}",
        payload={"alert_type": alert.alert_type.value},
        actor_user_id=user.id,
        actor_label=user.email,
    )
    session.flush()
    return alert
