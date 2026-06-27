from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.crawler.job_leasing import claim_next_job
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
) -> tuple[UUID, list[UUID]]:
    async with session_factory() as session:
        workspace = Workspace(name=f"Finalization Test {uuid4()}")
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
            seed_urls=urls,
            allowed_domains=["example.com"],
            max_pages=25,
            max_depth=2,
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
            for url in urls
        ]
        session.add_all(jobs)
        await session.commit()

        return crawl_run.id, [job.id for job in jobs]


@pytest.mark.anyio
async def test_complete_job_records_result_and_clears_lease() -> None:
    from app.crawler.job_finalization import complete_job

    _, _ = await create_crawl_run_with_jobs(
        ["https://example.com/complete"]
    )

    async with session_factory() as session:
        claimed_job = await claim_next_job(
            session,
            worker_id="worker-a",
            lease_seconds=60,
        )

    assert claimed_job is not None
    assert claimed_job.lease_token is not None

    async with session_factory() as session:
        completed_job = await complete_job(
            session,
            job_id=claimed_job.id,
            worker_id="worker-a",
            lease_token=claimed_job.lease_token,
            http_status_code=200,
            fetched_bytes=4_096,
            fetch_duration_ms=125,
        )

    assert completed_job.status == "SUCCEEDED"
    assert completed_job.lease_owner is None
    assert completed_job.lease_token is None
    assert completed_job.lease_expires_at is None
    assert completed_job.http_status_code == 200
    assert completed_job.fetched_bytes == 4_096
    assert completed_job.fetch_duration_ms == 125
    assert completed_job.finished_at is not None


@pytest.mark.anyio
async def test_complete_job_rejects_a_stale_or_wrong_lease_token() -> None:
    from app.crawler.job_finalization import (
        LeaseLostError,
        complete_job,
    )

    _, _ = await create_crawl_run_with_jobs(
        ["https://example.com/lease-token"]
    )

    async with session_factory() as session:
        claimed_job = await claim_next_job(
            session,
            worker_id="worker-a",
            lease_seconds=60,
        )

    assert claimed_job is not None

    async with session_factory() as session:
        with pytest.raises(LeaseLostError):
            await complete_job(
                session,
                job_id=claimed_job.id,
                worker_id="worker-a",
                lease_token=uuid4(),
                http_status_code=200,
                fetched_bytes=100,
                fetch_duration_ms=10,
            )

    async with session_factory() as session:
        job = await session.get(CrawlJob, claimed_job.id)

    assert job is not None
    assert job.status == "LEASED"
    assert job.lease_owner == "worker-a"
    assert job.http_status_code is None


@pytest.mark.anyio
async def test_completing_all_jobs_marks_crawl_run_succeeded() -> None:
    from app.crawler.job_finalization import complete_job

    crawl_run_id, _ = await create_crawl_run_with_jobs(
        [
            "https://example.com/first",
            "https://example.com/second",
        ]
    )

    for worker_id in ("worker-a", "worker-b"):
        async with session_factory() as session:
            claimed_job = await claim_next_job(
                session,
                worker_id=worker_id,
                lease_seconds=60,
            )

        assert claimed_job is not None
        assert claimed_job.lease_token is not None

        async with session_factory() as session:
            await complete_job(
                session,
                job_id=claimed_job.id,
                worker_id=worker_id,
                lease_token=claimed_job.lease_token,
                http_status_code=200,
                fetched_bytes=1_024,
                fetch_duration_ms=25,
            )

    async with session_factory() as session:
        crawl_run = await session.get(CrawlRun, crawl_run_id)

    assert crawl_run is not None
    assert crawl_run.status == "SUCCEEDED"
    assert crawl_run.started_at is not None
    assert crawl_run.completed_at is not None
