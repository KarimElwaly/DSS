"""Read model for the competitive position of our SKUs.

Everything here derives from the *latest* observation per listing, computed with
a single ``DISTINCT ON`` query rather than N+1 lookups.
"""

from __future__ import annotations

import datetime as dt
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Subquery

from app.models.enums import MatchStatus, StockState
from app.models.market import Competitor, CompetitorListing, PriceObservation


@dataclass(frozen=True)
class CompetitorPrice:
    competitor_slug: str
    competitor_name: str
    listing_id: uuid.UUID
    url: str
    observed_at: dt.datetime
    price: float
    shipping_cost: float
    stock_state: StockState
    promo_flag: bool

    @property
    def landed_price(self) -> float:
        return self.price + self.shipping_cost


@dataclass(frozen=True)
class MarketPosition:
    product_id: uuid.UUID
    points: tuple[CompetitorPrice, ...]

    @property
    def in_stock_points(self) -> tuple[CompetitorPrice, ...]:
        return tuple(p for p in self.points if p.stock_state == StockState.IN_STOCK)

    @property
    def n_listings(self) -> int:
        return len(self.points)

    @property
    def n_out_of_stock(self) -> int:
        return sum(1 for p in self.points if p.stock_state == StockState.OUT_OF_STOCK)

    @property
    def price_index(self) -> float | None:
        """Geometric mean of in-stock landed prices.

        A geometric mean is used because pricing decisions are made in relative
        (percentage) terms, and it is far less sensitive to a single outlier.
        """
        prices = [p.landed_price for p in self.in_stock_points if p.landed_price > 0]
        if not prices:
            return None
        return float(math.exp(sum(math.log(p) for p in prices) / len(prices)))

    @property
    def competitor_price_index(self) -> float | None:
        """Alias for price_index."""
        return self.price_index

    @property
    def cheapest(self) -> CompetitorPrice | None:
        candidates = self.in_stock_points or self.points
        return min(candidates, key=lambda p: p.landed_price, default=None)

    @property
    def observed_at(self) -> dt.datetime | None:
        return max((p.observed_at for p in self.points), default=None)

    def price_gap_pct(self, our_price: float) -> float | None:
        index = self.price_index
        if index is None or index <= 0:
            return None
        return (our_price - index) / index


_ACCEPTED = (MatchStatus.AUTO_ACCEPTED, MatchStatus.CONFIRMED)


def _latest_observation_subquery() -> Subquery:
    """One row per listing: the most recent price observation."""
    return (
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


def load_market_positions(
    session: Session,
    organization_id: uuid.UUID,
    product_ids: Sequence[uuid.UUID] | None = None,
) -> dict[uuid.UUID, MarketPosition]:
    latest = _latest_observation_subquery()

    stmt = (
        select(
            CompetitorListing.matched_product_id,
            CompetitorListing.id,
            CompetitorListing.url,
            Competitor.slug,
            Competitor.name,
            latest.c.observed_at,
            latest.c.price,
            latest.c.shipping_cost,
            latest.c.stock_state,
            latest.c.promo_flag,
        )
        .join(Competitor, Competitor.id == CompetitorListing.competitor_id)
        .join(latest, latest.c.listing_id == CompetitorListing.id)
        .where(
            CompetitorListing.organization_id == organization_id,
            CompetitorListing.matched_product_id.is_not(None),
            CompetitorListing.match_status.in_(_ACCEPTED),
        )
    )
    if product_ids is not None:
        if not product_ids:
            return {}
        stmt = stmt.where(CompetitorListing.matched_product_id.in_(list(product_ids)))

    buckets: dict[uuid.UUID, list[CompetitorPrice]] = {}
    for row in session.execute(stmt):
        buckets.setdefault(row[0], []).append(
            CompetitorPrice(
                competitor_slug=row[3],
                competitor_name=row[4],
                listing_id=row[1],
                url=row[2],
                observed_at=row[5],
                price=float(row[6]),
                shipping_cost=float(row[7]),
                stock_state=row[8],
                promo_flag=bool(row[9]),
            )
        )

    return {
        product_id: MarketPosition(
            product_id=product_id,
            points=tuple(sorted(points, key=lambda p: p.landed_price)),
        )
        for product_id, points in buckets.items()
    }
