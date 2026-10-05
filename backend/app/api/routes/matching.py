"""Match review queue and ingestion triggers (Module A, write side).

The review queue is the human-in-the-loop half of matching. The pipeline is
tuned for precision, so it defers anything it is not sure about rather than
guessing; these endpoints are how those deferrals get resolved.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import (
    CurrentOrg,
    CurrentUser,
    DbSession,
    Pagination,
    RequireAnalyst,
    RequirePricingManager,
)
from app.models.catalog import Product
from app.models.enums import AuditAction, MatchMethod, MatchStatus
from app.models.market import Competitor, CompetitorListing, MatchCandidate, PriceObservation
from app.schemas.common import Page
from app.schemas.market import LatestObservation
from app.schemas.matching import (
    CandidateOut,
    IngestResponse,
    MatchDecision,
    MatchRunRequest,
    MatchRunResponse,
    ReviewItem,
)
from app.services.audit import record_event
from app.services.ingestion import run_ingestion
from app.services.matching import run_matching

router = APIRouter(prefix="/matching", tags=["matching"])

#: Statuses that still need a person to look at them.
_REVIEWABLE = (MatchStatus.NEEDS_REVIEW, MatchStatus.PENDING)


@router.get("/review", response_model=Page[ReviewItem])
def review_queue(
    session: DbSession,
    org: CurrentOrg,
    _: CurrentUser,
    page: Pagination,
    match_status: str | None = Query(
        default=None, description="Defaults to everything still awaiting a decision."
    ),
) -> Page[ReviewItem]:
    status_enum = None
    if match_status:
        try:
            status_enum = MatchStatus(match_status.lower().strip())
        except ValueError:
            pass
    statuses = [status_enum] if status_enum else list(_REVIEWABLE)
    filters = [
        CompetitorListing.organization_id == org.id,
        CompetitorListing.match_status.in_(statuses),
    ]

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

    rows = session.execute(
        select(
            CompetitorListing,
            Competitor.name,
            latest.c.observed_at,
            latest.c.price,
            latest.c.shipping_cost,
            latest.c.stock_state,
            latest.c.promo_flag,
        )
        .join(Competitor, Competitor.id == CompetitorListing.competitor_id)
        .outerjoin(latest, latest.c.listing_id == CompetitorListing.id)
        .where(*filters)
        # Highest confidence first: those are the quickest decisions, so the
        # reviewer clears the easy ones before the genuinely ambiguous tail.
        .order_by(CompetitorListing.match_confidence.desc().nullslast())
        .limit(page.limit)
        .offset(page.offset)
    ).all()

    listing_ids = [row[0].id for row in rows]
    candidates = _load_candidates(session, listing_ids)

    items = [
        ReviewItem(
            listing_id=listing.id,
            competitor_name=competitor_name,
            external_id=listing.external_id,
            title=listing.title,
            brand=listing.brand,
            gtin=listing.gtin,
            url=listing.url,
            match_status=listing.match_status,
            match_method=listing.match_method,
            match_confidence=listing.match_confidence,
            reason=str((listing.match_explanation or {}).get("reason", "")),
            latest=(
                LatestObservation(
                    price=float(price),
                    shipping_cost=float(shipping),
                    landed_price=float(price) + float(shipping),
                    stock_state=stock,
                    promo_flag=bool(promo),
                    observed_at=observed_at,
                )
                if observed_at is not None
                else None
            ),
            candidates=candidates.get(listing.id, []),
        )
        for listing, competitor_name, observed_at, price, shipping, stock, promo in rows
    ]
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


def _load_candidates(
    session: DbSession, listing_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[CandidateOut]]:
    """Ranked suggestions for a page of listings, in one query."""
    if not listing_ids:
        return {}
    rows = session.execute(
        select(MatchCandidate, Product)
        .join(Product, Product.id == MatchCandidate.product_id)
        .where(MatchCandidate.listing_id.in_(listing_ids))
        .order_by(MatchCandidate.listing_id, MatchCandidate.rank)
    ).all()

    out: dict[uuid.UUID, list[CandidateOut]] = {}
    for candidate, product in rows:
        out.setdefault(candidate.listing_id, []).append(
            CandidateOut(
                product_id=product.id,
                sku=product.sku,
                name=product.name,
                our_price=float(product.current_price),
                score=candidate.score,
                vector_score=candidate.vector_score,
                fuzzy_score=candidate.fuzzy_score,
                rank=candidate.rank,
            )
        )
    return out


def _get_listing(session: DbSession, org_id: uuid.UUID, listing_id: uuid.UUID) -> CompetitorListing:
    listing = session.get(CompetitorListing, listing_id)
    if listing is None or listing.organization_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found.")
    return listing


@router.post("/review/{listing_id}/confirm", response_model=ReviewItem)
def confirm_match(
    listing_id: uuid.UUID,
    decision: MatchDecision,
    session: DbSession,
    org: CurrentOrg,
    user: RequireAnalyst,
) -> ReviewItem:
    """Link a listing to a product. The decision is final for the matcher."""
    if decision.product_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="product_id is required to confirm a match.",
        )
    listing = _get_listing(session, org.id, listing_id)
    product = session.get(Product, decision.product_id)
    if product is None or product.organization_id != org.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")

    listing.matched_product_id = product.id
    listing.match_status = MatchStatus.CONFIRMED
    listing.match_method = MatchMethod.MANUAL
    listing.match_confidence = 1.0
    explanation = dict(listing.match_explanation or {})
    explanation["reason"] = f"confirmed by {user.email}"
    if decision.note:
        explanation["note"] = decision.note
    listing.match_explanation = explanation

    record_event(
        session,
        organization_id=org.id,
        action=AuditAction.APPROVE,
        entity_type="competitor_listing",
        entity_id=listing.id,
        summary=f"Matched '{listing.title}' to {product.sku}",
        payload={"product_id": str(product.id), "note": decision.note},
        actor_user_id=user.id,
        actor_label=user.email,
    )
    session.flush()
    return _single_review_item(session, listing)


@router.post("/review/{listing_id}/reject", response_model=ReviewItem)
def reject_match(
    listing_id: uuid.UUID,
    decision: MatchDecision,
    session: DbSession,
    org: CurrentOrg,
    user: RequireAnalyst,
) -> ReviewItem:
    """Mark a listing as not one of our products.

    This is the correct outcome for a competitor's own-brand or unstocked
    variant, and it keeps the listing out of the price index permanently.
    """
    if decision.product_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="product_id must be omitted when rejecting a match.",
        )
    listing = _get_listing(session, org.id, listing_id)
    listing.matched_product_id = None
    listing.match_status = MatchStatus.REJECTED
    listing.match_method = MatchMethod.MANUAL
    listing.match_confidence = None
    explanation = dict(listing.match_explanation or {})
    explanation["reason"] = f"rejected by {user.email}"
    if decision.note:
        explanation["note"] = decision.note
    listing.match_explanation = explanation

    record_event(
        session,
        organization_id=org.id,
        action=AuditAction.REJECT,
        entity_type="competitor_listing",
        entity_id=listing.id,
        summary=f"Rejected '{listing.title}' as a catalog match",
        payload={"note": decision.note},
        actor_user_id=user.id,
        actor_label=user.email,
    )
    session.flush()
    return _single_review_item(session, listing)


def _single_review_item(session: DbSession, listing: CompetitorListing) -> ReviewItem:
    competitor_name = session.scalar(
        select(Competitor.name).where(Competitor.id == listing.competitor_id)
    )
    return ReviewItem(
        listing_id=listing.id,
        competitor_name=competitor_name or "",
        external_id=listing.external_id,
        title=listing.title,
        brand=listing.brand,
        gtin=listing.gtin,
        url=listing.url,
        match_status=listing.match_status,
        match_method=listing.match_method,
        match_confidence=listing.match_confidence,
        reason=str((listing.match_explanation or {}).get("reason", "")),
        candidates=_load_candidates(session, [listing.id]).get(listing.id, []),
    )


@router.post("/run", response_model=MatchRunResponse)
def run_matcher(
    request: MatchRunRequest,
    session: DbSession,
    org: CurrentOrg,
    user: RequirePricingManager,
) -> MatchRunResponse:
    """Re-embed the catalog and re-score listings."""
    report = run_matching(session, org.id, only_unmatched=request.only_unmatched)
    record_event(
        session,
        organization_id=org.id,
        action=AuditAction.TRAIN,
        entity_type="matching",
        summary="Re-ran the matching pipeline",
        payload=report.as_dict(),
        actor_user_id=user.id,
        actor_label=user.email,
    )
    return MatchRunResponse(**report.as_dict())


@router.post("/ingest", response_model=IngestResponse)
def trigger_ingestion(
    session: DbSession,
    org: CurrentOrg,
    user: RequirePricingManager,
) -> IngestResponse:
    """Poll every active competitor feed now, then match and alert."""
    now = dt.datetime.now(dt.UTC)
    report = run_ingestion(session, org.id, now=now)
    if report.failed:
        # Partial success is the norm with external feeds, so this is reported
        # in the body rather than raised: the feeds that worked are persisted.
        for failure in report.failed:
            record_event(
                session,
                organization_id=org.id,
                action=AuditAction.INGEST,
                entity_type="competitor",
                summary=f"Feed failed for {failure.competitor_slug}",
                payload={"error": failure.error},
                actor_user_id=user.id,
                actor_label=user.email,
            )
    return IngestResponse(
        competitors=[row.as_dict() for row in report.competitors],
        new_listings=sum(row.new_listings for row in report.competitors),
        new_observations=sum(row.new_observations for row in report.competitors),
        failed=len(report.failed),
        matched=report.matched,
        alerts=report.alerts,
        ran_at=now,
    )
