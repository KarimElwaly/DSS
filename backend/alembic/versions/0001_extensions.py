"""enable required postgres extensions

Revision ID: 0001_extensions
Revises:
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_extensions"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Vector similarity search for the product matching pipeline (Module A).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Trigram index support for fuzzy title pre-filtering.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS vector")
