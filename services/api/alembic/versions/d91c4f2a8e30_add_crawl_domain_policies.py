"""add crawl domain policies

Revision ID: d91c4f2a8e30
Revises: f6a81c4d9e21
Create Date: 2026-07-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d91c4f2a8e30"
down_revision: str | None = "f6a81c4d9e21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "crawl_domain_policies",
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("robots_txt", sa.Text(), nullable=True),
        sa.Column(
            "robots_fetched_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("robots_http_status", sa.Integer(), nullable=True),
        sa.Column(
            "crawl_delay_seconds",
            sa.Float(),
            nullable=True,
        ),
        sa.Column(
            "next_allowed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("active_lease_token", sa.Uuid(), nullable=True),
        sa.Column(
            "active_lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "crawl_delay_seconds IS NULL OR crawl_delay_seconds > 0",
            name="ck_crawl_domain_policies_crawl_delay_positive",
        ),
        sa.PrimaryKeyConstraint(
            "domain",
            name="pk_crawl_domain_policies",
        ),
    )
    op.create_index(
        "ix_crawl_domain_policies_next_allowed_at",
        "crawl_domain_policies",
        ["next_allowed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_crawl_domain_policies_next_allowed_at",
        table_name="crawl_domain_policies",
    )
    op.drop_table("crawl_domain_policies")
