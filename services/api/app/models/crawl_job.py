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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.crawl_run import CrawlRun


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"
    __table_args__ = (
        CheckConstraint("depth >= 0", name="depth_non_negative"),
        CheckConstraint(
            "attempt_count >= 0",
            name="attempt_count_non_negative",
        ),
        CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        UniqueConstraint(
            "crawl_run_id",
            "normalized_url",
            name="crawl_run_normalized_url_unique",
        ),
        Index(
            "ix_crawl_jobs_claimable",
            "status",
            "lease_expires_at",
            "created_at",
        ),
        Index(
            "ix_crawl_jobs_run_status",
            "crawl_run_id",
            "status",
        ),
        Index(
            "ix_crawl_jobs_run_domain",
            "crawl_run_id",
            "domain",
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
    parent_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("crawl_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    original_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    normalized_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    domain: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    depth: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'PENDING'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    lease_owner: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    last_claimed_by_worker_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    lease_token: Mapped[UUID | None] = mapped_column(
        nullable=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_error_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    last_error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    http_status_code: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    fetched_bytes: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    fetch_duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
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

    crawl_run: Mapped["CrawlRun"] = relationship(
        back_populates="jobs",
    )
    parent_job: Mapped["CrawlJob | None"] = relationship(
        remote_side="CrawlJob.id",
        back_populates="child_jobs",
    )
    child_jobs: Mapped[list["CrawlJob"]] = relationship(
        back_populates="parent_job",
    )
