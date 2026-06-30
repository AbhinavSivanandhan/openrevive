from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawler.job_leasing import release_domain_lease
from app.models.crawl_job import CrawlJob
from app.models.crawl_run import CrawlRun

TERMINAL_JOB_STATUSES = {
    "SUCCEEDED",
    "FAILED",
    "SKIPPED",
    "CANCELLED",
}


class LeaseLostError(RuntimeError):
    """Raised when a worker tries to finalize a job it no longer owns."""


async def complete_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    worker_id: str,
    lease_token: UUID,
    http_status_code: int,
    fetched_bytes: int,
    fetch_duration_ms: int,
) -> CrawlJob:
    """
    Mark a leased crawl job as successfully completed.

    Completion is accepted only when the caller still owns a live lease.
    This prevents a stale worker from reporting completion after another
    worker has reclaimed the job.
    """
    normalized_worker_id = worker_id.strip()

    if not normalized_worker_id:
        raise ValueError("worker_id must not be blank")

    if not 100 <= http_status_code <= 599:
        raise ValueError("http_status_code must be between 100 and 599")

    if fetched_bytes < 0:
        raise ValueError("fetched_bytes must not be negative")

    if fetch_duration_ms < 0:
        raise ValueError("fetch_duration_ms must not be negative")

    async with session.begin():
        database_now = await session.scalar(select(func.now()))

        if not isinstance(database_now, datetime):
            raise RuntimeError("database did not return a timestamp")

        job = await session.scalar(
            select(CrawlJob)
            .where(CrawlJob.id == job_id)
            .with_for_update()
        )

        if job is None:
            raise LeaseLostError("crawl job does not exist")

        lease_is_live = (
            job.lease_expires_at is not None
            and job.lease_expires_at > database_now
        )

        if (
            job.status != "LEASED"
            or job.lease_owner != normalized_worker_id
            or job.lease_token != lease_token
            or not lease_is_live
        ):
            raise LeaseLostError("crawl job lease is no longer owned")

        await release_domain_lease(
            session,
            domain=job.domain,
            lease_token=lease_token,
            database_now=database_now,
        )

        crawl_run = await session.scalar(
            select(CrawlRun)
            .where(CrawlRun.id == job.crawl_run_id)
            .with_for_update()
        )

        if crawl_run is None:
            raise LeaseLostError("crawl run does not exist")

        job.status = "SUCCEEDED"
        job.lease_owner = None
        job.lease_token = None
        job.lease_expires_at = None
        job.http_status_code = http_status_code
        job.fetched_bytes = fetched_bytes
        job.fetch_duration_ms = fetch_duration_ms
        job.finished_at = database_now

        remaining_non_terminal_jobs = await session.scalar(
            select(func.count())
            .select_from(CrawlJob)
            .where(
                CrawlJob.crawl_run_id == job.crawl_run_id,
                CrawlJob.status.not_in(TERMINAL_JOB_STATUSES),
            )
        )

        if (
            int(remaining_non_terminal_jobs or 0) == 0
            and crawl_run.status == "RUNNING"
        ):
            crawl_run.status = "SUCCEEDED"
            crawl_run.completed_at = database_now
            crawl_run.updated_at = database_now

    return job
