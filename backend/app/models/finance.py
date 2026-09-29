"""Module F -- corporate financial statements, metrics, findings, advisor output."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.catalog import MONEY
from app.models.enums import AlertSeverity, StatementType


class FinancialPeriod(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "financial_period"
    __table_args__ = (
        UniqueConstraint("organization_id", "label", name="uq_financial_period_label"),
        Index("ix_financial_period_org_start", "organization_id", "period_start"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(
        String(40), nullable=False, doc="e.g. '2026-Q1' or '2026-03'"
    )
    granularity: Mapped[str] = mapped_column(String(16), nullable=False, default="quarter")
    period_start: Mapped[dt.date] = mapped_column(Date, nullable=False)
    period_end: Mapped[dt.date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="csv_upload")
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    line_items: Mapped[list[FinancialLineItem]] = relationship(
        back_populates="period", cascade="all, delete-orphan"
    )
    metrics: Mapped[list[FinancialMetric]] = relationship(
        back_populates="period", cascade="all, delete-orphan"
    )


class FinancialLineItem(UUIDPrimaryKeyMixin, Base):
    """A single normalized statement line for one period."""

    __tablename__ = "financial_line_item"
    __table_args__ = (
        UniqueConstraint("period_id", "canonical_key", name="uq_line_item_period_key"),
        Index("ix_line_item_statement", "period_id", "statement_type"),
    )

    period_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("financial_period.id", ondelete="CASCADE"), nullable=False
    )
    statement_type: Mapped[StatementType] = mapped_column(
        Enum(StatementType, native_enum=False, length=40), nullable=False
    )
    canonical_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="Normalized account key, e.g. 'revenue', 'cogs', 'opex_marketing'.",
    )
    source_label: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    period: Mapped[FinancialPeriod] = relationship(back_populates="line_items")


class FinancialMetric(UUIDPrimaryKeyMixin, Base):
    """A derived KPI (gross margin, EBITDA, CAC, LTV, runway, CCC, ...)."""

    __tablename__ = "financial_metric"
    __table_args__ = (UniqueConstraint("period_id", "key", name="uq_metric_period_key"),)

    period_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("financial_period.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False, default="ratio")
    formula: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    period: Mapped[FinancialPeriod] = relationship(back_populates="metrics")


class FinancialFinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A detected anomaly / diagnostic on a metric time series."""

    __tablename__ = "financial_finding"
    __table_args__ = (Index("ix_finding_period_severity", "period_id", "severity"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    period_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("financial_period.id", ondelete="CASCADE"), nullable=False
    )
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    detector: Mapped[str] = mapped_column(String(40), nullable=False, default="robust_zscore")
    severity: Mapped[AlertSeverity] = mapped_column(
        Enum(AlertSeverity, native_enum=False, length=20),
        nullable=False,
        default=AlertSeverity.WARNING,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    current_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    z_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class AdvisorReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """LLM-generated strategic narrative, grounded in a validated facts payload."""

    __tablename__ = "advisor_report"
    __table_args__ = (Index("ix_advisor_org_created", "organization_id", "created_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    period_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("financial_period.id", ondelete="SET NULL"), nullable=True
    )
    model_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    facts: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        doc="The only numeric source the LLM is allowed to use.",
    )
    narrative: Mapped[str] = mapped_column(Text, nullable=False, default="")
    recommendations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    grounding_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    grounding_violations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
