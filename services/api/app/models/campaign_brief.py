from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CampaignBrief(Base):
    """
    One cached AI brief for a specific immutable campaign evidence fingerprint.

    A new Bedrock invocation is allowed only when the crawl corpus, model, or
    prompt version produces a different fingerprint.
    """

    __tablename__ = "campaign_briefs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('GENERATING', 'READY', 'FAILED')",
            name="campaign_brief_status_valid",
        ),
        UniqueConstraint(
            "crawl_run_id",
            "corpus_fingerprint",
            name="crawl_run_corpus_fingerprint_unique",
        ),
        Index(
            "ix_campaign_briefs_run_created",
            "crawl_run_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
    )
    crawl_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="CASCADE"),
        nullable=False,
    )

    corpus_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    model_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    prompt_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="GENERATING",
    )

    input_document_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    input_character_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    output_token_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    brief_json: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
