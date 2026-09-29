"""Ground-truth parameters emitted by the synthetic market generator.

This table exists **only** for validation: it lets the thesis measure how well the
estimators recover known parameters. Nothing in ``app/analytics``,
``app/optimization`` or ``app/simulation`` may read from it -- doing so would
leak the answer into the model and invalidate every reported metric.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import BigInteger, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class GroundTruth(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ground_truth"
    __table_args__ = (UniqueConstraint("product_id", name="uq_ground_truth_product"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=False
    )
    generator_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    own_elasticity: Mapped[float] = mapped_column(Float, nullable=False)
    competitor_cross_elasticity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    base_demand: Mapped[float] = mapped_column(Float, nullable=False)
    reference_price: Mapped[float] = mapped_column(Float, nullable=False)
    promo_lift: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    trend_per_year: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    seasonal_amplitude: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    noise_sigma: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sibling_cross_elasticities: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, doc="Map of sibling SKU code -> true cross elasticity."
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
