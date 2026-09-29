"""Ingestion pipeline: connector -> listings -> observations -> matching -> alerts.

One transaction per competitor. A site that changes its markup, or a
malformed spreadsheet, should cost us that competitor's refresh and nothing
else -- a single failing feed must not roll back the ones that worked.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.enums import AuditAction, MatchStatus
from app.models.market import Competitor, CompetitorListing, PriceObservation
from app.services.alerts import AlertReport, evaluate_alerts
from app.services.audit import record_event
from app.services.connectors import ConnectorError, ConnectorResult, build_connector
from app.services.matching import ListingMatcher

logger = get_logger(__name__)


@dataclass
class CompetitorIngestResult:
    competitor_slug: str
    new_listings: int = 0
    new_observations: int = 0
    duplicate_observations: int = 0
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "competitor": self.competitor_slug,
            "new_listings": self.new_listings,
            "new_observations": self.new_observations,
            "duplicate_observations": self.duplicate_observations,
            "error": self.error,
        }


@dataclass
class IngestReport:
    competitors: list[CompetitorIngestResult] = field(default_factory=list)
    matched: dict[str, int] = field(default_factory=dict)
    alerts: dict[str, int] = field(default_factory=dict)

    @property
    def failed(self) -> list[CompetitorIngestResult]:
        return [row for row in self.competitors if row.error]

    def as_dict(self) -> dict[str, object]:
        return {
            "competitors": [row.as_dict() for row in self.competitors],
            "new_listings": sum(row.new_listings for row in self.competitors),
            "new_observations": sum(row.new_observations for row in self.competitors),
            "failed": len(self.failed),
            "matched": self.matched,
            "alerts": self.alerts,
        }


def ingest_competitor(
    session: Session, competitor: Competitor, *, now: dt.datetime | None = None
) -> CompetitorIngestResult:
    """Pull one competitor's feed and persist it. Never raises."""
    now = now or dt.datetime.now(dt.UTC)
    outcome = CompetitorIngestResult(competitor_slug=competitor.slug)

    try:
        connector = build_connector(competitor.connector_type, session)
        result: ConnectorResult = connector.fetch(competitor, now=now)
    except ConnectorError as exc:
        outcome.error = str(exc)
        logger.warning("Connector failed for %s: %s", competitor.slug, exc)
        return outcome
    except Exception as exc:
        outcome.error = f"{type(exc).__name__}: {exc}"
        logger.exception("Unexpected connector failure for %s", competitor.slug)
        return outcome

    existing = {
        external_id: listing_id
        for listing_id, external_id in session.execute(
            select(CompetitorListing.id, CompetitorListing.external_id).where(
                CompetitorListing.competitor_id == competitor.id
            )
        )
    }

    for raw in result.listings:
        if raw.external_id in existing:
            continue
        listing = CompetitorListing(
            organization_id=competitor.organization_id,
            competitor_id=competitor.id,
            external_id=raw.external_id,
            title=raw.title,
            brand=raw.brand,
            gtin=raw.gtin,
            description=raw.description,
            url=raw.url,
            match_status=MatchStatus.PENDING,
        )
        session.add(listing)
        session.flush()
        existing[raw.external_id] = listing.id
        outcome.new_listings += 1

    # A connector re-reading the same file must not double-count a price point.
    # Only observations at or after the batch's earliest timestamp can collide,
    # so the lookup is bounded by the feed's window rather than all history.
    known_timestamps: set[tuple[uuid.UUID, dt.datetime]] = set()
    if result.observations:
        earliest = min(obs.observed_at for obs in result.observations)
        known_timestamps = {
            (listing_id, observed_at)
            for listing_id, observed_at in session.execute(
                select(PriceObservation.listing_id, PriceObservation.observed_at)
                .join(CompetitorListing, CompetitorListing.id == PriceObservation.listing_id)
                .where(
                    CompetitorListing.competitor_id == competitor.id,
                    PriceObservation.observed_at >= earliest,
                )
            )
        }

    for raw_obs in result.observations:
        listing_id = existing.get(raw_obs.external_id)
        if listing_id is None:
            continue
        if (listing_id, raw_obs.observed_at) in known_timestamps:
            outcome.duplicate_observations += 1
            continue
        known_timestamps.add((listing_id, raw_obs.observed_at))
        session.add(
            PriceObservation(
                organization_id=competitor.organization_id,
                listing_id=listing_id,
                observed_at=raw_obs.observed_at,
                price=raw_obs.price,
                shipping_cost=raw_obs.shipping_cost,
                currency=raw_obs.currency,
                stock_state=raw_obs.stock_state,
                promo_flag=raw_obs.promo_flag,
                raw_payload=raw_obs.raw_payload,
            )
        )
        outcome.new_observations += 1

    competitor.last_crawled_at = now
    session.flush()
    return outcome


def run_ingestion(
    session: Session,
    organization_id: uuid.UUID,
    *,
    now: dt.datetime | None = None,
    match: bool = True,
    alert: bool = True,
) -> IngestReport:
    """Refresh every active competitor, then match new listings and raise alerts."""
    now = now or dt.datetime.now(dt.UTC)
    report = IngestReport()

    competitors = list(
        session.scalars(
            select(Competitor)
            .where(Competitor.organization_id == organization_id, Competitor.is_active.is_(True))
            .order_by(Competitor.slug)
        )
    )
    for competitor in competitors:
        report.competitors.append(ingest_competitor(session, competitor, now=now))

    if match:
        new_listings = list(
            session.scalars(
                select(CompetitorListing).where(
                    CompetitorListing.organization_id == organization_id,
                    CompetitorListing.match_status == MatchStatus.PENDING,
                )
            )
        )
        if new_listings:
            matcher = ListingMatcher(session)
            matcher.fit_catalog(organization_id)
            _, match_report = matcher.match_listings(organization_id, new_listings)
            report.matched = match_report.as_dict()

    if alert:
        alert_report: AlertReport = evaluate_alerts(session, organization_id, now=now)
        report.alerts = alert_report.as_dict()

    record_event(
        session,
        organization_id=organization_id,
        action=AuditAction.INGEST,
        entity_type="competitor",
        entity_id=None,
        summary=f"Ingested {len(competitors)} competitor feed(s)",
        payload=report.as_dict(),
        actor_label="system:ingestion",
    )
    logger.info("Ingestion complete: %s", report.as_dict())
    return report
