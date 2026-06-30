from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawler.job_finalization import LeaseLostError, TERMINAL_JOB_STATUSES
from app.crawler.job_leasing import release_domain_lease
from app.models.crawl_job import CrawlJob
from app.models.crawl_run import CrawlRun


async def fail_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    worker_id: str,
    lease_token: UUID,
    error_code: str,
    error_message: str,
) -> CrawlJob:
    """
    Record a worker failure for a job it still owns.

    Jobs with retry budget remaining return to RETRY_PENDING.
    Jobs that exhaust their retry budget become FAILED.
    """
    normalized_worker_id = worker_id.strip()
    normalized_error_code = error_code.strip().upper()
    normalized_error_message = error_message.strip()

    if not normalized_worker_id:
        raise ValueError("worker_id must not be blank")

    if not normalized_error_code:
        raise ValueError("error_code must not be blank")

    if len(normalized_error_code) > 64:
        raise ValueError("error_code must be at most 64 characters")

    if not normalized_error_message:
        raise ValueError("error_message must not be blank")

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

        job.lease_owner = None
        job.lease_token = None
        job.lease_expires_at = None
        job.last_error_code = normalized_error_code
        job.last_error_message = normalized_error_message

        if crawl_run.status == "CANCELLED":
            job.status = "CANCELLED"
            job.finished_at = database_now
            return job

        if job.attempt_count < job.max_attempts:
            job.status = "RETRY_PENDING"
            job.finished_at = None
            return job

        job.status = "FAILED"
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
            succeeded_jobs = await session.scalar(
                select(func.count())
                .select_from(CrawlJob)
                .where(
                    CrawlJob.crawl_run_id == job.crawl_run_id,
                    CrawlJob.status == "SUCCEEDED",
                )
            )

            crawl_run.status = (
                "PARTIALLY_SUCCEEDED"
                if int(succeeded_jobs or 0) > 0
                else "FAILED"
            )
            crawl_run.completed_at = database_now
            crawl_run.updated_at = database_now

    return job
