from uuid import UUID, uuid4

import pytest

from app.db.session import session_factory
from app.models.collection import Collection
from app.models.crawl_job import CrawlJob
from app.models.crawl_run import CrawlRun
from app.models.workspace import Workspace


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def create_crawl_job() -> UUID:
    async with session_factory() as session:
        workspace = Workspace(
            name=f"Heartbeat Test {uuid4()}"
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
            seed_urls=["https://example.com/worker"],
            allowed_domains=["example.com"],
            max_pages=25,
            max_depth=2,
            request_timeout_seconds=15,
            max_attempts=3,
            idempotency_key=str(uuid4()),
        )
        session.add(crawl_run)
        await session.flush()

        job = CrawlJob(
            crawl_run_id=crawl_run.id,
            original_url="https://example.com/worker",
            normalized_url="https://example.com/worker",
            domain="example.com",
            depth=0,
            max_attempts=3,
        )
        session.add(job)
        await session.commit()

        return job.id


@pytest.mark.anyio
async def test_register_worker_creates_starting_heartbeat() -> None:
    from app.crawler.worker_heartbeats import register_worker

    worker_id = f"worker-{uuid4()}"

    async with session_factory() as session:
        heartbeat = await register_worker(
            session,
            worker_id=worker_id,
        )

    assert heartbeat.worker_id == worker_id
    assert heartbeat.status == "STARTING"
    assert heartbeat.current_job_id is None
    assert heartbeat.started_at is not None
    assert heartbeat.last_heartbeat_at is not None
    assert heartbeat.stopped_at is None


@pytest.mark.anyio
async def test_worker_heartbeat_records_processing_assignment() -> None:
    from app.crawler.worker_heartbeats import (
        record_heartbeat,
        register_worker,
    )

    worker_id = f"worker-{uuid4()}"
    job_id = await create_crawl_job()

    async with session_factory() as session:
        await register_worker(
            session,
            worker_id=worker_id,
        )

    async with session_factory() as session:
        heartbeat = await record_heartbeat(
            session,
            worker_id=worker_id,
            status="PROCESSING",
            current_job_id=job_id,
        )

    assert heartbeat.status == "PROCESSING"
    assert heartbeat.current_job_id == job_id
    assert heartbeat.stopped_at is None


@pytest.mark.anyio
async def test_idle_heartbeat_clears_current_job_assignment() -> None:
    from app.crawler.worker_heartbeats import (
        record_heartbeat,
        register_worker,
    )

    worker_id = f"worker-{uuid4()}"
    job_id = await create_crawl_job()

    async with session_factory() as session:
        await register_worker(
            session,
            worker_id=worker_id,
        )

    async with session_factory() as session:
        await record_heartbeat(
            session,
            worker_id=worker_id,
            status="PROCESSING",
            current_job_id=job_id,
        )

    async with session_factory() as session:
        heartbeat = await record_heartbeat(
            session,
            worker_id=worker_id,
            status="IDLE",
        )

    assert heartbeat.status == "IDLE"
    assert heartbeat.current_job_id is None


@pytest.mark.anyio
async def test_stop_worker_marks_heartbeat_stopped() -> None:
    from app.crawler.worker_heartbeats import (
        register_worker,
        stop_worker,
    )

    worker_id = f"worker-{uuid4()}"

    async with session_factory() as session:
        await register_worker(
            session,
            worker_id=worker_id,
        )

    async with session_factory() as session:
        heartbeat = await stop_worker(
            session,
            worker_id=worker_id,
        )

    assert heartbeat.status == "STOPPED"
    assert heartbeat.current_job_id is None
    assert heartbeat.stopped_at is not None


@pytest.mark.anyio
async def test_unknown_worker_cannot_report_a_heartbeat() -> None:
    from app.crawler.worker_heartbeats import (
        WorkerNotRegisteredError,
        record_heartbeat,
    )

    async with session_factory() as session:
        with pytest.raises(WorkerNotRegisteredError):
            await record_heartbeat(
                session,
                worker_id=f"missing-{uuid4()}",
                status="IDLE",
            )


@pytest.mark.anyio
async def test_registering_an_active_worker_preserves_process_lifetime() -> None:
    from app.crawler.worker_heartbeats import (
        record_heartbeat,
        register_worker,
    )

    worker_id = f"worker-{uuid4()}"

    async with session_factory() as session:
        first_registration = await register_worker(
            session,
            worker_id=worker_id,
        )

    first_started_at = first_registration.started_at

    async with session_factory() as session:
        await record_heartbeat(
            session,
            worker_id=worker_id,
            status="IDLE",
        )

    async with session_factory() as session:
        second_registration = await register_worker(
            session,
            worker_id=worker_id,
        )

    assert second_registration.status == "IDLE"
    assert second_registration.current_job_id is None
    assert second_registration.started_at == first_started_at
    assert second_registration.stopped_at is None
