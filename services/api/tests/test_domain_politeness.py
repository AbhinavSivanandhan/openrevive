from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.db.session import session_factory
from app.models.collection import Collection
from app.models.crawl_domain_policy import CrawlDomainPolicy
from app.models.crawl_job import CrawlJob
from app.models.crawl_run import CrawlRun
from app.models.workspace import Workspace


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def create_same_domain_jobs() -> tuple[UUID, list[UUID]]:
    async with session_factory() as session:
        workspace = Workspace(
            name=f"Politeness Test {uuid4()}",
        )
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
            status="RUNNING",
            started_at=datetime.now(UTC),
            seed_urls=[
                "https://example.com/first",
                "https://example.com/second",
            ],
            allowed_domains=["example.com"],
            max_pages=10,
            max_depth=1,
            request_timeout_seconds=15,
            max_attempts=3,
            idempotency_key=str(uuid4()),
        )
        session.add(crawl_run)
        await session.flush()

        jobs = [
            CrawlJob(
                crawl_run_id=crawl_run.id,
                original_url=url,
                normalized_url=url,
                domain="example.com",
                depth=0,
                max_attempts=3,
            )
            for url in crawl_run.seed_urls
        ]
        session.add_all(jobs)
        await session.commit()

        return crawl_run.id, [job.id for job in jobs]


@pytest.mark.anyio
async def test_same_domain_is_serialized_and_paced() -> None:
    from app.crawler.job_finalization import complete_job
    from app.crawler.job_leasing import claim_next_job

    _, job_ids = await create_same_domain_jobs()

    async with session_factory() as session:
        first_job = await claim_next_job(
            session,
            worker_id="worker-a",
            lease_seconds=60,
        )

    assert first_job is not None
    assert first_job.id in job_ids
    assert first_job.lease_token is not None

    second_job_id = next(
        job_id
        for job_id in job_ids
        if job_id != first_job.id
    )

    async with session_factory() as session:
        blocked_job = await claim_next_job(
            session,
            worker_id="worker-b",
            lease_seconds=60,
        )

    assert blocked_job is None

    async with session_factory() as session:
        second_job = await session.get(
            CrawlJob,
            second_job_id,
        )
        policy = await session.get(
            CrawlDomainPolicy,
            "example.com",
        )

    assert second_job is not None
    assert second_job.status == "PENDING"
    assert second_job.attempt_count == 0

    assert policy is not None
    assert policy.active_lease_token == first_job.lease_token
    assert policy.active_lease_expires_at is not None

    async with session_factory() as session:
        await complete_job(
            session,
            job_id=first_job.id,
            worker_id="worker-a",
            lease_token=first_job.lease_token,
            http_status_code=200,
            fetched_bytes=256,
            fetch_duration_ms=10,
        )

    async with session_factory() as session:
        policy = await session.get(
            CrawlDomainPolicy,
            "example.com",
        )

        assert policy is not None
        assert policy.active_lease_token is None
        assert policy.active_lease_expires_at is None
        assert policy.next_allowed_at is not None

        policy.next_allowed_at = (
            datetime.now(UTC) - timedelta(seconds=1)
        )
        await session.commit()

    async with session_factory() as session:
        next_job = await claim_next_job(
            session,
            worker_id="worker-c",
            lease_seconds=60,
        )

    assert next_job is not None
    assert next_job.id == second_job_id
