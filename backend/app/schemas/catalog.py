"""Catalog API schemas."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import ProductStatus
from app.schemas.common import ORMModel


class CategoryOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str


class ProductOut(ORMModel):
    id: uuid.UUID
    sku: str
    name: str
    brand: str
    gtin: str | None
    status: ProductStatus
    current_price: Decimal
    unit_cost: Decimal
    msrp: Decimal | None
    min_margin_pct: Decimal
    category: CategoryOut | None = None

    @property
    def gross_margin_pct(self) -> float:
        price = float(self.current_price)
        return 0.0 if price <= 0 else (price - float(self.unit_cost)) / price


class ProductMarketPosition(BaseModel):
    """Latest competitive snapshot for one SKU."""

    cheapest_competitor: str | None = None
    cheapest_competitor_price: float | None = None
    competitor_price_index: float | None = Field(
        default=None, description="Geometric mean of in-stock rival landed prices."
    )
    price_gap_pct: float | None = Field(
        default=None, description="Our price vs the competitor index, as a fraction."
    )
    n_listings: int = 0
    n_out_of_stock: int = 0
    observed_at: dt.datetime | None = None


class ProductDetail(ProductOut):
    description: str
    map_price: Decimal | None
    price_floor_override: Decimal | None
    price_ceiling_override: Decimal | None
    max_price_step_pct: Decimal
    launch_date: dt.date | None
    market: ProductMarketPosition | None = None


class SalesPoint(BaseModel):
    sale_date: dt.date
    units: int
    unit_price: float
    unit_cost: float
    promo_flag: bool
    revenue: float
    gross_margin: float
