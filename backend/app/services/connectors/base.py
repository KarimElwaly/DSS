"""Connector interface: how competitor data enters the system.

Everything downstream -- matching, alerting, the price index, Module B's
elasticity estimates -- consumes :class:`ConnectorResult` and nothing else. The
synthetic simulator, a CSV export and a live scraper are therefore
interchangeable, and swapping one for another changes no code outside this
package. That is what makes the demo data honest: the default source is
synthetic, but it travels the same path a real one would.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from app.models.enums import StockState
from app.models.market import Competitor


@dataclass(frozen=True)
class RawListing:
    """A competitor product page, before matching."""

    external_id: str
    title: str
    brand: str = ""
    gtin: str | None = None
    description: str = ""
    url: str = ""


@dataclass(frozen=True)
class RawObservation:
    """One price reading for a listing."""

    external_id: str
    observed_at: dt.datetime
    price: Decimal
    shipping_cost: Decimal = Decimal("0")
    currency: str = "USD"
    stock_state: StockState = StockState.UNKNOWN
    promo_flag: bool = False
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectorResult:
    listings: list[RawListing] = field(default_factory=list)
    observations: list[RawObservation] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "listings": len(self.listings),
            "observations": len(self.observations),
            "errors": len(self.errors),
        }


@runtime_checkable
class MarketConnector(Protocol):
    """A source of competitor listings and prices."""

    name: str

    def fetch(self, competitor: Competitor, *, now: dt.datetime) -> ConnectorResult: ...


class ConnectorError(RuntimeError):
    """Raised when a connector cannot produce data for a competitor."""
