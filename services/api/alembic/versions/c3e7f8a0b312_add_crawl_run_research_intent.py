"""add crawl run research intent

Revision ID: c3e7f8a0b312
Revises: 7d4b10d5c920
Create Date: 2026-06-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c3e7f8a0b312"
down_revision: str | None = "7d4b10d5c920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "crawl_runs",
        sa.Column(
            "research_intent",
            sa.Text(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("crawl_runs", "research_intent")
