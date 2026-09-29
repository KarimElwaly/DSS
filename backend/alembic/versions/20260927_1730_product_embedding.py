"""Add product embedding for listing matching

Revision ID: b2f4c1d90a37
Revises: 7131d874cdd2
Create Date: 2026-09-27

Competitor listings are the *query* side of matching -- many, external and
volatile -- while the catalog is small, ours and slow-moving, so the approximate
nearest-neighbour index belongs on ``product``.
"""

from __future__ import annotations

import pgvector.sqlalchemy
import sqlalchemy as sa

from alembic import op

revision: str = "b2f4c1d90a37"
down_revision: str | None = "7131d874cdd2"
branch_labels: str | None = None
depends_on: str | None = None

EMBEDDING_DIM = 384


def upgrade() -> None:
    op.add_column(
        "product",
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=EMBEDDING_DIM), nullable=True),
    )
    # HNSW over cosine distance, matching the operator the matcher queries with.
    # `m` and `ef_construction` are left at pgvector's defaults: the recall gain
    # from tuning them is not measurable at demo-catalog size.
    op.execute(
        "CREATE INDEX ix_product_embedding_hnsw ON product USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_product_embedding_hnsw")
    op.drop_column("product", "embedding")
