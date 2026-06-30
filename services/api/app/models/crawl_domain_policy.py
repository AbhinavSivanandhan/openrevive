from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CrawlDomainPolicy(Base):
    """
    Global crawl politeness state for one hostname.

    This is intentionally not scoped to one campaign: simultaneous campaigns
    must share robots metadata and request pacing for the same domain.
    """

    __tablename__ = "crawl_domain_policies"
    __table_args__ = (
        CheckConstraint(
            "crawl_delay_seconds IS NULL OR crawl_delay_seconds > 0",
            name="crawl_delay_positive",
        ),
        Index(
            "ix_crawl_domain_policies_next_allowed_at",
            "next_allowed_at",
        ),
    )

    domain: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
    )

    robots_txt: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    robots_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    robots_http_status: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    crawl_delay_seconds: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    next_allowed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    active_lease_token: Mapped[UUID | None] = mapped_column(
        nullable=True,
    )
    active_lease_expires_at: Mapped[datetime | None] = mapped_column(
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
