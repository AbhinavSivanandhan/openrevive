"""add campaign briefs

Revision ID: a4e9d7b3f021
Revises: d91c4f2a8e30
Create Date: 2026-07-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "a4e9d7b3f021"
down_revision: str | None = "d91c4f2a8e30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "campaign_briefs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("crawl_run_id", sa.Uuid(), nullable=False),
        sa.Column(
            "corpus_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "model_id",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "prompt_version",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'GENERATING'"),
            nullable=False,
        ),
        sa.Column(
            "input_document_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "input_character_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "output_token_count",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "brief_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "error_code",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
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
            "status IN ('GENERATING', 'READY', 'FAILED')",
            name="ck_campaign_briefs_campaign_brief_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["crawl_run_id"],
            ["crawl_runs.id"],
            name="fk_campaign_briefs_crawl_run_id_crawl_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_campaign_briefs",
        ),
        sa.UniqueConstraint(
            "crawl_run_id",
            "corpus_fingerprint",
            name="crawl_run_corpus_fingerprint_unique",
        ),
    )
    op.create_index(
        "ix_campaign_briefs_run_created",
        "campaign_briefs",
        ["crawl_run_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_campaign_briefs_run_created",
        table_name="campaign_briefs",
    )
    op.drop_table("campaign_briefs")
