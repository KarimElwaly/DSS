"""Opportunity and risk detection over the latest competitor observations.

The rules here are the point of Module A: raw prices are data, but "the only
rival stocking this SKU just went out of stock" is a decision. Each rule writes
an :class:`Alert` with a ``dedupe_key`` so that re-running ingestion does not
produce a second copy of a condition that is already open.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.catalog import Product
from app.models.enums import AlertSeverity, AlertStatus, AlertType, StockState
from app.models.market import Alert, Competitor, CompetitorListing, PriceObservation
from app.services.market_view import load_market_positions

logger = get_logger(__name__)

#: How far below the competitor index our price must sit before it is worth
#: reporting. Below this the signal is indistinguishable from rounding and
#: shipping noise, and a monitor that cries wolf gets ignored.
UNDERCUT_THRESHOLD = 0.05

#: An observation older than this means the feed is broken, not that the price
#: is stable.
STALE_AFTER = dt.timedelta(days=3)


@dataclass
class AlertReport:
    created: int = 0
    resolved: int = 0
    suppressed_duplicate: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "created": self.created,
            "resolved": self.resolved,
            "suppressed_duplicate": self.suppressed_duplicate,
        }


def _previous_states(
    session: Session, organization_id: uuid.UUID
) -> dict[uuid.UUID, tuple[StockState, StockState]]:
    """The two most recent stock states per listing, newest first.

    A transition needs both, and reading them in one pass avoids an N+1 query.
    """
    ranked = (
        select(
            PriceObservation.listing_id,
            PriceObservation.stock_state,
            PriceObservation.observed_at,
        )
        .where(PriceObservation.organization_id == organization_id)
        .order_by(PriceObservation.listing_id, PriceObservation.observed_at.desc())
        .subquery("ranked")
    )
    out: dict[uuid.UUID, list[StockState]] = {}
    for listing_id, state, _ in session.execute(select(ranked)):
        bucket = out.setdefault(listing_id, [])
        if len(bucket) < 2:
            bucket.append(state)
    return {
        listing_id: (states[0], states[1] if len(states) > 1 else states[0])
        for listing_id, states in out.items()
    }


def evaluate_alerts(
    session: Session, organization_id: uuid.UUID, *, now: dt.datetime | None = None
) -> AlertReport:
    now = now or dt.datetime.now(dt.UTC)
    report = AlertReport()

    open_keys = set(
        session.scalars(
            select(Alert.dedupe_key).where(
                Alert.organization_id == organization_id,
                Alert.status != AlertStatus.RESOLVED,
            )
        )
    )

    def emit(
        *,
        key: str,
        alert_type: AlertType,
        severity: AlertSeverity,
        title: str,
        message: str,
        product_id: uuid.UUID | None = None,
        competitor_id: uuid.UUID | None = None,
        context: dict | None = None,
    ) -> None:
        if key in open_keys:
            report.suppressed_duplicate += 1
            return
        open_keys.add(key)
        session.add(
            Alert(
                organization_id=organization_id,
                alert_type=alert_type,
                severity=severity,
                status=AlertStatus.OPEN,
                title=title,
                message=message,
                product_id=product_id,
                competitor_id=competitor_id,
                context=context or {},
                dedupe_key=key,
            )
        )
        report.created += 1

    products = {
        product.id: product
        for product in session.scalars(
            select(Product).where(Product.organization_id == organization_id)
        )
    }
    competitor_names = dict(
        session.execute(
            select(Competitor.id, Competitor.name).where(
                Competitor.organization_id == organization_id
            )
        ).all()
    )
    listings = {
        listing.id: listing
        for listing in session.scalars(
            select(CompetitorListing).where(
                CompetitorListing.organization_id == organization_id,
                CompetitorListing.matched_product_id.is_not(None),
            )
        )
    }

    # -- stock transitions --------------------------------------------------
    transitions = _previous_states(session, organization_id)
    for listing_id, (current, previous) in transitions.items():
        listing = listings.get(listing_id)
        if listing is None or listing.matched_product_id is None:
            continue
        product = products.get(listing.matched_product_id)
        if product is None:
            continue
        rival = competitor_names.get(listing.competitor_id, "A competitor")
        day = now.date().isoformat()

        if current == StockState.OUT_OF_STOCK and previous != StockState.OUT_OF_STOCK:
            emit(
                key=f"stockout:{listing_id}:{day}",
                alert_type=AlertType.COMPETITOR_STOCKOUT,
                severity=AlertSeverity.WARNING,
                title=f"{rival} is out of stock on {product.sku}",
                message=(
                    f"{rival} has stopped selling {product.name}. "
                    "Demand usually shifts to the remaining sellers, which widens "
                    "the room for a price increase while the stockout lasts."
                ),
                product_id=product.id,
                competitor_id=listing.competitor_id,
                context={"listing_id": str(listing_id), "previous_state": previous.value},
            )
        elif current == StockState.IN_STOCK and previous == StockState.OUT_OF_STOCK:
            emit(
                key=f"restock:{listing_id}:{day}",
                alert_type=AlertType.COMPETITOR_RESTOCK,
                severity=AlertSeverity.INFO,
                title=f"{rival} is back in stock on {product.sku}",
                message=(
                    f"{rival} is selling {product.name} again. Any price premium "
                    "taken during the stockout should be reviewed."
                ),
                product_id=product.id,
                competitor_id=listing.competitor_id,
                context={"listing_id": str(listing_id)},
            )

    # -- price position and feed health -------------------------------------
    positions = load_market_positions(session, organization_id)
    for product_id, position in positions.items():
        product = products.get(product_id)
        if product is None:
            continue

        our_price = float(product.current_price)
        gap = position.price_gap_pct(our_price)
        if gap is not None and gap > UNDERCUT_THRESHOLD:
            cheapest = position.cheapest
            emit(
                key=f"undercut:{product_id}:{now.date().isoformat()}",
                alert_type=AlertType.PRICE_UNDERCUT,
                severity=(
                    AlertSeverity.CRITICAL
                    if gap > 2 * UNDERCUT_THRESHOLD
                    else AlertSeverity.WARNING
                ),
                title=f"{product.sku} is {gap:.1%} above the market",
                message=(
                    f"We list {product.name} at {our_price:.2f} against a competitor "
                    f"index of {position.price_index:.2f}"
                    + (
                        f"; {cheapest.competitor_name} is cheapest at {cheapest.landed_price:.2f}."
                        if cheapest
                        else "."
                    )
                ),
                product_id=product_id,
                context={
                    "price_gap_pct": round(gap, 4),
                    "our_price": our_price,
                    "competitor_price_index": position.price_index,
                    "n_listings": position.n_listings,
                },
            )

        observed_at = position.observed_at
        if observed_at is not None and now - observed_at > STALE_AFTER:
            age = (now - observed_at).days
            emit(
                key=f"stale:{product_id}",
                alert_type=AlertType.DATA_STALE,
                severity=AlertSeverity.WARNING,
                title=f"No competitor data for {product.sku} in {age} days",
                message=(
                    "The price index for this SKU is being computed from stale "
                    "observations; treat any recommendation built on it with caution."
                ),
                product_id=product_id,
                context={"observed_at": observed_at.isoformat(), "age_days": age},
            )

    session.flush()
    logger.info("Alert evaluation complete: %s", report.as_dict())
    return report
