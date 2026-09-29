"""Analytics + pricing decision records (Modules B, C, D)."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.catalog import MONEY
from app.models.enums import (
    ElasticityMethod,
    ModelKind,
    PricingObjective,
    RecommendationStatus,
)


class ModelRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Lightweight stand-in for a model registry (MLflow is post-MVP)."""

    __tablename__ = "model_run"
    __table_args__ = (Index("ix_model_run_kind_created", "kind", "created_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[ModelKind] = mapped_column(
        Enum(ModelKind, native_enum=False, length=32), nullable=False
    )
    label: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    artifact_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    n_products: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    train_start: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    train_end: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    is_champion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ElasticityEstimate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Own-price elasticity of demand for one SKU, with uncertainty."""

    __tablename__ = "elasticity_estimate"
    __table_args__ = (
        UniqueConstraint("product_id", "model_run_id", name="uq_elasticity_product_run"),
        Index("ix_elasticity_product_created", "product_id", "created_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=False
    )
    model_run_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("model_run.id", ondelete="CASCADE"), nullable=False
    )

    method: Mapped[ElasticityMethod] = mapped_column(
        Enum(ElasticityMethod, native_enum=False, length=40), nullable=False
    )
    elasticity: Mapped[float] = mapped_column(Float, nullable=False)
    std_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    r_squared: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_observations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_variation_cv: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Coefficient of variation of price; low values => weak identification.",
    )
    intercept: Mapped[float | None] = mapped_column(Float, nullable=True)
    reference_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    coefficients: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_reliable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class CrossElasticity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Cross-price elasticity between two of our SKUs (cannibalization signal)."""

    __tablename__ = "cross_elasticity"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "related_product_id", "model_run_id", name="uq_cross_pair_run"
        ),
        CheckConstraint("product_id <> related_product_id", name="ck_cross_distinct"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=False
    )
    related_product_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=False
    )
    model_run_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("model_run.id", ondelete="CASCADE"), nullable=False
    )
    elasticity: Mapped[float] = mapped_column(Float, nullable=False)
    std_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_significant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ForecastPoint(UUIDPrimaryKeyMixin, Base):
    """Persisted demand forecast with a prediction interval."""

    __tablename__ = "forecast_point"
    __table_args__ = (
        UniqueConstraint("model_run_id", "product_id", "target_date", name="uq_forecast_point"),
        Index("ix_forecast_product_date", "product_id", "target_date"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=False
    )
    model_run_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("model_run.id", ondelete="CASCADE"), nullable=False
    )
    target_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    predicted_units: Mapped[float] = mapped_column(Float, nullable=False)
    lower_units: Mapped[float | None] = mapped_column(Float, nullable=True)
    upper_units: Mapped[float | None] = mapped_column(Float, nullable=True)
    assumed_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)


class PriceRecommendation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Output of the optimization engine, subject to human approval."""

    __tablename__ = "price_recommendation"
    __table_args__ = (
        Index("ix_recommendation_org_status", "organization_id", "status", "created_at"),
        Index("ix_recommendation_product_created", "product_id", "created_at"),
        CheckConstraint("recommended_price > 0", name="ck_recommendation_price"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=False
    )
    model_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("model_run.id", ondelete="SET NULL"), nullable=True
    )

    objective: Mapped[PricingObjective] = mapped_column(
        Enum(PricingObjective, native_enum=False, length=32), nullable=False
    )
    current_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    recommended_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    unconstrained_price: Mapped[Decimal | None] = mapped_column(
        MONEY, nullable=True, doc="Optimizer output before guardrails were applied."
    )
    price_floor: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    price_ceiling: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    expected_units: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_revenue: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_revenue: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_margin: Mapped[float | None] = mapped_column(Float, nullable=True)

    elasticity_used: Mapped[float | None] = mapped_column(Float, nullable=True)
    elasticity_ci_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    elasticity_ci_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    binding_constraints: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    rationale: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")

    status: Mapped[RecommendationStatus] = mapped_column(
        Enum(RecommendationStatus, native_enum=False, length=32),
        nullable=False,
        default=RecommendationStatus.PROPOSED,
    )
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    valid_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SimulationRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A saved what-if scenario and its results (Module D)."""

    __tablename__ = "simulation_run"
    __table_args__ = (Index("ix_simulation_org_created", "organization_id", "created_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False, default="Untitled scenario")
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    scenario: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    results: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    n_monte_carlo: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
