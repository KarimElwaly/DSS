"""Internal catalog: categories, products, cost history and sales facts."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ProductStatus

if TYPE_CHECKING:
    from app.models.market import CompetitorListing
    from app.models.organization import Organization

MONEY = Numeric(14, 4)
RATE = Numeric(8, 6)

#: Embedding width. Changing this requires an Alembic migration on both
#: ``product.embedding`` and ``competitor_listing.embedding``, plus a full
#: re-embed. Declared here rather than in ``market`` because that module
#: already imports from this one.
EMBEDDING_DIM = 384


class Category(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "category"
    __table_args__ = (UniqueConstraint("organization_id", "slug", name="uq_category_org_slug"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("category.id", ondelete="SET NULL"), nullable=True
    )

    organization: Mapped[Organization] = relationship(back_populates="categories")
    products: Mapped[list[Product]] = relationship(back_populates="category")


class Product(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A sellable SKU in the internal catalog."""

    __tablename__ = "product"
    __table_args__ = (
        UniqueConstraint("organization_id", "sku", name="uq_product_org_sku"),
        CheckConstraint("min_margin_pct >= 0 AND min_margin_pct < 1", name="ck_product_min_margin"),
        CheckConstraint("unit_cost >= 0", name="ck_product_unit_cost"),
        CheckConstraint("current_price > 0", name="ck_product_current_price"),
        Index("ix_product_org_category", "organization_id", "category_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("category.id", ondelete="SET NULL"), nullable=True
    )

    sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    gtin: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    #: Matching index side. Competitor listings are the query side: many, external
    #: and volatile, whereas the catalog is small and ours, so the approximate
    #: nearest-neighbour index belongs here.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    current_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    msrp: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)

    # -- pricing guardrail inputs (Module C) --------------------------------
    min_margin_pct: Mapped[Decimal] = mapped_column(RATE, nullable=False, default=Decimal("0.08"))
    max_price_step_pct: Mapped[Decimal] = mapped_column(
        RATE, nullable=False, default=Decimal("0.15")
    )
    price_floor_override: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    price_ceiling_override: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    map_price: Mapped[Decimal | None] = mapped_column(
        MONEY, nullable=True, doc="Minimum advertised price imposed by the manufacturer."
    )

    status: Mapped[ProductStatus] = mapped_column(
        Enum(ProductStatus, native_enum=False, length=32),
        nullable=False,
        default=ProductStatus.ACTIVE,
    )
    launch_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="products")
    category: Mapped[Category | None] = relationship(back_populates="products")
    cost_history: Mapped[list[CostHistory]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    sales: Mapped[list[SalesFact]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    listings: Mapped[list[CompetitorListing]] = relationship(back_populates="matched_product")

    @property
    def search_text(self) -> str:
        """Canonical text used to build the matching embedding.

        A part already contained in what came before it is skipped. Catalogs
        routinely repeat the brand in the product name and again in the
        description, and that repetition dilutes the few tokens -- capacity,
        trim, colour -- that actually separate one variant from another.
        """
        text = self.name
        if self.brand and self.brand.casefold() not in text.casefold():
            text = f"{self.brand} {text}"
        if self.description and self.description.casefold() not in text.casefold():
            text = f"{text} {self.description}"
        return text.strip()


class CostHistory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Landed-cost timeline.

    Cost shocks are exogenous to demand, which makes them a usable instrument
    when estimating price elasticity (Module B).
    """

    __tablename__ = "cost_history"
    __table_args__ = (
        UniqueConstraint("product_id", "valid_from", name="uq_cost_product_date"),
        Index("ix_cost_history_product_date", "product_id", "valid_from"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=False
    )
    valid_from: Mapped[dt.date] = mapped_column(Date, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    note: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    product: Mapped[Product] = relationship(back_populates="cost_history")


class SalesFact(UUIDPrimaryKeyMixin, Base):
    """Daily transactional aggregate -- the training set for elasticity/forecasting."""

    __tablename__ = "sales_fact"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "sale_date", "channel", name="uq_sales_product_date_channel"
        ),
        CheckConstraint("units >= 0", name="ck_sales_units"),
        Index("ix_sales_fact_org_date", "organization_id", "sale_date"),
        Index("ix_sales_fact_product_date", "product_id", "sale_date"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=False
    )
    sale_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False, default="web")

    units: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    promo_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    product: Mapped[Product] = relationship(back_populates="sales")

    @property
    def revenue(self) -> Decimal:
        return self.unit_price * self.units

    @property
    def gross_margin(self) -> Decimal:
        return (self.unit_price - self.unit_cost) * self.units
