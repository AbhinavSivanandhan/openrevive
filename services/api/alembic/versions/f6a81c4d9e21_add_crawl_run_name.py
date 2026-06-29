"""add crawl run name

Revision ID: f6a81c4d9e21
Revises: c3e7f8a0b312
Create Date: 2026-06-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a81c4d9e21"
down_revision: str | None = "c3e7f8a0b312"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "crawl_runs",
        sa.Column(
            "name",
            sa.String(length=160),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("crawl_runs", "name")
