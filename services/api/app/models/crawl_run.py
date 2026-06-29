from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.collection import Collection
    from app.models.crawl_job import CrawlJob


class CrawlRun(Base):
    __tablename__ = "crawl_runs"
    __table_args__ = (
        CheckConstraint("max_pages > 0", name="max_pages_positive"),
        CheckConstraint("max_depth >= 0", name="max_depth_non_negative"),
        CheckConstraint(
            "request_timeout_seconds > 0",
            name="request_timeout_positive",
        ),
        CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        UniqueConstraint(
            "collection_id",
            "idempotency_key",
            name="collection_idempotency_key_unique",
        ),
        Index(
            "ix_crawl_runs_collection_status",
            "collection_id",
            "status",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
    )
    collection_id: Mapped[UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'PENDING'"),
    )

    name: Mapped[str | None] = mapped_column(
        String(160),
        nullable=True,
    )

    seed_urls: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
    )
    allowed_domains: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
    )
    research_intent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    max_pages: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    max_depth: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    request_timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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

    collection: Mapped["Collection"] = relationship(
        back_populates="crawl_runs",
    )
    jobs: Mapped[list["CrawlJob"]] = relationship(
        back_populates="crawl_run",
        cascade="all, delete-orphan",
    )
