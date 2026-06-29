"""add crawl job frontier metadata

Revision ID: 7d4b10d5c920
Revises: 488dd46597ff
Create Date: 2026-06-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "7d4b10d5c920"
down_revision: str | None = "488dd46597ff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "crawl_jobs",
        sa.Column(
            "anchor_text",
            sa.Text(),
            nullable=True,
        ),
    )
    op.add_column(
        "crawl_jobs",
        sa.Column(
            "priority_score",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "crawl_jobs",
        sa.Column(
            "priority_band",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'LOW'"),
        ),
    )
    op.add_column(
        "crawl_jobs",
        sa.Column(
            "discovery_reason",
            sa.Text(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_crawl_jobs_frontier_order",
        "crawl_jobs",
        [
            "crawl_run_id",
            "status",
            "priority_score",
            "depth",
            "created_at",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_crawl_jobs_frontier_order",
        table_name="crawl_jobs",
    )
    op.drop_column("crawl_jobs", "discovery_reason")
    op.drop_column("crawl_jobs", "priority_band")
    op.drop_column("crawl_jobs", "priority_score")
    op.drop_column("crawl_jobs", "anchor_text")
