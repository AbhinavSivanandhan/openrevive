import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select

from app.db.session import session_factory
from app.models.collection import Collection
from app.models.crawl_job import CrawlJob
from app.models.crawl_run import CrawlRun
from app.models.workspace import Workspace


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def create_crawl_run_with_jobs(
    urls: list[str],
    *,
    run_status: str = "RUNNING",
) -> tuple[object, list[object]]:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)

    async with session_factory() as session:
        workspace = Workspace(name=f"Worker Test {uuid4()}")
        session.add(workspace)
        await session.flush()

        collection = Collection(
            workspace_id=workspace.id,
            name=f"Collection {uuid4()}",
        )
        session.add(collection)
        await session.flush()

        crawl_run = CrawlRun(
            collection_id=collection.id,
            status=run_status,
            started_at=(
                created_at
                if run_status == "RUNNING"
                else None
            ),
            seed_urls=urls,
            allowed_domains=["example.com"],
            max_pages=25,
            max_depth=2,
            request_timeout_seconds=15,
            max_attempts=3,
            idempotency_key=str(uuid4()),
            created_at=created_at,
        )
        session.add(crawl_run)
        await session.flush()

        jobs = [
            CrawlJob(
                crawl_run_id=crawl_run.id,
                original_url=url,
                normalized_url=url,
                domain=(
                    urlsplit(url).hostname
                    or "example.com"
                ),
                depth=0,
                max_attempts=3,
                created_at=created_at + timedelta(seconds=index),
            )
            for index, url in enumerate(urls)
        ]
        session.add_all(jobs)
        await session.commit()

        return crawl_run.id, [job.id for job in jobs]


@pytest.mark.anyio
async def test_claim_next_job_leases_oldest_pending_job_and_starts_run() -> None:
    from app.crawler.job_leasing import claim_next_job

    crawl_run_id, job_ids = await create_crawl_run_with_jobs(
        [
            "https://example.com/first",
            "https://example.com/second",
        ]
    )

    async with session_factory() as session:
        claimed_job = await claim_next_job(
            session,
            worker_id="worker-a",
            lease_seconds=60,
        )

    assert claimed_job is not None
    assert claimed_job.id == job_ids[0]
    assert claimed_job.status == "LEASED"
    assert claimed_job.lease_owner == "worker-a"
    assert claimed_job.lease_token is not None
    assert claimed_job.lease_expires_at is not None
    assert claimed_job.attempt_count == 1
    assert claimed_job.started_at is not None

    async with session_factory() as session:
        crawl_run = await session.get(CrawlRun, crawl_run_id)

    assert crawl_run is not None
    assert crawl_run.status == "RUNNING"
    assert crawl_run.started_at is not None


@pytest.mark.anyio
async def test_two_workers_claim_different_jobs_without_duplication() -> None:
    from app.crawler.job_leasing import claim_next_job

    _, job_ids = await create_crawl_run_with_jobs(
        [
            "https://alpha.example.com/first",
            "https://beta.example.com/second",
        ]
    )

    async def claim(worker_id: str) -> CrawlJob | None:
        async with session_factory() as session:
            return await claim_next_job(
                session,
                worker_id=worker_id,
                lease_seconds=60,
            )

    first_claim, second_claim = await asyncio.gather(
        claim("worker-a"),
        claim("worker-b"),
    )

    assert first_claim is not None
    assert second_claim is not None
    assert {first_claim.id, second_claim.id} == set(job_ids)
    assert {first_claim.lease_owner, second_claim.lease_owner} == {
        "worker-a",
        "worker-b",
    }


@pytest.mark.anyio
async def test_expired_lease_can_be_reclaimed_by_another_worker() -> None:
    from app.crawler.job_leasing import claim_next_job

    _, job_ids = await create_crawl_run_with_jobs(
        ["https://example.com/stale"]
    )

    async with session_factory() as session:
        job = await session.get(CrawlJob, job_ids[0])
        assert job is not None

        job.status = "LEASED"
        job.lease_owner = "dead-worker"
        job.lease_token = uuid4()
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        job.attempt_count = 1
        await session.commit()

    async with session_factory() as session:
        claimed_job = await claim_next_job(
            session,
            worker_id="worker-recovery",
            lease_seconds=60,
        )

    assert claimed_job is not None
    assert claimed_job.id == job_ids[0]
    assert claimed_job.status == "LEASED"
    assert claimed_job.lease_owner == "worker-recovery"
    assert claimed_job.attempt_count == 2
    assert claimed_job.lease_expires_at is not None

    async with session_factory() as session:
        stale_owner_jobs = await session.scalars(
            select(CrawlJob).where(
                CrawlJob.lease_owner == "dead-worker"
            )
        )

    assert list(stale_owner_jobs) == []


@pytest.mark.anyio
async def test_claim_records_last_claimed_worker_id() -> None:
    from app.crawler.job_leasing import claim_next_job

    _, job_ids = await create_crawl_run_with_jobs(
        ["https://example.com/attribution"]
    )

    async with session_factory() as session:
        claimed_job = await claim_next_job(
            session,
            worker_id="worker-attribution",
            lease_seconds=60,
        )

    assert claimed_job is not None
    assert claimed_job.id == job_ids[0]

    async with session_factory() as session:
        job = await session.get(CrawlJob, job_ids[0])

    assert job is not None
    assert job.last_claimed_by_worker_id == "worker-attribution"


@pytest.mark.anyio
async def test_claim_next_job_ignores_campaigns_that_are_not_running() -> None:
    from app.crawler.job_leasing import claim_next_job

    pending_run_id, pending_job_ids = await create_crawl_run_with_jobs(
        ["https://example.com/pending"],
        run_status="PENDING",
    )
    paused_run_id, paused_job_ids = await create_crawl_run_with_jobs(
        ["https://example.com/paused"],
        run_status="PAUSED",
    )
    cancelled_run_id, cancelled_job_ids = await create_crawl_run_with_jobs(
        ["https://example.com/cancelled"],
        run_status="CANCELLED",
    )
    running_run_id, running_job_ids = await create_crawl_run_with_jobs(
        ["https://example.com/running"],
        run_status="RUNNING",
    )

    async with session_factory() as session:
        claimed_job = await claim_next_job(
            session,
            worker_id="worker-control-test",
            lease_seconds=60,
        )

    assert claimed_job is not None
    assert claimed_job.id == running_job_ids[0]
    assert claimed_job.status == "LEASED"

    async with session_factory() as session:
        pending_run = await session.get(CrawlRun, pending_run_id)
        paused_run = await session.get(CrawlRun, paused_run_id)
        cancelled_run = await session.get(CrawlRun, cancelled_run_id)

        pending_job = await session.get(CrawlJob, pending_job_ids[0])
        paused_job = await session.get(CrawlJob, paused_job_ids[0])
        cancelled_job = await session.get(
            CrawlJob,
            cancelled_job_ids[0],
        )

    assert pending_run is not None
    assert paused_run is not None
    assert cancelled_run is not None
    assert pending_run.status == "PENDING"
    assert paused_run.status == "PAUSED"
    assert cancelled_run.status == "CANCELLED"

    assert pending_job is not None
    assert paused_job is not None
    assert cancelled_job is not None
    assert pending_job.status == "PENDING"
    assert paused_job.status == "PENDING"
    assert cancelled_job.status == "PENDING"


@pytest.mark.anyio
async def test_claim_next_job_prefers_priority_before_fifo() -> None:
    from app.crawler.job_leasing import claim_next_job

    _, job_ids = await create_crawl_run_with_jobs(
        [
            "https://example.com/low-priority-first",
            "https://example.com/core-priority-later",
        ]
    )

    async with session_factory() as session:
        low_priority_job = await session.get(
            CrawlJob,
            job_ids[0],
        )
        core_priority_job = await session.get(
            CrawlJob,
            job_ids[1],
        )

        assert low_priority_job is not None
        assert core_priority_job is not None

        low_priority_job.priority_score = 5
        low_priority_job.priority_band = "LOW"

        core_priority_job.priority_score = 140
        core_priority_job.priority_band = "CORE"

        await session.commit()

    async with session_factory() as session:
        claimed_job = await claim_next_job(
            session,
            worker_id="worker-priority",
            lease_seconds=60,
        )

    assert claimed_job is not None
    assert claimed_job.id == job_ids[1]
    assert claimed_job.priority_band == "CORE"
    assert claimed_job.priority_score == 140


@pytest.mark.anyio
async def test_paced_high_priority_domain_does_not_starve_ready_domain() -> None:
    from datetime import UTC, datetime, timedelta

    from app.crawler.job_leasing import claim_next_job
    from app.models.crawl_domain_policy import CrawlDomainPolicy

    _, job_ids = await create_crawl_run_with_jobs(
        [
            "https://alpha.example.com/high-priority",
            "https://beta.example.com/ready-lower-priority",
        ]
    )

    async with session_factory() as session:
        high_priority_job = await session.get(
            CrawlJob,
            job_ids[0],
        )
        ready_job = await session.get(
            CrawlJob,
            job_ids[1],
        )

        assert high_priority_job is not None
        assert ready_job is not None

        high_priority_job.priority_score = 100
        ready_job.priority_score = 1

        session.add(
            CrawlDomainPolicy(
                domain="alpha.example.com",
                next_allowed_at=(
                    datetime.now(UTC) + timedelta(seconds=60)
                ),
            )
        )

        await session.commit()

    async with session_factory() as session:
        claimed_job = await claim_next_job(
            session,
            worker_id="worker-ready-domain",
            lease_seconds=60,
        )

    assert claimed_job is not None
    assert claimed_job.id == job_ids[1]
    assert claimed_job.domain == "beta.example.com"
