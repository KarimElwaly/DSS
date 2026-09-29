"""Competitive intelligence: competitors, listings, price observations, alerts."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.catalog import EMBEDDING_DIM, MONEY
from app.models.enums import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    ConnectorType,
    MatchMethod,
    MatchStatus,
    StockState,
)

if TYPE_CHECKING:
    from app.models.catalog import Product

__all__ = [
    "EMBEDDING_DIM",
    "Alert",
    "Competitor",
    "CompetitorListing",
    "MatchCandidate",
    "PriceObservation",
]


class Competitor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "competitor"
    __table_args__ = (UniqueConstraint("organization_id", "slug", name="uq_competitor_org_slug"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    connector_type: Mapped[ConnectorType] = mapped_column(
        Enum(ConnectorType, native_enum=False, length=32),
        nullable=False,
        default=ConnectorType.SYNTHETIC,
    )
    connector_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_crawled_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    listings: Mapped[list[CompetitorListing]] = relationship(
        back_populates="competitor", cascade="all, delete-orphan"
    )


class CompetitorListing(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A product page on a competitor site, optionally matched to our SKU."""

    __tablename__ = "competitor_listing"
    __table_args__ = (
        UniqueConstraint("competitor_id", "external_id", name="uq_listing_competitor_external"),
        Index("ix_listing_match_status", "match_status"),
        Index("ix_listing_matched_product", "matched_product_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    competitor_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("competitor.id", ondelete="CASCADE"), nullable=False
    )

    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    brand: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    gtin: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")

    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    matched_product_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("product.id", ondelete="SET NULL"), nullable=True
    )
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_method: Mapped[MatchMethod] = mapped_column(
        Enum(MatchMethod, native_enum=False, length=32),
        nullable=False,
        default=MatchMethod.UNMATCHED,
    )
    match_status: Mapped[MatchStatus] = mapped_column(
        Enum(MatchStatus, native_enum=False, length=32),
        nullable=False,
        default=MatchStatus.PENDING,
    )
    match_explanation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    competitor: Mapped[Competitor] = relationship(back_populates="listings")
    matched_product: Mapped[Product | None] = relationship(back_populates="listings")
    observations: Mapped[list[PriceObservation]] = relationship(
        back_populates="listing", cascade="all, delete-orphan"
    )

    @property
    def search_text(self) -> str:
        """Query-side counterpart of :attr:`Product.search_text`.

        Built the same way, for the same reason: both sides must be tokenised
        alike or the comparison is measuring formatting, not similarity.
        """
        text = self.title
        if self.brand and self.brand.casefold() not in text.casefold():
            text = f"{self.brand} {text}"
        if self.description and self.description.casefold() not in text.casefold():
            text = f"{text} {self.description}"
        return text.strip()


class MatchCandidate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Top-K suggestions persisted for the human review queue."""

    __tablename__ = "match_candidate"
    __table_args__ = (
        UniqueConstraint("listing_id", "product_id", name="uq_candidate_listing_product"),
        Index("ix_match_candidate_listing_score", "listing_id", "score"),
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("competitor_listing.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    vector_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fuzzy_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rank: Mapped[int] = mapped_column(nullable=False, default=0)


class PriceObservation(UUIDPrimaryKeyMixin, Base):
    """One scraped/ingested price point for a competitor listing."""

    __tablename__ = "price_observation"
    __table_args__ = (
        CheckConstraint("price > 0", name="ck_observation_price"),
        Index("ix_observation_listing_time", "listing_id", "observed_at"),
        Index("ix_observation_org_time", "organization_id", "observed_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("competitor_listing.id", ondelete="CASCADE"),
        nullable=False,
    )
    observed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    shipping_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    stock_state: Mapped[StockState] = mapped_column(
        Enum(StockState, native_enum=False, length=32), nullable=False, default=StockState.UNKNOWN
    )
    promo_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_outlier: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    listing: Mapped[CompetitorListing] = relationship(back_populates="observations")

    @property
    def landed_price(self) -> Decimal:
        return self.price + self.shipping_cost


class Alert(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "alert"
    __table_args__ = (
        Index("ix_alert_org_status_created", "organization_id", "status", "created_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    alert_type: Mapped[AlertType] = mapped_column(
        Enum(AlertType, native_enum=False, length=40), nullable=False
    )
    severity: Mapped[AlertSeverity] = mapped_column(
        Enum(AlertSeverity, native_enum=False, length=20),
        nullable=False,
        default=AlertSeverity.INFO,
    )
    status: Mapped[AlertStatus] = mapped_column(
        Enum(AlertStatus, native_enum=False, length=20), nullable=False, default=AlertStatus.OPEN
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=True
    )
    competitor_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("competitor.id", ondelete="CASCADE"), nullable=True
    )
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
