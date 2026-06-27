from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CrawledDocument(Base):
    """
    Durable metadata for one successfully fetched crawl job.

    Raw HTML belongs in object storage. PostgreSQL stores the object key,
    integrity hash, and extracted text needed for later product features.
    """

    __tablename__ = "crawled_documents"
    __table_args__ = (
        UniqueConstraint(
            "crawl_job_id",
            name="uq_crawled_documents_crawl_job_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    crawl_job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "crawl_jobs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    raw_object_key: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )
    content_type: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    content_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    extracted_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
