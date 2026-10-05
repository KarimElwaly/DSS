"""Competitive-intelligence API schemas."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel

from app.models.enums import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    ConnectorType,
    MatchMethod,
    MatchStatus,
    StockState,
)
from app.schemas.common import ORMModel


class CompetitorOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    domain: str
    connector_type: ConnectorType
    is_active: bool
    last_crawled_at: dt.datetime | None


class LatestObservation(BaseModel):
    price: float
    shipping_cost: float
    landed_price: float
    stock_state: StockState
    promo_flag: bool
    observed_at: dt.datetime


class ListingOut(ORMModel):
    id: uuid.UUID
    competitor_id: uuid.UUID
    competitor_name: str | None = None
    external_id: str
    title: str
    brand: str
    gtin: str | None
    url: str
    matched_product_id: uuid.UUID | None
    matched_product_sku: str | None = None
    match_confidence: float | None
    match_method: MatchMethod
    match_status: MatchStatus
    latest: LatestObservation | None = None


# Backward-compatible aliases
CompetitorListingOut = ListingOut


class PriceObservationOut(ORMModel):
    observed_at: dt.datetime
    price: float
    shipping_cost: float
    stock_state: StockState
    promo_flag: bool


class CompetitorPricePoint(BaseModel):
    competitor_slug: str
    competitor_name: str
    observed_at: dt.datetime
    price: float
    landed_price: float
    stock_state: StockState
    promo_flag: bool
    url: str


class MarketSnapshot(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    our_price: float
    competitor_price_index: float | None
    cheapest_competitor_price: float | None
    price_gap_pct: float | None
    n_listings: int
    n_out_of_stock: int
    points: list[CompetitorPricePoint]


# Backward-compatible aliases
MarketSnapshotOut = MarketSnapshot


class AlertOut(ORMModel):
    id: uuid.UUID
    alert_type: AlertType
    severity: AlertSeverity
    status: AlertStatus
    title: str
    message: str
    product_id: uuid.UUID | None
    competitor_id: uuid.UUID | None
    context: dict
    created_at: dt.datetime


class AlertStatusUpdate(BaseModel):
    status: AlertStatus
