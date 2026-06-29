from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.db.session import session_factory
from app.models.collection import Collection
from app.models.crawl_job import CrawlJob
from app.models.crawl_run import CrawlRun
from app.models.worker_heartbeat import WorkerHeartbeat
from app.models.workspace import Workspace


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def create_crawl_job(
    *,
    max_attempts: int = 3,
) -> UUID:
    async with session_factory() as session:
        workspace = Workspace(
            name=f"Worker Service Test {uuid4()}"
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
            started_at=datetime.now(timezone.utc),
            seed_urls=["https://example.com/worker-service"],
            allowed_domains=["example.com"],
            max_pages=25,
            max_depth=2,
            request_timeout_seconds=15,
            max_attempts=max_attempts,
            idempotency_key=str(uuid4()),
        )
        session.add(crawl_run)
        await session.flush()

        job = CrawlJob(
            crawl_run_id=crawl_run.id,
            original_url="https://example.com/worker-service",
            normalized_url="https://example.com/worker-service",
            domain="example.com",
            depth=0,
            max_attempts=max_attempts,
        )
        session.add(job)
        await session.commit()

        return job.id


@pytest.mark.anyio
async def test_worker_cycle_reports_idle_when_queue_is_empty() -> None:
    from app.crawler.worker_runtime import FetchResult
    from app.crawler.worker_service import run_worker_cycle

    worker_id = f"worker-idle-{uuid4()}"
    fetch_called = False

    async def fetch_page(
        url: str,
        timeout_seconds: int,
    ) -> FetchResult:
        nonlocal fetch_called
        fetch_called = True
        raise AssertionError("fetcher must not run for an empty queue")

    outcome = await run_worker_cycle(
        worker_id=worker_id,
        lease_seconds=60,
        fetch_page=fetch_page,
    )

    assert outcome.state == "IDLE"
    assert outcome.job_id is None
    assert fetch_called is False

    async with session_factory() as session:
        heartbeat = await session.get(
            WorkerHeartbeat,
            worker_id,
        )

    assert heartbeat is not None
    assert heartbeat.status == "IDLE"
    assert heartbeat.current_job_id is None
    assert heartbeat.stopped_at is None


@pytest.mark.anyio
async def test_worker_cycle_reports_processing_while_fetching() -> None:
    from app.crawler.worker_runtime import FetchResult
    from app.crawler.worker_service import run_worker_cycle

    job_id = await create_crawl_job()
    worker_id = f"worker-processing-{uuid4()}"

    async def fetch_page(
        url: str,
        timeout_seconds: int,
    ) -> FetchResult:
        async with session_factory() as session:
            heartbeat = await session.get(
                WorkerHeartbeat,
                worker_id,
            )

        assert heartbeat is not None
        assert heartbeat.status == "PROCESSING"
        assert heartbeat.current_job_id == job_id

        return FetchResult(
            http_status_code=200,
            fetched_bytes=2_048,
            fetch_duration_ms=50,
        )

    outcome = await run_worker_cycle(
        worker_id=worker_id,
        lease_seconds=60,
        fetch_page=fetch_page,
    )

    assert outcome.state == "SUCCEEDED"
    assert outcome.job_id == job_id

    async with session_factory() as session:
        heartbeat = await session.get(
            WorkerHeartbeat,
            worker_id,
        )
        job = await session.get(CrawlJob, job_id)

    assert heartbeat is not None
    assert heartbeat.status == "IDLE"
    assert heartbeat.current_job_id is None

    assert job is not None
    assert job.status == "SUCCEEDED"


@pytest.mark.anyio
async def test_worker_cycle_returns_to_idle_after_fetch_failure() -> None:
    from app.crawler.worker_runtime import FetchFailure, FetchResult
    from app.crawler.worker_service import run_worker_cycle

    job_id = await create_crawl_job(max_attempts=1)
    worker_id = f"worker-failure-{uuid4()}"

    async def fetch_page(
        url: str,
        timeout_seconds: int,
    ) -> FetchResult:
        raise FetchFailure(
            error_code="HTTP_TIMEOUT",
            error_message="upstream request timed out",
        )

    outcome = await run_worker_cycle(
        worker_id=worker_id,
        lease_seconds=60,
        fetch_page=fetch_page,
    )

    assert outcome.state == "FAILED"
    assert outcome.job_id == job_id

    async with session_factory() as session:
        heartbeat = await session.get(
            WorkerHeartbeat,
            worker_id,
        )
        job = await session.get(CrawlJob, job_id)

    assert heartbeat is not None
    assert heartbeat.status == "IDLE"
    assert heartbeat.current_job_id is None

    assert job is not None
    assert job.status == "FAILED"
    assert job.last_error_code == "HTTP_TIMEOUT"
