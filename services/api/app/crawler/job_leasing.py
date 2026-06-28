from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crawl_job import CrawlJob
from app.models.crawl_run import CrawlRun


async def claim_next_job(
    session: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int,
) -> CrawlJob | None:
    """
    Atomically claim the oldest eligible crawl job.

    Eligible jobs are:
    - PENDING jobs that have attempts remaining
    - RETRY_PENDING jobs that have attempts remaining
    - LEASED jobs whose lease has expired and have attempts remaining

    PostgreSQL SKIP LOCKED ensures concurrently running workers do not
    block each other or claim the same job.
    """
    normalized_worker_id = worker_id.strip()

    if not normalized_worker_id:
        raise ValueError("worker_id must not be blank")

    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be greater than zero")

    async with session.begin():
        database_now = await session.scalar(select(func.now()))

        if not isinstance(database_now, datetime):
            raise RuntimeError("database did not return a timestamp")

        claimable_job = await session.scalar(
            select(CrawlJob)
            .join(
                CrawlRun,
                CrawlRun.id == CrawlJob.crawl_run_id,
            )
            .where(
                CrawlRun.status == "RUNNING",
                CrawlJob.attempt_count < CrawlJob.max_attempts,
                or_(
                    CrawlJob.status == "PENDING",
                    CrawlJob.status == "RETRY_PENDING",
                    and_(
                        CrawlJob.status == "LEASED",
                        CrawlJob.lease_expires_at < database_now,
                    ),
                ),
            )
            .order_by(
                CrawlJob.created_at.asc(),
                CrawlJob.id.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )

        if claimable_job is None:
            return None

        claimable_job.status = "LEASED"
        claimable_job.attempt_count += 1
        claimable_job.lease_owner = normalized_worker_id
        claimable_job.last_claimed_by_worker_id = normalized_worker_id
        claimable_job.lease_token = uuid4()
        claimable_job.lease_expires_at = (
            database_now + timedelta(seconds=lease_seconds)
        )
        claimable_job.started_at = (
            claimable_job.started_at or database_now
        )

    return claimable_job
