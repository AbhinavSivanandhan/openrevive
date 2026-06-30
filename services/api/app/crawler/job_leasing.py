from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crawl_domain_policy import CrawlDomainPolicy
from app.models.crawl_job import CrawlJob
from app.models.crawl_run import CrawlRun

DEFAULT_CRAWL_DELAY_SECONDS = 1.0
# Read a bounded candidate window without locking it, then lock one job
# at a time. This lets workers skip a locked or paced domain and claim
# ready work from another domain without locking the entire frontier.
MAX_CANDIDATES_PER_CLAIM = 32


async def release_domain_lease(
    session: AsyncSession,
    *,
    domain: str,
    lease_token: UUID,
    database_now: datetime,
) -> None:
    """
    Release one active domain reservation owned by this crawl-job lease.

    The next allowed time is set after a request completes or fails, rather
    than when it starts. Later robots.txt support can override
    crawl_delay_seconds per domain without changing this state transition.
    """
    policy = await session.scalar(
        select(CrawlDomainPolicy)
        .where(CrawlDomainPolicy.domain == domain)
        .with_for_update()
    )

    if policy is None or policy.active_lease_token != lease_token:
        return

    delay_seconds = (
        policy.crawl_delay_seconds
        or DEFAULT_CRAWL_DELAY_SECONDS
    )

    policy.active_lease_token = None
    policy.active_lease_expires_at = None
    policy.next_allowed_at = (
        database_now + timedelta(seconds=delay_seconds)
    )


async def claim_next_job(
    session: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int,
) -> CrawlJob | None:
    """
    Atomically claim one runnable crawl job and reserve its domain.

    PostgreSQL remains authoritative for both job and domain leases:
    - SKIP LOCKED prevents duplicate job claims.
    - A locked CrawlDomainPolicy row permits only one active request for a
      domain across every campaign.
    - Jobs blocked by an active reservation or next_allowed_at stay pending
      and do not consume a retry attempt.
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

        candidate_job_ids = list(
            await session.scalars(
                select(CrawlJob.id)
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
                    CrawlJob.priority_score.desc(),
                    CrawlJob.depth.asc(),
                    CrawlJob.created_at.asc(),
                    CrawlJob.id.asc(),
                )
                .limit(MAX_CANDIDATES_PER_CLAIM)
            )
        )

        for candidate_job_id in candidate_job_ids:
            claimable_job = await session.scalar(
                select(CrawlJob)
                .join(
                    CrawlRun,
                    CrawlRun.id == CrawlJob.crawl_run_id,
                )
                .where(
                    CrawlJob.id == candidate_job_id,
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
                .with_for_update(of=CrawlJob, skip_locked=True)
            )

            if claimable_job is None:
                continue
            await session.execute(
                insert(CrawlDomainPolicy)
                .values(domain=claimable_job.domain)
                .on_conflict_do_nothing(
                    index_elements=["domain"],
                )
            )

            policy = await session.scalar(
                select(CrawlDomainPolicy)
                .where(
                    CrawlDomainPolicy.domain
                    == claimable_job.domain
                )
                .with_for_update()
            )

            if policy is None:
                raise RuntimeError(
                    "crawl domain policy could not be created"
                )

            domain_has_active_request = (
                policy.active_lease_expires_at is not None
                and policy.active_lease_expires_at > database_now
            )

            domain_is_paced = (
                policy.next_allowed_at is not None
                and policy.next_allowed_at > database_now
            )

            if domain_has_active_request or domain_is_paced:
                continue

            lease_token = uuid4()
            lease_expires_at = (
                database_now + timedelta(seconds=lease_seconds)
            )

            claimable_job.status = "LEASED"
            claimable_job.attempt_count += 1
            claimable_job.lease_owner = normalized_worker_id
            claimable_job.last_claimed_by_worker_id = (
                normalized_worker_id
            )
            claimable_job.lease_token = lease_token
            claimable_job.lease_expires_at = lease_expires_at
            claimable_job.started_at = (
                claimable_job.started_at or database_now
            )

            policy.active_lease_token = lease_token
            policy.active_lease_expires_at = lease_expires_at

            return claimable_job

    return None
