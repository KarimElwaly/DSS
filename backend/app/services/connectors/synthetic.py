"""Synthetic connector: the default competitor data source.

It does not re-run the market simulator. Instead it advances each existing
listing one step from its last observed price, using the same mean-reverting
process the simulator uses, so a scheduled poll produces a plausible next
reading rather than an unrelated random one.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Subquery

from app.core.logging import get_logger
from app.models.enums import StockState
from app.models.market import Competitor, CompetitorListing, PriceObservation
from app.services.connectors.base import ConnectorResult, RawObservation

logger = get_logger(__name__)


def _latest_per_listing() -> Subquery:
    return (
        select(
            PriceObservation.listing_id,
            PriceObservation.price,
            PriceObservation.shipping_cost,
            PriceObservation.currency,
            PriceObservation.stock_state,
            PriceObservation.observed_at,
        )
        .distinct(PriceObservation.listing_id)
        .order_by(PriceObservation.listing_id, PriceObservation.observed_at.desc())
        .subquery("latest_obs")
    )


class SyntheticConnector:
    """Advances known listings by one day of simulated market movement."""

    name = "synthetic"

    def __init__(self, session: Session, seed: int | None = None) -> None:
        self.session = session
        self.rng = np.random.default_rng(seed)

    def fetch(self, competitor: Competitor, *, now: dt.datetime) -> ConnectorResult:
        config = competitor.connector_config or {}
        drift = float(config.get("price_noise", 0.015))
        stockout_rate = float(config.get("stockout_rate", 0.01))
        promo_rate = float(config.get("promo_rate", 0.06))
        restock_rate = float(config.get("restock_rate", 0.25))

        latest = _latest_per_listing()
        rows = self.session.execute(
            select(
                CompetitorListing.external_id,
                latest.c.price,
                latest.c.shipping_cost,
                latest.c.currency,
                latest.c.stock_state,
            )
            .join(latest, latest.c.listing_id == CompetitorListing.id)
            .where(CompetitorListing.competitor_id == competitor.id)
        ).all()

        result = ConnectorResult()
        for external_id, price, shipping, currency, stock_state in rows:
            step = float(np.exp(self.rng.normal(0.0, drift)))
            next_price = max(0.01, float(price) * step)

            if stock_state == StockState.OUT_OF_STOCK:
                # Stockouts end far more often than they begin, so recovery is
                # modelled with its own rate rather than the inverse of onset.
                in_stock = self.rng.random() < restock_rate
            else:
                in_stock = self.rng.random() >= stockout_rate

            result.observations.append(
                RawObservation(
                    external_id=external_id,
                    observed_at=now,
                    price=Decimal(f"{next_price:.2f}"),
                    shipping_cost=Decimal(str(shipping)),
                    currency=currency,
                    stock_state=StockState.IN_STOCK if in_stock else StockState.OUT_OF_STOCK,
                    promo_flag=bool(self.rng.random() < promo_rate),
                    raw_payload={"source": self.name},
                )
            )

        logger.info("Synthetic connector produced %d observations", len(result.observations))
        return result
